"""Calendar of the predicted deliveries."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ParcelloConfigEntry
from .api import Parcel
from .entity import ParcelloEntity

# Length of a delivery without a predicted end.
DEFAULT_DURATION = timedelta(hours=1)

DESCRIPTION = EntityDescription(key="deliveries", translation_key="deliveries")


# Home Assistant requires this exact coroutine signature.
async def async_setup_entry(  # NOSONAR
    hass: HomeAssistant,
    entry: ParcelloConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the calendar."""
    async_add_entities([ParcelloCalendar(entry.runtime_data, DESCRIPTION)])


def _event(parcel: Parcel) -> CalendarEvent | None:
    """Return the delivery window of a parcel as calendar event."""
    start = parcel.delivery_start
    if start is None:
        return None
    end = parcel.delivery_end
    if end is None or end <= start:
        end = start + DEFAULT_DURATION
    details = [
        part
        for part in (
            parcel.carrier,
            parcel.tracking_id,
            parcel.last_event.message if parcel.last_event else None,
        )
        if part
    ]
    return CalendarEvent(
        start=dt_util.as_local(start),
        end=dt_util.as_local(end),
        summary=parcel.name,
        description="\n".join(details) or None,
        uid=parcel.id,
    )


class ParcelloCalendar(ParcelloEntity, CalendarEntity):
    """Predicted delivery windows of the expected parcels."""

    # Newer releases read the color from a CalendarEntityDescription, which
    # older ones do not have.
    _attr_initial_color = None

    def _events(self) -> list[CalendarEvent]:
        events = [
            event
            for parcel in self.coordinator.expected_parcels()
            if (event := _event(parcel)) is not None
        ]
        return sorted(events, key=lambda event: event.start_datetime_local)

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next delivery."""
        now = dt_util.now()
        return next(
            (event for event in self._events() if event.end_datetime_local > now),
            None,
        )

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the deliveries within a time span."""
        return [
            event
            for event in self._events()
            if event.start_datetime_local < end_date
            and event.end_datetime_local > start_date
        ]
