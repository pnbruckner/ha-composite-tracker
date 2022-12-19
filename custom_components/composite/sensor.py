"""Composite Sensor."""
from __future__ import annotations

from dataclasses import dataclass
import logging
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

    sensor_device_class_speed = SensorDeviceClass.SPEED
except AttributeError:
    sensor_device_class_speed = None

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID, CONF_NAME

# UnitOfSpeed was new in 2022.11
try:
    from homeassistant.const import UnitOfSpeed

    meters_per_second = UnitOfSpeed.METERS_PER_SECOND
except ImportError:
    from homeassistant.const import SPEED_METERS_PER_SECOND

    meters_per_second = SPEED_METERS_PER_SECOND

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SIG_COMPOSITE_SPEED

_LOGGER = logging.getLogger(__name__)


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
        device_class=sensor_device_class_speed,
        icon="mdi:car-speed-limiter",
        name=cast(str, entry.data[CONF_NAME]) + " Speed",
        native_unit_of_measurement=meters_per_second,
        state_class=SensorStateClass.MEASUREMENT,
        id=cast(str, entry.data[CONF_ID]) + "_speed",
        signal=f"{SIG_COMPOSITE_SPEED}-{entry.data[CONF_ID]}",
    )
    async_add_entities([CompositeSensor(hass, entity_description)])


class CompositeSensor(SensorEntity):
    """Composite Sensor Entity."""

    def __init__(
        self, hass: HomeAssistant, entity_description: CompositeSensorEntityDescription
    ) -> None:
        """Initialize composite sensor entity."""
        assert entity_description.key == "speed"

        self.entity_description = entity_description
        self._attr_unique_id = entity_description.id
        self.entity_id = f"{S_DOMAIN}.{entity_description.id}"

        self.async_on_remove(
            async_dispatcher_connect(hass, entity_description.signal, self._update)
        )

    async def _update(self, value: float) -> None:
        """Update sensor with new value."""
        self._attr_native_value = value
        self.async_write_ha_state()
