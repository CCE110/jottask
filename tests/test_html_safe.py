from html_safe import esc, esc_br, plain_subject

SCRIPT = '<script>alert(1)</script>'
ATTR = '" onmouseover="alert(1)'


def test_esc_neutralises_tags_and_quotes():
    assert '<' not in esc(SCRIPT) and '>' not in esc(SCRIPT)
    assert '"' not in esc(ATTR) and "'" not in esc("' onclick='x")


def test_esc_none_and_non_string():
    assert esc(None) == ''
    assert esc(42) == '42'


def test_esc_br_keeps_line_breaks_but_not_tags():
    out = esc_br('a\r\n<b>b</b>\nc')
    assert out == 'a<br>&lt;b&gt;b&lt;/b&gt;<br>c'


def test_plain_subject_single_line_and_capped():
    s = plain_subject('line1\nline2\r\nline3' + 'x' * 500, limit=50)
    assert '\n' not in s and '\r' not in s and len(s) <= 50
    assert plain_subject(None) == ''
