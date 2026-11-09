"""Sensors for the Parcello integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from . import ParcelloConfigEntry
from .api import Parcel
from .const import (
    ATTR_CARRIER,
    ATTR_NAME,
    ATTR_PARCEL_ID,
    ATTR_TRACKING_ID,
    DOMAIN,
    STATUS_OUT_FOR_DELIVERY,
    STATUSES,
)
from .coordinator import ParcelloCoordinator
from .entity import ParcelloEntity, parcel_unique_id


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _summary(parcel: Parcel) -> dict[str, Any]:
    """Return the short form of a parcel for list attributes."""
    return {
        ATTR_NAME: parcel.name,
        ATTR_TRACKING_ID: parcel.tracking_id,
        ATTR_CARRIER: parcel.carrier,
        "status": parcel.status,
        "delivery_start": _iso(parcel.delivery_start),
        "delivery_end": _iso(parcel.delivery_end),
    }


def _next_delivery(coordinator: ParcelloCoordinator) -> Parcel | None:
    """Return the expected parcel with the earliest delivery window."""
    now = dt_util.utcnow()
    upcoming = [
        parcel
        for parcel in coordinator.expected_parcels()
        if parcel.delivery_start is not None
        and (parcel.delivery_end or parcel.delivery_start) >= now
    ]
    return min(upcoming, key=lambda parcel: parcel.delivery_start, default=None)


def _sorted(parcels: list[Parcel]) -> list[Parcel]:
    """Sort parcels by their delivery window, those without one last."""
    return sorted(
        parcels,
        key=lambda parcel: (
            parcel.delivery_start is None,
            parcel.delivery_start or dt_util.utcnow(),
        ),
    )


@dataclass(frozen=True, kw_only=True)
class ParcelloSensorEntityDescription(SensorEntityDescription):
    """Describes a Parcello account sensor."""

    value_fn: Callable[[ParcelloCoordinator], StateType | datetime]
    attrs_fn: Callable[[ParcelloCoordinator], dict[str, Any]] | None = None


def _next_delivery_attrs(coordinator: ParcelloCoordinator) -> dict[str, Any]:
    if (parcel := _next_delivery(coordinator)) is None:
        return {}
    return _summary(parcel)


SENSORS: tuple[ParcelloSensorEntityDescription, ...] = (
    ParcelloSensorEntityDescription(
        key="expected_parcels",
        translation_key="expected_parcels",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: len(coordinator.expected_parcels()),
        attrs_fn=lambda coordinator: {
            "parcels": [
                _summary(parcel) for parcel in _sorted(coordinator.expected_parcels())
            ]
        },
    ),
    ParcelloSensorEntityDescription(
        key="out_for_delivery",
        translation_key="out_for_delivery",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: sum(
            parcel.status == STATUS_OUT_FOR_DELIVERY
            for parcel in coordinator.expected_parcels()
        ),
        attrs_fn=lambda coordinator: {
            "parcels": [
                _summary(parcel)
                for parcel in _sorted(coordinator.expected_parcels())
                if parcel.status == STATUS_OUT_FOR_DELIVERY
            ]
        },
    ),
    ParcelloSensorEntityDescription(
        key="next_delivery",
        translation_key="next_delivery",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda coordinator: (
            parcel.delivery_start if (parcel := _next_delivery(coordinator)) else None
        ),
        attrs_fn=_next_delivery_attrs,
    ),
)

PARCEL_DESCRIPTION = SensorEntityDescription(
    key="parcel",
    translation_key="parcel",
    device_class=SensorDeviceClass.ENUM,
    options=STATUSES,
)


# Home Assistant requires this exact coroutine signature.
async def async_setup_entry(  # NOSONAR
    hass: HomeAssistant,
    entry: ParcelloConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors, one per parcel and some for the account."""
    coordinator = entry.runtime_data
    async_add_entities(
        ParcelloSensor(coordinator, description) for description in SENSORS
    )

    registry = er.async_get(hass)
    prefix = parcel_unique_id(coordinator, "")
    added: set[str] = set()

    @callback
    def _async_update_parcels() -> None:
        """Add entities for new parcels, remove those of finished ones."""
        visible = {
            parcel_id
            for parcel_id, parcel in (coordinator.data or {}).items()
            if coordinator.is_visible(parcel)
        }
        if new := visible - added:
            added.update(new)
            async_add_entities(
                ParcelloParcelSensor(coordinator, parcel_id) for parcel_id in new
            )
        # Also covers parcels deleted while Home Assistant was not running.
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if (
                entity.domain == SENSOR_DOMAIN
                and entity.platform == DOMAIN
                and entity.unique_id.startswith(prefix)
                and entity.unique_id.removeprefix(prefix) not in visible
            ):
                added.discard(entity.unique_id.removeprefix(prefix))
                registry.async_remove(entity.entity_id)

    _async_update_parcels()
    entry.async_on_unload(coordinator.async_add_listener(_async_update_parcels))


class ParcelloSensor(ParcelloEntity, SensorEntity):
    """Sensor summarizing the parcels of the account."""

    entity_description: ParcelloSensorEntityDescription

    @property
    def native_value(self) -> StateType | datetime:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator)


class ParcelloParcelSensor(ParcelloEntity, SensorEntity):
    """Status of a single parcel."""

    def __init__(self, coordinator: ParcelloCoordinator, parcel_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            PARCEL_DESCRIPTION,
            parcel_unique_id(coordinator, parcel_id),
        )
        self._parcel_id = parcel_id

    @property
    def parcel(self) -> Parcel | None:
        """Return the parcel, None once it is gone."""
        return (self.coordinator.data or {}).get(self._parcel_id)

    @property
    def name(self) -> str | None:
        """Return the name given to the parcel in Parcello."""
        parcel = self.parcel
        return parcel.name if parcel else None

    @property
    def available(self) -> bool:
        """Return True while the parcel is part of the account."""
        return super().available and self.parcel is not None

    @property
    def native_value(self) -> str | None:
        """Return the status of the parcel."""
        parcel = self.parcel
        return parcel.status if parcel else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the details of the parcel."""
        if (parcel := self.parcel) is None:
            return None
        last_event = parcel.last_event
        return {
            ATTR_PARCEL_ID: parcel.id,
            ATTR_TRACKING_ID: parcel.tracking_id,
            ATTR_CARRIER: parcel.carrier,
            "outgoing": parcel.outgoing,
            "pickup": parcel.pickup,
            "rescheduled": parcel.rescheduled,
            "delivery_start": _iso(parcel.delivery_start),
            "delivery_end": _iso(parcel.delivery_end),
            "last_event": last_event.message if last_event else None,
            "last_event_time": _iso(last_event.timestamp) if last_event else None,
            "last_event_location": last_event.location if last_event else None,
            "last_event_code": last_event.code if last_event else None,
            "tracking_url": parcel.tracking_url,
        }
