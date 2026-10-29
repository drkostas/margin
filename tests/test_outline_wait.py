import io
import urllib.error

import pytest

from margin import outline
from margin.outline import Outline, OutlineError


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body=b""):
    return urllib.error.HTTPError("http://outline.example.org/api/auth.info", code, "err", {}, io.BytesIO(body))


def serve(monkeypatch, answers):
    """urlopen raises or returns each of `answers` in turn; sleeping costs nothing."""
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return Reply(a)

    monkeypatch.setattr(outline.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(outline.time, "sleep", lambda s: None)
    return calls


ME = b'{"data": {"user": {"id": "u1"}}}'


def test_a_gateway_error_is_retried(monkeypatch):
    calls = serve(monkeypatch, [http_error(502), http_error(503), ME])
    assert Outline("http://outline.example.org", "k").me() == {"id": "u1"}
    assert len(calls) == 3


def test_a_gateway_error_past_the_retries_is_raised(monkeypatch):
    serve(monkeypatch, [http_error(502)] * 4)
    with pytest.raises(OutlineError) as e:
        Outline("http://outline.example.org", "k").call("auth.info")
    assert e.value.status == 502


def test_a_client_error_is_not_retried(monkeypatch):
    calls = serve(monkeypatch, [http_error(401, b'{"error": "authentication_required"}')])
    with pytest.raises(OutlineError):
        Outline("http://outline.example.org", "k").call("auth.info")
    assert len(calls) == 1


def test_wait_ready_waits_out_a_restart(monkeypatch):
    serve(monkeypatch, [http_error(502)] * 8 + [ME])
    said = []
    assert Outline("http://outline.example.org", "k").wait_ready(log=said.append) == {"id": "u1"}
    assert said and "502" in said[0]


def test_wait_ready_raises_a_bad_key_at_once(monkeypatch):
    calls = serve(monkeypatch, [http_error(401, b'{"error": "authentication_required"}')])
    with pytest.raises(OutlineError):
        Outline("http://outline.example.org", "k").wait_ready()
    assert len(calls) == 1


def test_wait_ready_gives_up_at_its_timeout(monkeypatch):
    serve(monkeypatch, [http_error(502)] * 4)
    with pytest.raises(OutlineError):
        Outline("http://outline.example.org", "k").wait_ready(timeout=0)
