"""Behandler et alarm-item: genkører workitems og sender besked."""

import logging
from html import escape

from mbu_rpa_core.exceptions import BusinessError

from ats_framework.helpers.ats_functions import set_workitem_status
from ats_framework.helpers.email import send_alarm_email
from ats_framework.processes import alarm_config

logger = logging.getLogger(__name__)


def _table(headers: list[str], rows: list[list]) -> str:
    """Bygger en HTML-tabel med escapede værdier."""
    head = "".join(f"<th align='left'>{escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in row) + "</tr>"
        for row in rows
    )
    return f"<table border='1' cellpadding='4' cellspacing='0'><tr>{head}</tr>{body}</table>"


def handle_stale_item(data: dict) -> None:
    """Genkører et workitem, der er gået i stå, og sender besked.

    Workitems med en status i ``alarm_config.RETRYABLE_STATUSES`` sættes til
    ``new``, så længe alarmens løbenummer ikke overstiger
    ``alarm_config.MAX_RETRIES_PER_ITEM``. Ellers sendes kun besked.

    Args:
        data: Alarm-itemets data fra ``stale_items.build_stale_items``.
    """
    attempt = data["attempt"]
    max_retries = alarm_config.MAX_RETRIES_PER_ITEM
    retry = data["status"] in alarm_config.RETRYABLE_STATUSES and attempt <= max_retries

    if retry:
        set_workitem_status(
            data["workitem_id"],
            "new",
            f"Genkørt af {alarm_config.PROCESS_NAME} (forsøg {attempt}/{max_retries})",
        )
        subject = alarm_config.subject("retried", queue=data["queue"])
        action = (
            f"Workitem'et er sat til status 'new' og bliver kørt igen "
            f"(forsøg {attempt}/{max_retries}). Tjek at det bliver gennemført."
        )
    elif data["status"] in alarm_config.RETRYABLE_STATUSES:
        subject = alarm_config.subject("manual", queue=data["queue"])
        action = (
            f"Workitem'et er gået i stå igen efter {max_retries} genkørsler og "
            "bliver ikke genkørt automatisk. Det skal håndteres manuelt."
        )
    else:
        subject = alarm_config.subject("waiting", queue=data["queue"])
        action = (
            "Workitem'et har status 'new', men er ikke blevet kørt. Tjek at "
            "processen, der dræner køen, kører."
        )

    html = (
        f"<p>{escape(action)}</p>"
        + _table(
            ["Kø", "Workitem-id", "Reference", "Status", "Sidst opdateret", "Besked"],
            [
                [
                    data["queue"],
                    data["workitem_id"],
                    data.get("workitem_reference") or "",
                    data["status"],
                    data["updated_at"],
                    data.get("message") or "",
                ]
            ],
        )
        + f"<p>Alarm nr. {attempt} for dette workitem.</p>"
    )
    send_alarm_email(subject, html)


def handle_missing_forms(data: dict) -> None:
    """Sender besked om OS2Forms-svar, der ikke er leveret til portalen.

    Args:
        data: Alarm-itemets data fra ``missing_forms.build_missing_item``.
    """
    missing = data["missing"]
    subject = alarm_config.subject("missing_forms", webform_id=data["webform_id"])
    html = (
        f"<p>{len(missing)} svar på webformen <b>{escape(data['webform_id'])}</b> "
        "er ikke nået frem til portalen. <i>Mangler i</i> er det første led, "
        "svaret ikke er nået til, og <i>Seneste led</i> det sidste, det nåede.</p>"
        + _table(
            ["sid", "uuid", "Oprettet", "Mangler i", "Seneste led"],
            [
                [s["sid"], s["uuid"], s["created"], s["missing_in"], s["last_seen"]]
                for s in missing
            ],
        )
    )
    send_alarm_email(subject, html)


HANDLERS = {
    "stale_item": handle_stale_item,
    "missing_forms": handle_missing_forms,
}


def handle_item(item_data: dict, item_reference: str) -> None:
    """Sender et alarm-item til handleren for dets type.

    Args:
        item_data: Alarm-itemets data med nøglen ``type``.
        item_reference: Alarm-itemets reference.

    Raises:
        BusinessError: Hvis itemets type er ukendt.
    """
    handler = HANDLERS.get(item_data.get("type", "no type defined"))
    if handler is None:
        raise BusinessError(
            f"Unknown alarm item type {item_data.get('type')!r} for {item_reference}"
        )
    handler(item_data)
    logger.info("Handled alarm item %s", item_reference)
