"""Tests for the Parcello API client."""

import base64
from datetime import UTC, datetime
import json

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.parcello.api import (
    Parcel,
    ParcelloAlreadyTrackedError,
    ParcelloApiClient,
    ParcelloApiError,
    ParcelloAuthError,
    _redact,
)
from custom_components.parcello.const import (
    API_URL,
    STATUS_DELIVERED,
    STATUS_IN_TRANSIT,
    STATUS_LOADING,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_RETURNED,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

TOKEN = "secret/token+1"
USER_ID = "u1"


def _parcel(**fields) -> dict:
    return {
        "id": "u1_p1",
        "trackingid": "00340434161094042557",
        "name": "Schuhe",
        "carrier": "DHL",
        "currentState": 1,
        "is_outgoing": 0,
        "prophesy_range": {"start": 1767254400, "end": 1767261600},
        "path": "[]",
        **fields,
    }


def test_parse_parcel() -> None:
    """The fields of the web app are parsed."""
    parcel = Parcel.from_api(
        _parcel(
            path=json.dumps(
                [
                    {
                        "code": "CODE_IN_TRANSIT",
                        "msg": "Im Paketzentrum",
                        "date": 1767168000,
                        "location": {"name": "Köln", "lat": 1, "lng": 2},
                    },
                    {
                        "code": "CODE_INFO_RECEIVED",
                        "msg": "Daten übermittelt",
                        "date": 1767081600000,
                        "location": None,
                    },
                ]
            ),
            tracking_url=base64.b64encode(b"https://example.com/track").decode(),
            is_pickupable="1",
        )
    )
    assert parcel.status == STATUS_IN_TRANSIT
    assert parcel.tracking_id == "00340434161094042557"
    assert parcel.delivery_start == datetime(2026, 1, 1, 8, tzinfo=UTC)
    assert parcel.delivery_end == datetime(2026, 1, 1, 10, tzinfo=UTC)
    assert parcel.last_event.message == "Im Paketzentrum"
    assert parcel.last_event.location == "Köln"
    assert parcel.events[1].timestamp == datetime(2025, 12, 30, 8, tzinfo=UTC)
    assert parcel.tracking_url == "https://example.com/track"
    assert parcel.pickup is True
    assert parcel.outgoing is False
    assert parcel.needs_update is False


def test_parse_events_sorted_by_time() -> None:
    """The latest event comes first, whatever the order of the server."""
    parcel = Parcel.from_api(
        _parcel(
            path=[
                {"code": "CODE_INFO_RECEIVED", "msg": "old", "date": 100},
                {"code": "CODE_OUT_FOR_DELIVERY", "msg": "new", "date": 200},
            ]
        )
    )
    assert parcel.last_event.message == "new"
    assert parcel.status == STATUS_OUT_FOR_DELIVERY


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({"currentState": 0}, STATUS_DELIVERED),
        ({"currentState": "8"}, STATUS_RETURNED),
        ({"currentState": 4}, STATUS_LOADING),
        ({"currentState": 1, "current_state_new": 0}, STATUS_IN_TRANSIT),
        ({"currentState": 1, "current_state_new": 8}, STATUS_RETURNED),
        ({"currentState": 1, "is_user_delivered": 1}, STATUS_DELIVERED),
        ({"currentState": 42}, None),
        ({"currentState": None}, None),
    ],
)
def test_parse_status(fields: dict, status: str | None) -> None:
    """The state of the app is mapped to a status."""
    assert Parcel.from_api(_parcel(**fields)).status == status


def test_parse_incomplete_parcel() -> None:
    """Missing or broken fields do not fail the parcel."""
    parcel = Parcel.from_api(
        {
            "id": 5,
            "path": "not json",
            "prophesy_range": None,
            "tracking_url": "not base64!",
        }
    )
    assert parcel.id == "5"
    assert parcel.name == "5"
    assert parcel.events == ()
    assert parcel.delivery_start is None
    assert parcel.tracking_url is None
    assert parcel.status is None


async def test_login(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The login sends the base64 encoded credentials."""
    credentials = base64.b64encode(b"a@example.com:pw").decode()
    aioclient_mock.post(
        f"{API_URL}/login/mail/{credentials}",
        json={"status": 200, "userid": USER_ID, "apiKey": TOKEN},
    )
    result = await ParcelloApiClient.async_login(
        async_get_clientsession(hass), "a@example.com", "pw"
    )
    assert result.user_id == USER_ID
    assert result.token == TOKEN


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"json": {"status": 401, "apiKey": None, "userid": None}}, ParcelloAuthError),
        ({"status": 401}, ParcelloAuthError),
        ({"json": {"status": 500}}, ParcelloApiError),
        ({"text": "<html>"}, ParcelloApiError),
        ({"status": 502}, ParcelloApiError),
    ],
)
async def test_login_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, kwargs: dict, error
) -> None:
    """A failed login raises the matching error."""
    credentials = base64.b64encode(b"a@example.com:pw").decode()
    aioclient_mock.post(f"{API_URL}/login/mail/{credentials}", **kwargs)
    with pytest.raises(error):
        await ParcelloApiClient.async_login(
            async_get_clientsession(hass), "a@example.com", "pw"
        )


async def test_get_parcels(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The parcels are requested with the token, broken ones are skipped."""
    aioclient_mock.get(
        f"{API_URL}/user/{USER_ID}/parcels-without-crawling",
        json=[_parcel(), {"name": "no id"}],
    )
    client = ParcelloApiClient(async_get_clientsession(hass), USER_ID, TOKEN)
    parcels = await client.async_get_parcels()
    assert [parcel.id for parcel in parcels] == ["u1_p1"]
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert url.query["token"] == TOKEN
    assert url.query["lang"] == "de"
    assert "Cache-Key" in headers


async def test_get_parcels_empty(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An empty answer means no parcels."""
    aioclient_mock.get(f"{API_URL}/user/{USER_ID}/parcels-without-crawling", text="")
    client = ParcelloApiClient(async_get_clientsession(hass), USER_ID, TOKEN)
    assert await client.async_get_parcels() == []


async def test_add_parcel(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A new parcel is sent like the app does."""
    aioclient_mock.post(f"{API_URL}/user/{USER_ID}/add/parcel", json={})
    client = ParcelloApiClient(async_get_clientsession(hass), USER_ID, TOKEN)
    parcel_id = await client.async_add_parcel("123", None, "DHL")
    assert parcel_id.startswith(f"{USER_ID}_p")
    _, url, body, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert body["id"] == USER_ID
    assert body["newParcel"]["id"] == parcel_id
    assert body["newParcel"]["trackingid"] == "123"
    assert body["newParcel"]["name"] == "123"
    assert body["newParcel"]["carrier"] == "DHL"


async def test_add_parcel_already_tracked(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """HTTP 409 means the parcel is tracked already."""
    aioclient_mock.post(f"{API_URL}/user/{USER_ID}/add/parcel", status=409)
    client = ParcelloApiClient(async_get_clientsession(hass), USER_ID, TOKEN)
    with pytest.raises(ParcelloAlreadyTrackedError):
        await client.async_add_parcel("123", None, None)


async def test_identify_carrier(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The carrier is guessed from the base64 encoded tracking ID."""
    aioclient_mock.get(f"{API_URL}/info/identifyTracking/MTIz", json={"carrier": "UPS"})
    client = ParcelloApiClient(async_get_clientsession(hass), USER_ID, TOKEN)
    assert await client.async_identify_carrier("123") == "UPS"
    _, url, _, _ = aioclient_mock.mock_calls[0]
    assert "token" not in url.query


class _FailingSession:
    """Session whose requests always fail with an error containing the URL."""

    def request(self, method, url, params=None, headers=None, json=None):
        raise aiohttp.ClientError(f"Failed {url}?token={(params or {}).get('token')}")


async def test_error_message_does_not_contain_token() -> None:
    """The token never ends up in the error message or its cause."""
    client = ParcelloApiClient(_FailingSession(), USER_ID, TOKEN)
    with pytest.raises(ParcelloApiError) as exc_info:
        await client.async_get_parcels()
    assert TOKEN not in str(exc_info.value)
    assert "ClientError" in str(exc_info.value)
    assert exc_info.value.__cause__ is None


async def test_login_error_does_not_contain_credentials() -> None:
    """Neither the password nor the encoded credentials are part of errors."""
    with pytest.raises(ParcelloApiError) as exc_info:
        await ParcelloApiClient.async_login(_FailingSession(), "a@b.c", "geheim")
    credentials = base64.b64encode(b"a@b.c:geheim").decode()
    assert credentials not in str(exc_info.value)
    assert "geheim" not in str(exc_info.value)


def test_redact_encoded_token() -> None:
    """URL-encoded forms of the token are redacted, too."""
    err = aiohttp.ClientError("url=?token=secret%2Ftoken%2B1 and secret/token%2B1")
    assert "secret" not in _redact(err, (TOKEN,))
    assert _redact(aiohttp.ClientError(), (TOKEN,)) == "ClientError"
