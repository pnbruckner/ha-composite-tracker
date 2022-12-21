"""A Device Tracker platform that combines one or more device trackers."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, MutableMapping
from contextlib import suppress
from datetime import datetime, timedelta, tzinfo
from functools import partial
import logging
import threading
from typing import Any, cast

import voluptuous as vol

from homeassistant.components.binary_sensor import DOMAIN as BS_DOMAIN
from homeassistant.components.device_tracker import (
    ATTR_BATTERY,
    ATTR_SOURCE_TYPE,
    DOMAIN as DT_DOMAIN,
    PLATFORM_SCHEMA as DT_PLATFORM_SCHEMA,
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

    source_type_type = str  # type: ignore[assignment, misc]
    source_type_bluetooth = SOURCE_TYPE_BLUETOOTH  # type: ignore[assignment]
    source_type_bluetooth_le = SOURCE_TYPE_BLUETOOTH_LE  # type: ignore[assignment]
    source_type_gps = SOURCE_TYPE_GPS  # type: ignore[assignment]
    source_type_router = SOURCE_TYPE_ROUTER  # type: ignore[assignment]

from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.components.persistent_notification import (
    async_create as pn_async_create,
)
from homeassistant.components.zone import ENTITY_ID_HOME
from homeassistant.components.zone import async_active_zone
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_BATTERY_CHARGING,
    ATTR_BATTERY_LEVEL,
    ATTR_ENTITY_ID,
    ATTR_ENTITY_PICTURE,
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
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import track_state_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import GPSType, UNDEFINED, UndefinedType
from homeassistant.util.async_ import run_callback_threadsafe
import homeassistant.util.dt as dt_util
from homeassistant.util.location import distance

from .const import (
    CONF_ALL_STATES,
    CONF_ENTITY,
    CONF_REQ_MOVEMENT,
    CONF_TIME_AS,
    CONF_TRACKERS,
    CONF_USE_PICTURE,
    DATA_LEGACY_WARNED,
    DATA_TF,
    DEF_TIME_AS,
    DEF_REQ_MOVEMENT,
    DOMAIN,
    MIN_SPEED_SECONDS,
    SIG_COMPOSITE_SPEED,
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

RESTORE_EXTRA_ATTRS = (
    ATTR_TIME_ZONE,
    ATTR_ENTITY_ID,
    ATTR_LAST_ENTITY_ID,
    ATTR_LAST_SEEN,
    ATTR_BATTERY_CHARGING,
)

INACTIVE = "inactive"
ACTIVE = "active"
WARNED = "warned"
USE_ALL_STATES = "use_all_states"
USE_PICTURE = "use_picture"
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


def _entities(entities: list[str | dict]) -> list[dict]:
    """Convert entity ID to dict of entity & all_states."""
    result: list[dict] = []
    already_using_picture = False
    for idx, entity in enumerate(entities):
        if isinstance(entity, dict):
            if entity[CONF_USE_PICTURE]:
                if already_using_picture:
                    raise vol.Invalid(
                        f"{CONF_USE_PICTURE} may only be true for one entity per "
                        "composite tracker",
                        path=[idx, CONF_USE_PICTURE],
                    )
                else:
                    already_using_picture = True
            result.append(entity)
        else:
            result.append(
                {CONF_ENTITY: entity, CONF_ALL_STATES: False, CONF_USE_PICTURE: False}
            )
    return result


ENTITIES = vol.All(
    cv.ensure_list,
    [
        vol.Any(
            cv.entity_id,
            vol.Schema(
                {
                    vol.Required(CONF_ENTITY): cv.entity_id,
                    vol.Optional(CONF_ALL_STATES, default=False): cv.boolean,
                    vol.Optional(CONF_USE_PICTURE, default=False): cv.boolean,
                }
            ),
        )
    ],
    vol.Length(1),
    _entities,
)
COMPOSITE_TRACKER = {
    vol.Required(CONF_NAME): cv.slugify,
    vol.Required(CONF_ENTITY_ID): ENTITIES,
    vol.Optional(CONF_TIME_AS): vol.In(TIME_AS_OPTS),
    vol.Optional(CONF_REQ_MOVEMENT): cv.boolean,
}
PLATFORM_SCHEMA = DT_PLATFORM_SCHEMA.extend(COMPOSITE_TRACKER)


def setup_scanner(
    hass: HomeAssistant,
    config: dict,
    see: Callable[..., None],
    discovery_info: dict[str, Any] | None = None,
) -> bool:
    """Set up a device scanner."""
    CompositeScanner(hass, config, see)
    if not hass.data[DOMAIN][DATA_LEGACY_WARNED]:
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
        hass.data[DOMAIN][DATA_LEGACY_WARNED] = True
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the device tracker platform."""
    async_add_entities([CompositeDeviceTracker(entry)])


def nearest_second(time: datetime) -> datetime:
    """Round time to nearest second."""
    return time.replace(microsecond=0) + timedelta(
        seconds=0 if time.microsecond < 500000 else 1
    )


def _config_from_entry(entry: ConfigEntry) -> dict | None:
    """Get CompositeScanner config from config entry."""
    if not entry.options:
        return None
    scanner_config = {CONF_NAME: entry.data[CONF_ID]}
    scanner_config.update(entry.options)
    return scanner_config


class CompositeDeviceTracker(TrackerEntity, RestoreEntity):
    """Composite Device Tracker."""

    _attr_extra_state_attributes: MutableMapping[
        str, Any
    ] | None = None  # type: ignore[assignment]
    _battery_level: int | None = None
    _source_type: str | None = None
    _location_accuracy = 0
    _location_name: str | None = None
    _latitude: float | None = None
    _longitude: float | None = None
    _scanner: CompositeScanner | None = None
    _see_called = False

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize Composite Device Tracker."""
        self._attr_name: str = entry.data[CONF_NAME]
        id: str = entry.data[CONF_ID]
        self._attr_unique_id = id
        self.entity_id = f"{DT_DOMAIN}.{id}"
        self._scanner_config: dict | None = _config_from_entry(entry)
        self._lock = asyncio.Lock()

        self.async_on_remove(
            entry.add_update_listener(self._async_config_entry_updated)
        )

    @property
    def force_update(self) -> bool:
        """Return True if state updates should be forced."""
        return False

    @property
    def battery_level(self) -> int | None:
        """Return the battery level of the device."""
        return self._battery_level

    @property
    def source_type(self) -> str | None:  # type: ignore[override]
        """Return the source type of the device."""
        return self._source_type

    @property
    def location_accuracy(self) -> int:
        """Return the location accuracy of the device."""
        return self._location_accuracy

    @property
    def location_name(self) -> str | None:
        """Return a location name for the current location of the device."""
        return self._location_name

    @property
    def latitude(self) -> float | None:
        """Return the latitude value of the device."""
        return self._latitude

    @property
    def longitude(self) -> float | None:
        """Rerturn the longitude value of the device."""
        return self._longitude

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        async with self._lock:
            await self._setup_scanner()

            if self._see_called and self._attr_entity_picture:
                return
            state = await self.async_get_last_state()
            if not state:
                return

            if not self._attr_entity_picture:
                self._attr_entity_picture = state.attributes.get(ATTR_ENTITY_PICTURE)

            if self._see_called:
                return
            self._battery_level = state.attributes.get(ATTR_BATTERY_LEVEL)
            self._source_type = state.attributes[ATTR_SOURCE_TYPE]
            self._location_accuracy = state.attributes.get(ATTR_GPS_ACCURACY) or 0
            self._latitude = state.attributes.get(ATTR_LATITUDE)
            self._longitude = state.attributes.get(ATTR_LONGITUDE)
            self._attr_extra_state_attributes = {
                k: v for k, v in state.attributes.items() if k in RESTORE_EXTRA_ATTRS
            }
            with suppress(KeyError):
                self._attr_extra_state_attributes[
                    ATTR_LAST_SEEN
                ] = dt_util.parse_datetime(
                    self._attr_extra_state_attributes[ATTR_LAST_SEEN]
                )

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        async with self._lock:
            await self._shutdown_scanner()
        await super().async_will_remove_from_hass()

    async def _setup_scanner(self) -> None:
        """Set up device scanner."""
        if not self._scanner_config or self._scanner:
            return

        def setup_scanner() -> None:
            """Set up device scanner."""
            self._scanner = CompositeScanner(
                self.hass, cast(dict, self._scanner_config), self._see
            )

        await self.hass.async_add_executor_job(setup_scanner)

    async def _shutdown_scanner(self) -> None:
        """Shutdown device scanner."""
        if not self._scanner:
            return

        def shutdown_scanner() -> None:
            """Shutdown device scanner."""
            cast(CompositeScanner, self._scanner).shutdown()
            self._scanner = None

        await self.hass.async_add_executor_job(shutdown_scanner)

    async def _async_config_entry_updated(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """Run when the config entry has been updated."""
        new_scanner_config = _config_from_entry(entry)
        if new_scanner_config == self._scanner_config:
            return
        async with self._lock:
            await self._shutdown_scanner()
            self._scanner_config = new_scanner_config
            await self._setup_scanner()

    def _see(
        self,
        *,
        dev_id: str | None = None,
        location_name: str | None = None,
        gps: GPSType | None = None,
        gps_accuracy: int | None = None,
        battery: int | None = None,
        attributes: dict | None = None,
        source_type: str | None = source_type_gps,
        picture: str | None | UndefinedType = UNDEFINED,
    ) -> None:
        """Process update from CompositeScanner."""
        self.hass.add_job(
            partial(
                self._async_see,
                dev_id=dev_id,
                location_name=location_name,
                gps=gps,
                gps_accuracy=gps_accuracy,
                battery=battery,
                attributes=attributes,
                source_type=source_type,
                picture=picture,
            )
        )

    @callback
    def _async_see(
        self,
        *,
        dev_id: str | None = None,
        location_name: str | None = None,
        gps: GPSType | None = None,
        gps_accuracy: int | None = None,
        battery: int | None = None,
        attributes: dict | None = None,
        source_type: str | None = source_type_gps,
        picture: str | None | UndefinedType = UNDEFINED,
    ) -> None:
        """Process update from CompositeScanner."""
        # Save previously "seen" values before updating for speed calculations below.
        if self._see_called:
            prev_seen = (self._attr_extra_state_attributes or {}).get(ATTR_LAST_SEEN)
            prev_lat = self.latitude
            prev_lon = self.longitude
        else:
            # Don't use restored attributes.
            prev_seen = prev_lat = prev_lon = None
            self._see_called = True

        self._battery_level = battery
        self._source_type = source_type
        self._location_accuracy = gps_accuracy or 0
        self._location_name = location_name
        if gps:
            lat, lon = gps
        else:
            lat = lon = None
        self._latitude = lat
        self._longitude = lon

        self._attr_extra_state_attributes = attributes
        if picture is not UNDEFINED:
            self._attr_entity_picture = picture

        self.async_write_ha_state()

        speed = None
        if prev_seen and prev_lat and prev_lon and gps:
            last_seen = cast(datetime, attributes[ATTR_LAST_SEEN])
            seconds = (last_seen - cast(datetime, prev_seen)).total_seconds()
            if seconds < MIN_SPEED_SECONDS:
                _LOGGER.debug(
                    "%s: Not sending speed (time delta %0.1f < %0.1f", seconds, MIN_SPEED_SECONDS
                )
                return
            meters = cast(float, distance(prev_lat, prev_lon, lat, lon))
            try:
                speed = round(meters / seconds, 1)
            except TypeError:
                _LOGGER.error("%s: distance() returned None", self.name)
        _LOGGER.debug("%s: Sending speed: %s m/s", self.name, speed)
        async_dispatcher_send(self.hass, f"{SIG_COMPOSITE_SPEED}-{self.unique_id}", speed)


class CompositeScanner:
    """Composite device scanner."""

    _prev_seen: datetime | None = None
    _remove: CALLBACK_TYPE | None = None

    def __init__(
        self, hass: HomeAssistant, config: dict, see: Callable[..., None]
    ) -> None:
        """Initialize CompositeScanner."""
        self._hass = hass
        self._see = see
        entities: list[dict[str, Any]] = config[CONF_ENTITY_ID]
        self._entities: dict[str, dict[str, Any]] = {}
        entity_ids: list[str] = []
        for entity in entities:
            entity_id: str = entity[CONF_ENTITY]
            self._entities[entity_id] = {
                USE_ALL_STATES: entity[CONF_ALL_STATES],
                USE_PICTURE: entity[CONF_USE_PICTURE],
                STATUS: INACTIVE,
                SEEN: None,
                SOURCE_TYPE: None,
                DATA: None,
            }
            entity_ids.append(entity_id)
        self._dev_id: str = config[CONF_NAME]
        self._entity_id = f"{DT_DOMAIN}.{self._dev_id}"
        self._time_as: str = config.get(CONF_TIME_AS, DEF_TIME_AS)
        if self._time_as in [TZ_DEVICE_UTC, TZ_DEVICE_LOCAL]:
            self._tf = hass.data[DOMAIN][DATA_TF]
        self._req_movement: bool = config.get(CONF_REQ_MOVEMENT, DEF_REQ_MOVEMENT)
        self._lock = threading.Lock()

        self._startup(entity_ids)

        for entity_id in entity_ids:
            self._update_info(entity_id, None, hass.states.get(entity_id))

    def _startup(self, entity_ids: str | Iterable[str]) -> None:
        """Start updating."""
        self._remove = track_state_change(self._hass, entity_ids, self._update_info)

    def shutdown(self) -> None:
        """Stop updating."""
        if self._remove:
            self._remove()
            self._remove = None
            # In case an update started just prior to call to self._remove above, wait
            # for it to complete so that our caller will know, when we return, this
            # CompositeScanner instance is completely stopped.
            with self._lock:
                pass

    def _bad_entity(self, entity_id: str, message: str) -> None:
        """Mark entity ID as bad."""
        msg = f"{entity_id} {message}"
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

    def _good_entity(
        self, entity_id: str, seen: datetime, source_type: str, data: Any
    ) -> None:
        """Mark entity ID as good."""
        self._entities[entity_id].update(
            {STATUS: ACTIVE, SEEN: seen, SOURCE_TYPE: source_type, DATA: data}
        )

    def _use_non_gps_data(self, entity_id: str, state: str) -> bool:
        """Determine if state should be used for non-GPS based entity."""
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

    def _dt_attr_from_utc(self, utc: datetime, tzone: tzinfo | None) -> datetime:
        """Determine state attribute value from datetime & timezone."""
        if self._time_as in [TZ_DEVICE_UTC, TZ_DEVICE_LOCAL] and tzone:
            return utc.astimezone(tzone)
        if self._time_as in [TZ_LOCAL, TZ_DEVICE_LOCAL]:
            return dt_util.as_local(utc)
        return utc

    def _update_info(
        self, entity_id: str, old_state: State | None, new_state: State | None
    ) -> None:
        """Update composite tracker from input entity state change."""
        if new_state is None or new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return

        with self._lock:
            # Get time device was last seen, which is the entity's last_seen
            # attribute, or if that doesn't exist, then last_updated from the
            # new state object. Make sure last_seen is timezone aware in UTC.
            # Note that dt_util.as_utc assumes naive datetime is in local
            # timezone.
            last_seen: datetime | str | None = new_state.attributes.get(ATTR_LAST_SEEN)
            if isinstance(last_seen, datetime):
                last_seen = dt_util.as_utc(last_seen)
            else:
                try:
                    last_seen = dt_util.utc_from_timestamp(
                        float(last_seen)  # type: ignore[arg-type]
                    )
                except (TypeError, ValueError):
                    last_seen = new_state.last_updated

            old_last_seen: datetime | None = self._entities[entity_id][SEEN]
            if old_last_seen and last_seen < old_last_seen:
                self._bad_entity(entity_id, "last_seen went backwards")
                return

            # Try to get GPS and battery data.
            try:
                gps: GPSType | None = cast(
                    GPSType,
                    (
                        new_state.attributes[ATTR_LATITUDE],
                        new_state.attributes[ATTR_LONGITUDE],
                    ),
                )
            except KeyError:
                gps = None
            gps_accuracy: int | None = new_state.attributes.get(ATTR_GPS_ACCURACY)
            battery: int | None = new_state.attributes.get(
                ATTR_BATTERY, new_state.attributes.get(ATTR_BATTERY_LEVEL)
            )
            charging: bool | None = new_state.attributes.get(
                ATTR_BATTERY_CHARGING, new_state.attributes.get(ATTR_CHARGING)
            )
            # Don't use location_name unless we have to.
            location_name: str | None = None

            # What type of tracker is this?
            if new_state.domain == BS_DOMAIN:
                source_type: str | None = SOURCE_TYPE_BINARY_SENSOR
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
                old_data: tuple[GPSType, int] | None = self._entities[entity_id][DATA]
                if old_data:
                    if last_seen == old_last_seen and new_data == old_data:
                        return
                    old_gps, old_acc = old_data
                self._good_entity(entity_id, last_seen, source_type, new_data)

                if (
                    self._req_movement
                    and old_data
                    and cast(float, distance(gps[0], gps[1], old_gps[0], old_gps[1]))
                    <= gps_accuracy + old_acc
                ):
                    _LOGGER.debug(
                        "For %s skipping update from %s: not enough movement",
                        self._entity_id,
                        entity_id,
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
                    cur_lat: float = cast(State, cur_state).attributes[ATTR_LATITUDE]
                    cur_lon: float = cast(State, cur_state).attributes[ATTR_LONGITUDE]
                    cur_acc: int = cast(State, cur_state).attributes[ATTR_GPS_ACCURACY]
                    cur_gps_is_home = (
                        cast(
                            State,
                            run_callback_threadsafe(
                                self._hass.loop,
                                async_active_zone,
                                self._hass,
                                cur_lat,
                                cur_lon,
                                cur_acc,
                            ).result(),
                        ).entity_id
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
                    gps = self._hass.config.latitude, self._hass.config.longitude
                    gps_accuracy = 0
                    source_type = source_type_gps
                # Otherwise, don't use any GPS data, but set location_name to
                # new state.
                else:
                    location_name = state

            else:
                self._bad_entity(entity_id, f"unsupported source_type: {source_type}")
                return

            # Is this newer info than last update?
            if self._prev_seen and last_seen <= self._prev_seen:
                _LOGGER.debug(
                    "For %s skipping update from %s: "
                    "last_seen not newer than previous update (%s) <= (%s)",
                    self._entity_id,
                    entity_id,
                    last_seen,
                    self._prev_seen,
                )
                return

            _LOGGER.debug("Updating %s from %s", self._entity_id, entity_id)

            tzone: tzinfo | None = None
            if self._time_as in [TZ_DEVICE_UTC, TZ_DEVICE_LOCAL]:
                tzname: str | None = None
                if gps:
                    # timezone_at will return a string or None.
                    tzname = self._tf.timezone_at(lng=gps[1], lat=gps[0])
                    # get_time_zone will return a tzinfo or None.
                    tzone = dt_util.get_time_zone(tzname) if tzname else None
                attrs: dict[str, Any] = {ATTR_TIME_ZONE: tzname or STATE_UNKNOWN}
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

            kwargs = {
                "dev_id": self._dev_id,
                "location_name": location_name,
                "gps": gps,
                "gps_accuracy": gps_accuracy,
                "battery": battery,
                "attributes": attrs,
                "source_type": source_type,
            }
            if self._entities[entity_id][USE_PICTURE]:
                kwargs["picture"] = new_state.attributes.get(ATTR_ENTITY_PICTURE)
            self._see(**kwargs)

            self._prev_seen = last_seen
