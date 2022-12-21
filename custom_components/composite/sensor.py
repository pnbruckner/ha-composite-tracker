"""Composite Sensor."""
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from homeassistant.components.sensor import (
    DOMAIN as S_DOMAIN,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)

# SensorDeviceClass.SPEED was new in 2022.10
try:
    from homeassistant.components.sensor import SensorDeviceClass

    speed_sensor_device_class = SensorDeviceClass.SPEED
except AttributeError:
    speed_sensor_device_class = None
    from homeassistant.const import (
        EVENT_CORE_CONFIG_UPDATE,
        LENGTH_KILOMETERS,
        LENGTH_METERS,
        LENGTH_MILES,
        SPEED_KILOMETERS_PER_HOUR,
        SPEED_MILES_PER_HOUR,
    )
    from homeassistant.util.distance import convert
    from homeassistant.util.unit_system import METRIC_SYSTEM

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID, CONF_NAME

# UnitOfSpeed was new in 2022.11
try:
    from homeassistant.const import UnitOfSpeed

    meters_per_second = UnitOfSpeed.METERS_PER_SECOND
except ImportError:
    from homeassistant.const import SPEED_METERS_PER_SECOND

    meters_per_second = SPEED_METERS_PER_SECOND

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_ANGLE, ATTR_DIRECTION, SIG_COMPOSITE_SPEED


@dataclass
class CompositeSensorEntityDescription(SensorEntityDescription):
    """Composite sensor entity description."""

    id: str = None
    signal: str = None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    entity_description = CompositeSensorEntityDescription(
        "speed",
        icon="mdi:car-speed-limiter",
        name=cast(str, entry.data[CONF_NAME]) + " Speed",
        state_class=SensorStateClass.MEASUREMENT,
        id=cast(str, entry.data[CONF_ID]) + "_speed",
        signal=f"{SIG_COMPOSITE_SPEED}-{entry.data[CONF_ID]}",
    )
    if speed_sensor_device_class:
        entity_description.device_class = speed_sensor_device_class
        entity_description.native_unit_of_measurement = meters_per_second
    async_add_entities([CompositeSensor(hass, entity_description)])


class CompositeSensor(SensorEntity):
    """Composite Sensor Entity."""

    _attr_should_poll = False
    _to_unit: str | None = None

    def __init__(
        self, hass: HomeAssistant, entity_description: CompositeSensorEntityDescription
    ) -> None:
        """Initialize composite sensor entity."""
        assert entity_description.key == "speed"

        self.entity_description = entity_description

        @callback
        def set_unit_of_measurement(event: Event = None) -> None:
            """Set unit of measurement based on HA config."""
            if hass.config.units is METRIC_SYSTEM:
                uom = SPEED_KILOMETERS_PER_HOUR
                self._to_unit = LENGTH_KILOMETERS
            else:
                uom = SPEED_MILES_PER_HOUR
                self._to_unit = LENGTH_MILES
            self.entity_description.native_unit_of_measurement = uom

        if not entity_description.device_class:
            set_unit_of_measurement()
            self.async_on_remove(
                hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, set_unit_of_measurement)
            )

        self._attr_unique_id = entity_description.id
        self._attr_extra_state_attributes = {
            ATTR_ANGLE: None,
            ATTR_DIRECTION: None,
        }
        self.entity_id = f"{S_DOMAIN}.{entity_description.id}"

        self.async_on_remove(
            async_dispatcher_connect(hass, entity_description.signal, self._update)
        )

    async def _update(self, value: float | None, angle: int | None) -> None:
        """Update sensor with new value."""

        def direction(angle: int | None) -> str | None:
            """Determine compass direction."""
            if angle is None:
                return None
            ("W", "SW", "S", "SE", "E", "NE", "N", "NW", "W")[
                int((angle + 180 + 360 / 16) // (360 / 8))
            ]

        if value and self._to_unit:
            value = f"{convert(value, LENGTH_METERS, self._to_unit) * (60 * 60):0.1f}"
        self._attr_native_value = value
        self.entity_description.force_update = bool(value)
        self._attr_extra_state_attributes = {
            ATTR_ANGLE: angle,
            ATTR_DIRECTION: direction(angle),
        }
        self.async_write_ha_state()
