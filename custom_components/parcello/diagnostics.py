"""Diagnostics for the Parcello integration.

The API is not documented, so the raw parcels help to find out what changed
when something breaks. Personal data is redacted.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant

from . import ParcelloConfigEntry
from .api import Parcel, decode_json
from .const import CONF_TOKEN, CONF_USER_ID

TO_REDACT = {
    CONF_EMAIL,
    CONF_TOKEN,
    CONF_USER_ID,
    "assigned_address",
    "email",
    "id",
    "location",
    "msg",
    "name",
    "shipping_email",
    "survey",
    "token",
    "tracking_url",
    "trackingid",
    "userid",
}
# Fields the API sends as JSON encoded strings.
JSON_FIELDS = ("assigned_address", "path", "survey")


def _raw(parcel: Parcel) -> dict[str, Any]:
    """Return the raw parcel, decoded so nested personal data is redacted."""
    raw = dict(parcel.raw)
    for key in JSON_FIELDS:
        if key in raw:
            raw[key] = decode_json(raw[key])
    return async_redact_data(raw, TO_REDACT)


# Home Assistant requires this exact coroutine signature.
async def async_get_config_entry_diagnostics(  # NOSONAR
    hass: HomeAssistant, entry: ParcelloConfigEntry
) -> dict[str, Any]:
    """Return the diagnostics of a config entry."""
    coordinator = entry.runtime_data
    return {
        "data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "last_update_success": coordinator.last_update_success,
        "parcels": [
            {
                "status": parcel.status,
                "raw": _raw(parcel),
            }
            for parcel in (coordinator.data or {}).values()
        ],
    }
