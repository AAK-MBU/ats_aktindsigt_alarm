"""Tests af sammensætningen af alarm-items i populatoren."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ats_framework.processes import alarm_config, populate
from ats_framework.processes.alarm_config import FormDestination, FormMapping
from ats_framework.processes.missing_forms import Submission

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
ALARM_QUEUE = SimpleNamespace(name="aktindsigt.alarm", id=99)


def _stale_row(item_id):
    updated = (NOW - timedelta(hours=2)).astimezone(alarm_config.ATS_TIMEZONE)
    return {
        "id": item_id,
        "reference": f"ref_{item_id}",
        "status": "failed",
        "updated_at": updated.replace(tzinfo=None).isoformat(),
    }


def test_own_alarm_queue_is_not_monitored(monkeypatch):
    monkeypatch.setattr(
        alarm_config, "MONITORED_QUEUES", ["aktindsigt.a", ALARM_QUEUE.name]
    )
    queues = {
        "aktindsigt.a": SimpleNamespace(name="aktindsigt.a", id=1),
    }
    rows = {1: [_stale_row(10)], 99: [_stale_row(20)]}
    monkeypatch.setattr(populate, "fetch_workqueue", lambda name: queues[name])
    monkeypatch.setattr(populate, "iter_workqueue_rows", lambda wq: iter(rows[wq.id]))

    items = populate.collect_stale_items(ALARM_QUEUE, NOW)
    assert [i["reference"] for i in items] == ["stale_aktindsigt.a_10_1"]


@pytest.fixture
def form_setup(monkeypatch):
    """To webforms med samme kø og portal, og en registrering af portalkald."""
    flow = (
        FormDestination(kind="ats_queue", name="q"),
        FormDestination(kind="aktindsigt"),
    )
    monkeypatch.setattr(
        alarm_config,
        "FORM_MAP",
        [
            FormMapping(webform_id="f1", destinations=flow),
            FormMapping(webform_id="f2", destinations=flow),
        ],
    )
    old = (NOW - timedelta(hours=2)).isoformat()
    subs = {
        "f1": [Submission(sid="1", uuid="u1", created=old)],
        "f2": [Submission(sid="2", uuid="u2", created=old)],
    }
    calls = {"queue": 0, "portal": []}

    def queue_items(_queue, return_data):
        assert return_data
        calls["queue"] += 1
        return {"u1": {"status": "completed"}}

    def portal(webform_id, key):
        calls["portal"].append((webform_id, key))
        return {"u1": "sag 1"} if webform_id == "f1" else {}

    monkeypatch.setattr(populate, "get_credential_password", lambda name: f"key:{name}")
    monkeypatch.setattr(
        populate.missing_forms, "fetch_submissions", lambda wid, _key: subs[wid]
    )
    monkeypatch.setattr(populate, "fetch_workqueue", lambda name: name)
    monkeypatch.setattr(populate, "get_workqueue_items", queue_items)
    monkeypatch.setattr(populate, "fetch_received", portal)
    return calls


def test_missing_form_items_per_mapping(form_setup):
    items = populate.collect_missing_form_items(NOW)
    assert [i["data"]["webform_id"] for i in items] == ["f2"]
    assert [m["uuid"] for m in items[0]["data"]["missing"]] == ["u2"]
    assert form_setup["queue"] == 1
    assert [c[0] for c in form_setup["portal"]] == ["f1", "f2"]
    assert form_setup["portal"][0][1] == f"key:{alarm_config.AKTINDSIGT_CREDENTIAL}"


def test_no_form_mapping_skips_os2forms(monkeypatch):
    monkeypatch.setattr(alarm_config, "FORM_MAP", [])
    assert populate.collect_missing_form_items(NOW) == []
