"""Config flow for Composite integration."""
from __future__ import annotations

from abc import abstractmethod
from functools import cached_property  # pylint: disable=hass-deprecated-import
import logging
from pathlib import Path
import shutil
from typing import Any, cast

import filetype
import voluptuous as vol

from homeassistant.components.binary_sensor import DOMAIN as BS_DOMAIN
from homeassistant.components.device_tracker import DOMAIN as DT_DOMAIN
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import (
    SOURCE_IMPORT,
    ConfigEntry,
    ConfigEntryBaseFlow,
    ConfigFlow,
    ConfigFlowResult,
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
from homeassistant.helpers.selector import (
    BooleanSelector,
    DurationSelector,
    DurationSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    FileSelector,
    FileSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
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
    CONF_END_DRIVING_DELAY,
    CONF_ENTITY,
    CONF_ENTITY_PICTURE,
    CONF_MAX_SPEED_AGE,
    CONF_REQ_MOVEMENT,
    CONF_SHOW_UNKNOWN_AS_0,
    CONF_USE_PICTURE,
    DOMAIN,
    MIME_TO_SUFFIX,
    PICTURE_SUFFIXES,
)

_LOGGER = logging.getLogger(__name__)


def split_conf(conf: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return pieces of configuration data."""
    return {
        kw: {k: v for k, v in conf.items() if k in ks}
        for kw, ks in (
            ("data", (CONF_NAME, CONF_ID)),
            (
                "options",
                (
                    CONF_ENTITY_ID,
                    CONF_REQ_MOVEMENT,
                    CONF_MAX_SPEED_AGE,
                    CONF_SHOW_UNKNOWN_AS_0,
                    CONF_DRIVING_SPEED,
                    CONF_END_DRIVING_DELAY,
                    CONF_ENTITY_PICTURE,
                ),
            ),
        )
    }


class CompositeFlow(ConfigEntryBaseFlow):
    """Composite flow mixin."""

    @cached_property
    def _entries(self) -> list[ConfigEntry]:
        """Get existing config entries."""
        return self.hass.config_entries.async_entries(DOMAIN)

    @cached_property
    def _local_dir(self) -> Path:
        """Return real path to "/local" directory."""
        return Path(self.hass.config.path("www"))

    @cached_property
    def _uploaded_dir(self) -> Path:
        """Return real path to "/local/uploaded" directory."""
        return self._local_dir / "uploaded"

    def _local_files(self) -> list[str]:
        """Return a list of files in "/local" and subdirectories.

        Must be called in an executor since it does file I/O.
        """
        if not (local_dir := self._local_dir).is_dir():
            _LOGGER.debug("/local directory (%s) does not exist", local_dir)
            return []

        local_files: list[str] = []
        for suffix in PICTURE_SUFFIXES:
            local_files.extend(
                [
                    str(local_file.relative_to(local_dir))
                    for local_file in local_dir.rglob(f"*.{suffix}")
                ]
            )
        return sorted(local_files)

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

    @property
    def _cur_entity_picture(self) -> tuple[str | None, str | None]:
        """Return current entity picture source.

        Returns: (entity_id, local_file)

        local_file is relative to "/local".
        """
        entity_id = None
        for cfg in self.options[CONF_ENTITY_ID]:
            if cfg[CONF_USE_PICTURE]:
                entity_id = cfg[CONF_ENTITY]
                break
        if local_file := cast(str | None, self.options.get(CONF_ENTITY_PICTURE)):
            local_file = local_file.removeprefix("/local/")
        return entity_id, local_file

    def _set_entity_picture(
        self, *, entity_id: str | None = None, local_file: str | None = None
    ) -> None:
        """Set composite's entity picture source.

        local_file is relative to "/local".
        """
        for cfg in self.options[CONF_ENTITY_ID]:
            cfg[CONF_USE_PICTURE] = cfg[CONF_ENTITY] == entity_id
        if local_file:
            self.options[CONF_ENTITY_PICTURE] = f"/local/{local_file}"
        elif CONF_ENTITY_PICTURE in self.options:
            del self.options[CONF_ENTITY_PICTURE]

    def _save_uploaded_file(self, uploaded_file_id: str) -> str:
        """Save uploaded file.

        Must be called in an executor since it does file I/O.

        Returns name of file relative to "/local".
        """
        with process_uploaded_file(self.hass, uploaded_file_id) as uf_path:
            ud = self._uploaded_dir
            ud.mkdir(parents=True, exist_ok=True)
            suffix = MIME_TO_SUFFIX[cast(str, filetype.guess_mime(uf_path))]
            fn = ud / f"x.{suffix}"
            idx = 0
            while (uf := fn.with_stem(f"image{idx:03d}")).exists():
                idx += 1
            shutil.move(uf_path, uf)
            return str(uf.relative_to(self._local_dir))

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Get config options."""
        errors = {}

        if user_input is not None:
            self.options[CONF_REQ_MOVEMENT] = user_input[CONF_REQ_MOVEMENT]
            if user_input[CONF_SHOW_UNKNOWN_AS_0]:
                self.options[CONF_SHOW_UNKNOWN_AS_0] = True
            elif CONF_SHOW_UNKNOWN_AS_0 in self.options:
                # For backward compatibility, represent False as the absence of the
                # option.
                del self.options[CONF_SHOW_UNKNOWN_AS_0]
            if CONF_MAX_SPEED_AGE in user_input:
                self.options[CONF_MAX_SPEED_AGE] = user_input[CONF_MAX_SPEED_AGE]
            elif CONF_MAX_SPEED_AGE in self.options:
                del self.options[CONF_MAX_SPEED_AGE]
            if CONF_DRIVING_SPEED in user_input:
                self.options[CONF_DRIVING_SPEED] = SpeedConverter.convert(
                    user_input[CONF_DRIVING_SPEED],
                    self._speed_uom,
                    UnitOfSpeed.METERS_PER_SECOND,
                )
            else:
                if CONF_DRIVING_SPEED in self.options:
                    del self.options[CONF_DRIVING_SPEED]
                if CONF_END_DRIVING_DELAY in self.options:
                    del self.options[CONF_END_DRIVING_DELAY]
            prv_cfgs = {
                cfg[CONF_ENTITY]: cfg for cfg in self.options.get(CONF_ENTITY_ID, [])
            }
            new_cfgs = [
                prv_cfgs.get(
                    entity_id,
                    {
                        CONF_ENTITY: entity_id,
                        CONF_USE_PICTURE: False,
                        CONF_ALL_STATES: False,
                    },
                )
                for entity_id in user_input[CONF_ENTITY_ID]
            ]
            self.options[CONF_ENTITY_ID] = new_cfgs
            if new_cfgs:
                if CONF_DRIVING_SPEED in self.options:
                    return await self.async_step_end_driving_delay()
                return await self.async_step_ep_menu()
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
                vol.Required(CONF_SHOW_UNKNOWN_AS_0): BooleanSelector(),
                vol.Optional(CONF_MAX_SPEED_AGE): DurationSelector(
                    DurationSelectorConfig(
                        enable_day=False, enable_millisecond=False, allow_negative=False
                    )
                ),
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
                CONF_SHOW_UNKNOWN_AS_0: self.options.get(CONF_SHOW_UNKNOWN_AS_0, False),
            }
            if CONF_MAX_SPEED_AGE in self.options:
                suggested_values[CONF_MAX_SPEED_AGE] = self.options[CONF_MAX_SPEED_AGE]
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

    async def async_step_end_driving_delay(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Get end driving delay."""
        if user_input is not None:
            if CONF_END_DRIVING_DELAY in user_input:
                self.options[CONF_END_DRIVING_DELAY] = user_input[
                    CONF_END_DRIVING_DELAY
                ]
            elif CONF_END_DRIVING_DELAY in self.options:
                del self.options[CONF_END_DRIVING_DELAY]
            return await self.async_step_ep_menu()

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_END_DRIVING_DELAY): DurationSelector(
                    DurationSelectorConfig(
                        enable_day=False, enable_millisecond=False, allow_negative=False
                    )
                ),
            }
        )
        if CONF_END_DRIVING_DELAY in self.options:
            suggested_values = {
                CONF_END_DRIVING_DELAY: self.options[CONF_END_DRIVING_DELAY]
            }
            data_schema = self.add_suggested_values_to_schema(
                data_schema, suggested_values
            )
        return self.async_show_form(
            step_id="end_driving_delay", data_schema=data_schema, last_step=False
        )

    async def async_step_ep_menu(
        self, _: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Specify where to get composite's picture from."""
        entity_id, local_file = self._cur_entity_picture
        cur_source: Path | str | None
        if local_file:
            cur_source = self._local_dir / local_file
        else:
            cur_source = entity_id

        menu_options = ["all_states", "ep_upload_file", "ep_input_entity"]
        if await self.hass.async_add_executor_job(self._local_files):
            menu_options.insert(1, "ep_local_file")
        if cur_source:
            menu_options.append("ep_none")

        return self.async_show_menu(
            step_id="ep_menu",
            menu_options=menu_options,
            description_placeholders={"cur_source": str(cur_source)},
        )

    async def async_step_ep_input_entity(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Specify which input to get composite's picture from."""
        if user_input is not None:
            self._set_entity_picture(entity_id=user_input.get(CONF_ENTITY))
            return await self.async_step_all_states()

        include_entities = self._entity_ids
        data_schema = vol.Schema(
            {
                vol.Optional(CONF_ENTITY): EntitySelector(
                    EntitySelectorConfig(include_entities=include_entities)
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
            step_id="ep_input_entity", data_schema=data_schema, last_step=False
        )

    async def async_step_ep_local_file(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Specify a local file for composite's picture."""
        if user_input is not None:
            self._set_entity_picture(local_file=user_input.get(CONF_ENTITY_PICTURE))
            return await self.async_step_all_states()

        local_files = await self.hass.async_add_executor_job(self._local_files)
        _, local_file = self._cur_entity_picture
        if local_file and local_file not in local_files:
            local_files.append(local_file)
        data_schema = vol.Schema(
            {
                vol.Optional(CONF_ENTITY_PICTURE): SelectSelector(
                    SelectSelectorConfig(
                        options=local_files,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        if local_file:
            data_schema = self.add_suggested_values_to_schema(
                data_schema, {CONF_ENTITY_PICTURE: local_file}
            )
        return self.async_show_form(
            step_id="ep_local_file", data_schema=data_schema, last_step=False
        )

    async def async_step_ep_upload_file(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Upload a file for composite's picture."""
        if user_input is not None:
            if (uploaded_file_id := user_input.get(CONF_ENTITY_PICTURE)) is None:
                self._set_entity_picture()
                return await self.async_step_all_states()

            def save_uploaded_file() -> tuple[bool, str]:
                """Save uploaded file.

                Must be called in an executor since it does file I/O.

                Returns if local directory existed beforehand and name of uploaded file.
                """
                local_dir_exists = self._local_dir.is_dir()
                local_file = self._save_uploaded_file(uploaded_file_id)
                return local_dir_exists, local_file

            local_dir_exists, local_file = await self.hass.async_add_executor_job(
                save_uploaded_file
            )
            self._set_entity_picture(local_file=local_file)
            if not local_dir_exists:
                return await self.async_step_ep_warn()
            return await self.async_step_all_states()

        accept = ", ".join(f".{ext}" for ext in PICTURE_SUFFIXES)
        data_schema = vol.Schema(
            {
                vol.Optional(CONF_ENTITY_PICTURE): FileSelector(
                    FileSelectorConfig(accept=accept)
                )
            }
        )
        return self.async_show_form(
            step_id="ep_upload_file", data_schema=data_schema, last_step=False
        )

    async def async_step_ep_warn(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Warn that since "/local" was created system might need to be restarted."""
        if user_input is not None:
            return await self.async_step_all_states()

        return self.async_show_form(
            step_id="ep_warn",
            description_placeholders={"local_dir": str(self._local_dir)},
            last_step=False,
        )

    async def async_step_ep_none(
        self, _: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set composite's entity picture to none."""
        self._set_entity_picture()
        return await self.async_step_all_states()

    async def async_step_all_states(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
    async def async_step_done(
        self, _: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
        return config_entry.source != SOURCE_IMPORT

    @property
    def options(self) -> dict[str, Any]:
        """Return mutable copy of options."""
        return self._options

    async def async_step_import(self, data: dict[str, Any]) -> ConfigFlowResult:
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

    async def async_step_user(
        self, _: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
    ) -> ConfigFlowResult:
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

    async def async_step_done(
        self, _: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Finish the flow."""
        return self.async_create_entry(title=self._name, data={}, options=self.options)


class CompositeOptionsFlow(OptionsFlowWithConfigEntry, CompositeFlow):
    """Composite integration options flow."""

    async def async_step_done(
        self, _: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Finish the flow."""
        return self.async_create_entry(title="", data=self.options)
