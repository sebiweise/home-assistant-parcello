"""Constants for the Parcello integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "parcello"

# Backend of the Parcello app and web app (www.parcello.org/app).
API_URL: Final = "https://api-v4.parcello.org/v1/app"

# Config entry data / options
CONF_USER_ID: Final = "user_id"
CONF_TOKEN: Final = "token"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_DELIVERED_DAYS: Final = "delivered_days"

# Minutes between two updates. The list of parcels comes from the Parcello
# server, which tracks the carriers on its own, so there is no need to poll
# more often.
DEFAULT_SCAN_INTERVAL: Final = 15
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 240
# Delivered parcels stay in the account until they are deleted in the app.
# Their entities are kept for this many days after the last tracking event.
DEFAULT_DELIVERED_DAYS: Final = 3
MAX_DELIVERED_DAYS: Final = 30

# Status of a parcel, the options of the parcel sensor.
STATUS_DELIVERED: Final = "delivered"
STATUS_IN_TRANSIT: Final = "in_transit"
STATUS_OUT_FOR_DELIVERY: Final = "out_for_delivery"
STATUS_WARNING: Final = "warning"
STATUS_PROBLEM: Final = "problem"
STATUS_LOADING: Final = "loading"
STATUS_MISSING_INFO: Final = "missing_info"
STATUS_NO_ADDRESS: Final = "no_address"
STATUS_NO_DATA_YET: Final = "no_data_yet"
STATUS_RETURNED: Final = "returned"
STATUSES: Final = [
    STATUS_DELIVERED,
    STATUS_IN_TRANSIT,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_WARNING,
    STATUS_PROBLEM,
    STATUS_LOADING,
    STATUS_MISSING_INFO,
    STATUS_NO_ADDRESS,
    STATUS_NO_DATA_YET,
    STATUS_RETURNED,
]
# Parcels which are still expected; all others are finished.
ACTIVE_STATUSES: Final = frozenset(
    {
        STATUS_IN_TRANSIT,
        STATUS_OUT_FOR_DELIVERY,
        STATUS_WARNING,
        STATUS_PROBLEM,
        STATUS_LOADING,
        STATUS_MISSING_INFO,
        STATUS_NO_ADDRESS,
        STATUS_NO_DATA_YET,
    }
)

# Events fired on the Home Assistant bus
EVENT_STATUS_CHANGED: Final = "parcello_status_changed"

# Services
SERVICE_ADD_PARCEL: Final = "add_parcel"
SERVICE_REMOVE_PARCEL: Final = "remove_parcel"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_TRACKING_ID: Final = "tracking_id"
ATTR_NAME: Final = "name"
ATTR_CARRIER: Final = "carrier"
ATTR_PARCEL_ID: Final = "parcel_id"
