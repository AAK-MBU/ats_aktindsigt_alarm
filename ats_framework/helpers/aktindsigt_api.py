"""Opslag af modtagne formularsvar i aktindsigt-portalens backend.

Kalder ``GET {AKTINDSIGT_BASE_URL}/api/intake/modtagne`` med headeren
``X-API-Key``. Svaret har formen
``{"modtagne": [{"uuid", "sagId", "webformId", "modtaget", "slettet"}, ...]}``
med én række pr. sag, portalen har oprettet ud fra et formularsvar. Sager,
portalen har slettet efter endt opbevaring, står der stadig med deres gamle
``sagId`` og ``slettet: true``.
"""

import logging

import requests

from ats_framework.processes import alarm_config

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"


def fetch_received(webform_id: str, api_key: str) -> dict[str, str]:
    """Henter de svar på en webform, portalen har modtaget.

    Args:
        webform_id: Webformens maskinnavn.
        api_key: Aktindsigts intake-API-nøgle.

    Returns:
        Dict fra svarets uuid til ``"sag <sagId>"``, eller
        ``"sag <sagId> (slettet)"`` for en sag, portalen har slettet.

    Raises:
        requests.HTTPError: Hvis portalen afviser kaldet.
    """
    response = requests.get(
        f"{alarm_config.AKTINDSIGT_BASE_URL.rstrip('/')}/api/intake/modtagne",
        params={"webform_id": webform_id},
        headers={API_KEY_HEADER: api_key},
        timeout=60,
    )
    response.raise_for_status()
    rows = response.json().get("modtagne") or []

    received = {
        str(r["uuid"]): f"sag {r.get('sagId')}"
        + (" (slettet)" if r.get("slettet") else "")
        for r in rows
        if r.get("uuid")
    }
    logger.info("Portal has %d received submissions for %s", len(received), webform_id)
    return received
