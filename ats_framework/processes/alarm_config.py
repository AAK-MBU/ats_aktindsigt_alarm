"""Konfiguration af alarmprocessen: overvågede køer, tærskler og formular-mapping.

Kønavnene slås op i ATS med ``fetch_workqueue``, så et kø-id kan ændre sig i
ATS, uden at konfigurationen skal følge med. Værdier med præfikset
``UDFYLDES_`` er pladsholdere. ``validate_config`` afviser dem, så en kørsel
fejler højlydt, indtil de er udfyldt.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from zoneinfo import ZoneInfo

PLACEHOLDER_PREFIX = "UDFYLDES_"

PROCESS_NAME = "ats_aktindsigt_alarm"

# ATS gemmer tidsstempler som serverens lokaltid uden tidszone.
ATS_TIMEZONE = ZoneInfo("Europe/Copenhagen")

# ----------------------
# Workitems der er gået i stå
# ----------------------
# Et workitem regnes som gået i stå, når dets status er i STALE_STATUSES, og
# der er gået mere end MAX_AGE siden ATS sidst opdaterede det (updated_at).
MAX_AGE = timedelta(minutes=60)
STALE_STATUSES = frozenset({"new", "in progress", "failed"})
# Statusser der genkøres (sættes til "new"). Et item med status "new" venter
# allerede på at blive taget, så der sendes kun besked.
RETRYABLE_STATUSES = frozenset({"in progress", "failed"})
# Maks antal alarmer pr. workitem, der må føre til en genkørsel. Derefter
# sendes kun besked, så et menneske kan tage over.
MAX_RETRIES_PER_ITEM = 3

# ATS-køen, polling-servicen lægger aktindsigts formularsvar på.
INTAKE_QUEUE = "UDFYLDES_aktindsigt_intake_koe"

# Workqueue-navne i ATS, der overvåges.
MONITORED_QUEUES: list[str] = [INTAKE_QUEUE]

# ----------------------
# OS2Forms-svar der ikke er leveret
# ----------------------
OS2FORMS_BASE_URL = "https://selvbetjening.aarhuskommune.dk/da"
# Credential i rpa.Credentials, hvis password er OS2Forms' api-key.
OS2FORMS_CREDENTIAL = "os2_api"

# Aktindsigt-portalens backend. Endpointet /api/intake/modtagne lægges til.
AKTINDSIGT_BASE_URL = "UDFYLDES_aktindsigt_base_url"
# Credential i rpa.Credentials, hvis password er aktindsigts intake-API-nøgle
# (sendes i headeren X-API-Key).
AKTINDSIGT_CREDENTIAL = "UDFYLDES_aktindsigt_credential"


@dataclass(frozen=True)
class FormDestination:
    """Et led i flowet, et formularsvar skal nå frem til.

    Attributes:
        kind: ``"ats_queue"`` for en ATS-kø, hvor svaret tæller som landet,
            når dets id står som delstreng i en item-reference, eller
            ``"aktindsigt"`` for portalen, hvor svarets uuid skal stå i
            ``/api/intake/modtagne``.
        name: Kønavnet ved ``"ats_queue"``. Tom ved ``"aktindsigt"``.
        match_on: Hvilket submission-id der matches på: ``"uuid"`` eller
            ``"sid"``. Portalen kender kun ``"uuid"``.
    """

    kind: Literal["ats_queue", "aktindsigt"]
    name: str = ""
    match_on: Literal["uuid", "sid"] = "uuid"

    @property
    def label(self) -> str:
        """Leddets navn i alarm-mails."""
        return f"ATS-køen {self.name}" if self.kind == "ats_queue" else "portalen"


@dataclass(frozen=True)
class FormMapping:
    """Kobler en OS2Forms-webform til de led, dens svar skal igennem.

    Attributes:
        webform_id: Webformens maskinnavn i OS2Forms.
        destinations: Leddene i flowets rækkefølge. Et manglende svar meldes
            ved det første led, det ikke er nået til.
    """

    webform_id: str
    destinations: tuple[FormDestination, ...]


AKTINDSIGT_FLOW = (
    FormDestination(kind="ats_queue", name=INTAKE_QUEUE, match_on="uuid"),
    FormDestination(kind="aktindsigt"),
)

FORM_MAP: list[FormMapping] = [
    FormMapping(
        webform_id="aktindsigt_medarbejder_indgang_p", destinations=AKTINDSIGT_FLOW
    ),
    FormMapping(
        webform_id="aktindsigt_indgang_personale", destinations=AKTINDSIGT_FLOW
    ),
]

# ----------------------
# Mail
# ----------------------
# Navne på konstanter i rpa.Constants.
RECIPIENTS_CONSTANT = "rpa_team_email"  # JSON-liste eller kommasepareret
SENDER_CONSTANT = "e-mail_noreply"
SMTP_SERVER_CONSTANT = "smtp_adm_server"
SMTP_PORT_CONSTANT = "smtp_port"

SUBJECT_PREFIX = "OBS!! Aktindsigt fejl"
# Emneskabeloner. Formatteres med alarm-itemets felter.
SUBJECTS = {
    "retried": "Workitem genkørt - tjek op: {queue}",
    "manual": "Workitem kræver manuel håndtering: {queue}",
    "waiting": "Workitem venter, men bliver ikke kørt: {queue}",
    "missing_forms": "Formularsvar ikke leveret: {webform_id}",
}


def subject(key: str, **fields: object) -> str:
    """Bygger emnelinjen for en alarm-mail.

    Args:
        key: Nøglen i ``SUBJECTS``.
        **fields: Værdierne, skabelonen formatteres med.

    Returns:
        ``SUBJECT_PREFIX`` efterfulgt af den formatterede skabelon.
    """
    return f"{SUBJECT_PREFIX}: {SUBJECTS[key].format(**fields)}"


def _uses_portal() -> bool:
    """True hvis en mapping har portalen som led."""
    return any(d.kind == "aktindsigt" for m in FORM_MAP for d in m.destinations)


def _validate_mapping(mapping: FormMapping) -> list[str]:
    """Validerer én mapping og returnerer dens værdier til pladsholder-tjekket.

    Args:
        mapping: Mappingen der valideres.

    Returns:
        Webform-id'et og kønavnene i mappingen.

    Raises:
        ValueError: Hvis mappingen ikke har led, eller et led har en ukendt
            type, en ukendt match-type, mangler kønavn, eller matcher portalen
            på andet end uuid.
    """
    if not mapping.destinations:
        raise ValueError(f"No destinations for {mapping.webform_id}")
    values = [mapping.webform_id]
    for dest in mapping.destinations:
        if dest.kind not in ("ats_queue", "aktindsigt"):
            raise ValueError(
                f"Invalid destination kind {dest.kind!r} for {mapping.webform_id}"
            )
        if dest.match_on not in ("uuid", "sid"):
            raise ValueError(
                f"Invalid match_on {dest.match_on!r} for {mapping.webform_id}"
            )
        if dest.kind == "ats_queue":
            if not dest.name:
                raise ValueError(f"Queue name missing for {mapping.webform_id}")
            values.append(dest.name)
        elif dest.match_on != "uuid":
            raise ValueError(f"The portal only matches on uuid ({mapping.webform_id})")
    return values


def validate_config() -> None:
    """Afviser konfiguration med pladsholdere eller ugyldige værdier.

    Raises:
        ValueError: Hvis et kønavn, et webform-id eller portalens opsætning
            er en pladsholder, eller hvis et led har en ukendt type eller
            match-type.
    """
    values = [*MONITORED_QUEUES]
    for mapping in FORM_MAP:
        values += _validate_mapping(mapping)

    if _uses_portal():
        values += [AKTINDSIGT_BASE_URL, AKTINDSIGT_CREDENTIAL]

    placeholders = [v for v in values if v.startswith(PLACEHOLDER_PREFIX)]
    if placeholders:
        raise ValueError(f"alarm_config contains unfilled placeholders: {placeholders}")
