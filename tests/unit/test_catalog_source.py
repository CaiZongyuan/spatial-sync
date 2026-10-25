"""Server retry instructions are observed without making the tests wait."""

from types import SimpleNamespace

import httpx
import pytest

from packages.catalog import source as module


@pytest.mark.parametrize("headers", [
    {"Retry-After": "600"}, {"RateLimit": '"api";r=0;t=600'},
    {"Retry-After": "600", "RateLimit": '"api";r=0;t=600'},
])
def test_source_does_not_retry_before_the_servers_long_wait(monkeypatch, headers):
    elapsed = [0.0]
    requests = []

    class Cancel:
        def is_set(self):
            return False

        def wait(self, seconds):
            elapsed[0] += seconds
            return False

    def handler(request):
        requests.append(elapsed[0])
        if len(requests) == 1:
            return httpx.Response(429, headers=headers)
        return httpx.Response(200, json={"sha": "a" * 40})

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = module.SimReadySource(client=client, cancel=Cancel())
        assert source.revision() == "a" * 40
    assert len(requests) == 2 and requests[1] >= 600


def test_waiting_worker_rechecks_a_reset_extended_by_another_worker(monkeypatch):
    elapsed = [0.0]
    requests = []

    class Cancel:
        def is_set(self):
            return False

        def wait(self, seconds):
            elapsed[0] += seconds
            if elapsed[0] == 10:
                source._rate_until = 20  # Another in-flight request receives a later reset.
            return False

    def handler(request):
        requests.append(elapsed[0])
        return httpx.Response(200, json={"sha": "a" * 40})

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = module.SimReadySource(client=client, cancel=Cancel())
        source._rate_until = 10
        assert source.revision() == "a" * 40
    assert requests == [20]
