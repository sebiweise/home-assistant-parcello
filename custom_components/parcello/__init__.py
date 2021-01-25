"""Parcello Integration"""

from .const import DOMAIN, VERSION, ISSUE_URL, PLATFORM
import logging

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass, config_entry):
    """ Disallow configuration via YAML """

    return True