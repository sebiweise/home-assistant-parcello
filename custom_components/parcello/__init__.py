"""The Parcello integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import (
    ParcelloAlreadyTrackedError,
    ParcelloApiClient,
    ParcelloApiError,
    ParcelloAuthError,
)
from .const import (
    ATTR_CARRIER,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_NAME,
    ATTR_PARCEL_ID,
    ATTR_TRACKING_ID,
    CONF_TOKEN,
    CONF_USER_ID,
    DOMAIN,
    SERVICE_ADD_PARCEL,
    SERVICE_REMOVE_PARCEL,
)
from .coordinator import ParcelloCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type ParcelloConfigEntry = ConfigEntry[ParcelloCoordinator]

ADD_PARCEL_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_TRACKING_ID): vol.All(cv.string, vol.Strip, vol.Length(1)),
        vol.Optional(ATTR_NAME): vol.All(cv.string, vol.Strip),
        vol.Optional(ATTR_CARRIER): vol.All(cv.string, vol.Strip),
    }
)
REMOVE_PARCEL_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_TRACKING_ID): vol.All(cv.string, vol.Strip, vol.Length(1)),
    }
)


def api_language(hass: HomeAssistant) -> str:
    """Return the language of the tracking messages (German or English)."""
    return "de" if hass.config.language.split("-")[0] == "de" else "en"


# Home Assistant requires this exact coroutine signature.
async def async_setup(  # NOSONAR
    hass: HomeAssistant,
    config: ConfigType,  # NOSONAR
) -> bool:
    """Register the integration services."""
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ParcelloConfigEntry) -> bool:
    """Set up Parcello from a config entry."""
    client = ParcelloApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_USER_ID],
        entry.data[CONF_TOKEN],
        api_language(hass),
    )
    coordinator = ParcelloCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ParcelloConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: ParcelloConfigEntry
) -> None:
    """Reload the entry when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_get_entries(
    hass: HomeAssistant, entry_id: str | None
) -> list[ParcelloConfigEntry]:
    """Return the loaded entries targeted by a service call."""
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
        and (entry_id is None or entry.entry_id == entry_id)
    ]
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_found"
        )
    return entries


def _api_error(err: ParcelloApiError) -> HomeAssistantError:
    if isinstance(err, ParcelloAuthError):
        return HomeAssistantError(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        )
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="api_error",
        translation_placeholders={"error": str(err)},
    )


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    async def _handle_add_parcel(call: ServiceCall) -> ServiceResponse:
        entries = _async_get_entries(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        if len(entries) > 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_required"
            )
        coordinator = entries[0].runtime_data
        client = coordinator.client
        tracking_id = call.data[ATTR_TRACKING_ID]
        try:
            carrier = call.data.get(ATTR_CARRIER) or (
                await client.async_identify_carrier(tracking_id)
            )
            parcel_id = await client.async_add_parcel(
                tracking_id, call.data.get(ATTR_NAME), carrier
            )
            # Lets the server track the new parcel at the carrier.
            await client.async_get_parcel(parcel_id)
        except ParcelloAlreadyTrackedError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="already_tracked",
                translation_placeholders={"tracking_id": tracking_id},
            ) from err
        except ParcelloApiError as err:
            raise _api_error(err) from err
        await coordinator.async_request_refresh()
        return {ATTR_PARCEL_ID: parcel_id, ATTR_CARRIER: carrier}

    async def _handle_remove_parcel(call: ServiceCall) -> None:
        entries = _async_get_entries(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        tracking_id = call.data[ATTR_TRACKING_ID]
        removed = 0
        for entry in entries:
            coordinator = entry.runtime_data
            parcels = [
                parcel
                for parcel in (coordinator.data or {}).values()
                if (parcel.tracking_id or "").casefold() == tracking_id.casefold()
            ]
            for parcel in parcels:
                try:
                    await coordinator.client.async_delete_parcel(parcel.id)
                except ParcelloApiError as err:
                    raise _api_error(err) from err
            if parcels:
                removed += len(parcels)
                await coordinator.async_request_refresh()
        if not removed:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="parcel_not_found",
                translation_placeholders={"tracking_id": tracking_id},
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_PARCEL,
        _handle_add_parcel,
        schema=ADD_PARCEL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_PARCEL,
        _handle_remove_parcel,
        schema=REMOVE_PARCEL_SCHEMA,
    )
