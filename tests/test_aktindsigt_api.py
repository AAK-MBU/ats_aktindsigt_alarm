"""Tests af opslaget i aktindsigt-portalens modtagne svar."""

from datetime import UTC, datetime

from ats_framework.helpers import aktindsigt_api
from ats_framework.processes import alarm_config


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_fetch_received_maps_uuid_to_sag(monkeypatch):
    monkeypatch.setattr(alarm_config, "AKTINDSIGT_BASE_URL", "https://akt/")
    seen = {}

    def fake_get(url, params, headers, timeout):
        seen.update(url=url, params=params, headers=headers, timeout=timeout)
        return _Response(
            {
                "modtagne": [
                    {"uuid": "u1", "sagId": 7, "webformId": "f", "modtaget": "x"},
                    {"uuid": None, "sagId": 8},
                ]
            }
        )

    monkeypatch.setattr(aktindsigt_api.requests, "get", fake_get)
    since = datetime(2026, 10, 1, tzinfo=UTC)

    assert aktindsigt_api.fetch_received("f", since, "k") == {"u1": "sag 7"}
    assert seen["url"] == "https://akt/api/intake/modtagne"
    assert seen["params"] == {"webform_id": "f", "siden": since.isoformat()}
    assert seen["headers"] == {"X-API-Key": "k"}
