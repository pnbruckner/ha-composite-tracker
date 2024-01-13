"""Config flow for Composite integration."""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

import voluptuous as vol

from homeassistant.backports.functools import cached_property
from homeassistant.config_entries import (
    SOURCE_IMPORT,
    ConfigEntry,
    ConfigFlow,
    OptionsFlowWithConfigEntry,
)
from homeassistant.const import CONF_ENTITY_ID, CONF_ID, CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowHandler, FlowResult
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
)

from .const import (
    CONF_ALL_STATES,
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
            ("options", (CONF_ENTITY_ID, CONF_REQ_MOVEMENT)),
        )
    }


class CompositeFlow(FlowHandler):
    """Composite flow mixin."""

    @cached_property
    def _entries(self) -> list[ConfigEntry]:
        """Get existing config entries."""
        return self.hass.config_entries.async_entries(DOMAIN)

    @property
    @abstractmethod
    def options(self) -> dict[str, Any]:
        """Return mutable copy of options."""

    @property
    def _entity_ids(self) -> list[str]:
        """Get currently configured entity IDs."""
        return [cfg[CONF_ENTITY] for cfg in self.options[CONF_ENTITY_ID]]

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Get config options."""
        errors = {}

        if user_input is not None:
            self.options[CONF_REQ_MOVEMENT] = user_input[CONF_REQ_MOVEMENT]
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

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ENTITY_ID): EntitySelector(
                    EntitySelectorConfig(
                        domain=["binary_sensor", "device_tracker"],
                        multiple=True,
                    )
                ),
                vol.Required(CONF_REQ_MOVEMENT): BooleanSelector(),
            }
        )
        if CONF_ENTITY_ID in self.options:
            data_schema = self.add_suggested_values_to_schema(
                data_schema,
                {
                    CONF_ENTITY_ID: self._entity_ids,
                    CONF_REQ_MOVEMENT: self.options[CONF_REQ_MOVEMENT],
                },
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
            for cfg in self.options[CONF_ENTITY_ID]:
                cfg[CONF_ALL_STATES] = cfg[CONF_ENTITY] in user_input[CONF_ENTITY]
            return await self.async_step_done()

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ENTITY): EntitySelector(
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
