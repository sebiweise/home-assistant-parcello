"""Client for the Parcello app API.

Parcello has no public API for private users. This client talks to the same
backend as the Parcello app and the web app (www.parcello.org/app); the
endpoints and fields are taken from the web app and are not documented:

    POST /login/mail/<base64(email:password)>
        -> {"status": 200, "userid": "...", "apiKey": "..."}
           (HTTP 200 with "status": 401 for wrong credentials)
    GET  /user/<id>/parcels-without-crawling?token=<apiKey>&lang=de
        -> list of parcels as stored on the server
    GET  /user/<id>/parcel/<parcel id>?token=<apiKey>&lang=de
        -> one parcel, tracked again at the carrier
    POST /user/<id>/add/parcel?token=<apiKey>   (409: already tracked)
    DELETE /user/<id>/parcel/<parcel id>/delete?token=<apiKey>
    GET  /info/identifyTracking/<base64(tracking id)>
        -> {"carrier": "DHL", "confidence": 0.9, ...}

A wrong or expired token is answered with HTTP 401.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
import json
import logging
import random
import time
from typing import Any
from urllib.parse import quote, quote_plus

import aiohttp
from yarl import URL

from homeassistant.util import dt as dt_util

from .const import (
    API_URL,
    STATUS_DELIVERED,
    STATUS_IN_TRANSIT,
    STATUS_LOADING,
    STATUS_MISSING_INFO,
    STATUS_NO_ADDRESS,
    STATUS_NO_DATA_YET,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_PROBLEM,
    STATUS_RETURNED,
    STATUS_WARNING,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30

# "currentState" of a parcel in the app (enum DeliveryState).
STATE_DELIVERED = 0
STATE_LOADING = 4
STATES: dict[int, str] = {
    STATE_DELIVERED: STATUS_DELIVERED,
    1: STATUS_IN_TRANSIT,
    2: STATUS_WARNING,
    3: STATUS_PROBLEM,
    STATE_LOADING: STATUS_LOADING,
    5: STATUS_MISSING_INFO,
    6: STATUS_NO_ADDRESS,
    7: STATUS_NO_DATA_YET,
    8: STATUS_RETURNED,
}
# Event codes of the tracking history ("path").
CODE_OUT_FOR_DELIVERY = "CODE_OUT_FOR_DELIVERY"


class ParcelloApiError(Exception):
    """Generic Parcello API error."""


class ParcelloAuthError(ParcelloApiError):
    """The credentials or the token were rejected."""


class ParcelloAlreadyTrackedError(ParcelloApiError):
    """The parcel is already tracked in the account (HTTP 409)."""


@dataclass(frozen=True, slots=True)
class ParcelloCredentials:
    """Result of a login."""

    user_id: str
    token: str


@dataclass(frozen=True, slots=True)
class ParcelEvent:
    """An entry of the tracking history."""

    code: str | None
    message: str | None
    timestamp: datetime | None
    location: str | None

    def as_dict(self) -> dict[str, Any]:
        """Return the event for state attributes and events."""
        return {
            "code": self.code,
            "message": self.message,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "location": self.location,
        }


@dataclass(frozen=True, slots=True)
class Parcel:
    """A parcel of the Parcello account."""

    id: str
    tracking_id: str | None
    name: str
    carrier: str | None
    status: str | None
    outgoing: bool
    pickup: bool
    rescheduled: bool
    tracking_url: str | None
    delivery_start: datetime | None
    delivery_end: datetime | None
    events: tuple[ParcelEvent, ...]
    # The server has not tracked the parcel at the carrier yet.
    needs_update: bool
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    @property
    def last_event(self) -> ParcelEvent | None:
        """Return the latest entry of the tracking history."""
        return self.events[0] if self.events else None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> Parcel:
        """Create a parcel from the API response.

        The fields are taken from the web app. "path", "assigned_address" and
        "survey" are JSON encoded strings there; "prophesy_range" holds the
        predicted delivery window in seconds since the epoch.
        """
        events = _parse_events(data.get("path"))
        state = _int(data.get("current_state_new")) or _int(data.get("currentState"))
        # The app marks a parcel as delivered when the user did so.
        if _int(data.get("is_user_delivered")) == 1:
            state = STATE_DELIVERED
        status = STATES.get(state) if state is not None else None
        if (
            status == STATUS_IN_TRANSIT
            and events
            and events[0].code == (CODE_OUT_FOR_DELIVERY)
        ):
            status = STATUS_OUT_FOR_DELIVERY
        window = data.get("prophesy_range")
        if not isinstance(window, Mapping):
            window = {}
        tracking_id = _str(data.get("trackingid"))
        return cls(
            id=str(data["id"]),
            tracking_id=tracking_id,
            name=_str(data.get("name")) or tracking_id or str(data["id"]),
            carrier=_str(data.get("carrier")),
            status=status,
            outgoing=bool(_int(data.get("is_outgoing"))),
            pickup=bool(_int(data.get("is_pickupable"))),
            rescheduled=bool(_int(data.get("is_rescheduled"))),
            tracking_url=_tracking_url(data.get("tracking_url")),
            delivery_start=_timestamp(window.get("start")),
            delivery_end=_timestamp(window.get("end")),
            events=events,
            needs_update=state == STATE_LOADING,
            raw=dict(data),
        )


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def decode_json(value: Any) -> Any:
    """Decode a JSON encoded field, leave decoded values as they are."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _timestamp(value: Any) -> datetime | None:
    """Parse seconds or milliseconds since the epoch or a date string."""
    if isinstance(value, str) and not value.strip().lstrip("-").isdigit():
        parsed = dt_util.parse_datetime(value.strip())
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
        return parsed
    number = _int(value)
    if not number or number < 0:
        return None
    if number > 100_000_000_000:  # milliseconds
        number //= 1000
    return dt_util.utc_from_timestamp(number)


def _parse_events(value: Any) -> tuple[ParcelEvent, ...]:
    """Return the tracking history, the latest event first."""
    path = decode_json(value)
    if not isinstance(path, list):
        return ()
    events: list[ParcelEvent] = []
    for item in path:
        if not isinstance(item, Mapping):
            continue
        location = item.get("location")
        events.append(
            ParcelEvent(
                code=_str(item.get("code")),
                message=_str(item.get("msg")),
                timestamp=_timestamp(item.get("date")),
                location=(
                    _str(location.get("name"))
                    if isinstance(location, Mapping)
                    else None
                ),
            )
        )
    # The order of the history is not documented. Sort it when every event
    # has a time, otherwise keep the order of the server (latest first).
    if all(event.timestamp for event in events):
        events.sort(key=lambda event: event.timestamp, reverse=True)
    return tuple(events)


def _tracking_url(value: Any) -> str | None:
    """Return the tracking URL; the app stores own URLs base64 encoded."""
    url = _str(value)
    if url is None or url.lower().startswith(("http://", "https://")):
        return url
    try:
        decoded = base64.b64decode(url, validate=True).decode()
    except ValueError:
        return None
    return decoded if decoded.lower().startswith(("http://", "https://")) else None


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _cache_key() -> str:
    """Return the "Cache-Key" header the app sends with its GET requests.

    Its meaning is not documented: random digits around twice the current
    time in milliseconds.
    """
    digits = "0123456789"
    return (
        "".join(random.choices(digits, k=1630))
        + str(2 * int(time.time() * 1000))
        + "".join(random.choices(digits, k=832))
    )


class ParcelloApiClient:
    """Async client for the Parcello app API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        user_id: str,
        token: str,
        language: str = "de",
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._user_id = user_id
        self._token = token
        self._language = language

    @classmethod
    async def async_login(
        cls, session: aiohttp.ClientSession, email: str, password: str
    ) -> ParcelloCredentials:
        """Log in with email and password and return the token."""
        credentials = _b64(f"{email}:{password}")
        data = await _async_request(
            session,
            "POST",
            f"/login/mail/{credentials}",
            secrets=(credentials, password),
            json_body=credentials,
        )
        if not isinstance(data, Mapping):
            raise ParcelloApiError("Unexpected login response from Parcello")
        status = _int(data.get("status"))
        if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            raise ParcelloAuthError("Wrong email address or password")
        user_id, token = _str(data.get("userid")), _str(data.get("apiKey"))
        if status != HTTPStatus.OK or not user_id or not token:
            raise ParcelloApiError(f"Login failed with status {status}")
        return ParcelloCredentials(user_id=user_id, token=token)

    @property
    def user_id(self) -> str:
        """Return the ID of the Parcello account."""
        return self._user_id

    async def async_get_parcels(self) -> list[Parcel]:
        """Return all parcels as currently known by the server."""
        data = await self._get(f"/user/{self._user_id}/parcels-without-crawling")
        if data is None:
            return []
        if not isinstance(data, list):
            raise ParcelloApiError("Unexpected list of parcels from Parcello")
        parcels: list[Parcel] = []
        for item in data:
            try:
                parcels.append(Parcel.from_api(item))
            except (AttributeError, KeyError, TypeError, ValueError):
                _LOGGER.debug("Skipping an invalid parcel: %s", item)
        return parcels

    async def async_get_parcel(self, parcel_id: str) -> Parcel:
        """Track a parcel again at the carrier and return it."""
        data = await self._get(f"/user/{self._user_id}/parcel/{quote(parcel_id)}")
        try:
            return Parcel.from_api(data)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ParcelloApiError("Unexpected parcel from Parcello") from None

    async def async_identify_carrier(self, tracking_id: str) -> str | None:
        """Return the carrier Parcello guesses for a tracking ID."""
        data = await self._get(
            f"/info/identifyTracking/{quote(_b64(tracking_id), safe='')}",
            authenticated=False,
        )
        return _str(data.get("carrier")) if isinstance(data, Mapping) else None

    async def async_add_parcel(
        self, tracking_id: str, name: str | None, carrier: str | None
    ) -> str:
        """Add a parcel to the account and return its ID.

        The parcel is built like the app does; the server tracks it once it
        is requested on its own (async_get_parcel).
        """
        parcel_id = f"{self._user_id}_p{int(time.time() * 1000)}"
        parcel = {
            "id": parcel_id,
            "id_auto": 0,
            "trackingid": tracking_id,
            "name": name or tracking_id,
            "carrier": carrier or "",
            "icon": "package",
            "tracking_url": "",
            "prophesy_timestamp": 0,
            "currentState": STATE_LOADING,
            "is_foreign": False,
            "is_express": False,
            "is_outgoing": 0,
            "needsUpdate": False,
            "is_pickupable": 0,
            "is_redirect": 0,
            "verlauf": [],
            "path": [],
            "survey": None,
        }
        await self._request(
            "POST",
            f"/user/{self._user_id}/add/parcel",
            json_body={"token": self._token, "id": self._user_id, "newParcel": parcel},
        )
        return parcel_id

    async def async_delete_parcel(self, parcel_id: str) -> None:
        """Delete a parcel from the account."""
        await self._request(
            "DELETE", f"/user/{self._user_id}/parcel/{quote(parcel_id)}/delete"
        )

    async def _get(self, path: str, authenticated: bool = True) -> Any:
        params = {"token": self._token, "lang": self._language}
        return await _async_request(
            self._session,
            "GET",
            path,
            secrets=(self._token,),
            params=params if authenticated else None,
            # The app only adds the header to requests without a token in
            # the Authorization header.
            headers={"Cache-Key": _cache_key()},
        )

    async def _request(
        self, method: str, path: str, json_body: Any | None = None
    ) -> Any:
        return await _async_request(
            self._session,
            method,
            path,
            secrets=(self._token,),
            params={"token": self._token},
            headers={"Authorization": f"Bearer {self._token}"},
            json_body=json_body,
        )


async def _async_request(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    *,
    secrets: tuple[str, ...],
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
) -> Any:
    """Send a request and return the decoded JSON body (None if empty)."""
    # The login credentials are part of the path; keep it as it is, like
    # the app does.
    url = URL(f"{API_URL}{path}", encoded=True)
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with session.request(
                method, url, params=params, headers=headers, json=json_body
            ) as resp:
                status = resp.status
                body = await resp.text()
    except (TimeoutError, aiohttp.ClientError) as err:
        # aiohttp includes the request URL (with the token or credentials)
        # in some errors, so the message is redacted and the cause not
        # chained.
        raise ParcelloApiError(
            f"Error communicating with Parcello: {_redact(err, secrets)}"
        ) from None

    _LOGGER.debug("%s %s answered with HTTP %s", method, path.split("/")[1], status)
    if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        raise ParcelloAuthError("Parcello rejected the login or the token")
    if status == HTTPStatus.CONFLICT:
        raise ParcelloAlreadyTrackedError("The parcel is already tracked")
    if status >= HTTPStatus.BAD_REQUEST:
        raise ParcelloApiError(f"Unexpected response from Parcello: HTTP {status}")
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except ValueError:
        raise ParcelloApiError("Parcello did not answer with JSON") from None


def _redact(err: BaseException, secrets: tuple[str, ...]) -> str:
    """Describe an error without the token or the credentials."""
    text = str(err)
    for secret in secrets:
        if not secret:
            continue
        # aiohttp/yarl encode the query differently than urllib.
        yarl_encoded = URL.build(query={"k": secret}).raw_query_string[2:]
        for variant in {
            secret,
            quote(secret, safe=""),
            quote_plus(secret),
            yarl_encoded,
        }:
            if variant:
                text = text.replace(variant, "***")
    name = type(err).__name__
    return f"{name}: {text}" if text else name
