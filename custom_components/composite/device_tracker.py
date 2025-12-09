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
from typing import Any, cast

from propcache.api import cached_property

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
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State
from homeassistant.helpers import entity_registry as er
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import GPSType
import homeassistant.util.dt as dt_util
from homeassistant.util.location import distance

from .const import (
    ATTR_ACC,
    ATTR_CHARGING,
    ATTR_ENTITIES,
    ATTR_LAST_ENTITY_ID,
    ATTR_LAST_SEEN,
    ATTR_LAST_TIMESTAMP,
    ATTR_LAT,
    ATTR_LON,
    CONF_ALL_STATES,
    CONF_DRIVING_SPEED,
    CONF_END_DRIVING_DELAY,
    CONF_ENTITY,
    CONF_ENTITY_PICTURE,
    CONF_MAX_SPEED_AGE,
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


_RESTORE_EXTRA_ATTRS = (
    ATTR_ENTITY_ID,
    ATTR_ENTITIES,
    ATTR_LAST_ENTITY_ID,
    ATTR_LAST_SEEN,
    ATTR_BATTERY_CHARGING,
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

    NOT_SET = auto()
    GOOD = auto()
    BAD = auto()
    WARNED = auto()
    SUSPEND = auto()


@dataclass
class Location:
    """Location (latitude, longitude & accuracy)."""

    gps: GPSType
    accuracy: float


@dataclass
class EntityData:
    """Input entity data."""

    entity_id: str
    use_all_states: bool
    use_picture: bool
    _status: EntityStatus = EntityStatus.NOT_SET
    seen: datetime | None = None
    source_type: SourceType = SourceType.GPS
    data: Location | str | None = None

    @property
    def is_good(self) -> bool:
        """Return if last update was good."""
        return self._status == EntityStatus.GOOD

    def set_params(self, use_all_states: bool, use_picture: bool) -> None:
        """Set parameters."""
        self.use_all_states = use_all_states
        self.use_picture = use_picture

    def good(
        self, seen: datetime, source_type: SourceType, data: Location | str
    ) -> None:
        """Mark entity as good."""
        self._status = EntityStatus.GOOD
        self.seen = seen
        self.source_type = source_type
        self.data = data

    def bad(self, message: str) -> None:
        """Mark entity as bad."""
        if self._status == EntityStatus.SUSPEND:
            return
        msg = f"{self.entity_id} {message}"
        if self._status == EntityStatus.WARNED:
            _LOGGER.error(msg)
            self._status = EntityStatus.SUSPEND
        # Only warn if this is not the first state change for the entity.
        elif self._status != EntityStatus.NOT_SET:
            _LOGGER.warning(msg)
            self._status = EntityStatus.WARNED
        else:
            _LOGGER.debug(msg)
            self._status = EntityStatus.BAD


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
    _prev_seen: datetime | None = None
    _prev_speed: float | None = None

    _remove_track_states: Callable[[], None] | None = None
    _remove_speed_is_stale: Callable[[], None] | None = None
    _remove_driving_ended: Callable[[], None] | None = None
    _req_movement: bool
    _max_speed_age: timedelta | None
    _driving_speed: float | None  # m/s
    _end_driving_delay: timedelta | None
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

    @cached_property
    def force_update(self) -> bool:
        """Return True if state updates should be forced."""
        return False

    @property
    def battery_level(self) -> int | None:
        """Return the battery level of the device."""
        return self._battery_level

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()

        self.async_on_remove(
            cast(ConfigEntry, self.platform.config_entry).add_update_listener(
                self._config_entry_updated
            )
        )
        await self.async_request_call(self._restore_state())
        await self.async_request_call(self._process_config_options())

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        if self._remove_track_states:
            self._remove_track_states()
            self._remove_track_states = None
        self._cancel_speed_stale_monitor()
        self._cancel_drive_ending_delay()
        await super().async_will_remove_from_hass()

    async def _process_config_options(self) -> None:
        """Process options from config entry."""
        options = cast(ConfigEntry, self.platform.config_entry).options
        self._req_movement = options[CONF_REQ_MOVEMENT]
        if (msa := options.get(CONF_MAX_SPEED_AGE)) is None:
            self._max_speed_age = None
        else:
            self._max_speed_age = cast(timedelta, cv.time_period(msa))
        self._driving_speed = options.get(CONF_DRIVING_SPEED)
        if (edd := options.get(CONF_END_DRIVING_DELAY)) is None:
            self._end_driving_delay = None
        else:
            self._end_driving_delay = cast(timedelta, cv.time_period(edd))
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

        async def state_listener(event: Event[EventStateChangedData]) -> None:
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
        if not (last_state := await self.async_get_last_state()):
            return

        self._attr_entity_picture = last_state.attributes.get(ATTR_ENTITY_PICTURE)
        self._battery_level = last_state.attributes.get(ATTR_BATTERY_LEVEL)
        # Prior versions allowed a source_type of binary_sensor. To better conform to
        # the TrackerEntity base class, inputs that do not directly map to one of the
        # SourceType options will be represented as SourceType.ROUTER.
        if (source_type := last_state.attributes[ATTR_SOURCE_TYPE]) in SourceType:
            self._attr_source_type = source_type
        else:
            self._attr_source_type = SourceType.ROUTER
        self._attr_location_accuracy = last_state.attributes.get(ATTR_GPS_ACCURACY) or 0
        self._attr_latitude = last_state.attributes.get(ATTR_LATITUDE)
        self._attr_longitude = last_state.attributes.get(ATTR_LONGITUDE)
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
            last_seen = dt_util.parse_datetime(
                self._attr_extra_state_attributes[ATTR_LAST_SEEN]
            )
            if last_seen is None:
                self._attr_extra_state_attributes[ATTR_LAST_SEEN] = None
            else:
                self._attr_extra_state_attributes[ATTR_LAST_SEEN] = dt_util.as_local(
                    last_seen
                )
                self._prev_seen = dt_util.as_utc(last_seen)
        if self.source_type != SourceType.GPS and (
            self.latitude is None or self.longitude is None
        ):
            self._attr_location_name = last_state.state

    def _clear_state(self) -> None:
        """Clear state."""
        self._battery_level = None
        self._attr_source_type = SourceType.GPS
        self._attr_location_accuracy = 0
        self._attr_location_name = None
        self._attr_latitude = None
        self._attr_longitude = None
        self._attr_extra_state_attributes = {}
        self._prev_seen = None
        self._prev_speed = None
        self._cancel_speed_stale_monitor()
        self._cancel_drive_ending_delay()

    def _cancel_speed_stale_monitor(self) -> None:
        """Cancel monitoring of speed sensor staleness."""
        if self._remove_speed_is_stale:
            self._remove_speed_is_stale()
            self._remove_speed_is_stale = None

    def _start_speed_stale_monitor(self) -> None:
        """Start monitoring speed sensor staleness."""
        self._cancel_speed_stale_monitor()
        if self._max_speed_age is None:
            return

        async def speed_is_stale(_utcnow: datetime) -> None:
            """Speed sensor is stale."""
            self._remove_speed_is_stale = None

            async def clear_speed_sensor_state() -> None:
                """Clear speed sensor's state."""
                self._send_speed(None, None)

            await self.async_request_call(clear_speed_sensor_state())
            self.async_write_ha_state()

        self._remove_speed_is_stale = async_call_later(
            self.hass, self._max_speed_age, speed_is_stale
        )

    def _send_speed(self, speed: float | None, angle: int | None) -> None:
        """Send values to speed sensor."""
        _LOGGER.debug("%s: Sending speed: %s m/s, angle: %s°", self.name, speed, angle)
        async_dispatcher_send(
            self.hass, f"{SIG_COMPOSITE_SPEED}-{self.unique_id}", speed, angle
        )

    def _cancel_drive_ending_delay(self) -> None:
        """Cancel ending of driving state."""
        if self._remove_driving_ended:
            self._remove_driving_ended()
            self._remove_driving_ended = None

    def _start_drive_ending_delay(self) -> None:
        """Start delay to end driving state if configured."""
        self._cancel_drive_ending_delay()
        if self._end_driving_delay is None:
            return

        async def driving_ended(_utcnow: datetime) -> None:
            """End driving state."""
            self._remove_driving_ended = None

            async def end_driving() -> None:
                """End driving state."""
                self._attr_location_name = None

            await self.async_request_call(end_driving())
            self.async_write_ha_state()

        self._remove_driving_ended = async_call_later(
            self.hass, self._end_driving_delay, driving_ended
        )

    @property
    def _drive_ending_delayed(self) -> bool:
        """Return if end of driving state is being delayed."""
        return self._remove_driving_ended is not None

    async def _entity_updated(  # noqa: C901
        self, entity_id: str, new_state: State | None
    ) -> None:
        """Run when an input entity has changed state."""
        if not new_state or new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return

        entity = self._entities[entity_id]
        new_attrs = Attributes(new_state.attributes)

        # Get time device was last seen, which is specified by one of the entity's
        # attributes defined by _LAST_SEEN_ATTRS, as a datetime.

        def get_last_seen() -> datetime | None:
            """Get last_seen (in UTC) from one of the possible attributes."""
            if (raw_last_seen := new_attrs.get(_LAST_SEEN_ATTRS)) is None:
                return None
            if isinstance(raw_last_seen, datetime):
                return dt_util.as_utc(raw_last_seen)
            with suppress(TypeError, ValueError):
                return dt_util.utc_from_timestamp(float(raw_last_seen))
            with suppress(TypeError):
                if (parsed_last_seen := dt_util.parse_datetime(raw_last_seen)) is None:
                    return None
                return dt_util.as_utc(parsed_last_seen)
            return None

        # Use last_updated from the new state object if no valid "last seen" was found.
        last_seen = get_last_seen() or new_state.last_updated

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
        gps_accuracy = cast(float | None, new_attrs.get(_GPS_ACCURACY_ATTRS))
        battery = cast(int | None, new_attrs.get(_BATTERY_ATTRS))
        charging = cast(bool | None, new_attrs.get(_CHARGING_ATTRS))

        # What type of tracker is this?
        if new_state.domain == BS_DOMAIN:
            source_type: str | None = SourceType.ROUTER.value
        else:
            source_type = new_attrs.get(
                ATTR_SOURCE_TYPE,
                SourceType.GPS.value if gps and gps_accuracy is not None else None,
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
            old_data = cast(Location | None, entity.data)
            if last_seen == old_last_seen and new_data == old_data:
                return
            entity.good(last_seen, SourceType.GPS, new_data)

            if self._req_movement and old_data:
                dist = distance(gps[0], gps[1], old_data.gps[0], old_data.gps[1])
                if dist is not None and dist <= gps_accuracy + old_data.accuracy:
                    _LOGGER.debug(
                        "For %s skipping update from %s: not enough movement",
                        self.entity_id,
                        entity_id,
                    )
                    return

        elif source_type in SourceType:
            # Convert 'on'/'off' state of binary_sensor
            # to 'home'/'not_home'.
            if new_state.domain == BS_DOMAIN:
                if state == STATE_ON:
                    state = STATE_HOME
                else:
                    state = STATE_NOT_HOME

            entity.good(last_seen, SourceType(source_type), state)  # type: ignore[arg-type]

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

            # If input entity's state is 'home' and our current state is 'home' w/ GPS
            # data, use it and make source_type gps.
            if state == STATE_HOME and home_w_gps:
                gps = cast(GPSType, (self.latitude, self.longitude))
                gps_accuracy = self.location_accuracy
                source_type = SourceType.GPS.value
            # Otherwise, if new GPS data is valid (which is unlikely if
            # new state is not 'home'),
            # use it and make source_type gps.
            elif gps:
                source_type = SourceType.GPS.value
            # Otherwise, if new state is 'home' and old state is not 'home' w/ GPS data
            # (i.e., not 'home' or no GPS data), then use HA's configured Home location
            # and make source_type gps.
            elif state == STATE_HOME:
                gps = (self.hass.config.latitude, self.hass.config.longitude)
                gps_accuracy = 0
                source_type = SourceType.GPS.value
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
                dt_util.as_local(last_seen),
                dt_util.as_local(self._prev_seen),
            )
            return

        _LOGGER.debug("Updating %s from %s", self.entity_id, entity_id)

        attrs = {
            ATTR_ENTITIES: tuple(
                entity_id
                for entity_id, _entity in self._entities.items()
                if _entity.is_good
            ),
            ATTR_LAST_ENTITY_ID: entity_id,
            ATTR_LAST_SEEN: dt_util.as_local(_nearest_second(last_seen)),
        }
        if charging is not None:
            attrs[ATTR_BATTERY_CHARGING] = charging

        self._set_state(
            location_name, gps, gps_accuracy, battery, attrs, SourceType(source_type)  # type: ignore[arg-type]
        )

        self._prev_seen = last_seen

    def _set_state(
        self,
        location_name: str | None,
        gps: GPSType | None,
        gps_accuracy: float | None,
        battery: int | None,
        attributes: dict,
        source_type: SourceType,
    ) -> None:
        """Set new state."""
        # Save previously "seen" values before updating for speed calculations, etc.
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
        was_driving = (
            self._prev_speed is not None
            and self._driving_speed is not None
            and self._prev_speed >= self._driving_speed
        )

        self._battery_level = battery
        self._attr_source_type = source_type
        self._attr_location_accuracy = gps_accuracy or 0
        self._attr_location_name = location_name
        lat: float | None
        lon: float | None
        if gps:
            lat, lon = gps
        else:
            lat = lon = None
        self._attr_latitude = lat
        self._attr_longitude = lon

        self._attr_extra_state_attributes = attributes

        last_seen = cast(datetime, attributes[ATTR_LAST_SEEN])
        speed = None
        angle = None
        use_new_speed = True
        if (
            prev_ent
            and self._prev_seen
            and prev_lat is not None
            and prev_lon is not None
            and lat is not None
            and lon is not None
        ):
            # It's ok that last_seen is in local tz and self._prev_seen is in UTC.
            # last_seen's value will automatically be converted to UTC during the
            # subtraction operation.
            seconds = (last_seen - self._prev_seen).total_seconds()
            min_seconds = MIN_SPEED_SECONDS
            if cast(str, attributes[ATTR_LAST_ENTITY_ID]) != prev_ent:
                min_seconds *= 3
            if seconds < min_seconds:
                _LOGGER.debug(
                    "%s: Not sending speed & angle (time delta %0.1f < %0.1f)",
                    self.name,
                    seconds,
                    min_seconds,
                )
                use_new_speed = False
            else:
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

        if use_new_speed:
            self._send_speed(speed, angle)
            self._prev_speed = speed
            self._start_speed_stale_monitor()
        else:
            speed = self._prev_speed

        # Only set state to driving if it's currently "away" (i.e., not in a zone.)
        if self.state != STATE_NOT_HOME:
            self._cancel_drive_ending_delay()
            return

        driving = (
            speed is not None
            and self._driving_speed is not None
            and speed >= self._driving_speed
        )

        if driving:
            self._cancel_drive_ending_delay()
        elif was_driving:
            self._start_drive_ending_delay()

        if driving or self._drive_ending_delayed:
            self._attr_location_name = STATE_DRIVING

    def _use_non_gps_data(self, entity_id: str, state: str) -> bool:
        """Determine if state should be used for non-GPS based entity."""
        if state == STATE_HOME or self._entities[entity_id].use_all_states:
            return True
        good_entities = (entity for entity in self._entities.values() if entity.is_good)
        if any(entity.source_type == SourceType.GPS for entity in good_entities):
            return False
        return all(cast(str, entity.data) != STATE_HOME for entity in good_entities)
