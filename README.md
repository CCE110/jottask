# Jottask

## Quick Reference

**GitHub**: https://github.com/CCE110/jottask
**Railway**: rob-crm-tasks-production.up.railway.app
**Status**: FULLY OPERATIONAL

## Working Features
- Email-to-task processing (every 15 min)
- Task reminders (30 min before, AEST)
- Daily summaries (8 AM AEST)
- Clickable buttons (complete/postpone)
- 5 businesses + full CRM database

## Key Commands
```bash
# Deploy
git add . && git commit -m "msg" && git push

# Logs
railway logs --tail 50

# Test
python3 -c "from enhanced_task_manager import EnhancedTaskManager; EnhancedTaskManager().send_enhanced_daily_summary()"
```

## Critical Info
- Timezone: AEST (8 AM = 22:00 UTC)
- Email in: robcrm.ai@gmail.com
- Email out: rob@cloudcleanenergy.com.au
- Zoho SMTP: smtp.zoho.com.au:465
- Railway CLI for env vars

## Business IDs
- Cloud Clean Energy: feb14276-5c3d-4fcf-af06-9a8f54cf7159
- DSW: 390fbfb9-1166-45a5-ba17-39c9c48d5f9a
- KVELL: e15518d2-39c2-4503-95bd-cb6f0b686022
- AI Project Pro: ec5d7aab-8d74-4ef2-9d92-01b143c68c82
- VHC: 0b083ea5-ff45-4606-8cae-6ed387926641

