"""Update coordinator for the Parcello integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Parcel, ParcelloApiClient, ParcelloApiError, ParcelloAuthError
from .const import (
    ACTIVE_STATUSES,
    ATTR_CARRIER,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_NAME,
    ATTR_PARCEL_ID,
    ATTR_TRACKING_ID,
    CONF_DELIVERED_DAYS,
    CONF_SCAN_INTERVAL,
    DEFAULT_DELIVERED_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_STATUS_CHANGED,
    STATUS_DELIVERED,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import ParcelloConfigEntry

_LOGGER = logging.getLogger(__name__)

# Parcels the server has not tracked yet are requested one by one, like the
# app does. This keeps a long list from causing many requests.
MAX_SINGLE_UPDATES = 5


class ParcelloCoordinator(DataUpdateCoordinator[dict[str, Parcel]]):
    """Fetch the parcels of a Parcello account."""

    config_entry: ParcelloConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ParcelloConfigEntry,
        client: ParcelloApiClient,
    ) -> None:
        """Initialize the coordinator."""
        minutes = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(minutes=minutes),
        )
        self.client = client
        self.delivered_days = int(
            entry.options.get(CONF_DELIVERED_DAYS, DEFAULT_DELIVERED_DAYS)
        )

    async def _async_update_data(self) -> dict[str, Parcel]:
        """Fetch the parcels and fire an event for every changed status."""
        try:
            parcels = {
                parcel.id: parcel for parcel in await self.client.async_get_parcels()
            }
            pending = [parcel for parcel in parcels.values() if parcel.needs_update]
            for parcel in pending[:MAX_SINGLE_UPDATES]:
                try:
                    parcels[parcel.id] = await self.client.async_get_parcel(parcel.id)
                except ParcelloAuthError:
                    raise
                except ParcelloApiError as err:
                    # The parcel from the list is good enough until the
                    # next update.
                    _LOGGER.debug("Updating parcel %s failed: %s", parcel.id, err)
        except ParcelloAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except ParcelloApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        # No events for the parcels known before Home Assistant started.
        if self.data is not None:
            for parcel in parcels.values():
                previous = self.data.get(parcel.id)
                old_status = previous.status if previous else None
                if parcel.status != old_status:
                    self._fire_status_changed(parcel, old_status)
        return parcels

    def _fire_status_changed(self, parcel: Parcel, old_status: str | None) -> None:
        last_event = parcel.last_event
        data: dict[str, Any] = {
            ATTR_CONFIG_ENTRY_ID: self.config_entry.entry_id,
            ATTR_PARCEL_ID: parcel.id,
            ATTR_TRACKING_ID: parcel.tracking_id,
            ATTR_NAME: parcel.name,
            ATTR_CARRIER: parcel.carrier,
            "old_status": old_status,
            "status": parcel.status,
            "outgoing": parcel.outgoing,
            "last_event": last_event.as_dict() if last_event else None,
        }
        self.hass.bus.async_fire(EVENT_STATUS_CHANGED, data)

    def is_visible(self, parcel: Parcel) -> bool:
        """Return True if the parcel has entities.

        Delivered parcels are kept for the configured number of days after
        their last tracking event (always, if no event has a time).
        """
        if parcel.status != STATUS_DELIVERED:
            return True
        if self.delivered_days <= 0:
            return False
        last_event = parcel.last_event
        if last_event is None or last_event.timestamp is None:
            return True
        return dt_util.utcnow() - last_event.timestamp <= timedelta(
            days=self.delivered_days
        )

    def expected_parcels(self) -> list[Parcel]:
        """Return the incoming parcels which are not delivered yet."""
        return [
            parcel
            for parcel in (self.data or {}).values()
            if not parcel.outgoing and parcel.status in ACTIVE_STATUSES
        ]
