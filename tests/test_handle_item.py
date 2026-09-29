"""Tests af behandlingen af alarm-items."""

import pytest
from mbu_rpa_core.exceptions import BusinessError

from ats_framework.processes import alarm_config, handle_item


@pytest.fixture
def calls(monkeypatch):
    recorded = {"status": [], "mail": []}
    monkeypatch.setattr(
        handle_item,
        "set_workitem_status",
        lambda *args: recorded["status"].append(args),
    )
    monkeypatch.setattr(
        handle_item,
        "send_alarm_email",
        lambda subject, html: recorded["mail"].append((subject, html)),
    )
    return recorded


def _stale(status="failed", attempt=1):
    return {
        "type": "stale_item",
        "queue": "aktindsigt.intake",
        "workitem_id": 7,
        "workitem_reference": "form_1_opret_sag",
        "status": status,
        "updated_at": "2026-09-25T10:00:00",
        "message": "<boom>",
        "attempt": attempt,
    }


@pytest.mark.parametrize("status", ["failed", "in progress"])
def test_retryable_item_is_reset_to_new_and_reported(calls, status):
    handle_item.handle_item(_stale(status=status), "stale_x_7_1")
    assert len(calls["status"]) == 1
    item_id, new_status, message = calls["status"][0]
    assert (item_id, new_status) == (7, "new")
    assert f"1/{alarm_config.MAX_RETRIES_PER_ITEM}" in message
    assert len(calls["mail"]) == 1
    assert "genkørt" in calls["mail"][0][0]
    assert "&lt;boom&gt;" in calls["mail"][0][1]


def test_retry_limit_reached_only_reports(calls):
    attempt = alarm_config.MAX_RETRIES_PER_ITEM + 1
    handle_item.handle_item(_stale(attempt=attempt), "stale_x_7_4")
    assert calls["status"] == []
    assert "manuel" in calls["mail"][0][0]


def test_new_item_only_reports(calls):
    handle_item.handle_item(_stale(status="new"), "stale_x_7_1")
    assert calls["status"] == []
    assert "venter" in calls["mail"][0][0]


def test_missing_forms_sends_one_mail(calls):
    data = {
        "type": "missing_forms",
        "webform_id": "aktindsigt_indgang_personale",
        "missing": [
            {
                "sid": "11",
                "uuid": "u1",
                "created": "2026-09-25T10:00:00+00:00",
                "missing_in": "ATS-køen aktindsigt.intake",
                "last_seen": "",
            },
            {
                "sid": "12",
                "uuid": "u2",
                "created": "2026-09-25T10:05:00+00:00",
                "missing_in": "portalen",
                "last_seen": "ATS-køen aktindsigt.intake: status completed",
            },
        ],
    }
    handle_item.handle_item(
        data, "missing_aktindsigt_indgang_personale_20260925T120000"
    )
    assert calls["status"] == []
    assert len(calls["mail"]) == 1
    subject, html = calls["mail"][0]
    assert subject.startswith(alarm_config.SUBJECT_PREFIX)
    assert "aktindsigt_indgang_personale" in subject
    assert "u1" in html and "u2" in html
    assert "status completed" in html


@pytest.mark.usefixtures("calls")
def test_unknown_type_is_business_error():
    with pytest.raises(BusinessError):
        handle_item.handle_item({"type": "nope"}, "ref")
