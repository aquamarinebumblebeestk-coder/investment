import pytest

from jpstock.client import JQuantsClient, PlanNotSupportedError, RateLimiter
from jpstock.plans import get_plan


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.ok = 200 <= status < 300
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.requests = []

    def get(self, url, params=None, timeout=None):
        self.requests.append((url, dict(params or {})))
        return self.responses.pop(0)


def _client(responses):
    session = FakeSession(responses)
    limiter = RateLimiter(60, clock=lambda: 0.0, sleep=lambda s: None)
    return JQuantsClient("KEY", get_plan("free"), session=session, rate_limiter=limiter), session


def test_pagination_and_auth_header():
    client, session = _client([
        FakeResponse(200, {"data": [{"Date": "1"}], "pagination_key": "abc"}),
        FakeResponse(200, {"data": [{"Date": "2"}]}),
    ])
    rows = client.daily_bars("7203", "2026-01-01", "2026-02-01")
    assert [r["Date"] for r in rows] == ["1", "2"]
    assert session.headers["x-api-key"] == "KEY"
    assert session.requests[0][0].endswith("/v2/equities/bars/daily")
    assert session.requests[1][1]["pagination_key"] == "abc"


def test_403_raises_plan_error():
    client, _ = _client([FakeResponse(403, {"message": "forbidden"})])
    with pytest.raises(PlanNotSupportedError):
        client.dividends("7203")


def test_rate_limiter_waits_interval():
    t = [0.0]
    slept = []
    limiter = RateLimiter(5, clock=lambda: t[0], sleep=slept.append)
    limiter.wait()
    t[0] = 2.0
    limiter.wait()
    assert slept == [pytest.approx(10.0)]
