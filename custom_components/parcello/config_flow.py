"""Adds config flow for Parcello."""

from collections import OrderedDict
import homeassistant.helpers.config_validation as cv
from homeassistant.core import callback
from homeassistant import config_entries
from .const import (
    CONF_SCAN_INTERVAL,
    DOMAIN,
    DEFAULT_SCAN_INTERVAL,
    API_BASEURL,
)
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
)
import logging
import base64
import aiohttp
import json
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

ATTR_USERNAME = "username"
ATTR_PASSWORD = "password"
ATTR_SCAN_INTERVAL = "scan_interval"

async def _login(user, pwd):
    """function used to login"""

    url = f"{API_BASEURL}/login/mail/{_encode_cred(user, pwd)}"

    async with aiohttp.ClientSession() as session:
        async with session.post(url) as resp:
            if resp.status != 200:
                _LOGGER.error("Http GET error: %s", resp.status)
                return None

            data_byte = await resp.read()
            data = json.loads(data_byte.decode('utf-8'))
            _LOGGER.debug("Authentication successfully")
            return data

def _encode_cred(user, pwd):
    auth_str = f"{user}:{pwd}"
    auth_bytes = auth_str.encode("utf-8")
    auth = base64.b64encode(auth_bytes)
    
    return auth

class ParcelloFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self):
        """Initialize."""
        self._data = {}
        self._errors = {}
        
    async def async_step_user(self, user_input={}):
        """Handle a flow initialized by the user."""
        self._errors = {}

        if user_input is not None:
            self._data.update(user_input)
            resp = await _login(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            if resp is not None:
                return self.async_create_entry(
                    title=self._data[CONF_USERNAME],
                    data={
                        "auth": _encode_cred(user_input[CONF_USERNAME], user_input[CONF_PASSWORD]),
                        "apiKey": resp["apiKey"],
                        "userid": resp["userid"]
                    }
                )
            else:
                self._errors["base"] = "communication"

            return await self._show_config_form(user_input)

        return await self._show_config_form(user_input)

    async def _show_config_form(self, user_input):
        """Show the configuration form to edit location data."""

        # Defaults
        username = ""
        password = ""

        if user_input is not None:
            if ATTR_USERNAME in user_input:
                username = user_input[ATTR_USERNAME]
            if ATTR_PASSWORD in user_input:
                password = user_input[ATTR_PASSWORD]

        data_schema = OrderedDict()
        data_schema[vol.Required(ATTR_USERNAME, default=username)] = str
        data_schema[vol.Required(ATTR_PASSWORD, default=password)] = str
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(data_schema), errors=self._errors
        )
    
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ParcelloOptionsFlow(config_entry)
    
class ParcelloOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Parcello."""

    def __init__(self, config_entry):
        """Initialize."""
        self.config = config_entry
        self._data = dict(config_entry.options)
        self._errors = {}
    
    async def async_step_init(self, user_input=None):
        """Parcello options."""
        if user_input is not None:
            self._data.update(user_input)

            resp = await _login(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            if resp is not None:
                return await self.async_step_options_2()
            else:
                self._errors["base"] = "communication"

            return await self._show_options_form(user_input)

        return await self._show_options_form(user_input)
    
    async def _show_options_form(self, user_input):
        """Show the configuration form to edit location data."""

        # Defaults
        username = self.config.options.get(CONF_USERNAME)
        password = self.config.options.get(CONF_PASSWORD)

        if user_input is not None:
            if ATTR_USERNAME in user_input:
                username = user_input[ATTR_USERNAME]
            if ATTR_PASSWORD in user_input:
                password = user_input[ATTR_PASSWORD]

        data_schema = OrderedDict()
        data_schema[vol.Required(ATTR_USERNAME, default=username)] = str
        data_schema[vol.Required(ATTR_PASSWORD, default=password)] = str
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(data_schema), errors=self._errors
        )