"""
Support for Yamaha Receivers with the YNCA protocol

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/media_player.yamaha_ynca/
"""
import logging

import voluptuous as vol

from homeassistant.components.media_player import (
    SUPPORT_TURN_OFF, SUPPORT_TURN_ON, SUPPORT_VOLUME_MUTE, SUPPORT_VOLUME_SET,
    SUPPORT_SELECT_SOURCE, SUPPORT_PLAY_MEDIA, SUPPORT_PAUSE, SUPPORT_STOP,
    SUPPORT_NEXT_TRACK, SUPPORT_PREVIOUS_TRACK, SUPPORT_PLAY,
    MEDIA_TYPE_MUSIC,
    MediaPlayerDevice, PLATFORM_SCHEMA)
from homeassistant.const import (CONF_NAME, CONF_PORT,  STATE_OFF, STATE_ON,
                                 STATE_PLAYING, STATE_IDLE)
import homeassistant.helpers.config_validation as cv

# REQUIREMENTS = ['rxv==0.4.0']


import queue
import re
import sys
import threading
import time

import serial
import serial.threaded


class YncaProtocol(serial.threaded.LineReader):
    # YNCA spec defines a minimum timeinterval of 100 milliseconds between sending commands
    COMMAND_INTERVAL = 0.1

    # YNCA spec says standby timeout is 40 seconds, so use 30 seconds to be on the safe side
    KEEP_ALIVE_INTERVAL = 30

    def __init__(self):
        super(YncaProtocol, self).__init__()
        self._callback = None
        self._send_queue = None
        self._send_thread = None

    def connection_made(self, transport):
        super(YncaProtocol, self).connection_made(transport)
        sys.stdout.write('port opened\n')

        self._send_queue = queue.Queue()
        self._send_thread = threading.Thread(target=self._send_handler)
        self._send_thread.start()

        # When the device is in low power mode the first command is to wake up and gets lost
        # So send a dummy keep-alive on connect
        self._send_keepalive()

    def connection_lost(self, exc):
        # There seems to be no way to clear the queue so just read all and add the _EXIT command
        try:
            while self._send_queue.get(False):
                pass
        except queue.Empty:
            self._send_queue.put("_EXIT")

        if exc:
            sys.stdout.write(repr(exc))
        sys.stdout.write('port closed\n')

    def handle_line(self, line):
        # sys.stdout.write(repr(line))
        # Match lines formatted like @SUBUNIT:FUNCTION=PARAMETER
        match = re.match(r"@(?P<subunit>.*?):(?P<function>.*?)=(?P<value>.*)", line)
        if match is not None:
            if self._callback is not None:
                self._callback(match.group("subunit"), match.group("function"), match.group("value"))
        elif line == "@UNDEFINED":
            raise Exception("Undefined command: {}".format(line))
        elif line == "@RESTRICTED":
            raise Exception("Restricted command: {}".format(line))

    def _send_keepalive(self):
        self.get('SYS', 'MODELNAME')  # This message is suggested by YNCA spec for keep-alive

    def _send_handler(self):
        stop = False
        while not stop:
            try:
                message = self._send_queue.get(True, self.KEEP_ALIVE_INTERVAL)

                if message == "_EXIT":
                    stop = True
                else:
                    self.write_line(message)
                    time.sleep(self.COMMAND_INTERVAL)  # Maintain required commandspacing
            except queue.Empty:
                # To avoid random message being eaten because device goes to sleep, keep it alive
                self._send_keepalive()

    def put(self, subunit, funcname, parameter):
        self._send_queue.put(
            '@{subunit}:{funcname}={parameter}'.format(subunit=subunit, funcname=funcname, parameter=parameter))

    def get(self, subunit, funcname):
        self.put(subunit, funcname, '?')


class Ynca:
    def __init__(self, port=None, callback=None):
        self._port = port
        self.callback = callback
        self._serial = None
        self._readerthread = None
        self._protocol = None

    def connect(self):
        self._serial = serial.Serial(self._port, 9600)
        self._readerthread = serial.threaded.ReaderThread(self._serial, YncaProtocol)
        self._readerthread.start()
        dummy, self._protocol = self._readerthread.connect()
        self._protocol._callback = self.callback

    def disconnect(self):
        self._readerthread.close()

    def put(self, subunit, funcname, parameter):
        self._protocol.put(subunit, funcname, parameter)

    def get(self, subunit, funcname):
        self._protocol.get(subunit, funcname)


# ---------------------


_LOGGER = logging.getLogger(__name__)

SUPPORT_YAMAHA = SUPPORT_VOLUME_SET | SUPPORT_VOLUME_MUTE | \
    SUPPORT_TURN_ON | SUPPORT_TURN_OFF | SUPPORT_SELECT_SOURCE

CONF_SOURCE_NAMES = 'source_names'
CONF_SOURCE_IGNORE = 'source_ignore'
CONF_ZONE_IGNORE = 'zone_ignore'

DEFAULT_NAME = 'Yamaha Receiver (YNCA)'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_PORT): cv.string,
    vol.Optional(CONF_SOURCE_IGNORE, default=[]):
        vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_ZONE_IGNORE, default=[]):
        vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_SOURCE_NAMES, default={}): {cv.string: cv.string},
})


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Setup the Yamaha YNCA platform."""
    #import ynca

    name = config.get(CONF_NAME)
    port = config.get(CONF_PORT)
    source_ignore = config.get(CONF_SOURCE_IGNORE)
    source_names = config.get(CONF_SOURCE_NAMES)
    # zone_ignore = config.get(CONF_ZONE_IGNORE)

    #receivers = [ynca.Ynca(port)]
    receivers = [Ynca(port)]

    for receiver in receivers:
        # if receiver.zone not in zone_ignore:
        add_devices([YamahaYncaDevice(name, receiver, source_ignore, source_names)])


class YamahaYncaDevice(MediaPlayerDevice):
    """Representation of a Yamaha device."""

    def __init__(self, name, receiver, source_ignore, source_names):
        """Initialize the Yamaha Receiver."""
        self._receiver = receiver
        self._muted = False
        self._volume = -50.0  # Some low default
        self._max_volume = -20.0  # Lets keep max a bit low until all works TODO read from amplifier
        self._power_state = STATE_OFF
        self._current_source = None
        self._source_list = {}
        self._source_ignore = source_ignore or []
        self._source_names = source_names or {}
        self._reverse_mapping = None
        self._name = name
        self._zone = "MAIN"

        self._receiver.callback = self.handle_ynca
        self._receiver.connect()
        self.retrieve_initial_receiver_values()

    def retrieve_initial_receiver_values(self):
        #self._receiver.get(self._zone, "PWR")
        #self._receiver.get(self._zone, "MUTE")
        #self._receiver.get(self._zone, "VOL")
        #self._receiver.get(self._zone, "INP")
        self._receiver.get(self._zone, "BASIC")  # Gets PWR, SLEEP, VOL, MUTE, INP, STRAIGHT, ENHANCER and SOUDPROG (for main zone)
        self._receiver.get("SYS", "INPNAME")

    def handle_ynca(self, subunit, function, value):
        _LOGGER.info("Subunit:{0}, Function:{1}, Value:{2}".format(subunit, function, value))
        update_hass = True

        # ignore subunit for now

        # Handle functions
        if function == "PWR":
            if value == "On":
                self._power_state = STATE_ON
            else:
                self._power_state = STATE_OFF
        elif function == "INP":
            self._current_source = value
        elif function == "MUTE":
            if value == "Off":
                self._muted = False
            else:
                self._muted = True
        elif function == "VOL":
            self._volume = float(value)
        elif function.startswith("INPNAME"):
            input = function[7:]
            self._source_list[input] = value
        else:
            update_hass = False

        if update_hass:
            #self.update_ha_state()
            self.schedule_update_ha_state()

    @staticmethod
    def scale(input_value, input_range, output_range):
        input_min = input_range[0]
        input_max = input_range[1]
        input_spread = input_max - input_min

        output_min = output_range[0]
        output_max = output_range[1]
        output_spread = output_max - output_min

        value_scaled = float(input_value - input_min) / float(input_spread)

        return output_min + (value_scaled * output_spread)

    @property
    def should_poll(self):
        """No polling needed."""
        return True

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def state(self):
        """Return the state of the device."""
        return self._power_state

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        return self.scale(self._volume, [-80.5, self._max_volume], [0, 1])

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        return self._muted

    @property
    def source(self):
        """Return the current input source."""
        return self._current_source

    @property
    def source_list(self):
        """List of available input sources."""
        return sorted(self._source_list.keys())

    @property
    def supported_media_commands(self):
        """Flag of media commands that are supported."""
        supported_commands = SUPPORT_YAMAHA
        return supported_commands

    def turn_off(self):
        """Turn off media player."""
        self._receiver.put("SYS", "PWR", "Standby")

    def set_volume_level(self, volume):
        """Set volume level, range 0..1."""
        receiver_vol = self.scale(volume, [0, 1], [-80.5, self._max_volume])
        self._receiver.put(self._zone, "VOL", "{0:.1f}".format(round(receiver_vol / 0.5) * 0.5)) # Rounding to make sure to get increments of .5?

    def mute_volume(self, mute):
        """Mute (true) or unmute (false) media player."""
        if mute:
            self._receiver.put(self._zone, "MUTE", "On")
        else:
            self._receiver.put(self._zone, "MUTE", "Off")

    def turn_on(self):
        """Turn the media player on."""
        self._receiver.put("SYS", "PWR", "On")

    def select_source(self, source):
        """Select input source."""
        self._receiver.put(self._zone, "INP", source)

    def media_play(self):
        pass

    def media_previous_track(self):
        pass

    def clear_playlist(self):
        pass

    def media_next_track(self):
        pass

    def play_media(self, media_type, media_id, **kwargs):
        pass

    def media_seek(self, position):
        pass

    def media_pause(self):
        pass

    def media_stop(self):
        pass

