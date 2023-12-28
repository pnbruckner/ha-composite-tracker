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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_ANGLE, ATTR_DIRECTION, SIG_COMPOSITE_SPEED


@dataclass
class CompositeSensorEntityDescription(SensorEntityDescription):
    """Composite sensor entity description."""

    obj_id: str = None  # type: ignore[assignment]
    signal: str = None  # type: ignore[assignment]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    entity_description = CompositeSensorEntityDescription(
        key="speed",
        icon="mdi:car-speed-limiter",
        name=cast(str, entry.data[CONF_NAME]) + " Speed",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        obj_id=cast(str, entry.data[CONF_ID]) + "_speed",
        signal=f"{SIG_COMPOSITE_SPEED}-{entry.data[CONF_ID]}",
    )
    async_add_entities([CompositeSensor(hass, entity_description)])


class CompositeSensor(SensorEntity):
    """Composite Sensor Entity."""

    _attr_should_poll = False
    _first_state_written = False

    def __init__(
        self, hass: HomeAssistant, entity_description: CompositeSensorEntityDescription
    ) -> None:
        """Initialize composite sensor entity."""
        assert entity_description.key == "speed"

        self.entity_description = entity_description
        self._attr_unique_id = entity_description.obj_id
        self._attr_extra_state_attributes = {
            ATTR_ANGLE: None,
            ATTR_DIRECTION: None,
        }
        self.entity_id = f"{S_DOMAIN}.{entity_description.obj_id}"

        self.async_on_remove(
            async_dispatcher_connect(hass, entity_description.signal, self._update)
        )

    @callback
    def async_write_ha_state(self) -> None:
        """Write the state to the state machine."""
        super().async_write_ha_state()
        self._first_state_written = True

    async def _update(self, value: float | None, angle: int | None) -> None:
        """Update sensor with new value."""

        def direction(angle: int | None) -> str | None:
            """Determine compass direction."""
            if angle is None:
                return None
            return ("N", "NE", "E", "SE", "S", "SW", "W", "NW", "N")[
                int((angle + 360 / 16) // (360 / 8))
            ]

        self._attr_native_value = value
        self._attr_force_update = bool(value)
        self._attr_extra_state_attributes = {
            ATTR_ANGLE: angle,
            ATTR_DIRECTION: direction(angle),
        }
        # It's possible for dispatcher signal to arrive, causing this method to execute,
        # before this sensor entity has been completely "added to hass", meaning
        # self.hass might not yet have been initialized, causing this call to
        # async_write_ha_state to fail. We still update our state, so that the call to
        # async_write_ha_state at the end of the "add to hass" process will see it. Once
        # we know that call has completed, we can go ahead and write the state here for
        # future updates.
        if self._first_state_written:
            self.async_write_ha_state()
