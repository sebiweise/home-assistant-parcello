"""Platform for sensor integration."""

from . import const
import logging
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_RESOURCES
)
import aiohttp
from homeassistant.helpers.entity import Entity

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):

    config = {
        CONF_USERNAME: entry.data[CONF_USERNAME],
        CONF_PASSWORD: entry.data[CONF_PASSWORD],
        const.API_BASEURL: entry.data[const.API_BASEURL],
        const.API_KEY: entry.data[const.API_KEY],
        const.CONF_SCAN_INTERVAL: entry.data[const.CONF_SCAN_INTERVAL],
        CONF_RESOURCES: entry.data[CONF_RESOURCES],
    }

    unique_id = entry.entry_id

    sensors = []

    if CONF_RESOURCES in entry.options:
        resources = entry.options[CONF_RESOURCES]
    else:
        resources = entry.data[CONF_RESOURCES]

    data = Data(hass, config)

    for variable in resources:
        sensors.append(PackagesSensor(data, variable, unique_id))

    async_add_entities(sensors, True)

class Data:
    """The class for handling the data retrieval."""

    def __init__(self, hass, config):
        """Initialize the data object."""
        self._hass = hass
        self._user = config.get(CONF_USERNAME)
        self._pwd = config.get(CONF_PASSWORD)
        self._scan_interval = timedelta(minutes=config.get(const.CONF_SCAN_INTERVAL))
        self._resources = config.get(CONF_RESOURCES)
        self._data = None

        _LOGGER.debug("Config scan interval: %s", self._scan_interval)

        self.update = Throttle(self._scan_interval)(self.update)

class PackagesSensor(Entity):
    """ Represntation of a sensor """

    def __init__(self, data, sensor_type, unique_id):
        """ Initialize the sensor """
        self._name = const.SENSOR_TYPES[sensor_type][const.SENSOR_NAME]
        self._icon = const.SENSOR_TYPES[sensor_type][const.SENSOR_ICON]
        self._unit_of_measurement = const.SENSOR_TYPES[sensor_type][const.SENSOR_UNIT]
        self.type = sensor_type
        self.data = data
        self._state = None
        self._unique_id = unique_id
        self.update()

    @property
    def unique_id(self):
        """Return a unique, Home Assistant friendly identifier for this entity."""
        return f"{self._name}_{self._unique_id}"

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        return self._unit_of_measurement

    @property
    def icon(self):
        """Return the unit of measurement."""
        return self._icon

    def update(self):
        """Fetch new state data for the sensor.
        This is the only method that should fetch new data for Home Assistant.
        """

        self.data.update()
        # Using a dict to send the data back
        self._state = self.data._data[self.type]

async def get_data(url):
    """ Get data from url """

    url = [API_BASEURL] + url

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                _LOGGER.error("Http GET error: %s", resp.status)
                return

            data = await resp.read()
            _LOGGER.debug("%s", data)
            return data