"""Tests for the Parcello integration."""

import base64
from datetime import datetime, timedelta
import json
import re

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.parcello.const import (
    API_URL,
    CONF_DELIVERED_DAYS,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USER_ID,
    DOMAIN,
    EVENT_STATUS_CHANGED,
)
from custom_components.parcello.diagnostics import async_get_config_entry_diagnostics
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

EMAIL = "max@example.com"
PASSWORD = "geheim"
USER_ID = "u1"
TOKEN = "token1"
NOW = datetime.fromisoformat("2026-01-01T06:00:00+00:00")
PARCELS_URL = f"{API_URL}/user/{USER_ID}/parcels-without-crawling"
LOGIN_URL = (
    f"{API_URL}/login/mail/" + base64.b64encode(f"{EMAIL}:{PASSWORD}".encode()).decode()
)


def _ts(hours: float) -> int:
    """Return seconds since the epoch, hours after NOW."""
    return int((NOW + timedelta(hours=hours)).timestamp())


def _parcel(parcel_id: str = "u1_p1", **fields) -> dict:
    return {
        "id": parcel_id,
        "trackingid": f"TN{parcel_id}",
        "name": "Schuhe",
        "carrier": "DHL",
        "currentState": 1,
        "is_outgoing": 0,
        "prophesy_range": {"start": _ts(4), "end": _ts(6)},
        "path": json.dumps(
            [
                {
                    "code": "CODE_IN_TRANSIT",
                    "msg": "Im Paketzentrum",
                    "date": _ts(-2),
                    "location": {"name": "Köln"},
                }
            ]
        ),
        **fields,
    }


@pytest.fixture(autouse=True)
def _freeze_time(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(NOW)


async def _setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    parcels: list[dict] | None = None,
    **options,
) -> MockConfigEntry:
    aioclient_mock.get(PARCELS_URL, json=[_parcel()] if parcels is None else parcels)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=USER_ID,
        data={CONF_EMAIL: EMAIL, CONF_USER_ID: USER_ID, CONF_TOKEN: TOKEN},
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _update(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    parcels: list[dict] | None = None,
    **kwargs,
) -> None:
    """Answer the next update with other parcels."""
    aioclient_mock.clear_requests()
    if parcels is not None:
        kwargs["json"] = parcels
    aioclient_mock.get(PARCELS_URL, **kwargs)
    freezer.tick(timedelta(minutes=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_config_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The user flow logs in and stores the token, not the password."""
    aioclient_mock.post(
        LOGIN_URL, json={"status": 200, "userid": USER_ID, "apiKey": TOKEN}
    )
    aioclient_mock.get(PARCELS_URL, json=[])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: f" {EMAIL} ", CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["data"] == {
        CONF_EMAIL: EMAIL,
        CONF_USER_ID: USER_ID,
        CONF_TOKEN: TOKEN,
    }
    assert result["result"].unique_id == USER_ID


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"json": {"status": 401, "userid": None, "apiKey": None}}, "invalid_auth"),
        ({"status": 500}, "cannot_connect"),
    ],
)
async def test_config_flow_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, kwargs: dict, error: str
) -> None:
    """A failed login shows an error and keeps the email."""
    aioclient_mock.post(LOGIN_URL, **kwargs)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_config_flow_already_configured(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An account can only be set up once."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(
        LOGIN_URL, json={"status": 200, "userid": USER_ID, "apiKey": "other"}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_rejected_token_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """HTTP 401 asks for the password, a new login stores the new token."""
    entry = await _setup(hass, aioclient_mock)
    await _update(hass, aioclient_mock, freezer, status=401)
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]

    aioclient_mock.post(
        LOGIN_URL, json={"status": 200, "userid": USER_ID, "apiKey": "token2"}
    )
    aioclient_mock.get(PARCELS_URL, json=[_parcel()])
    result = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == "token2"


async def test_reauth_other_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The reauth flow does not switch to another account."""
    entry = await _setup(hass, aioclient_mock)
    aioclient_mock.post(
        LOGIN_URL, json={"status": 200, "userid": "u2", "apiKey": "token2"}
    )
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_TOKEN] == TOKEN


async def test_options_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The options are stored as integers."""
    entry = await _setup(hass, aioclient_mock)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30.0, CONF_DELIVERED_DAYS: 0.0}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 30, CONF_DELIVERED_DAYS: 0}
    assert entry.runtime_data.update_interval == timedelta(minutes=30)


async def test_setup_retry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The entry is retried if Parcello cannot be reached."""
    aioclient_mock.get(PARCELS_URL, status=503)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER_ID,
        data={CONF_EMAIL: EMAIL, CONF_USER_ID: USER_ID, CONF_TOKEN: TOKEN},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_sensors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The account sensors only count incoming parcels on their way."""
    await _setup(
        hass,
        aioclient_mock,
        [
            _parcel(),
            _parcel(
                "u1_p2",
                name="Buch",
                prophesy_range={"start": _ts(1), "end": _ts(2)},
                path=[{"code": "CODE_OUT_FOR_DELIVERY", "msg": "Im Auto"}],
            ),
            _parcel("u1_p3", name="Retoure", is_outgoing=1),
            _parcel("u1_p4", name="Alt", currentState=0, path=[]),
        ],
    )
    state = hass.states.get("sensor.parcello_expected_parcels")
    assert state.state == "2"
    assert [parcel["name"] for parcel in state.attributes["parcels"]] == [
        "Buch",
        "Schuhe",
    ]
    state = hass.states.get("sensor.parcello_out_for_delivery")
    assert state.state == "1"
    assert state.attributes["parcels"][0]["tracking_id"] == "TNu1_p2"
    state = hass.states.get("sensor.parcello_next_delivery")
    assert dt_util.parse_datetime(state.state) == NOW + timedelta(hours=1)
    assert state.attributes["name"] == "Buch"

    state = hass.states.get("sensor.parcello_schuhe")
    assert state.state == "in_transit"
    assert state.attributes["tracking_id"] == "TNu1_p1"
    assert state.attributes["carrier"] == "DHL"
    assert state.attributes["last_event"] == "Im Paketzentrum"
    assert state.attributes["last_event_location"] == "Köln"
    assert state.attributes["delivery_start"] == (NOW + timedelta(hours=4)).isoformat()
    assert hass.states.get("sensor.parcello_buch").state == "out_for_delivery"
    assert hass.states.get("sensor.parcello_retoure").attributes["outgoing"] is True
    # Delivered without a time: kept, nothing tells how old it is.
    assert hass.states.get("sensor.parcello_alt").state == "delivered"


async def test_no_parcels(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without parcels the account sensors still exist."""
    await _setup(hass, aioclient_mock, [])
    assert hass.states.get("sensor.parcello_expected_parcels").state == "0"
    assert hass.states.get("sensor.parcello_next_delivery").state == "unknown"
    assert hass.states.get("calendar.parcello_deliveries").state == "off"


async def test_parcels_come_and_go(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """New parcels get a sensor, deleted ones lose theirs."""
    await _setup(hass, aioclient_mock)
    assert hass.states.get("sensor.parcello_schuhe")

    await _update(
        hass, aioclient_mock, freezer, [_parcel(), _parcel("u1_p2", name="Buch")]
    )
    assert hass.states.get("sensor.parcello_buch").state == "in_transit"

    await _update(hass, aioclient_mock, freezer, [_parcel("u1_p2", name="Buch")])
    assert hass.states.get("sensor.parcello_schuhe") is None
    assert entity_registry.async_get("sensor.parcello_schuhe") is None
    assert hass.states.get("sensor.parcello_buch")


async def test_stale_entities_removed_on_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
) -> None:
    """Parcels deleted while Home Assistant was stopped lose their sensor."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER_ID,
        data={CONF_EMAIL: EMAIL, CONF_USER_ID: USER_ID, CONF_TOKEN: TOKEN},
    )
    entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_parcel_gone",
        config_entry=entry,
        suggested_object_id="parcello_gone",
    )
    aioclient_mock.get(PARCELS_URL, json=[_parcel()])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entity_registry.async_get("sensor.parcello_gone") is None
    assert entity_registry.async_get("sensor.parcello_schuhe")


async def test_delivered_parcels_removed_after_days(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Delivered parcels keep their sensor for the configured days."""
    delivered = _parcel(
        currentState=0,
        path=[{"code": "CODE_DELIVERED", "msg": "Zugestellt", "date": _ts(-1)}],
    )
    await _setup(hass, aioclient_mock, [delivered], **{CONF_DELIVERED_DAYS: 2})
    assert hass.states.get("sensor.parcello_schuhe").state == "delivered"

    freezer.tick(timedelta(days=2))
    await _update(hass, aioclient_mock, freezer, [delivered])
    assert hass.states.get("sensor.parcello_schuhe") is None


async def test_delivered_parcels_hidden(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With 0 days delivered parcels have no sensor at all."""
    await _setup(
        hass,
        aioclient_mock,
        [_parcel(currentState=0), _parcel("u1_p2", name="Buch")],
        **{CONF_DELIVERED_DAYS: 0},
    )
    assert hass.states.get("sensor.parcello_schuhe") is None
    assert hass.states.get("sensor.parcello_buch")


async def test_status_changed_event(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A changed status fires an event, the first update does not."""
    events = async_capture_events(hass, EVENT_STATUS_CHANGED)
    entry = await _setup(hass, aioclient_mock)
    assert events == []

    await _update(
        hass,
        aioclient_mock,
        freezer,
        [
            _parcel(path=[{"code": "CODE_OUT_FOR_DELIVERY", "msg": "Im Auto"}]),
            _parcel("u1_p2", name="Buch"),
        ],
    )
    assert [event.data["status"] for event in events] == [
        "out_for_delivery",
        "in_transit",
    ]
    assert events[0].data == {
        "config_entry_id": entry.entry_id,
        "parcel_id": "u1_p1",
        "tracking_id": "TNu1_p1",
        "name": "Schuhe",
        "carrier": "DHL",
        "old_status": "in_transit",
        "status": "out_for_delivery",
        "outgoing": False,
        "last_event": {
            "code": "CODE_OUT_FOR_DELIVERY",
            "message": "Im Auto",
            "timestamp": None,
            "location": None,
        },
    }
    assert events[1].data["old_status"] is None

    await _update(
        hass,
        aioclient_mock,
        freezer,
        [
            _parcel(path=[{"code": "CODE_OUT_FOR_DELIVERY", "msg": "Im Auto"}]),
            _parcel("u1_p2", name="Buch"),
        ],
    )
    assert len(events) == 2


async def test_parcel_not_tracked_yet(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A parcel the server did not track yet is requested on its own."""
    aioclient_mock.get(
        f"{API_URL}/user/{USER_ID}/parcel/u1_p1",
        json=_parcel(path=[{"code": "CODE_OUT_FOR_DELIVERY", "msg": "Im Auto"}]),
    )
    await _setup(hass, aioclient_mock, [_parcel(currentState=4)])
    assert hass.states.get("sensor.parcello_schuhe").state == "out_for_delivery"


async def test_single_update_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A failing single update keeps the parcel from the list."""
    aioclient_mock.get(f"{API_URL}/user/{USER_ID}/parcel/u1_p1", status=500)
    await _setup(hass, aioclient_mock, [_parcel(currentState=4), "broken"])
    assert hass.states.get("sensor.parcello_schuhe").state == "loading"


async def test_update_failure_keeps_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed update marks the entities unavailable but keeps them."""
    await _setup(hass, aioclient_mock)
    await _update(hass, aioclient_mock, freezer, status=500)
    assert hass.states.get("sensor.parcello_schuhe").state == "unavailable"
    await _update(hass, aioclient_mock, freezer, [_parcel()])
    assert hass.states.get("sensor.parcello_schuhe").state == "in_transit"


async def test_calendar(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The calendar shows the delivery windows of the expected parcels."""
    await _setup(
        hass,
        aioclient_mock,
        [
            _parcel(),
            _parcel("u1_p2", name="Ohne Prognose", prophesy_range={}),
            _parcel("u1_p3", name="Zugestellt", currentState=0),
        ],
    )
    state = hass.states.get("calendar.parcello_deliveries")
    assert state.state == "off"
    assert state.attributes["message"] == "Schuhe"

    result = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.parcello_deliveries",
            "start_date_time": NOW.isoformat(),
            "duration": {"days": 1},
        },
        blocking=True,
        return_response=True,
    )
    events = result["calendar.parcello_deliveries"]["events"]
    assert len(events) == 1
    assert events[0]["summary"] == "Schuhe"
    assert events[0]["description"] == "DHL\nTNu1_p1\nIm Paketzentrum"
    assert dt_util.parse_datetime(events[0]["start"]) == NOW + timedelta(hours=4)
    assert dt_util.parse_datetime(events[0]["end"]) == NOW + timedelta(hours=6)


async def test_add_parcel(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The service adds a parcel, detects the carrier and tracks it."""
    await _setup(hass, aioclient_mock, [])
    aioclient_mock.get(f"{API_URL}/info/identifyTracking/MTIz", json={"carrier": "UPS"})
    aioclient_mock.post(f"{API_URL}/user/{USER_ID}/add/parcel", json={})
    aioclient_mock.get(
        re.compile(rf"/user/{USER_ID}/parcel/{USER_ID}_p\d+\?"),
        json=_parcel(name="Neu"),
    )
    result = await hass.services.async_call(
        DOMAIN,
        "add_parcel",
        {"tracking_id": " 123 ", "name": "Neu"},
        blocking=True,
        return_response=True,
    )
    assert result["carrier"] == "UPS"
    assert result["parcel_id"].startswith(f"{USER_ID}_p")
    add_calls = [
        call
        for call in aioclient_mock.mock_calls
        if call[1].path.endswith("/add/parcel")
    ]
    assert add_calls[0][2]["newParcel"]["trackingid"] == "123"
    assert add_calls[0][2]["newParcel"]["carrier"] == "UPS"
    assert any(
        call[1].path.endswith(result["parcel_id"]) for call in aioclient_mock.mock_calls
    )


async def test_add_parcel_already_tracked(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A tracked parcel is reported as such."""
    await _setup(hass, aioclient_mock, [])
    aioclient_mock.post(f"{API_URL}/user/{USER_ID}/add/parcel", status=409)
    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "add_parcel",
            {"tracking_id": "123", "carrier": "DHL"},
            blocking=True,
        )
    assert exc_info.value.translation_key == "already_tracked"


async def test_add_parcel_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """API errors are raised as Home Assistant errors."""
    await _setup(hass, aioclient_mock, [])
    aioclient_mock.post(f"{API_URL}/user/{USER_ID}/add/parcel", status=500)
    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "add_parcel",
            {"tracking_id": "123", "carrier": "DHL"},
            blocking=True,
        )
    assert exc_info.value.translation_key == "api_error"


async def test_remove_parcel(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The service deletes the parcels with the tracking number."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.delete(f"{API_URL}/user/{USER_ID}/parcel/u1_p1/delete", json={})
    await hass.services.async_call(
        DOMAIN, "remove_parcel", {"tracking_id": "tnu1_p1"}, blocking=True
    )
    assert any(call[0] == "DELETE" for call in aioclient_mock.mock_calls)

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN, "remove_parcel", {"tracking_id": "unknown"}, blocking=True
        )
    assert exc_info.value.translation_key == "parcel_not_found"


async def test_service_without_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unknown entry ID is rejected."""
    await _setup(hass, aioclient_mock)
    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "add_parcel",
            {"tracking_id": "123", "config_entry_id": "unknown"},
            blocking=True,
        )
    assert exc_info.value.translation_key == "entry_not_found"


async def test_diagnostics(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The diagnostics contain no token, email or tracking data."""
    entry = await _setup(hass, aioclient_mock)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diagnostics)
    for secret in (TOKEN, EMAIL, USER_ID, "TNu1_p1", "Köln", "Schuhe", "Paketzentrum"):
        assert secret not in text
    assert diagnostics["parcels"][0]["status"] == "in_transit"
    assert diagnostics["parcels"][0]["raw"]["carrier"] == "DHL"


async def test_unload(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The entry unloads."""
    entry = await _setup(hass, aioclient_mock)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
