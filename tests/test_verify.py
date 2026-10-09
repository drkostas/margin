import hashlib
import hmac
import time

from margin.inbox import verify


def sign(secret, body, t=None):
    t = str(int((t or time.time()) * 1000))
    return f"t={t},s=" + hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()


def test_good_signature_passes():
    assert verify("k", sign("k", b"{}"), b"{}")


def test_wrong_secret_body_or_old_timestamp_fail():
    assert not verify("k", sign("other", b"{}"), b"{}")
    assert not verify("k", sign("k", b"{}"), b"{ }")
    assert not verify("k", sign("k", b"{}", time.time() - 3600), b"{}")
    assert not verify("k", None, b"{}")
    assert not verify("k", "garbage", b"{}")


def test_no_secret_configured_accepts():
    assert verify(None, None, b"{}")
