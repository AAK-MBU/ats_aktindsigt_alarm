"""Samler alarm-items til alarmprocessens egen kø."""

import logging
from datetime import UTC, datetime

from automation_server_client import Workqueue

from ats_framework.helpers.aktindsigt_api import fetch_received
from ats_framework.helpers.ats_functions import (
    fetch_workqueue,
    get_workqueue_items,
    iter_workqueue_rows,
)
from ats_framework.helpers.rpa_db import get_credential_password
from ats_framework.processes import alarm_config, missing_forms, stale_items
from ats_framework.processes.alarm_config import FormDestination

logger = logging.getLogger(__name__)


def collect_stale_items(alarm_queue: Workqueue, now: datetime) -> list[dict]:
    """Bygger alarm-items for workitems i de overvågede køer, der er gået i stå.

    Args:
        alarm_queue: Alarmprocessens egen kø. Dens items er historikken, og
            den overvåges ikke selv.
        now: Kørselstidspunktet (tidszonebevidst).

    Returns:
        Alarm-items som ``{"reference": ..., "data": ...}``.
    """
    queue_rows = {
        name: list(iter_workqueue_rows(fetch_workqueue(name)))
        for name in alarm_config.MONITORED_QUEUES
        if name != alarm_queue.name
    }
    alarm_rows = list(iter_workqueue_rows(alarm_queue))
    return stale_items.build_stale_items(queue_rows, alarm_rows, now)


class _KnownReferences:
    """Henter og husker de referencer, hvert led kender, i én kørsel.

    En kø læses kun én gang, selv om flere webforms deler den. Portalen
    spørges pr. webform.
    """

    def __init__(self):
        self._queues: dict[str, dict[str, str]] = {}
        self._portal_key: str | None = None

    def for_destination(
        self, destination: FormDestination, webform_id: str
    ) -> dict[str, str]:
        """Returnerer de referencer, et led kender.

        Args:
            destination: Leddet.
            webform_id: Webformen, portalen spørges om.

        Returns:
            Dict fra reference til en beskrivelse: ``"status <status>"`` for
            et kø-item, ``"sag <sagId>"`` for en sag i portalen.
        """
        if destination.kind == "ats_queue":
            if destination.name not in self._queues:
                rows = get_workqueue_items(
                    fetch_workqueue(destination.name), return_data=True
                )
                self._queues[destination.name] = {
                    ref: f"status {row.get('status')}" for ref, row in rows.items()
                }
            return self._queues[destination.name]

        if self._portal_key is None:
            self._portal_key = get_credential_password(
                alarm_config.AKTINDSIGT_CREDENTIAL
            )
        return fetch_received(webform_id, self._portal_key)


def collect_missing_form_items(now: datetime) -> list[dict]:
    """Bygger ét alarm-item pr. webform, der har svar, som ikke er leveret.

    Args:
        now: Kørselstidspunktet (tidszonebevidst).

    Returns:
        Alarm-items som ``{"reference": ..., "data": ...}``.
    """
    if not alarm_config.FORM_MAP:
        return []

    api_key = get_credential_password(alarm_config.OS2FORMS_CREDENTIAL)
    known = _KnownReferences()
    items = []
    for mapping in alarm_config.FORM_MAP:
        submissions = missing_forms.fetch_submissions(mapping.webform_id, api_key)
        if not submissions:
            continue
        references = [
            known.for_destination(dest, mapping.webform_id)
            for dest in mapping.destinations
        ]
        missing = missing_forms.classify_missing(mapping, submissions, references, now)
        if missing:
            logger.info(
                "%d submissions for %s not delivered", len(missing), mapping.webform_id
            )
            items.append(missing_forms.build_missing_item(mapping, missing, now))
    return items


def collect_alarm_items(alarm_queue: Workqueue) -> list[dict]:
    """Samler alle alarm-items for kørslen.

    Args:
        alarm_queue: Alarmprocessens egen kø.

    Returns:
        Alarm-items for workitems, der er gået i stå, og for webforms med
        svar, der ikke er leveret.

    Raises:
        ValueError: Hvis ``alarm_config`` indeholder pladsholdere eller
            ugyldige værdier.
    """
    alarm_config.validate_config()
    now = datetime.now(UTC)
    return collect_stale_items(alarm_queue, now) + collect_missing_form_items(now)
