"""Base entity for the Parcello integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ParcelloCoordinator


def parcel_unique_id(coordinator: ParcelloCoordinator, parcel_id: str) -> str:
    """Return the unique ID of a parcel's entity. It must never change."""
    return f"{coordinator.config_entry.entry_id}_parcel_{parcel_id}"


class ParcelloEntity(CoordinatorEntity[ParcelloCoordinator]):
    """Common base for Parcello entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ParcelloCoordinator,
        description: EntityDescription,
        unique_id: str | None = None,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = unique_id or f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Parcello",
            manufacturer="Parcello",
            model="Parcel tracking",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.parcello.org/app/",
        )
