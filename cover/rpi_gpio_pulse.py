"""
Support for building a Raspberry Pi cover in HA.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/cover.rpi_gpio/
"""
import logging
from time import sleep

import voluptuous as vol

from homeassistant.components.cover import CoverDevice, PLATFORM_SCHEMA
from homeassistant.const import CONF_NAME
import homeassistant.components.rpi_gpio as rpi_gpio
import homeassistant.helpers.config_validation as cv

positive_float = vol.All(vol.Coerce(float), vol.Range(min=0))

_LOGGER = logging.getLogger(__name__)

CONF_COVERS = 'covers'
CONF_UP_PIN = 'up_pin'
CONF_DOWN_PIN = 'down_pin'
CONF_PULSE_TIME = 'pulse_time'

DEFAULT_PULSE_TIME = 0.2
DEPENDENCIES = ['rpi_gpio']

_COVERS_SCHEMA = vol.All(
    cv.ensure_list,
    [
        vol.Schema({
            CONF_NAME: cv.string,
            CONF_UP_PIN: cv.positive_int,
            CONF_DOWN_PIN: cv.positive_int,
        })
    ]
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_COVERS): _COVERS_SCHEMA,
    vol.Optional(CONF_PULSE_TIME, default=DEFAULT_PULSE_TIME): cv.small_float,
})


# pylint: disable=unused-argument
def setup_platform(hass, config, add_devices, discovery_info=None):
    """Setup the RPi cover platform."""
    pulse_time = config.get(CONF_PULSE_TIME)
    covers = []
    covers_conf = config.get(CONF_COVERS)

    for cover in covers_conf:
        covers.append(RPiGPIOPulseCover(
            cover[CONF_NAME], cover[CONF_UP_PIN], cover[CONF_DOWN_PIN],
            pulse_time))
    add_devices(covers)


class RPiGPIOPulseCover(CoverDevice):
    """Representation of a Raspberry GPIO cover."""

    def __init__(self, name, up_pin, down_pin, pulse_time):
        """Initialize the cover."""
        self._name = name
        self._state = None
        self._up_pin = up_pin
        self._down_pin = down_pin
        self._pulse_time = pulse_time
        rpi_gpio.setup_output(self._up_pin)
        rpi_gpio.setup_output(self._down_pin)

    @property
    def unique_id(self):
        """Return the ID of this cover."""
        return '{}.{}'.format(self.__class__, self._name)

    @property
    def name(self):
        """Return the name of the cover if any."""
        return self._name

    def update(self):
        """Update the state of the cover."""
        pass # No feedback, this makes sure that both up _and_ down buttons stay enabled in frontend UI

    @property
    def is_closed(self):
        """Return true if cover is closed."""
        return self._state

    def _trigger(self, pin):
        """Trigger the cover."""
        rpi_gpio.write_output(pin, True)
        sleep(self._pulse_time)
        rpi_gpio.write_output(pin, False)

    def close_cover(self):
        """Close the cover."""
        self._trigger(self._down_pin)

    def open_cover(self):
        """Open the cover."""
        self._trigger(self._up_pin)
