"""make_opensolar must send the surname as family_name (OpenSolar ignores last_name)."""
import os
import types

os.environ.setdefault('PIPEREPLY_TOKEN', 'x')
import dsw_lead_poller as d


def _payload(monkeypatch, **kw):
    sent = []

    class R:
        ok, status_code, text = True, 201, ''
        def json(self): return {'id': 1, 'lat': -27.0, 'lon': 152.0}

    monkeypatch.setattr(d, 'opensolar', lambda: types.SimpleNamespace(token='t', org_id=1))
    monkeypatch.setattr(d.req, 'post', lambda url, **k: (sent.append(k['json']['contacts_new'][0]) or R()))
    d.make_opensolar(phone='+61432943477', email='x@y.com', address='11 Juniper Dr', city='Greenbank',
                     state='QLD', postcode='4124', **kw)
    return sent[0]


def test_first_and_last_from_pipereply(monkeypatch):
    c = _payload(monkeypatch, name='Tracie Burton', first_name='Tracie', last_name='Burton')
    assert (c['first_name'], c['family_name']) == ('Tracie', 'Burton')


def test_display_name_only_is_split(monkeypatch):
    c = _payload(monkeypatch, name='Mary Jane Watson')
    assert (c['first_name'], c['family_name']) == ('Mary', 'Jane Watson')


def test_single_name_has_empty_family_name(monkeypatch):
    c = _payload(monkeypatch, name='Madonna', first_name='Madonna', last_name='')
    assert (c['first_name'], c['family_name']) == ('Madonna', '')
