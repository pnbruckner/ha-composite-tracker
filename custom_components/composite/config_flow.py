"""Config flow for Composite integration."""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

import voluptuous as vol

from homeassistant.backports.functools import cached_property
from homeassistant.components.binary_sensor import DOMAIN as BS_DOMAIN
from homeassistant.components.device_tracker import DOMAIN as DT_DOMAIN
from homeassistant.config_entries import (
    SOURCE_IMPORT,
    ConfigEntry,
    ConfigFlow,
    OptionsFlowWithConfigEntry,
)
from homeassistant.const import (
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_ENTITY_ID,
    CONF_ID,
    CONF_NAME,
    UnitOfSpeed,
)
from homeassistant.core import State, callback
from homeassistant.data_entry_flow import FlowHandler, FlowResult
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)
from homeassistant.util.unit_conversion import SpeedConverter
from homeassistant.util.unit_system import METRIC_SYSTEM

from .const import (
    ATTR_ACC,
    ATTR_LAT,
    ATTR_LON,
    CONF_ALL_STATES,
    CONF_DRIVING_SPEED,
    CONF_ENTITY,
    CONF_REQ_MOVEMENT,
    CONF_USE_PICTURE,
    DOMAIN,
)


def split_conf(conf: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return pieces of configuration data."""
    return {
        kw: {k: v for k, v in conf.items() if k in ks}
        for kw, ks in (
            ("data", (CONF_NAME, CONF_ID)),
            ("options", (CONF_ENTITY_ID, CONF_REQ_MOVEMENT, CONF_DRIVING_SPEED)),
        )
    }


class CompositeFlow(FlowHandler):
    """Composite flow mixin."""

    @cached_property
    def _entries(self) -> list[ConfigEntry]:
        """Get existing config entries."""
        return self.hass.config_entries.async_entries(DOMAIN)

    @cached_property
    def _speed_uom(self) -> str:
        """Return speed unit_of_measurement."""
        if self.hass.config.units is METRIC_SYSTEM:
            return UnitOfSpeed.KILOMETERS_PER_HOUR
        return UnitOfSpeed.MILES_PER_HOUR

    @property
    @abstractmethod
    def options(self) -> dict[str, Any]:
        """Return mutable copy of options."""

    @property
    def _entity_ids(self) -> list[str]:
        """Get currently configured entity IDs."""
        return [cfg[CONF_ENTITY] for cfg in self.options.get(CONF_ENTITY_ID, [])]

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Get config options."""
        errors = {}

        if user_input is not None:
            self.options[CONF_REQ_MOVEMENT] = user_input[CONF_REQ_MOVEMENT]
            if CONF_DRIVING_SPEED in user_input:
                self.options[CONF_DRIVING_SPEED] = SpeedConverter.convert(
                    user_input[CONF_DRIVING_SPEED],
                    self._speed_uom,
                    UnitOfSpeed.METERS_PER_SECOND,
                )
            elif CONF_DRIVING_SPEED in self.options:
                del self.options[CONF_DRIVING_SPEED]
            prv_cfgs = {
                cfg[CONF_ENTITY]: cfg for cfg in self.options.get(CONF_ENTITY_ID, [])
            }
            new_cfgs: list[dict[str, Any]] = []
            for entity_id in user_input[CONF_ENTITY_ID]:
                new_cfgs.append(
                    prv_cfgs.get(
                        entity_id,
                        {
                            CONF_ENTITY: entity_id,
                            CONF_USE_PICTURE: False,
                            CONF_ALL_STATES: False,
                        },
                    )
                )
            self.options[CONF_ENTITY_ID] = new_cfgs
            if new_cfgs:
                return await self.async_step_use_picture()
            errors[CONF_ENTITY_ID] = "at_least_one_entity"

        def entity_filter(state: State) -> bool:
            """Return if entity should be included in input list."""
            if state.domain in (BS_DOMAIN, DT_DOMAIN):
                return True
            attributes = state.attributes
            if ATTR_GPS_ACCURACY not in attributes and ATTR_ACC not in attributes:
                return False
            if ATTR_LATITUDE in attributes and ATTR_LONGITUDE in attributes:
                return True
            return ATTR_LAT in attributes and ATTR_LON in attributes

        include_entities = set(self._entity_ids)
        include_entities |= {
            state.entity_id
            for state in filter(entity_filter, self.hass.states.async_all())
        }
        data_schema = vol.Schema(
            {
                vol.Required(CONF_ENTITY_ID): EntitySelector(
                    EntitySelectorConfig(
                        include_entities=list(include_entities), multiple=True
                    )
                ),
                vol.Required(CONF_REQ_MOVEMENT): BooleanSelector(),
                vol.Optional(CONF_DRIVING_SPEED): NumberSelector(
                    NumberSelectorConfig(
                        unit_of_measurement=self._speed_uom,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        if CONF_ENTITY_ID in self.options:
            suggested_values = {
                CONF_ENTITY_ID: self._entity_ids,
                CONF_REQ_MOVEMENT: self.options[CONF_REQ_MOVEMENT],
            }
            if CONF_DRIVING_SPEED in self.options:
                suggested_values[CONF_DRIVING_SPEED] = SpeedConverter.convert(
                    self.options[CONF_DRIVING_SPEED],
                    UnitOfSpeed.METERS_PER_SECOND,
                    self._speed_uom,
                )
            data_schema = self.add_suggested_values_to_schema(
                data_schema, suggested_values
            )
        return self.async_show_form(
            step_id="options", data_schema=data_schema, errors=errors, last_step=False
        )

    async def async_step_use_picture(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Specify which input to get composite's picture from."""
        if user_input is not None:
            entity_id = user_input.get(CONF_ENTITY)
            for cfg in self.options[CONF_ENTITY_ID]:
                cfg[CONF_USE_PICTURE] = cfg[CONF_ENTITY] == entity_id
            return await self.async_step_all_states()

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_ENTITY): EntitySelector(
                    EntitySelectorConfig(include_entities=self._entity_ids)
                )
            }
        )
        picture_entity_id = None
        for cfg in self.options[CONF_ENTITY_ID]:
            if cfg[CONF_USE_PICTURE]:
                picture_entity_id = cfg[CONF_ENTITY]
                break
        if picture_entity_id:
            data_schema = self.add_suggested_values_to_schema(
                data_schema, {CONF_ENTITY: picture_entity_id}
            )
        return self.async_show_form(
            step_id="use_picture", data_schema=data_schema, last_step=False
        )

    async def async_step_all_states(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Specify if all states should be used for appropriate entities."""
        if user_input is not None:
            entity_ids = user_input.get(CONF_ENTITY, [])
            for cfg in self.options[CONF_ENTITY_ID]:
                cfg[CONF_ALL_STATES] = cfg[CONF_ENTITY] in entity_ids
            return await self.async_step_done()

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_ENTITY): EntitySelector(
                    EntitySelectorConfig(
                        include_entities=self._entity_ids, multiple=True
                    )
                )
            }
        )
        all_state_entities = [
            cfg[CONF_ENTITY]
            for cfg in self.options[CONF_ENTITY_ID]
            if cfg[CONF_ALL_STATES]
        ]
        if all_state_entities:
            data_schema = self.add_suggested_values_to_schema(
                data_schema, {CONF_ENTITY: all_state_entities}
            )
        return self.async_show_form(step_id="all_states", data_schema=data_schema)

    @abstractmethod
    async def async_step_done(self, _: dict[str, Any] | None = None) -> FlowResult:
        """Finish the flow."""


class CompositeConfigFlow(ConfigFlow, CompositeFlow, domain=DOMAIN):
    """Composite config flow."""

    VERSION = 1

    _name = ""

    def __init__(self) -> None:
        """Initialize config flow."""
        self._options: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CompositeOptionsFlow:
        """Get the options flow for this handler."""
        flow = CompositeOptionsFlow(config_entry)
        flow.init_step = "options"
        return flow

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: ConfigEntry) -> bool:
        """Return options flow support for this handler."""
        if config_entry.source == SOURCE_IMPORT:
            return False
        return True

    @property
    def options(self) -> dict[str, Any]:
        """Return mutable copy of options."""
        return self._options

    async def async_step_import(self, data: dict[str, Any]) -> FlowResult:
        """Import config entry from configuration."""
        if (driving_speed := data.get(CONF_DRIVING_SPEED)) is not None:
            data[CONF_DRIVING_SPEED] = SpeedConverter.convert(
                driving_speed, self._speed_uom, UnitOfSpeed.METERS_PER_SECOND
            )
        if existing_entry := await self.async_set_unique_id(data[CONF_ID]):
            self.hass.config_entries.async_update_entry(
                existing_entry, **split_conf(data)  # type: ignore[arg-type]
            )
            return self.async_abort(reason="already_configured")

        return self.async_create_entry(
            title=f"{data[CONF_NAME]} (from configuration)",
            **split_conf(data),  # type: ignore[arg-type]
        )

    async def async_step_user(self, _: dict[str, Any] | None = None) -> FlowResult:
        """Start user config flow."""
        return await self.async_step_name()

    def _name_used(self, name: str) -> bool:
        """Return if name has already been used."""
        for entry in self._entries:
            if entry.source == SOURCE_IMPORT:
                if name == entry.data[CONF_NAME]:
                    return True
            elif name == entry.title:
                return True
        return False

    async def async_step_name(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Get name."""
        errors = {}

        if user_input is not None:
            self._name = user_input[CONF_NAME]
            if not self._name_used(self._name):
                return await self.async_step_options()
            errors[CONF_NAME] = "name_used"

        data_schema = vol.Schema({vol.Required(CONF_NAME): TextSelector()})
        data_schema = self.add_suggested_values_to_schema(
            data_schema, {CONF_NAME: self._name}
        )
        return self.async_show_form(
            step_id="name", data_schema=data_schema, errors=errors, last_step=False
        )

    async def async_step_done(self, _: dict[str, Any] | None = None) -> FlowResult:
        """Finish the flow."""
        return self.async_create_entry(title=self._name, data={}, options=self.options)


class CompositeOptionsFlow(OptionsFlowWithConfigEntry, CompositeFlow):
    """Composite integration options flow."""

    async def async_step_done(self, _: dict[str, Any] | None = None) -> FlowResult:
        """Finish the flow."""
        return self.async_create_entry(title="", data=self.options)
