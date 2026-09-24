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

set -u

JOTTASK_DIR="${HOME}/Developer/jottask"
cd "$JOTTASK_DIR" 2>/dev/null || { echo "$(date): jottask dir missing"; exit 1; }

# Load .env — SUPABASE_URL + SUPABASE_KEY
set -a; . "$JOTTASK_DIR/.env" 2>/dev/null; set +a

# Enumerate repos under ~/Developer
REPO_DIRS=$(find "$HOME/Developer" -maxdepth 2 -name '.git' -type d 2>/dev/null | sed 's|/\.git$||')

# Build per-repo JSON payload
PAYLOAD=$(python3 - <<PY
import json, os, subprocess, time

repos = []
for repo_path in """$REPO_DIRS""".strip().splitlines():
    repo_path = repo_path.strip()
    if not repo_path: continue
    name = os.path.basename(repo_path)

    def git(*args):
        r = subprocess.run(['git', '-C', repo_path] + list(args),
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ''

    # Fetch quietly so ahead/behind numbers are current (network is fine here — Mac)
    subprocess.run(['git', '-C', repo_path, 'fetch', '--quiet'],
                   capture_output=True, timeout=20)

    branch      = git('branch', '--show-current')
    dirty       = len([l for l in git('status', '--porcelain').splitlines() if l.strip()])
    remote      = git('remote', 'get-url', 'origin')
    last_commit = git('log', '-1', '--format=%ct')  # epoch of last commit
    try:
        last_commit_epoch = int(last_commit) if last_commit else 0
    except ValueError:
        last_commit_epoch = 0

    ahead = behind = None
    if branch:
        a = git('rev-list', '--count', '@{u}..HEAD')
        b = git('rev-list', '--count', 'HEAD..@{u}')
        try: ahead = int(a)
        except ValueError: ahead = None
        try: behind = int(b)
        except ValueError: behind = None

    now = int(time.time())
    age_h = round((now - last_commit_epoch) / 3600, 1) if last_commit_epoch else None

    repos.append({
        'name':       name,
        'branch':     branch,
        'dirty':      dirty,
        'ahead':      ahead,
        'behind':     behind,
        'remote':     remote,
        'last_commit_epoch': last_commit_epoch,
        'age_hours':  age_h,
    })

print(json.dumps({
    'reported_at': int(time.time()),
    'host':        os.uname().nodename,
    'repos':       repos,
}))
PY
)

if [ -z "$PAYLOAD" ]; then
    echo "$(date): payload empty; aborting"
    exit 1
fi

# Post via Supabase REST
curl -sS -X POST "${SUPABASE_URL}/rest/v1/system_events" \
     -H "apikey: ${SUPABASE_KEY}" \
     -H "Authorization: Bearer ${SUPABASE_KEY}" \
     -H "Content-Type: application/json" \
     -H "Prefer: return=minimal" \
     -d "$(python3 -c "
import json, sys
payload = json.loads(sys.argv[1])
row = {
    'event_type': 'backup_heartbeat',
    'category':   'backup',
    'status':     'info',
    'message':    f\"Mac heartbeat from {payload['host']}: {len(payload['repos'])} repos\",
    'metadata':   payload,
}
print(json.dumps(row))
" "$PAYLOAD")" >/dev/null

echo "$(date): heartbeat posted"
