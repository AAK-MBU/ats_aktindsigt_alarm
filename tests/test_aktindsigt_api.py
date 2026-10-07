"""Tests af opslaget i aktindsigt-portalens modtagne svar."""

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
    assert aktindsigt_api.fetch_received("f", "k") == {"u1": "sag 7"}
    assert seen["url"] == "https://akt/api/intake/modtagne"
    assert seen["params"] == {"webform_id": "f"}
    assert seen["headers"] == {"X-API-Key": "k"}


def test_fetch_received_marks_deleted_sager(monkeypatch):
    monkeypatch.setattr(alarm_config, "AKTINDSIGT_BASE_URL", "https://akt")
    rows = [
        {"uuid": "u1", "sagId": 7, "slettet": False},
        {"uuid": "u2", "sagId": 8, "slettet": True},
    ]
    monkeypatch.setattr(
        aktindsigt_api.requests,
        "get",
        lambda *_a, **_k: _Response({"modtagne": rows}),
    )
    assert aktindsigt_api.fetch_received("f", "k") == {
        "u1": "sag 7",
        "u2": "sag 8 (slettet)",
    }
