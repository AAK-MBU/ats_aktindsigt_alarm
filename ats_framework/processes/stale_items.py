"""Finder workitems i de overvågede køer, der er gået i stå.

Et workitem er gået i stå, når dets status er i ``alarm_config.STALE_STATUSES``,
og ATS ikke har opdateret det i mere end ``alarm_config.MAX_AGE``. Hvert stop
bliver til ét alarm-item på alarmprocessens egen kø med referencen
``stale_<kø>_<workitem-id>_<n>``, hvor ``n`` tæller alarmerne for workitem'et.

Alarm-køens egne items bruges som historik, så det samme stop kun meldes én
gang:

- Er det seneste alarm-item for workitem'et endnu ikke behandlet (``new``
  eller ``in progress``), oprettes der ikke et nyt.
- Er workitem'et ikke opdateret siden det seneste alarm-item blev behandlet,
  er det samme stop, og der oprettes ikke et nyt.
- Ellers er det et nyt stop, og der oprettes et alarm-item med ``n + 1``.
"""

import logging
from datetime import datetime, timedelta

from ats_framework.processes import alarm_config

logger = logging.getLogger(__name__)

REFERENCE_PREFIX = "stale_"
PENDING_ALARM_STATUSES = frozenset({"new", "in progress"})


def parse_ats_time(value: str) -> datetime:
    """Omsætter et ATS-tidsstempel til en tidszonebevidst datetime.

    Args:
        value: ISO-8601-streng. Uden tidszone tolkes den som ATS-serverens
            lokaltid (``alarm_config.ATS_TIMEZONE``).

    Returns:
        Tidszonebevidst datetime.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=alarm_config.ATS_TIMEZONE)
    return parsed


def is_stale(
    row: dict,
    now: datetime,
    max_age: timedelta = alarm_config.MAX_AGE,
    statuses: frozenset[str] = alarm_config.STALE_STATUSES,
) -> bool:
    """Afgør om et workitem er gået i stå.

    Args:
        row: Workitem-række fra ATS med ``status`` og ``updated_at``.
        now: Tidspunktet der måles fra (tidszonebevidst).
        max_age: Hvor længe et workitem må stå uændret.
        statuses: De statusser, der kan gå i stå.

    Returns:
        True hvis status er i ``statuses``, og der er gået mere end
        ``max_age`` siden ``updated_at``.
    """
    return (
        row.get("status") in statuses
        and now - parse_ats_time(row["updated_at"]) > max_age
    )


def alarm_key(queue_name: str, workitem_id: int) -> str:
    """Referencen for et workitems alarmer uden løbenummer."""
    return f"{REFERENCE_PREFIX}{queue_name}_{workitem_id}"


def latest_alarms(alarm_rows: list[dict]) -> dict[str, tuple[int, dict]]:
    """Finder det seneste alarm-item pr. workitem i alarm-køen.

    Args:
        alarm_rows: Rækker fra alarm-køen. Rækker hvis reference ikke har
            formen ``stale_<kø>_<id>_<n>`` ignoreres.

    Returns:
        Dict fra ``alarm_key`` til ``(n, række)`` for det højeste ``n``.
    """
    latest: dict[str, tuple[int, dict]] = {}
    for row in alarm_rows:
        reference = row.get("reference") or ""
        if not reference.startswith(REFERENCE_PREFIX):
            continue
        key, _, number = reference.rpartition("_")
        if not number.isdigit():
            continue
        n = int(number)
        if key not in latest or n > latest[key][0]:
            latest[key] = (n, row)
    return latest


def build_stale_items(
    queue_rows: dict[str, list[dict]],
    alarm_rows: list[dict],
    now: datetime,
) -> list[dict]:
    """Bygger alarm-items for de workitems, der er gået i stå siden sidst.

    Args:
        queue_rows: Dict fra kønavn til køens workitem-rækker.
        alarm_rows: Rækker fra alarmprocessens egen kø (historikken).
        now: Tidspunktet der måles fra (tidszonebevidst).

    Returns:
        Liste af ``{"reference": ..., "data": ...}``. ``data`` har nøglerne
        ``type`` (``"stale_item"``), ``queue``, ``workitem_id``,
        ``workitem_reference``, ``status``, ``updated_at``, ``message`` og
        ``attempt`` (alarmens løbenummer for workitem'et).
    """
    latest = latest_alarms(alarm_rows)
    items = []

    for queue_name, rows in queue_rows.items():
        for row in rows:
            if not is_stale(row, now):
                continue

            key = alarm_key(queue_name, row["id"])
            count = 0
            if key in latest:
                count, alarm_row = latest[key]
                if alarm_row.get("status") in PENDING_ALARM_STATUSES:
                    logger.info("%s already has a pending alarm", key)
                    continue
                if parse_ats_time(row["updated_at"]) <= parse_ats_time(
                    alarm_row["updated_at"]
                ):
                    logger.info("%s unchanged since last alarm", key)
                    continue

            attempt = count + 1
            items.append(
                {
                    "reference": f"{key}_{attempt}",
                    "data": {
                        "type": "stale_item",
                        "queue": queue_name,
                        "workitem_id": row["id"],
                        "workitem_reference": row.get("reference"),
                        "status": row["status"],
                        "updated_at": row["updated_at"],
                        "message": row.get("message") or "",
                        "attempt": attempt,
                    },
                }
            )

    logger.info("Found %d stale workitems", len(items))
    return items
