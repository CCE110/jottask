"""Call Point leads (James 27 Oct, Tommy 26 Oct 2026) never became tasks: their only tags are
'1. lead to appt pipeline' + 'ai off', which no LEAD_TAGS token matched. Tags below are copied from the live contacts."""
import os
os.environ.setdefault('PIPEREPLY_TOKEN', 'x')
import dsw_lead_poller as d


def test_call_point_lead_is_ingested():
    assert d.is_lead_contact({'source': 'Call Point', 'tags': ['1. lead to appt pipeline', 'ai off']})


def test_existing_source_tags_still_match():
    assert d.is_lead_contact({'tags': ['b-008. facebook form: first entry', 'ai off']})


def test_later_pipeline_stages_alone_do_not_match():
    assert not d.is_lead_contact({'tags': ['3. quote to sale pipeline', '3. project sent', 'ai off']})
    assert not d.is_lead_contact({'tags': []})
    assert not d.is_lead_contact({})
