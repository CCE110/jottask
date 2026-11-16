import pytest
from signup_guard import check_signup, is_disposable_email


def test_real_scam_submission_rejected():
    ok, why = check_signup(
        '* * * SCAM SERVICE 24/7: https://myxa.cc/ * * * hs=a09dbf9a69065fc03ee',
        '2ry~e95v0d4d2y@merepost.com')
    assert not ok


def test_second_real_spam_rejected():
    ok, _ = check_signup('* * * <a href="http://fretigo.com/go-na.php?hlqgtf">$3,222 deposit available',
                         'bgg~671lbkbz0p7mt7ywd@fextemp.com')
    assert not ok


@pytest.mark.parametrize('name,email', [
    ('Alex Smith', 'alex@solarco.example.com.au'),
    ("Siobhan O'Brien", 'siobhan@example.com.au'),
    ('Dr. J. Nguyen-Tran', 'j.nguyen@gmail.com'),
    ('Sam Jones', 'sam@installer.example.com'),
])
def test_legit_signups_pass(name, email):
    assert check_signup(name, email) == (True, None)


@pytest.mark.parametrize('name', [
    'visit www.spam.ru', 'http://x.co', 'bob.com', '<b>x</b>', 'Win bitcoin now',
    'FREE casino', 'a' * 81, '', '   ', 'line\nbreak',
])
def test_bad_names_rejected(name):
    assert not check_signup(name, 'ok@gmail.com')[0]


@pytest.mark.parametrize('email', [
    'a@merepost.com', 'a@sub.mailinator.com', 'A@YOPMAIL.COM', 'a@fextemp.com',
])
def test_disposable_rejected(email):
    assert is_disposable_email(email)
    assert check_signup('Real Person', email) == (False, 'disposable-email-domain')


def test_honeypot_rejected():
    assert check_signup('Real Person', 'ok@gmail.com', honeypot='http://x')[1] == 'honeypot-filled'


def test_bad_email_format():
    assert not check_signup('Real Person', 'not-an-email')[0]
