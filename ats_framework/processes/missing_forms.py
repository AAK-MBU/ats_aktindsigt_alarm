"""Finder OS2Forms-svar, der ikke er nået igennem flowet til portalen.

Svarene hentes fra OS2Forms' REST-API (``os2forms_rest_api``) med header
``api-key``:

- ``GET {base}/webform_rest/{webform_id}/submissions`` giver et map fra
  ``sid`` til svarets URL.
- ``GET <svarets URL>`` giver svaret med ``entity.uuid``, ``entity.sid`` og
  ``entity.created``.

Hvert led i en ``FormMapping`` beskrives af de referencer, leddet kender:
item-referencerne i en ATS-kø eller de svar-uuids, portalen har modtaget. Et
svar tæller som landet i et led, når dets ``uuid`` eller ``sid`` (efter
leddets ``match_on``) findes som delstreng i en af leddets referencer.

Svar yngre end ``alarm_config.MAX_AGE`` springes over, så de når at lande. Et manglende svar
meldes kun ved det første led, det ikke er nået til, sammen med det seneste
led, det nåede. Hver webform med mindst ét manglende svar bliver til ét
alarm-item med referencen ``missing_<webform_id>_<tidspunkt>``, så der meldes
ved hver kørsel, så længe svarene mangler.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import requests

from ats_framework.processes import alarm_config
from ats_framework.processes.alarm_config import FormDestination, FormMapping

logger = logging.getLogger(__name__)

REFERENCE_PREFIX = "missing_"


@dataclass(frozen=True)
class Submission:
    """Et OS2Forms-svar.

    Attributes:
        sid: Svarets løbenummer i webformen.
        uuid: Svarets UUID.
        created: Hvornår svaret blev oprettet (ISO-8601 i UTC).
    """

    sid: str
    uuid: str
    created: str


def _first_value(entity: dict, field: str):
    """Læser værdien af et Drupal-entityfelt (``[{"value": ...}]`` eller skalar)."""
    value = entity.get(field)
    if isinstance(value, list):
        value = value[0].get("value") if value else None
    return value


def parse_created(value) -> datetime:
    """Omsætter OS2Forms' ``created`` til en tidszonebevidst datetime.

    Args:
        value: Unix-tidsstempel (tal eller talstreng) eller ISO-8601-streng.
            ISO-strenge uden tidszone tolkes som UTC.

    Returns:
        Tidszonebevidst datetime.
    """
    if isinstance(value, int | float) or str(value).isdigit():
        return datetime.fromtimestamp(int(value), tz=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_submission(sid: str, url: str, payload: dict) -> Submission:
    """Bygger et Submission ud fra svaret på et enkelt-svar-kald.

    Args:
        sid: Nøglen fra svarlisten.
        url: Svarets URL fra svarlisten. Sidste stykke er svarets UUID og
            bruges, hvis ``entity.uuid`` mangler.
        payload: JSON fra ``GET <url>`` med nøglen ``entity``.

    Returns:
        Svaret.
    """
    entity = payload.get("entity", {})
    uuid = _first_value(entity, "uuid") or url.rstrip("/").rsplit("/", 1)[-1]
    created = parse_created(_first_value(entity, "created"))
    return Submission(
        sid=str(_first_value(entity, "sid") or sid),
        uuid=str(uuid),
        created=created.astimezone(UTC).isoformat(),
    )


def fetch_submissions(webform_id: str, api_key: str) -> list[Submission]:
    """Henter alle svar på en webform fra OS2Forms.

    Args:
        webform_id: Webformens maskinnavn.
        api_key: OS2Forms' api-key.

    Returns:
        Alle svar på webformen.

    Raises:
        requests.HTTPError: Hvis OS2Forms afviser et kald.
    """
    headers = {"api-key": api_key, "Content-Type": "application/json"}
    response = requests.get(
        f"{alarm_config.OS2FORMS_BASE_URL}/webform_rest/{webform_id}/submissions",
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()
    submission_urls = response.json().get("submissions") or {}

    submissions = []
    for sid, url in submission_urls.items():
        detail = requests.get(url, headers=headers, timeout=60)
        detail.raise_for_status()
        submissions.append(parse_submission(str(sid), url, detail.json()))

    logger.info("Fetched %d submissions for %s", len(submissions), webform_id)
    return submissions


def find_missing(
    destination: FormDestination,
    submissions: list[Submission],
    references: set[str],
    now: datetime,
    max_age: timedelta = alarm_config.MAX_AGE,
) -> list[Submission]:
    """Finder de svar, der ikke er landet i et led.

    Args:
        destination: Leddet, hvis ``match_on`` afgør hvilket id der matches på.
        submissions: Webformens svar.
        references: Alle referencer, leddet kender.
        now: Tidspunktet der måles fra (tidszonebevidst).
        max_age: Hvor gammelt et svar skal være, før det skal være landet.

    Returns:
        De svar, der er ældre end ``max_age``, og hvis id ikke indgår i
        nogen reference, sorteret efter oprettelsestidspunkt.
    """
    missing = []
    for submission in submissions:
        if now - parse_created(submission.created) <= max_age:
            continue
        key = getattr(submission, destination.match_on)
        if not any(key in reference for reference in references):
            missing.append(submission)
    return sorted(missing, key=lambda s: s.created)


def classify_missing(
    mapping: FormMapping,
    submissions: list[Submission],
    known: list[dict[str, str]],
    now: datetime,
    max_age: timedelta = alarm_config.MAX_AGE,
) -> list[dict]:
    """Finder de svar, der ikke er nået igennem flowet, og hvor de stoppede.

    Args:
        mapping: Webformens mapping med leddene i flowets rækkefølge.
        submissions: Webformens svar.
        known: Pr. led i ``mapping.destinations`` et dict fra reference til en
            beskrivelse af, hvad leddet ved om den (fx kø-itemets status).
        now: Tidspunktet der måles fra (tidszonebevidst).
        max_age: Hvor gammelt et svar skal være, før det skal være landet.

    Returns:
        Én dict pr. manglende svar med nøglerne ``sid``, ``uuid``,
        ``created``, ``missing_in`` (etiketten på det første led, svaret ikke
        er nået til) og ``last_seen`` (etiket og beskrivelse fra det seneste
        led, svaret nåede, eller tom streng). Sorteret efter
        oprettelsestidspunkt.
    """
    remaining = [s for s in submissions if now - parse_created(s.created) > max_age]
    last_seen: dict[str, str] = {}
    results: list[dict] = []

    for dest, references in zip(mapping.destinations, known, strict=True):
        missing = find_missing(dest, remaining, set(references), now, max_age)
        missing_uuids = {s.uuid for s in missing}
        for submission in missing:
            results.append(
                {
                    **asdict(submission),
                    "missing_in": dest.label,
                    "last_seen": last_seen.get(submission.uuid, ""),
                }
            )

        remaining = [s for s in remaining if s.uuid not in missing_uuids]
        for submission in remaining:
            key = getattr(submission, dest.match_on)
            reference = next((r for r in references if key in r), None)
            if reference is not None:
                last_seen[submission.uuid] = f"{dest.label}: {references[reference]}"

    return sorted(results, key=lambda r: r["created"])


def build_missing_item(
    mapping: FormMapping, missing: list[dict], now: datetime
) -> dict:
    """Bygger alarm-item'et for en webform med manglende svar.

    Args:
        mapping: Webformens mapping.
        missing: De manglende svar fra ``classify_missing`` (mindst ét).
        now: Kørselstidspunktet, der indgår i referencen.

    Returns:
        ``{"reference": ..., "data": ...}``. ``data`` har nøglerne ``type``
        (``"missing_forms"``), ``webform_id`` og ``missing`` (dicts fra
        ``classify_missing``).
    """
    return {
        "reference": f"{REFERENCE_PREFIX}{mapping.webform_id}_{now:%Y%m%dT%H%M%S}",
        "data": {
            "type": "missing_forms",
            "webform_id": mapping.webform_id,
            "missing": missing,
        },
    }
