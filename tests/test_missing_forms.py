"""Tests af udvælgelsen af OS2Forms-svar, der ikke er nået igennem flowet."""

from datetime import UTC, datetime, timedelta

from ats_framework.processes.alarm_config import FormDestination, FormMapping
from ats_framework.processes.missing_forms import (
    Submission,
    build_missing_item,
    classify_missing,
    find_missing,
    parse_created,
    parse_submission,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
UUID_MAPPING = FormDestination(kind="ats_queue", name="aktindsigt.intake")
SID_MAPPING = FormDestination(kind="ats_queue", name="q", match_on="sid")
QUEUE = UUID_MAPPING
PORTAL = FormDestination(kind="aktindsigt")
FLOW = FormMapping(webform_id="akt", destinations=(QUEUE, PORTAL))


def _sub(sid="11", uuid="aaaa-1111", age=timedelta(hours=2)):
    return Submission(sid=sid, uuid=uuid, created=(NOW - age).isoformat())


def test_parse_created_accepts_timestamp_and_iso():
    assert parse_created(1758801600) == datetime(2025, 9, 25, 12, 0, tzinfo=UTC)
    assert parse_created("1758801600") == datetime(2025, 9, 25, 12, 0, tzinfo=UTC)
    assert parse_created("2026-09-25T14:00:00+02:00") == NOW
    assert parse_created("2026-09-25T12:00:00") == NOW


def test_parse_submission_reads_drupal_entity_fields():
    payload = {
        "entity": {
            "uuid": [{"value": "aaaa-1111"}],
            "sid": [{"value": 11}],
            "created": [{"value": "2026-09-25T14:00:00+02:00"}],
        }
    }
    sub = parse_submission(
        "11", "https://x/webform_rest/f/submission/aaaa-1111", payload
    )
    assert sub == Submission(sid="11", uuid="aaaa-1111", created=NOW.isoformat())


def test_parse_submission_falls_back_to_url_for_uuid():
    payload = {"entity": {"created": [{"value": "1758801600"}]}}
    sub = parse_submission(
        "11", "https://x/webform_rest/f/submission/bbbb-2222", payload
    )
    assert (sub.sid, sub.uuid) == ("11", "bbbb-2222")


def test_submission_landed_when_uuid_is_part_of_reference():
    refs = {"intake_aaaa-1111", "something_else"}
    assert find_missing(UUID_MAPPING, [_sub()], refs, NOW) == []


def test_submission_missing_when_uuid_not_in_any_reference():
    assert find_missing(UUID_MAPPING, [_sub()], {"intake_other"}, NOW) == [_sub()]


def test_match_on_sid():
    assert find_missing(SID_MAPPING, [_sub(sid="11")], {"form_11"}, NOW) == []
    assert find_missing(SID_MAPPING, [_sub(sid="12")], {"form_11"}, NOW) == [
        _sub(sid="12")
    ]


def test_young_submissions_are_skipped():
    young = _sub(age=timedelta(minutes=10))
    assert find_missing(UUID_MAPPING, [young], set(), NOW) == []


def test_missing_sorted_by_created():
    old = _sub(sid="1", uuid="u1", age=timedelta(days=2))
    newer = _sub(sid="2", uuid="u2", age=timedelta(hours=2))
    assert find_missing(UUID_MAPPING, [newer, old], set(), NOW) == [old, newer]


def test_classify_missing_in_queue_is_reported_once():
    missing = classify_missing(FLOW, [_sub()], [{}, {}], NOW)
    assert len(missing) == 1
    assert missing[0]["missing_in"] == "ATS-køen aktindsigt.intake"
    assert missing[0]["last_seen"] == ""


def test_classify_in_queue_but_not_in_portal_carries_queue_status():
    known = [{"aaaa-1111": "status completed"}, {}]
    missing = classify_missing(FLOW, [_sub()], known, NOW)
    assert [(m["missing_in"], m["last_seen"]) for m in missing] == [
        ("portalen", "ATS-køen aktindsigt.intake: status completed")
    ]


def test_classify_delivered_submission_is_not_missing():
    known = [{"aaaa-1111": "status completed"}, {"aaaa-1111": "sag 7"}]
    assert classify_missing(FLOW, [_sub()], known, NOW) == []


def test_classify_skips_young_submissions():
    young = _sub(age=timedelta(minutes=10))
    assert classify_missing(FLOW, [young], [{}, {}], NOW) == []


def test_classify_mixes_stages_sorted_by_created():
    in_queue = _sub(sid="1", uuid="u1", age=timedelta(hours=2))
    nowhere = _sub(sid="2", uuid="u2", age=timedelta(hours=5))
    known = [{"u1": "status failed"}, {}]
    missing = classify_missing(FLOW, [in_queue, nowhere], known, NOW)
    assert [(m["uuid"], m["missing_in"]) for m in missing] == [
        ("u2", "ATS-køen aktindsigt.intake"),
        ("u1", "portalen"),
    ]


def test_one_item_per_form_with_run_timestamp():
    missing = classify_missing(FLOW, [_sub(), _sub(sid="12", uuid="u2")], [{}, {}], NOW)
    item = build_missing_item(FLOW, missing, NOW)
    assert item["reference"] == "missing_akt_20260925T120000"
    data = item["data"]
    assert data["type"] == "missing_forms"
    assert data["webform_id"] == "akt"
    assert [m["sid"] for m in data["missing"]] == ["11", "12"]
