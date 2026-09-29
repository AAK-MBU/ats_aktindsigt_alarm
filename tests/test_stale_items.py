"""Tests af udvælgelsen af workitems, der er gået i stå."""

from datetime import datetime, timedelta

import pytest

from ats_framework.processes import alarm_config
from ats_framework.processes.stale_items import (
    build_stale_items,
    is_stale,
    latest_alarms,
    parse_ats_time,
)

TZ = alarm_config.ATS_TIMEZONE
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=TZ)
QUEUE = "aktindsigt.intake"


def _iso(dt: datetime) -> str:
    """ATS-format: lokaltid uden tidszone."""
    return dt.replace(tzinfo=None).isoformat()


def _row(item_id=1, status="failed", age=timedelta(hours=2), reference="form_1"):
    return {
        "id": item_id,
        "reference": reference,
        "status": status,
        "message": "boom",
        "updated_at": _iso(NOW - age),
    }


def _alarm(reference, status="completed", updated=NOW - timedelta(minutes=30)):
    return {"reference": reference, "status": status, "updated_at": _iso(updated)}


def test_naive_ats_time_is_server_local_time():
    assert parse_ats_time("2026-09-25T12:00:00") == NOW


def test_aware_ats_time_is_kept():
    assert parse_ats_time("2026-09-25T10:00:00+00:00") == NOW


@pytest.mark.parametrize("status", ["new", "in progress", "failed"])
def test_stale_statuses_older_than_threshold(status):
    assert is_stale(_row(status=status), NOW)


@pytest.mark.parametrize("status", ["completed", "pending user action"])
def test_other_statuses_are_never_stale(status):
    assert not is_stale(_row(status=status), NOW)


def test_threshold_is_exclusive():
    assert not is_stale(_row(age=alarm_config.MAX_AGE), NOW)
    assert is_stale(_row(age=alarm_config.MAX_AGE + timedelta(seconds=1)), NOW)


def test_latest_alarms_picks_highest_number_and_ignores_other_references():
    rows = [
        _alarm(f"stale_{QUEUE}_7_1"),
        _alarm(f"stale_{QUEUE}_7_2", status="failed"),
        _alarm("missing_form_20260925T100000"),
        _alarm("stale_garbage"),
    ]
    latest = latest_alarms(rows)
    assert list(latest) == [f"stale_{QUEUE}_7"]
    assert latest[f"stale_{QUEUE}_7"][0] == 2


def test_first_alarm_gets_number_one_and_payload():
    items = build_stale_items({QUEUE: [_row(item_id=7)]}, [], NOW)
    assert len(items) == 1
    assert items[0]["reference"] == f"stale_{QUEUE}_7_1"
    data = items[0]["data"]
    assert data["type"] == "stale_item"
    assert data["queue"] == QUEUE
    assert data["workitem_id"] == 7
    assert data["workitem_reference"] == "form_1"
    assert data["status"] == "failed"
    assert data["attempt"] == 1


def test_fresh_and_finished_items_give_no_alarm():
    rows = [
        _row(item_id=1, age=timedelta(minutes=5)),
        _row(item_id=2, status="completed"),
    ]
    assert build_stale_items({QUEUE: rows}, [], NOW) == []


@pytest.mark.parametrize("status", ["new", "in progress"])
def test_pending_alarm_blocks_new_alarm(status):
    alarms = [_alarm(f"stale_{QUEUE}_7_1", status=status)]
    assert build_stale_items({QUEUE: [_row(item_id=7)]}, alarms, NOW) == []


def test_same_stop_is_not_reported_twice():
    # Workitem'et er sidst opdateret FØR alarmen blev behandlet.
    alarms = [_alarm(f"stale_{QUEUE}_7_1", updated=NOW - timedelta(minutes=30))]
    row = _row(item_id=7, age=timedelta(hours=2))
    assert build_stale_items({QUEUE: [row]}, alarms, NOW) == []


def test_new_stop_after_handled_alarm_gets_next_number():
    # Alarmen blev behandlet for 3 timer siden; workitem'et fejlede igen for 2 timer siden.
    alarms = [_alarm(f"stale_{QUEUE}_7_1", updated=NOW - timedelta(hours=3))]
    items = build_stale_items({QUEUE: [_row(item_id=7)]}, alarms, NOW)
    assert [i["reference"] for i in items] == [f"stale_{QUEUE}_7_2"]
    assert items[0]["data"]["attempt"] == 2
