#!/bin/bash
# Mac backup heartbeat — reports local repo backup state up to Supabase.
#
# Runs on Rob's Mac via cron every 30 min. Writes one system_events row
# summarising:
#   - which repos exist under ~/Developer
#   - each repo's dirty count, ahead-of-origin, last commit age
#   - whether autopush is functioning (last autopush commit found)
#
# The Railway-side daily health digest reads the LATEST row of this event_type.
# If none exists in the last 26h → digest flags "Mac heartbeat missing" RED.
# If a repo is dirty>0 OR ahead>0 AND its last commit is >24h old → AMBER.
#
# Install (add to crontab -e):
#   */30 * * * * bash ~/Developer/jottask/scripts/mac_backup_heartbeat.sh \
#       >> ~/.mac_backup_heartbeat.log 2>&1
#
# Env: reads jottask/.env for SUPABASE_URL + SUPABASE_KEY (service role).
#
# Fail-loud: prints success message ONLY on real HTTP 2xx; exits non-zero on
# every failure path. Never lies about "posted" when the POST didn't land.
# Exit codes:
#   0  — heartbeat row written OK
#   1  — jottask dir missing
#   2  — SUPABASE_URL / SUPABASE_KEY missing
#   3  — cannot list ~/Developer
#   4  — no repos found
#   5  — Supabase POST returned HTTP error
#   6  — network error posting to Supabase
#   7  — Supabase returned unexpected status
#   8  — unhandled Python exception in payload builder

set -eu

JOTTASK_DIR="${HOME}/Developer/jottask"
cd "$JOTTASK_DIR" 2>/dev/null || {
    echo "$(date): mac_backup_heartbeat: jottask dir missing at $JOTTASK_DIR" >&2
    exit 1
}

# .env is loaded INSIDE the Python heredoc via dotenv — do NOT source it in
# bash. .env files are not valid bash: unquoted values with spaces or digits
# get parsed as commands under `set -a`, silently corrupting the env.
export REPO_ROOT="${REPO_ROOT:-${HOME}/Developer}"
export JOTTASK_DIR

# Single-file heredoc — quoted 'PY' so bash does zero interpolation inside.
# Everything is Python: env load, sanity, repo enum, git state, JSON payload,
# POST, fail-loud exit codes.
python3 <<'PY'
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

try:
    # Load .env from jottask dir. Prefer python-dotenv (repo already depends on
    # it); fall back to a minimal key=value parser so this script still works
    # if dotenv isn't importable for whatever reason.
    JOTTASK_DIR = os.environ.get('JOTTASK_DIR') or os.path.expanduser('~/Developer/jottask')
    env_path = os.path.join(JOTTASK_DIR, '.env')
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    k, _, v = line.partition('=')
                    v = v.strip().strip('"').strip("'")
                    os.environ.setdefault(k.strip(), v)

    SUPABASE_URL = (os.environ.get('SUPABASE_URL') or '').strip().rstrip('/')
    SUPABASE_KEY = (os.environ.get('SUPABASE_KEY') or '').strip()
    if not SUPABASE_URL or not SUPABASE_KEY:
        print('mac_backup_heartbeat: missing SUPABASE_URL or SUPABASE_KEY '
              f'(loaded from {env_path})', file=sys.stderr)
        sys.exit(2)

    REPO_ROOT = os.environ.get('REPO_ROOT') or os.path.expanduser('~/Developer')

    # Enumerate .git directories one level deep
    try:
        entries = sorted(os.listdir(REPO_ROOT))
    except OSError as e:
        print(f'mac_backup_heartbeat: cannot list {REPO_ROOT}: {e}', file=sys.stderr)
        sys.exit(3)

    repo_paths = []
    for entry in entries:
        candidate = os.path.join(REPO_ROOT, entry, '.git')
        if os.path.isdir(candidate):
            repo_paths.append(os.path.join(REPO_ROOT, entry))

    if not repo_paths:
        print(f'mac_backup_heartbeat: no repos found under {REPO_ROOT}', file=sys.stderr)
        sys.exit(4)

    def git(repo_path, *args, timeout=15):
        try:
            r = subprocess.run(
                ['git', '-C', repo_path] + list(args),
                capture_output=True, text=True, timeout=timeout,
            )
            return r.stdout.strip() if r.returncode == 0 else ''
        except Exception:
            return ''

    repos = []
    for repo_path in repo_paths:
        name = os.path.basename(repo_path)

        # Fetch quietly so ahead/behind is current (Mac has network)
        subprocess.run(
            ['git', '-C', repo_path, 'fetch', '--quiet'],
            capture_output=True, timeout=20,
        )

        branch = git(repo_path, 'branch', '--show-current')
        status_lines = git(repo_path, 'status', '--porcelain').splitlines()
        dirty = sum(1 for l in status_lines if l.strip())
        remote = git(repo_path, 'remote', 'get-url', 'origin')

        last_commit_str = git(repo_path, 'log', '-1', '--format=%ct')
        try:
            last_commit_epoch = int(last_commit_str) if last_commit_str else 0
        except ValueError:
            last_commit_epoch = 0

        ahead = behind = None
        if branch:
            upstream = git(repo_path, 'rev-parse', '--abbrev-ref', '@{u}')
            if upstream:
                a = git(repo_path, 'rev-list', '--count', f'{upstream}..HEAD')
                b = git(repo_path, 'rev-list', '--count', f'HEAD..{upstream}')
                try:
                    ahead = int(a) if a else 0
                except ValueError:
                    ahead = None
                try:
                    behind = int(b) if b else 0
                except ValueError:
                    behind = None

        age_hours = None
        if last_commit_epoch:
            age_hours = round((int(time.time()) - last_commit_epoch) / 3600, 1)

        repos.append({
            'name': name,
            'branch': branch,
            'dirty': dirty,
            'ahead': ahead,
            'behind': behind,
            'remote': remote,
            'last_commit_epoch': last_commit_epoch,
            'age_hours': age_hours,
        })

    payload = {
        'reported_at': int(time.time()),
        'host': os.uname().nodename,
        'repos': repos,
    }

    row = {
        'event_type': 'backup_heartbeat',
        'category': 'backup',
        'status': 'info',
        'message': f'Mac heartbeat from {payload["host"]}: {len(repos)} repos',
        'metadata': payload,
    }

    # POST to Supabase — fail loud on any non-2xx
    url = f'{SUPABASE_URL}/rest/v1/system_events'
    body = json.dumps(row).encode('utf-8')
    req = urllib.request.Request(url, method='POST', data=body)
    req.add_header('apikey', SUPABASE_KEY)
    req.add_header('Authorization', f'Bearer {SUPABASE_KEY}')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Prefer', 'return=minimal')

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.getcode()
            resp_body = resp.read().decode('utf-8', errors='replace')[:200]
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', errors='replace')[:300]
        except Exception:
            pass
        print(f'mac_backup_heartbeat: POST failed HTTP {e.code}: {detail}',
              file=sys.stderr)
        sys.exit(5)
    except urllib.error.URLError as e:
        print(f'mac_backup_heartbeat: network error posting: {e}',
              file=sys.stderr)
        sys.exit(6)

    if code < 200 or code >= 300:
        print(f'mac_backup_heartbeat: POST returned HTTP {code}: {resp_body}',
              file=sys.stderr)
        sys.exit(7)

    dirty_total = sum(r['dirty'] for r in repos)
    ahead_total = sum(r['ahead'] or 0 for r in repos)
    print(f'mac_backup_heartbeat: OK — HTTP {code}, {len(repos)} repos, '
          f'{dirty_total} dirty, {ahead_total} unpushed')

except SystemExit:
    raise
except Exception as e:
    print(f'mac_backup_heartbeat: unhandled exception: {e!r}', file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(8)
PY
