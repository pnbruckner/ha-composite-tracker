"""A Device Tracker platform that combines one or more device trackers."""
from datetime import datetime, timedelta
import logging
import threading

import voluptuous as vol

from homeassistant.components.binary_sensor import DOMAIN as BS_DOMAIN
from homeassistant.components.device_tracker import (
    ATTR_BATTERY,
    ATTR_SOURCE_TYPE,
    DOMAIN as DT_DOMAIN,
    PLATFORM_SCHEMA,
)

# SourceType was new in 2022.9
try:
    from homeassistant.components.device_tracker import SourceType

    source_type_type = SourceType
    source_type_bluetooth = SourceType.BLUETOOTH
    source_type_bluetooth_le = SourceType.BLUETOOTH_LE
    source_type_gps = SourceType.GPS
    source_type_router = SourceType.ROUTER
except ImportError:
    from homeassistant.components.device_tracker import (
        SOURCE_TYPE_BLUETOOTH,
        SOURCE_TYPE_BLUETOOTH_LE,
        SOURCE_TYPE_GPS,
        SOURCE_TYPE_ROUTER,
    )

    source_type_type = str
    source_type_bluetooth = SOURCE_TYPE_BLUETOOTH
    source_type_bluetooth_le = SOURCE_TYPE_BLUETOOTH_LE
    source_type_gps = SOURCE_TYPE_GPS
    source_type_router = SOURCE_TYPE_ROUTER

from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.components.persistent_notification import (
    async_create as pn_async_create,
)
from homeassistant.components.zone import ENTITY_ID_HOME
from homeassistant.components.zone import async_active_zone
from homeassistant.const import (
    ATTR_BATTERY_CHARGING,
    ATTR_BATTERY_LEVEL,
    ATTR_ENTITY_ID,
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_ENTITY_ID,
    CONF_ID,
    CONF_NAME,
    CONF_PLATFORM,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import track_state_change
from homeassistant.util.async_ import run_callback_threadsafe
import homeassistant.util.dt as dt_util
from homeassistant.util.location import distance

from .const import (
    CONF_ALL_STATES,
    CONF_ENTITY,
    CONF_REQ_MOVEMENT,
    CONF_TIME_AS,
    CONF_TRACKERS,
    DOMAIN,
    TIME_AS_OPTS,
    TZ_DEVICE_LOCAL,
    TZ_DEVICE_UTC,
    TZ_LOCAL,
)

_LOGGER = logging.getLogger(__name__)

ATTR_CHARGING = "charging"
ATTR_LAST_SEEN = "last_seen"
ATTR_LAST_ENTITY_ID = "last_entity_id"
ATTR_TIME_ZONE = "time_zone"

INACTIVE = "inactive"
ACTIVE = "active"
WARNED = "warned"
USE_ALL_STATES = "use_all_states"
STATUS = "status"
SEEN = "seen"
SOURCE_TYPE = ATTR_SOURCE_TYPE
DATA = "data"

SOURCE_TYPE_BINARY_SENSOR = BS_DOMAIN
STATE_BINARY_SENSOR_HOME = STATE_ON

SOURCE_TYPE_NON_GPS = (
    SOURCE_TYPE_BINARY_SENSOR,
    source_type_bluetooth,
    source_type_bluetooth_le,
    source_type_router,
)


def _entities(entities):
    result = []
    for entity in entities:
        if isinstance(entity, dict):
            result.append(entity)
        else:
            result.append({CONF_ENTITY: entity, CONF_ALL_STATES: False})
    return result


ENTITIES = vol.All(
    cv.ensure_list,
    [
        vol.Any(
            {
                vol.Required(CONF_ENTITY): cv.entity_id,
                vol.Required(CONF_ALL_STATES): cv.boolean,
            },
            cv.entity_id,
            msg="Expected an entity ID",
        )
    ],
    _entities,
)
COMPOSITE_TRACKER = {
    vol.Required(CONF_NAME): cv.slugify,
    vol.Required(CONF_ENTITY_ID): ENTITIES,
    vol.Optional(CONF_TIME_AS, default=TIME_AS_OPTS[0]): vol.In(TIME_AS_OPTS),
    vol.Optional(CONF_REQ_MOVEMENT, default=False): cv.boolean,
}
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(COMPOSITE_TRACKER)


def setup_scanner(hass, config, see, discovery_info=None):
    """Set up a device scanner."""
    CompositeScanner(hass, config, see)
    _LOGGER.warning(
        '"%s: %s" under %s is deprecated. Move to "%s: %s"',
        CONF_PLATFORM,
        DOMAIN,
        DT_DOMAIN,
        DOMAIN,
        CONF_TRACKERS,
    )
    pn_async_create(
        hass,
        title="Composite configuration has changed",
        message="```text\n"
        f"{DT_DOMAIN}:\n"
        f"- platform: {DOMAIN}\n"
        "  <TRACKER CONFIG>\n\n"
        "```\n"
        "is deprecated. Move to:\n\n"
        "```text\n"
        f"{DOMAIN}:\n"
        f"  {CONF_TRACKERS}:\n"
        "  - <TRACKER_CONFIG>\n"
        "```\n\n"
        "Also remove entries from known_devices.yaml.",
    )
    return True


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the device tracker platform."""
    async_add_entities([CompositeDeviceTracker(entry)])


def nearest_second(time):
    """Round time to nearest second."""
    return time.replace(microsecond=0) + timedelta(
        seconds=0 if time.microsecond < 500000 else 1
    )


class CompositeDeviceTracker(TrackerEntity):
    """Composite Device Tracker."""

    _attr_extra_state_attributes = None
    _battery_level = None
    _source_type = None
    _location_accuracy = 0
    _location_name = None
    _latitude = None
    _longitude = None
    _scanner = None

    def __init__(self, entry):
        """Initialize Composite Device Tracker."""
        self._attr_name = entry.data[CONF_NAME]
        id = entry.data[CONF_ID]
        self._attr_unique_id = id
        self.entity_id = f"{DT_DOMAIN}.{id}"
        self._scanner_config = {CONF_NAME: entry.data[CONF_ID]}
        self._scanner_config.update(entry.options)

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._attr_unique_id

    @property
    def name(self):
        """Return the name of the entity."""
        return self._attr_name

    @property
    def device_state_attributes(self):
        """Return entity specific state attributes."""
        return self._attr_extra_state_attributes

    @property
    def extra_state_attributes(self):
        """Return entity specific state attributes."""
        return self._attr_extra_state_attributes

    @property
    def force_update(self):
        """Return True if state updates should be forced."""
        return False

    @property
    def battery_level(self):
        """Return the battery level of the device."""
        return self._battery_level

    @property
    def source_type(self):
        """Return the source type of the device."""
        return self._source_type

    @property
    def location_accuracy(self):
        """Return the location accuracy of the device."""
        return self._location_accuracy

    @property
    def location_name(self):
        """Return a location name for the current location of the device."""
        return self._location_name

    @property
    def latitude(self):
        """Return the latitude value of the device."""
        return self._latitude

    @property
    def longitude(self):
        """Rerturn the longitude value of the device."""
        return self._longitude

    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()

        def setup_scanner():
            """Set up device scanner."""
            self._scanner = CompositeScanner(self.hass, self._scanner_config, self._see)

        await self.hass.async_add_executor_job(setup_scanner)

    async def async_will_remove_from_hass(self):
        """Run when entity will be removed from hass."""

        def shutdown_scanner():
            """Shutdown device scanner."""
            self._scanner.shutdown()
            self._scanner = None

        if self._scanner:
            await self.hass.async_add_executor_job(shutdown_scanner)
        await super().async_will_remove_from_hass()

    def _see(
        self, dev_id, location_name, gps, gps_accuracy, battery, attributes, source_type
    ):
        """Process update from CompositeScanner."""
        self._battery_level = battery
        self._source_type = source_type
        self._location_accuracy = gps_accuracy or 0
        self._location_name = location_name
        if gps:
            self._latitude = gps[0]
            self._longitude = gps[1]
        else:
            self._latitude = self._longitude = None
        self._attr_extra_state_attributes = attributes
        self.hass.add_job(self.async_write_ha_state)


class CompositeScanner:
    """Composite device scanner."""

    _prev_seen = None
    _remove = None

    def __init__(self, hass, config, see):
        """Initialize CompositeScanner."""
        self._hass = hass
        self._see = see
        entities = config[CONF_ENTITY_ID]
        self._entities = {}
        entity_ids = []
        for entity in entities:
            entity_id = entity[CONF_ENTITY]
            self._entities[entity_id] = {
                USE_ALL_STATES: entity[CONF_ALL_STATES],
                STATUS: INACTIVE,
                SEEN: None,
                SOURCE_TYPE: None,
                DATA: None,
            }
            entity_ids.append(entity_id)
        self._dev_id = config[CONF_NAME]
        self._entity_id = f"{DT_DOMAIN}.{self._dev_id}"
        self._time_as = config[CONF_TIME_AS]
        if self._time_as in [TZ_DEVICE_UTC, TZ_DEVICE_LOCAL]:
            self._tf = hass.data[DOMAIN]
        self._req_movement = config[CONF_REQ_MOVEMENT]
        self._lock = threading.Lock()

        self._startup(entity_ids)

        for entity_id in entity_ids:
            self._update_info(entity_id, None, hass.states.get(entity_id))

    def _startup(self, entity_ids):
        """Start updating."""
        self._remove = track_state_change(self._hass, entity_ids, self._update_info)

    def shutdown(self):
        """Stop updating."""
        if self._remove:
            self._remove()
            self._remove = None

    def _bad_entity(self, entity_id, message):
        msg = "{} {}".format(entity_id, message)
        # Has there already been a warning for this entity?
        if self._entities[entity_id][STATUS] == WARNED:
            _LOGGER.error(msg)
            self.shutdown()
            self._entities.pop(entity_id)
            # Are there still any entities to watch?
            self._startup(self._entities.keys())
        # Only warn if this is not the first state change for the entity.
        elif self._entities[entity_id][STATUS] == ACTIVE:
            _LOGGER.warning(msg)
            self._entities[entity_id][STATUS] = WARNED
        else:
            _LOGGER.debug(msg)
            self._entities[entity_id][STATUS] = ACTIVE

    def _good_entity(self, entity_id, seen, source_type, data):
        self._entities[entity_id].update(
            {STATUS: ACTIVE, SEEN: seen, SOURCE_TYPE: source_type, DATA: data}
        )

    def _use_non_gps_data(self, entity_id, state):
        if state == STATE_HOME or self._entities[entity_id][USE_ALL_STATES]:
            return True
        entities = self._entities.values()
        if any(entity[SOURCE_TYPE] == source_type_gps for entity in entities):
            return False
        return all(
            entity[DATA] != STATE_HOME
            for entity in entities
            if entity[SOURCE_TYPE] in SOURCE_TYPE_NON_GPS
        )

    def _dt_attr_from_utc(self, utc, tzone):
        if self._time_as in [TZ_DEVICE_UTC, TZ_DEVICE_LOCAL] and tzone:
            return utc.astimezone(tzone)
        if self._time_as in [TZ_LOCAL, TZ_DEVICE_LOCAL]:
            return dt_util.as_local(utc)
        return utc

    def _update_info(self, entity_id, old_state, new_state):
        if new_state is None or new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return

        with self._lock:
            # Get time device was last seen, which is the entity's last_seen
            # attribute, or if that doesn't exist, then last_updated from the
            # new state object. Make sure last_seen is timezone aware in UTC.
            # Note that dt_util.as_utc assumes naive datetime is in local
            # timezone.
            last_seen = new_state.attributes.get(ATTR_LAST_SEEN)
            if isinstance(last_seen, datetime):
                last_seen = dt_util.as_utc(last_seen)
            else:
                try:
                    last_seen = dt_util.utc_from_timestamp(float(last_seen))
                except (TypeError, ValueError):
                    last_seen = new_state.last_updated

            old_last_seen = self._entities[entity_id][SEEN]
            if old_last_seen and last_seen < old_last_seen:
                self._bad_entity(entity_id, "last_seen went backwards")
                return

            # Try to get GPS and battery data.
            try:
                gps = (
                    new_state.attributes[ATTR_LATITUDE],
                    new_state.attributes[ATTR_LONGITUDE],
                )
            except KeyError:
                gps = None
            gps_accuracy = new_state.attributes.get(ATTR_GPS_ACCURACY)
            battery = new_state.attributes.get(
                ATTR_BATTERY, new_state.attributes.get(ATTR_BATTERY_LEVEL)
            )
            charging = new_state.attributes.get(
                ATTR_BATTERY_CHARGING, new_state.attributes.get(ATTR_CHARGING)
            )
            # Don't use location_name unless we have to.
            location_name = None

            # What type of tracker is this?
            if new_state.domain == BS_DOMAIN:
                source_type = SOURCE_TYPE_BINARY_SENSOR
            else:
                source_type = new_state.attributes.get(ATTR_SOURCE_TYPE)

            state = new_state.state

            if source_type == source_type_gps:
                # GPS coordinates and accuracy are required.
                if gps is None:
                    self._bad_entity(entity_id, "missing gps attributes")
                    return
                if gps_accuracy is None:
                    self._bad_entity(entity_id, "missing gps_accuracy attribute")
                    return

                new_data = gps, gps_accuracy
                old_data = self._entities[entity_id][DATA]
                if old_data:
                    if last_seen == old_last_seen and new_data == old_data:
                        return
                    old_gps, old_acc = old_data
                self._good_entity(entity_id, last_seen, source_type, new_data)

                if (
                    self._req_movement
                    and old_data
                    and distance(gps[0], gps[1], old_gps[0], old_gps[1])
                    <= gps_accuracy + old_acc
                ):
                    _LOGGER.debug(
                        "For {} skipping update from {}: "
                        "not enough movement".format(self._entity_id, entity_id)
                    )
                    return

            elif source_type in SOURCE_TYPE_NON_GPS:
                # Convert 'on'/'off' state of binary_sensor
                # to 'home'/'not_home'.
                if source_type == SOURCE_TYPE_BINARY_SENSOR:
                    if state == STATE_BINARY_SENSOR_HOME:
                        state = STATE_HOME
                    else:
                        state = STATE_NOT_HOME

                self._good_entity(entity_id, last_seen, source_type, state)

                if not self._use_non_gps_data(entity_id, state):
                    return

                # Don't use new GPS data if it's not complete.
                if gps is None or gps_accuracy is None:
                    gps = gps_accuracy = None
                # Get current GPS data, if any, and determine if it is in
                # 'zone.home'.
                cur_state = self._hass.states.get(self._entity_id)
                try:
                    cur_lat = cur_state.attributes[ATTR_LATITUDE]
                    cur_lon = cur_state.attributes[ATTR_LONGITUDE]
                    cur_acc = cur_state.attributes[ATTR_GPS_ACCURACY]
                    cur_gps_is_home = (
                        run_callback_threadsafe(
                            self._hass.loop,
                            async_active_zone,
                            self._hass,
                            cur_lat,
                            cur_lon,
                            cur_acc,
                        )
                        .result()
                        .entity_id
                        == ENTITY_ID_HOME
                    )
                except (AttributeError, KeyError):
                    cur_gps_is_home = False

                # It's important, for this composite tracker, to avoid the
                # component level code's "stale processing." This can be done
                # one of two ways: 1) provide GPS data w/ source_type of gps,
                # or 2) provide a location_name (that will be used as the new
                # state.)

                # If router entity's state is 'home' and current GPS data from
                # composite entity is available and is in 'zone.home',
                # use it and make source_type gps.
                if state == STATE_HOME and cur_gps_is_home:
                    gps = cur_lat, cur_lon
                    gps_accuracy = cur_acc
                    source_type = source_type_gps
                # Otherwise, if new GPS data is valid (which is unlikely if
                # new state is not 'home'),
                # use it and make source_type gps.
                elif gps:
                    source_type = source_type_gps
                # Otherwise, if new state is 'home' and old state is not 'home'
                # and no GPS data, then use HA's configured Home location and
                # make source_type gps.
                elif state == STATE_HOME and (
                    cur_state is None or cur_state.state != STATE_HOME
                ):
                    gps = (self._hass.config.latitude, self._hass.config.longitude)
                    gps_accuracy = 0
                    source_type = source_type_gps
                # Otherwise, don't use any GPS data, but set location_name to
                # new state.
                else:
                    location_name = state

            else:
                self._bad_entity(
                    entity_id, "unsupported source_type: {}".format(source_type)
                )
                return

            # Is this newer info than last update?
            if self._prev_seen and last_seen <= self._prev_seen:
                _LOGGER.debug(
                    "For {} skipping update from {}: "
                    "last_seen not newer than previous update ({} <= {})".format(
                        self._entity_id, entity_id, last_seen, self._prev_seen
                    )
                )
                return

            _LOGGER.debug("Updating %s from %s", self._entity_id, entity_id)

            tzone = None
            if self._time_as in [TZ_DEVICE_UTC, TZ_DEVICE_LOCAL]:
                tzname = None
                if gps:
                    # timezone_at will return a string or None.
                    tzname = self._tf.timezone_at(lng=gps[1], lat=gps[0])
                    # get_time_zone will return a tzinfo or None.
                    tzone = dt_util.get_time_zone(tzname)
                attrs = {ATTR_TIME_ZONE: tzname or STATE_UNKNOWN}
            else:
                attrs = {}

            attrs.update(
                {
                    ATTR_ENTITY_ID: tuple(
                        entity_id
                        for entity_id, entity in self._entities.items()
                        if entity[ATTR_SOURCE_TYPE] is not None
                    ),
                    ATTR_LAST_ENTITY_ID: entity_id,
                    ATTR_LAST_SEEN: self._dt_attr_from_utc(
                        nearest_second(last_seen), tzone
                    ),
                }
            )
            if charging is not None:
                attrs[ATTR_BATTERY_CHARGING] = charging
            self._see(
                dev_id=self._dev_id,
                location_name=location_name,
                gps=gps,
                gps_accuracy=gps_accuracy,
                battery=battery,
                attributes=attrs,
                source_type=source_type,
            )

            self._prev_seen = last_seen
