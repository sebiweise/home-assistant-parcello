"""Config flow for the Parcello integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ParcelloApiClient,
    ParcelloApiError,
    ParcelloAuthError,
    ParcelloCredentials,
)
from .const import (
    CONF_DELIVERED_DAYS,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USER_ID,
    DEFAULT_DELIVERED_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_DELIVERED_DAYS,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

EMAIL_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(
        type=selector.TextSelectorType.EMAIL, autocomplete="username"
    )
)
PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(
        type=selector.TextSelectorType.PASSWORD, autocomplete="current-password"
    )
)
SCAN_INTERVAL_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=MIN_SCAN_INTERVAL,
        max=MAX_SCAN_INTERVAL,
        step=1,
        unit_of_measurement="min",
        mode=selector.NumberSelectorMode.BOX,
    )
)
DELIVERED_DAYS_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=MAX_DELIVERED_DAYS,
        step=1,
        unit_of_measurement="d",
        mode=selector.NumberSelectorMode.BOX,
    )
)


class ParcelloConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Parcello."""

    VERSION = 1

    async def _async_login(
        self, email: str, password: str, errors: dict[str, str]
    ) -> ParcelloCredentials | None:
        """Log in, filling errors on failure."""
        try:
            return await ParcelloApiClient.async_login(
                async_get_clientsession(self.hass), email, password
            )
        except ParcelloAuthError:
            errors["base"] = "invalid_auth"
        except ParcelloApiError as err:
            _LOGGER.warning("Login at Parcello failed: %s", err)
            errors["base"] = "cannot_connect"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            credentials = await self._async_login(
                email, user_input[CONF_PASSWORD], errors
            )
            if credentials is not None:
                await self.async_set_unique_id(credentials.user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data={
                        CONF_EMAIL: email,
                        CONF_USER_ID: credentials.user_id,
                        CONF_TOKEN: credentials.token,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_EMAIL): EMAIL_SELECTOR,
                        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                    }
                ),
                {CONF_EMAIL: (user_input or {}).get(CONF_EMAIL)},
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a rejected token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in again with the password."""
        entry = self._get_reauth_entry()
        email = entry.data[CONF_EMAIL]
        errors: dict[str, str] = {}
        if user_input is not None:
            credentials = await self._async_login(
                email, user_input[CONF_PASSWORD], errors
            )
            if credentials is not None:
                await self.async_set_unique_id(credentials.user_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: credentials.token}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"email": email},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return ParcelloOptionsFlow()


class ParcelloOptionsFlow(OptionsFlow):
    """Handle Parcello options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_DELIVERED_DAYS: int(user_input[CONF_DELIVERED_DAYS]),
                }
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): SCAN_INTERVAL_SELECTOR,
                    vol.Required(
                        CONF_DELIVERED_DAYS,
                        default=options.get(
                            CONF_DELIVERED_DAYS, DEFAULT_DELIVERED_DAYS
                        ),
                    ): DELIVERED_DAYS_SELECTOR,
                }
            ),
        )
