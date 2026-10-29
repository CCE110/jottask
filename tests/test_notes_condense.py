"""The condenser must never read, send to the LLM, or alter anything of Rob's."""
import notes_condense as nc
from tests.fake_supabase import FakeDB

ROB_NOTES = "Cold room 3 kw per hour \n4 freezers 1.5 kw \nbarn style shed"
UNLABELLED = "roof house is tiled, shed is collorbond\nshed first then house\n10kw solar?\n\nbattery - has space inside shed\n2 options : 28 and 30kw battery"

DESC = f"""Phone: +61400000000
Email: a@b.com
Source: 📋 SolarQuotes
CRM: https://app.pipereply.com/v2/location/L/contacts/detail/CID1
OpenSolar: https://app.opensolar.com/#/projects/1/info
Sub-note: hasnt started the build yet

CUSTOMER REQUIREMENTS
* 13.3kW solar system
* Cash payment preferred

PROPERTY
* 14 Kerwick St, Redbank QLD 4301
* Quarterly bill $500-$1,000

CRM NOTES:
MY NOTES (18 Jun 2026 10:25 AM):
{ROB_NOTES}
OpenSolar: https://app.opensolar.com/#/projects/1/info
<!-- crm-note: abc -->

Project successfully created.
<!-- crm-note: def -->

Size: Fill my roof with solar panels
Timeframe: In the next 3 months
Features: On Grid Solar
Battery System
Homevisit: Yes
Importantnotessplit: dup:dup
<!-- crm-note: ghi -->

Message contained within proposal email:

Subject: Proposal
Best Regards,
Rob Lowe
View Solar Energy Proposal

{UNLABELLED}

AI Call Summary:

1. What the caller wants
- Battery and solar

<!-- APPT-POLL -->
event_id: E1
<!-- /APPT-POLL -->

MY NOTES:
{ROB_NOTES}"""


class SpyMerge:
    def __init__(self): self.seen = None
    def __call__(self, text):
        self.seen = text
        return 'Requirements\n- 13.3kW solar\n- Cash'


def test_llm_never_sees_rob_or_unrecognised_text():
    spy = SpyMerge()
    out, rep = nc.condense(DESC, merge=spy)
    for line in ROB_NOTES.split('\n') + UNLABELLED.split('\n'):
        if line.strip():
            assert line not in spy.seen, line
    assert 'Sub-note' not in spy.seen and 'hasnt started' not in spy.seen
    assert '13.3kW' in spy.seen and 'Kerwick' in spy.seen            # system text did go through


def test_notes_survive_byte_identical_and_verify_passes():
    out, rep = nc.condense(DESC, merge=SpyMerge())
    assert nc.verify(rep, out)
    assert ROB_NOTES in out and UNLABELLED in out and 'Sub-note: hasnt started the build yet' in out
    assert out.count(ROB_NOTES) == 2                                  # top-level + mirrored, neither merged away
    assert 'event_id: E1' in out


def test_partition_is_lossless():
    body = nc.split_description(DESC)['body']
    assert '\n'.join(t for _, t in nc.partition(body)) == body


def test_unrecognised_my_notes_form_aborts():
    assert nc.condense("Phone: 1\n\nMY NOTES-ish stuff\nMY NOTES here", merge=SpyMerge()) is None


def test_stored_block_reused_until_description_changes_and_description_never_written():
    db = FakeDB()
    db.tables['tasks'] = [{'id': 't1', 'description': DESC, 'metadata': {}, 'category': 'DSW Solar'}]
    calls = []
    merge = lambda txt: (calls.append(1) or 'Requirements\n- x')
    row = lambda: dict(db.tables['tasks'][0])
    a = nc.stored_or_build(row(), db, merge=merge)
    assert a and len(calls) == 1 and db.tables['tasks'][0]['description'] == DESC     # raw untouched
    assert db.tables['tasks'][0]['metadata']['condensed']['cust_display'] == a['cust_display']
    nc.stored_or_build(row(), db, merge=merge)
    assert len(calls) == 1                                                            # cached
    db.tables['tasks'][0]['description'] = DESC + '\nnew CRM line\nTimeframe: soon'
    nc.stored_or_build(row(), db, merge=merge)
    assert len(calls) == 2                                                            # re-condensed on change
