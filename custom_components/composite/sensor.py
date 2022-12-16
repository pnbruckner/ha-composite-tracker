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
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID, CONF_NAME, UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SIG_COMPOSITE_SPEED


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
        device_class=SensorDeviceClass.SPEED,
        icon="mdi:car-speed-limiter",
        name=cast(str, entry.data[CONF_NAME]) + " Speed",
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        id=cast(str, entry.data[CONF_ID]) + "_speed",
        signal=SIG_COMPOSITE_SPEED,
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
