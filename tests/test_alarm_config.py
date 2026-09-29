"""Tests af valideringen af alarm-konfigurationen."""

from datetime import datetime

import pytest

from ats_framework.processes import alarm_config
from ats_framework.processes.alarm_config import FormDestination, FormMapping

QUEUE = FormDestination(kind="ats_queue", name="q")
PORTAL = FormDestination(kind="aktindsigt")


@pytest.fixture
def filled(monkeypatch):
    """En fuldt udfyldt konfiguration med kø og portal."""
    monkeypatch.setattr(alarm_config, "MONITORED_QUEUES", ["q"])
    monkeypatch.setattr(
        alarm_config,
        "FORM_MAP",
        [FormMapping(webform_id="f", destinations=(QUEUE, PORTAL))],
    )
    monkeypatch.setattr(alarm_config, "AKTINDSIGT_BASE_URL", "https://akt")
    monkeypatch.setattr(alarm_config, "AKTINDSIGT_CREDENTIAL", "akt_api")
    monkeypatch.setattr(alarm_config, "SIDEN", "2026-10-01T00:00:00")


@pytest.mark.usefixtures("filled")
def test_filled_config_is_accepted():
    alarm_config.validate_config()


@pytest.mark.usefixtures("filled")
@pytest.mark.parametrize(
    "name", ["SIDEN", "AKTINDSIGT_BASE_URL", "AKTINDSIGT_CREDENTIAL"]
)
def test_placeholders_are_rejected(monkeypatch, name):
    monkeypatch.setattr(alarm_config, name, "UDFYLDES_x")
    with pytest.raises(ValueError, match="UDFYLDES_x"):
        alarm_config.validate_config()


@pytest.mark.usefixtures("filled")
def test_placeholder_queue_in_destination_is_rejected(monkeypatch):
    dest = FormDestination(kind="ats_queue", name="UDFYLDES_koe")
    monkeypatch.setattr(
        alarm_config, "FORM_MAP", [FormMapping(webform_id="f", destinations=(dest,))]
    )
    with pytest.raises(ValueError, match="UDFYLDES_koe"):
        alarm_config.validate_config()


@pytest.mark.usefixtures("filled")
def test_portal_settings_not_required_without_portal(monkeypatch):
    monkeypatch.setattr(
        alarm_config, "FORM_MAP", [FormMapping(webform_id="f", destinations=(QUEUE,))]
    )
    monkeypatch.setattr(alarm_config, "AKTINDSIGT_BASE_URL", "UDFYLDES_x")
    alarm_config.validate_config()


@pytest.mark.usefixtures("filled")
def test_invalid_siden_is_rejected(monkeypatch):
    monkeypatch.setattr(alarm_config, "SIDEN", "i morgen")
    with pytest.raises(ValueError, match="SIDEN"):
        alarm_config.validate_config()


@pytest.mark.usefixtures("filled")
@pytest.mark.parametrize(
    ("dest", "match"),
    [
        (FormDestination(kind="ats_queue", name="q", match_on="serial"), "serial"),  # type: ignore[arg-type]
        (FormDestination(kind="ftp", name="q"), "ftp"),  # type: ignore[arg-type]
        (FormDestination(kind="ats_queue"), "Queue name"),
        (FormDestination(kind="aktindsigt", match_on="sid"), "uuid"),
    ],
)
def test_invalid_destination_is_rejected(monkeypatch, dest, match):
    monkeypatch.setattr(
        alarm_config, "FORM_MAP", [FormMapping(webform_id="f", destinations=(dest,))]
    )
    with pytest.raises(ValueError, match=match):
        alarm_config.validate_config()


def test_naive_siden_is_read_in_ats_timezone(monkeypatch):
    monkeypatch.setattr(alarm_config, "SIDEN", "2026-10-01T00:00:00")
    assert alarm_config.siden() == datetime(
        2026, 10, 1, tzinfo=alarm_config.ATS_TIMEZONE
    )


def test_subject_has_prefix():
    subject = alarm_config.subject("missing_forms", webform_id="f")
    assert subject.startswith(alarm_config.SUBJECT_PREFIX)
    assert subject.endswith("f")
