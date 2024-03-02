"""A Device Tracker platform that combines one or more device trackers."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto
import logging
from math import atan2, degrees
from types import MappingProxyType
from typing import Any, Optional, cast

from homeassistant.components.binary_sensor import DOMAIN as BS_DOMAIN
from homeassistant.components.device_tracker import (
    ATTR_BATTERY,
    ATTR_SOURCE_TYPE,
    DOMAIN as DT_DOMAIN,
    SourceType,
)
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
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
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import GPSType
import homeassistant.util.dt as dt_util
from homeassistant.util.location import distance

from .const import (
    ATTR_ACC,
    ATTR_ENTITIES,
    ATTR_LAT,
    ATTR_LON,
    CONF_ALL_STATES,
    CONF_DRIVING_SPEED,
    CONF_ENTITY,
    CONF_ENTITY_PICTURE,
    CONF_REQ_MOVEMENT,
    CONF_USE_PICTURE,
    MIN_ANGLE_SPEED,
    MIN_SPEED_SECONDS,
    SIG_COMPOSITE_SPEED,
    STATE_DRIVING,
)

_LOGGER = logging.getLogger(__name__)

# Cause Semaphore to be created to make async_update, and anything protected by
# async_request_call, atomic.
PARALLEL_UPDATES = 1

ATTR_CHARGING = "charging"
ATTR_LAST_SEEN = "last_seen"
ATTR_LAST_TIMESTAMP = "last_timestamp"
ATTR_LAST_ENTITY_ID = "last_entity_id"

_RESTORE_EXTRA_ATTRS = (
    ATTR_ENTITY_ID,
    ATTR_ENTITIES,
    ATTR_LAST_ENTITY_ID,
    ATTR_LAST_SEEN,
    ATTR_BATTERY_CHARGING,
)

_SOURCE_TYPE_BINARY_SENSOR = BS_DOMAIN
_STATE_BINARY_SENSOR_HOME = STATE_ON

_SOURCE_TYPE_NON_GPS = (
    _SOURCE_TYPE_BINARY_SENSOR,
    SourceType.BLUETOOTH,
    SourceType.BLUETOOTH_LE,
    SourceType.ROUTER,
)

_GPS_ACCURACY_ATTRS = (ATTR_GPS_ACCURACY, ATTR_ACC)
_BATTERY_ATTRS = (ATTR_BATTERY_LEVEL, ATTR_BATTERY)
_CHARGING_ATTRS = (ATTR_BATTERY_CHARGING, ATTR_CHARGING)
_LAST_SEEN_ATTRS = (ATTR_LAST_SEEN, ATTR_LAST_TIMESTAMP)


async def async_setup_entry(
    _hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the device tracker platform."""
    async_add_entities([CompositeDeviceTracker(entry)])


def _nearest_second(time: datetime) -> datetime:
    """Round time to nearest second."""
    return time.replace(microsecond=0) + timedelta(
        seconds=0 if time.microsecond < 500000 else 1
    )


class EntityStatus(Enum):
    """Input entity status."""

    INACTIVE = auto()
    ACTIVE = auto()
    WARNED = auto()
    SUSPEND = auto()


@dataclass
class Location:
    """Location (latitude, longitude & accuracy)."""

    gps: GPSType
    accuracy: int


@dataclass
class EntityData:
    """Input entity data."""

    entity_id: str
    use_all_states: bool
    use_picture: bool
    status: EntityStatus = EntityStatus.INACTIVE
    seen: datetime | None = None
    source_type: str | None = None
    data: Location | str | None = None

    def set_params(self, use_all_states: bool, use_picture: bool) -> None:
        """Set parameters."""
        self.use_all_states = use_all_states
        self.use_picture = use_picture

    def good(self, seen: datetime, source_type: str, data: Location | str) -> None:
        """Mark entity as good."""
        self.status = EntityStatus.ACTIVE
        self.seen = seen
        self.source_type = source_type
        self.data = data

    def bad(self, message: str) -> None:
        """Mark entity as bad."""
        if self.status == EntityStatus.SUSPEND:
            return
        msg = f"{self.entity_id} {message}"
        if self.status == EntityStatus.WARNED:
            _LOGGER.error(msg)
            self.status = EntityStatus.SUSPEND
        # Only warn if this is not the first state change for the entity.
        elif self.status == EntityStatus.ACTIVE:
            _LOGGER.warning(msg)
            self.status = EntityStatus.WARNED
        else:
            _LOGGER.debug(msg)
            self.status = EntityStatus.ACTIVE


class Attributes:
    """Flexible attribute retrieval."""

    def __init__(self, attrs: Mapping[str, Any]) -> None:
        """Initialize."""
        self._attrs = MappingProxyType(attrs)

    def __getitem__(self, key: str) -> Any:
        """Implement Attributes_object[key]."""
        return self._attrs[key]

    def get(self, key: str | Sequence[str], default: Any | None = None) -> Any | None:
        """Get item for first found key, or default if no key found."""
        if isinstance(key, str):
            return self._attrs.get(key, default)
        for _key in key:
            if _key in self._attrs:
                return self._attrs[_key]
        return default


class CompositeDeviceTracker(TrackerEntity, RestoreEntity):
    """Composite Device Tracker."""

    _attr_translation_key = "tracker"
    _unrecorded_attributes = frozenset({ATTR_ENTITIES, ATTR_ENTITY_PICTURE})

    # State vars
    _battery_level: int | None = None
    _source_type: str | None = None
    _location_accuracy = 0
    _location_name: str | None = None
    _latitude: float | None = None
    _longitude: float | None = None

    _prev_seen: datetime | None = None
    _remove_track_states: Callable[[], None] | None = None
    _req_movement: bool
    _driving_speed: float | None  # m/s
    _use_entity_picture: bool

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize Composite Device Tracker."""
        if entry.source == SOURCE_IMPORT:
            obj_id = entry.data[CONF_ID]
            self.entity_id = f"{DT_DOMAIN}.{obj_id}"
            self._attr_name = cast(str, entry.data[CONF_NAME])
            self._attr_unique_id = obj_id
        else:
            self._attr_name = entry.title
            self._attr_unique_id = entry.entry_id
        self._attr_extra_state_attributes = {}
        self._entities: dict[str, EntityData] = {}

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

        self.async_on_remove(
            cast(ConfigEntry, self.platform.config_entry).add_update_listener(
                self._config_entry_updated
            )
        )
        await self.async_request_call(self._process_config_options())
        await self.async_request_call(self._restore_state())

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        if self._remove_track_states:
            self._remove_track_states()
            self._remove_track_states = None
        await super().async_will_remove_from_hass()

    async def _process_config_options(self) -> None:
        """Process options from config entry."""
        options = cast(ConfigEntry, self.platform.config_entry).options
        self._req_movement = options[CONF_REQ_MOVEMENT]
        self._driving_speed = options.get(CONF_DRIVING_SPEED)
        entity_cfgs = {
            entity_cfg[CONF_ENTITY]: entity_cfg
            for entity_cfg in options[CONF_ENTITY_ID]
        }

        cur_entity_ids = set(self._entities)
        cfg_entity_ids = set(entity_cfgs)

        del_entity_ids = cur_entity_ids - cfg_entity_ids
        new_entity_ids = cfg_entity_ids - cur_entity_ids
        cur_entity_ids &= cfg_entity_ids

        last_entity_id = (
            self.extra_state_attributes
            and self.extra_state_attributes[ATTR_LAST_ENTITY_ID]
        )
        for entity_id in del_entity_ids:
            entity = self._entities.pop(entity_id)
            if entity_id == last_entity_id:
                self._clear_state()
                if entity.use_picture:
                    self._attr_entity_picture = None

        for entity_id in cur_entity_ids:
            entity_cfg = entity_cfgs[entity_id]
            self._entities[entity_id].set_params(
                entity_cfg[CONF_ALL_STATES], entity_cfg[CONF_USE_PICTURE]
            )

        for entity_id in new_entity_ids:
            entity_cfg = entity_cfgs[entity_id]
            self._entities[entity_id] = EntityData(
                entity_id, entity_cfg[CONF_ALL_STATES], entity_cfg[CONF_USE_PICTURE]
            )

        for entity_id in cfg_entity_ids:
            await self._entity_updated(entity_id, self.hass.states.get(entity_id))

        self._use_entity_picture = True
        if entity_picture := options.get(CONF_ENTITY_PICTURE):
            self._attr_entity_picture = entity_picture
        elif not any(entity.use_picture for entity in self._entities.values()):
            self._attr_entity_picture = None
            self._use_entity_picture = False

        async def state_listener(event: Event) -> None:
            """Process input entity state update."""
            await self.async_request_call(
                self._entity_updated(event.data["entity_id"], event.data["new_state"])
            )
            self.async_write_ha_state()

        if self._remove_track_states:
            self._remove_track_states()
        self._remove_track_states = async_track_state_change_event(
            self.hass, cfg_entity_ids, state_listener
        )

    async def _config_entry_updated(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """Run when the config entry has been updated."""
        if (new_name := entry.title) != self._attr_name:
            self._attr_name = new_name
            er.async_get(hass).async_update_entity(
                self.entity_id, original_name=self.name
            )
        await self.async_request_call(self._process_config_options())
        self.async_write_ha_state()

    async def _restore_state(self) -> None:
        """Restore state."""

        # Do we need to restore from saved state and, if so, is there one?
        if self._prev_seen and self.entity_picture:
            return
        if not (last_state := await self.async_get_last_state()):
            return

        # Even if we don't need to restore most of the state (i.e., if we've been
        # updated by at least one new state), we may need to restore entity picture, if
        # we had one but the entities we've been updated from so far do not.
        if not self.entity_picture and self._use_entity_picture:
            self._attr_entity_picture = last_state.attributes.get(ATTR_ENTITY_PICTURE)

        if self._prev_seen:
            return

        self._battery_level = last_state.attributes.get(ATTR_BATTERY_LEVEL)
        self._source_type = last_state.attributes[ATTR_SOURCE_TYPE]
        self._location_accuracy = last_state.attributes.get(ATTR_GPS_ACCURACY) or 0
        self._latitude = last_state.attributes.get(ATTR_LATITUDE)
        self._longitude = last_state.attributes.get(ATTR_LONGITUDE)
        self._attr_extra_state_attributes = {
            k: v for k, v in last_state.attributes.items() if k in _RESTORE_EXTRA_ATTRS
        }
        # List of seen entity IDs used to be in ATTR_ENTITY_ID.
        # If present, move it to ATTR_ENTITIES.
        if ATTR_ENTITY_ID in self._attr_extra_state_attributes:
            self._attr_extra_state_attributes[
                ATTR_ENTITIES
            ] = self._attr_extra_state_attributes.pop(ATTR_ENTITY_ID)
        with suppress(KeyError):
            self._attr_extra_state_attributes[ATTR_LAST_SEEN] = dt_util.parse_datetime(
                self._attr_extra_state_attributes[ATTR_LAST_SEEN]
            )
        if self.source_type in _SOURCE_TYPE_NON_GPS and (
            self.latitude is None or self.longitude is None
        ):
            self._location_name = last_state.state

    def _clear_state(self) -> None:
        """Clear state."""
        self._battery_level = None
        self._source_type = None
        self._location_accuracy = 0
        self._location_name = None
        self._latitude = None
        self._longitude = None
        self._attr_extra_state_attributes = {}
        self._prev_seen = None

    async def _entity_updated(  # noqa: C901
        self, entity_id: str, new_state: State | None
    ) -> None:
        """Run when an input entity has changed state."""
        if not new_state or new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return

        entity = self._entities[entity_id]
        new_attrs = Attributes(new_state.attributes)

        # Get time device was last seen, which is specified by one of the entity's
        # attributes defined by _LAST_SEEN_ATTRS, or if that doesn't exist, then
        # last_updated from the new state object.
        # Make sure last_seen is timezone aware in local timezone.
        # Note that dt_util.as_local assumes naive datetime is in local timezone.
        last_seen: datetime | str | None = new_attrs.get(_LAST_SEEN_ATTRS)
        if not isinstance(last_seen, datetime):
            try:
                last_seen = dt_util.utc_from_timestamp(
                    float(last_seen)  # type: ignore[arg-type]
                )
            except (TypeError, ValueError):
                last_seen = new_state.last_updated
        last_seen = dt_util.as_local(last_seen)

        old_last_seen = entity.seen
        if old_last_seen and last_seen < old_last_seen:
            entity.bad("last_seen went backwards")
            return

        # Try to get GPS and battery data.
        gps: GPSType | None = None
        with suppress(KeyError):
            gps = new_attrs[ATTR_LATITUDE], new_attrs[ATTR_LONGITUDE]
        if not gps:
            with suppress(KeyError):
                gps = new_attrs[ATTR_LAT], new_attrs[ATTR_LON]
        gps_accuracy = cast(Optional[int], new_attrs.get(_GPS_ACCURACY_ATTRS))
        battery = cast(Optional[int], new_attrs.get(_BATTERY_ATTRS))
        charging = cast(Optional[bool], new_attrs.get(_CHARGING_ATTRS))

        # What type of tracker is this?
        if new_state.domain == BS_DOMAIN:
            source_type: str | None = _SOURCE_TYPE_BINARY_SENSOR
        else:
            source_type = new_attrs.get(
                ATTR_SOURCE_TYPE, SourceType.GPS if gps and gps_accuracy else None
            )

        if entity.use_picture:
            self._attr_entity_picture = new_attrs.get(ATTR_ENTITY_PICTURE)

        state = new_state.state
        # Don't use location_name unless we have to.
        location_name: str | None = None

        if source_type == SourceType.GPS:
            # GPS coordinates and accuracy are required.
            if not gps:
                entity.bad("missing gps attributes")
                return
            if gps_accuracy is None:
                entity.bad("missing gps_accuracy attribute")
                return

            new_data = Location(gps, gps_accuracy)
            old_data = cast(Optional[Location], entity.data)
            if last_seen == old_last_seen and new_data == old_data:
                return
            entity.good(last_seen, source_type, new_data)

            if self._req_movement and old_data:
                dist = distance(gps[0], gps[1], old_data.gps[0], old_data.gps[1])
                if dist is not None and dist <= gps_accuracy + old_data.accuracy:
                    _LOGGER.debug(
                        "For %s skipping update from %s: not enough movement",
                        self.entity_id,
                        entity_id,
                    )
                    return

        elif source_type in _SOURCE_TYPE_NON_GPS:
            # Convert 'on'/'off' state of binary_sensor
            # to 'home'/'not_home'.
            if source_type == _SOURCE_TYPE_BINARY_SENSOR:
                if state == _STATE_BINARY_SENSOR_HOME:
                    state = STATE_HOME
                else:
                    state = STATE_NOT_HOME

            entity.good(last_seen, source_type, state)

            if not self._use_non_gps_data(entity_id, state):
                return

            # Don't use new GPS data if it's not complete.
            if not gps or gps_accuracy is None:
                gps = gps_accuracy = None

            # Is current state home w/ GPS data?
            if home_w_gps := self.location_name is None and self.state == STATE_HOME:
                if self.latitude is None or self.longitude is None:
                    _LOGGER.warning("%s: Unexpectedly home without GPS data", self.name)
                    home_w_gps = False

            # It's important, for this composite tracker, to avoid the
            # component level code's "stale processing." This can be done
            # one of two ways: 1) provide GPS data w/ source_type of gps,
            # or 2) provide a location_name (that will be used as the new
            # state.)

            # If router entity's state is 'home' and our current state is 'home' w/ GPS
            # data, use it and make source_type gps.
            if state == STATE_HOME and home_w_gps:
                gps = cast(GPSType, (self.latitude, self.longitude))
                gps_accuracy = self.location_accuracy
                source_type = SourceType.GPS
            # Otherwise, if new GPS data is valid (which is unlikely if
            # new state is not 'home'),
            # use it and make source_type gps.
            elif gps:
                source_type = SourceType.GPS
            # Otherwise, if new state is 'home' and old state is not 'home' w/ GPS data
            # (i.e., not 'home' or no GPS data), then use HA's configured Home location
            # and make source_type gps.
            elif state == STATE_HOME:
                gps = cast(
                    GPSType,
                    (self.hass.config.latitude, self.hass.config.longitude),
                )
                gps_accuracy = 0
                source_type = SourceType.GPS
            # Otherwise, don't use any GPS data, but set location_name to
            # new state.
            else:
                location_name = state

        else:
            entity.bad(f"unsupported source_type: {source_type}")
            return

        # Is this newer info than last update?
        if self._prev_seen and last_seen <= self._prev_seen:
            _LOGGER.debug(
                "For %s skipping update from %s: "
                "last_seen not newer than previous update (%s) <= (%s)",
                self.entity_id,
                entity_id,
                last_seen,
                self._prev_seen,
            )
            return

        _LOGGER.debug("Updating %s from %s", self.entity_id, entity_id)

        attrs = {
            ATTR_ENTITIES: tuple(
                entity_id
                for entity_id, _entity in self._entities.items()
                if _entity.source_type
            ),
            ATTR_LAST_ENTITY_ID: entity_id,
            ATTR_LAST_SEEN: _nearest_second(last_seen),
        }
        if charging is not None:
            attrs[ATTR_BATTERY_CHARGING] = charging

        self._set_state(location_name, gps, gps_accuracy, battery, attrs, source_type)

        self._prev_seen = last_seen

    def _set_state(
        self,
        location_name: str | None,
        gps: GPSType | None,
        gps_accuracy: int | None,
        battery: int | None,
        attributes: dict,
        source_type: SourceType | str | None,
    ) -> None:
        """Set new state."""
        # Save previously "seen" values before updating for speed calculations below.
        prev_ent: str | None
        prev_lat: float | None
        prev_lon: float | None
        if self._prev_seen:
            prev_ent = self._attr_extra_state_attributes[ATTR_LAST_ENTITY_ID]
            prev_lat = self.latitude
            prev_lon = self.longitude
        else:
            # Don't use restored attributes.
            prev_ent = prev_lat = prev_lon = None

        self._battery_level = battery
        self._source_type = source_type
        self._location_accuracy = gps_accuracy or 0
        self._location_name = location_name
        lat: float | None
        lon: float | None
        if gps:
            lat, lon = gps
        else:
            lat = lon = None
        self._latitude = lat
        self._longitude = lon

        self._attr_extra_state_attributes = attributes

        speed = None
        angle = None
        if prev_ent and self._prev_seen and prev_lat and prev_lon and gps:
            assert lat
            assert lon
            assert attributes
            last_ent = cast(str, attributes[ATTR_LAST_ENTITY_ID])
            last_seen = cast(datetime, attributes[ATTR_LAST_SEEN])
            seconds = (last_seen - self._prev_seen).total_seconds()
            min_seconds = MIN_SPEED_SECONDS
            if last_ent != prev_ent:
                min_seconds *= 3
            if seconds < min_seconds:
                _LOGGER.debug(
                    "%s: Not sending speed & angle (time delta %0.1f < %0.1f)",
                    self.name,
                    seconds,
                    min_seconds,
                )
                return
            meters = cast(float, distance(prev_lat, prev_lon, lat, lon))
            try:
                speed = round(meters / seconds, 1)
            except TypeError:
                _LOGGER.error("%s: distance() returned None", self.name)
            else:
                if speed > MIN_ANGLE_SPEED:
                    angle = round(degrees(atan2(lon - prev_lon, lat - prev_lat)))
                    if angle < 0:
                        angle += 360
        if (
            speed is not None
            and self._driving_speed is not None
            and speed >= self._driving_speed
            and self.state == STATE_NOT_HOME
        ):
            self._location_name = STATE_DRIVING
        _LOGGER.debug("%s: Sending speed: %s m/s, angle: %s°", self.name, speed, angle)
        async_dispatcher_send(
            self.hass, f"{SIG_COMPOSITE_SPEED}-{self.unique_id}", speed, angle
        )

    def _use_non_gps_data(self, entity_id: str, state: str) -> bool:
        """Determine if state should be used for non-GPS based entity."""
        if state == STATE_HOME or self._entities[entity_id].use_all_states:
            return True
        entities = self._entities.values()
        if any(entity.source_type == SourceType.GPS for entity in entities):
            return False
        return all(
            cast(str, entity.data) != STATE_HOME
            for entity in entities
            if entity.source_type in _SOURCE_TYPE_NON_GPS
        )
