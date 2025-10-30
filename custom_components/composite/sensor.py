"""Composite Sensor."""
from __future__ import annotations

from typing import cast

from homeassistant.components.sensor import (
    DOMAIN as S_DOMAIN,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_ID, CONF_NAME, UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_ANGLE,
    ATTR_DIRECTION,
    CONF_SHOW_UNKNOWN_AS_0,
    SIG_COMPOSITE_SPEED,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensor platform."""
    async_add_entities([CompositeSensor(hass, entry)])


class CompositeSensor(SensorEntity):
    """Composite Sensor Entity."""

    _attr_should_poll = False
    _raw_value: float | None = None
    _ok_to_write_state = False
    _show_unknown_as_0: bool

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize composite sensor entity."""
        if entry.source == SOURCE_IMPORT:
            entity_description = SensorEntityDescription(
                key="speed",
                device_class=SensorDeviceClass.SPEED,
                icon="mdi:car-speed-limiter",
                name=cast(str, entry.data[CONF_NAME]) + " Speed",
                translation_key="speed",
                native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
                state_class=SensorStateClass.MEASUREMENT,
            )
            obj_id = (signal := cast(str, entry.data[CONF_ID])) + "_speed"
            self.entity_id = f"{S_DOMAIN}.{obj_id}"
            self._attr_unique_id = obj_id
        else:
            entity_description = SensorEntityDescription(
                key="speed",
                device_class=SensorDeviceClass.SPEED,
                icon="mdi:car-speed-limiter",
                has_entity_name=True,
                translation_key="speed",
                native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
                state_class=SensorStateClass.MEASUREMENT,
            )
            self._attr_translation_placeholders = {"name": entry.title}
            self._attr_unique_id = signal = entry.entry_id
        self.entity_description = entity_description
        self._attr_extra_state_attributes = {
            ATTR_ANGLE: None,
            ATTR_DIRECTION: None,
        }

        self.async_on_remove(
            async_dispatcher_connect(
                hass, f"{SIG_COMPOSITE_SPEED}-{signal}", self._update
            )
        )

    @property
    def native_value(self) -> float | None:
        """Return the value reported by the sensor."""
        if self._raw_value is not None:
            return self._raw_value
        if self._show_unknown_as_0:
            return 0
        return None

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(
            cast(ConfigEntry, self.platform.config_entry).add_update_listener(
                self._config_entry_updated
            )
        )
        await self.async_request_call(self._process_config_options())
        self._ok_to_write_state = True

    async def _process_config_options(self) -> None:
        """Process options from config entry."""
        options = cast(ConfigEntry, self.platform.config_entry).options
        # For backward compatibility, if the option is not present, interpret that as
        # the same as False.
        self._show_unknown_as_0 = options.get(CONF_SHOW_UNKNOWN_AS_0, False)

    async def _config_entry_updated(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """Run when the config entry has been updated."""
        if entry.source == SOURCE_IMPORT:
            return
        if (new_name := entry.title) != self._attr_translation_placeholders["name"]:
            # Need to change _attr_translation_placeholders (instead of the dict to
            # which it refers) to clear the cached_property.
            self._attr_translation_placeholders = {"name": new_name}
            er.async_get(hass).async_update_entity(
                self.entity_id, original_name=self.name
            )
        await self.async_request_call(self._process_config_options())
        self.async_write_ha_state()

    async def _update(self, value: float | None, angle: int | None) -> None:
        """Update sensor with new value."""

        def direction(angle: int | None) -> str | None:
            """Determine compass direction."""
            if angle is None:
                return None
            return ("N", "NE", "E", "SE", "S", "SW", "W", "NW", "N")[
                int((angle + 360 / 16) // (360 / 8))
            ]

        self._raw_value = value
        self._attr_extra_state_attributes = {
            ATTR_ANGLE: angle,
            ATTR_DIRECTION: direction(angle),
        }
        # It's possible for dispatcher signal to arrive, causing this method to execute,
        # before this sensor entity has been completely "added to hass", meaning
        # self.hass might not yet have been initialized, causing this call to
        # async_write_ha_state to fail. We still update our state, so that the call to
        # async_write_ha_state at the end of the "add to hass" process will see it. Once
        # added to hass, we can go ahead and write the state here for future updates.
        if self._ok_to_write_state:
            self.async_write_ha_state()
