# scratchpad-persistent/

Reversal payloads and audit trails that must survive session termination.

`/tmp/claude-501/.../scratchpad/` dies when the Claude Code session ends or the
OS reaps `/tmp`. Anything Rob needs as an **undo record for writes we've done**
belongs here, not there.

## What goes in here

- Backup JSONs of rows we cancelled/updated (dupe_cancel, cross-rep cleanup, etc.)
- One-shot payloads that could rebuild deleted state
- Any "if this write was wrong, here's how to undo it" material

## Naming

`<action>_backup_<YYYYMMDDTHHMMSS>.json` — e.g. `dupe_cancel_backup_20260915T000558.json`.

## Gitignore

This whole folder is gitignored — the JSON files may contain lead PII (phones,
addresses, emails) that shouldn't hit GitHub. If you want a redacted copy in git
for audit, dump one with names/phones stripped and commit that separately.

## What's lost from before this folder existed

Prior session scratchpads that already got reaped:

- `dupe_cancel_backup_20260915T000558.json` — Petrina O'Hara `7e5433ae` + Hilliar
  Carter `ddd377eb` cancelled 2026-09-15. Reversal is trivial without the JSON:
  `UPDATE tasks SET status='pending' WHERE id IN ('7e5433ae-...','ddd377eb-...');`
- Silke/Aimee storm cleanup (2026-09-10) — 8+5 dupe cancellations, 50 CRM notes deleted.
  Backups in a prior session dir, now gone. State on production has been stable since,
  so no known undo need.
- Cross-rep task cancellations (Mukhtar/Johan/Mary/Andrew/Lucas, 2026-09-11) — 5 tasks.
  Backup + audit note pattern was used; per-task cancel timestamps recoverable from
  `tasks.completed_at` where `status='cancelled'`.

Going forward, every reversal write we do lands here.
