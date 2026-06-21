"""Config flow for Composite integration."""
from __future__ import annotations

from abc import abstractmethod
from collections import defaultdict
from datetime import datetime, timedelta
from functools import cached_property  # pylint: disable:hass-deprecated-import
import logging
from pathlib import Path
import shutil
from typing import Any, cast

import filetype
import voluptuous as vol

from homeassistant.components.binary_sensor import DOMAIN as BS_DOMAIN
from homeassistant.components.device_tracker import DOMAIN as DT_DOMAIN
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.components.recorder import get_instance, history
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
from homeassistant.helpers import entity_registry as er
import homeassistant.util.dt as dt_util
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

        # Create options with entity_id and friendly name
        entity_options = []
        for entity_id in sorted(include_entities):
            if state := self.hass.states.get(entity_id):
                friendly = state.attributes.get("friendly_name", entity_id)
                entity_options.append({
                    "value": entity_id,
                    "label": f"{friendly} ({entity_id})"
                })
            else:
                entity_options.append({
                    "value": entity_id,
                    "label": entity_id
                })

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ENTITY_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=entity_options,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
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

        menu_options = ["entity_diagnostics", "all_states", "ep_upload_file", "ep_input_entity"]
        if await self.hass.async_add_executor_job(self._local_files):
            menu_options.insert(2, "ep_local_file")
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


    async def async_step_entity_diagnostics(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show entity health diagnostics."""
        if user_input is not None:
            return await self.async_step_ep_menu()

        # Analyze entity health
        entity_stats = await self._analyze_entity_health()

        # Analyze entity correlations for suggestions
        suggestions = await self._analyze_entity_correlations()

        # Build diagnostic report
        report_lines = []
        report_lines.append("# Entity Health Report\n")

        # Add suggestions section if we have any
        if suggestions:
            report_lines.append("## Suggested Entities\n")
            report_lines.append("_These entities frequently transition at similar times as your configured trackers_\n")

            for suggestion in suggestions:
                score = suggestion["correlation_score"]
                entity_id = suggestion["entity_id"]
                friendly_name = suggestion["friendly_name"]

                # Determine confidence level and emoji
                if score >= 70:
                    confidence = "High"
                    emoji = "✅"
                elif score >= 50:
                    confidence = "Good"
                    emoji = "✅"
                else:
                    confidence = "Moderate"
                    emoji = "ℹ️"

                report_lines.append(f"\n### {emoji} {friendly_name}")
                report_lines.append(f"**Entity ID**: `{entity_id}`")
                report_lines.append(f"- **Correlation**: {score:.0f}% ({confidence} confidence)")
                report_lines.append(
                    f"- **Correlated Events**: {suggestion['correlated_events']} of {suggestion['total_events']} transitions"
                )
                report_lines.append(f"- **Updates (14d)**: {suggestion['update_count']}")
                report_lines.append(f"- **Avg Update Interval**: {suggestion['avg_interval']}")

                if suggestion['warnings']:
                    report_lines.append(f"- ⚠️ **Note**: {', '.join(suggestion['warnings'])}")

            report_lines.append("\n---\n")

        # Add configured entities health section
        report_lines.append("## Configured Entities Health\n")

        if not entity_stats:
            report_lines.append("No historical data available for analysis.\n")
        else:
            # Sort by reliability score
            sorted_entities = sorted(
                entity_stats.items(),
                key=lambda x: x[1]["reliability_score"],
                reverse=True
            )

            for entity_id, stats in sorted_entities:
                report_lines.append(f"\n### {entity_id}")

                # Add source information
                if stats.get('source_integration'):
                    report_lines.append(f"- **Source**: {stats['source_integration']}")
                if stats.get('source_type'):
                    report_lines.append(f"- **Source Type**: {stats['source_type']}")

                report_lines.append(f"- **Last Update**: {stats['last_update']}")
                report_lines.append(f"- **Update Count (7d)**: {stats['update_count']}")
                report_lines.append(f"- **Avg Update Interval**: {stats['avg_interval']}")
                report_lines.append(f"- **Reliability**: {stats['reliability_text']}")
                report_lines.append(f"- **Responsiveness**: {stats['responsiveness_text']}")
                report_lines.append(f"- **Stability**: {stats['stability_text']}")

                if stats['warnings']:
                    report_lines.append(f"- ⚠️ **Warnings**: {', '.join(stats['warnings'])}")

        diagnostic_text = "\n".join(report_lines)

        return self.async_show_form(
            step_id="entity_diagnostics",
            description_placeholders={"diagnostic_report": diagnostic_text},
            last_step=False,
        )

    async def _analyze_entity_health(self) -> dict[str, dict[str, Any]]:
        """Analyze health of tracked entities using historical data."""
        entity_ids = self._entity_ids
        if not entity_ids:
            return {}

        now = dt_util.utcnow()
        start_time = now - timedelta(days=7)

        # Get historical states for all entities
        entity_stats: dict[str, dict[str, Any]] = {}

        for entity_id in entity_ids:
            # Get source information from entity registry and current state
            source_integration = "Unknown"
            source_type = None

            # Check entity registry for integration
            entity_registry = er.async_get(self.hass)
            if entity_entry := entity_registry.async_get(entity_id):
                if entity_entry.platform:
                    source_integration = entity_entry.platform.replace("_", " ").title()

            # Check current state for source_type attribute
            if state := self.hass.states.get(entity_id):
                source_type = state.attributes.get("source_type")
                if source_type:
                    source_type = source_type.replace("_", " ").title()

            try:
                # Get state history for the last 7 days
                states = await get_instance(self.hass).async_add_executor_job(
                    history.state_changes_during_period,
                    self.hass,
                    start_time,
                    now,
                    entity_id
                )

                if not states or entity_id not in states:
                    entity_stats[entity_id] = {
                        "source_integration": source_integration,
                        "source_type": source_type,
                        "last_update": "Never",
                        "update_count": 0,
                        "avg_interval": "N/A",
                        "reliability_score": 0,
                        "reliability_text": "No data",
                        "responsiveness_text": "No data",
                        "stability_text": "No data",
                        "warnings": ["No historical data available"]
                    }
                    continue

                entity_states = states[entity_id]
                update_count = len(entity_states)

                if update_count == 0:
                    entity_stats[entity_id] = {
                        "source_integration": source_integration,
                        "source_type": source_type,
                        "last_update": "Never",
                        "update_count": 0,
                        "avg_interval": "N/A",
                        "reliability_score": 0,
                        "reliability_text": "No updates",
                        "responsiveness_text": "No updates",
                        "stability_text": "N/A",
                        "warnings": ["No updates in last 7 days"]
                    }
                    continue

                # Calculate statistics
                last_state = entity_states[-1]
                last_update = last_state.last_updated
                time_since_update = (now - last_update).total_seconds()

                # Calculate intervals between updates
                intervals = []
                for i in range(1, len(entity_states)):
                    interval = (entity_states[i].last_updated - entity_states[i-1].last_updated).total_seconds()
                    intervals.append(interval)

                avg_interval = sum(intervals) / len(intervals) if intervals else 0

                # Calculate volatility (location changes)
                location_changes = 0
                for i in range(1, len(entity_states)):
                    prev = entity_states[i-1]
                    curr = entity_states[i]
                    if prev.state != curr.state:
                        location_changes += 1
                    elif prev.attributes.get(ATTR_LATITUDE) != curr.attributes.get(ATTR_LATITUDE):
                        location_changes += 1

                # Scoring
                warnings = []

                # Staleness check
                hours_since_update = time_since_update / 3600
                if hours_since_update > 72:  # 3 days
                    warnings.append(f"Stale - no updates for {int(hours_since_update / 24)} days")
                    reliability_score = 0
                    reliability_text = "❌ Stale/Inactive"
                elif hours_since_update > 24:
                    warnings.append(f"Slow - last update {int(hours_since_update)} hours ago")
                    reliability_score = 30
                    reliability_text = "⚠️ Sluggish"
                elif update_count < 10:
                    reliability_score = 50
                    reliability_text = "⚠️ Limited data"
                else:
                    reliability_score = 100
                    reliability_text = "✅ Active"

                # Responsiveness (based on average interval)
                if avg_interval < 300:  # < 5 minutes
                    responsiveness_text = "✅ Very Responsive"
                elif avg_interval < 900:  # < 15 minutes
                    responsiveness_text = "✅ Responsive"
                elif avg_interval < 3600:  # < 1 hour
                    responsiveness_text = "⚠️ Moderate"
                else:
                    responsiveness_text = "❌ Sluggish"

                # Stability (volatility check)
                volatility_rate = location_changes / update_count if update_count > 0 else 0
                if volatility_rate > 0.8:
                    stability_text = "⚠️ Very volatile (yo-yoing)"
                    warnings.append("Frequent location changes detected")
                elif volatility_rate > 0.5:
                    stability_text = "⚠️ Moderately volatile"
                else:
                    stability_text = "✅ Stable"

                # Format average interval
                if avg_interval < 60:
                    avg_interval_text = f"{int(avg_interval)}s"
                elif avg_interval < 3600:
                    avg_interval_text = f"{int(avg_interval / 60)}m"
                else:
                    avg_interval_text = f"{avg_interval / 3600:.1f}h"

                entity_stats[entity_id] = {
                    "source_integration": source_integration,
                    "source_type": source_type,
                    "last_update": last_update.strftime("%Y-%m-%d %H:%M:%S"),
                    "update_count": update_count,
                    "avg_interval": avg_interval_text,
                    "reliability_score": reliability_score,
                    "reliability_text": reliability_text,
                    "responsiveness_text": responsiveness_text,
                    "stability_text": stability_text,
                    "warnings": warnings
                }

            except Exception as err:
                _LOGGER.error("Error analyzing entity %s: %s", entity_id, err)
                entity_stats[entity_id] = {
                    "source_integration": source_integration,
                    "source_type": source_type,
                    "last_update": "Error",
                    "update_count": 0,
                    "avg_interval": "N/A",
                    "reliability_score": 0,
                    "reliability_text": "Error",
                    "responsiveness_text": "Error",
                    "stability_text": "Error",
                    "warnings": [f"Analysis error: {str(err)}"]
                }

        return entity_stats

    async def _analyze_entity_correlations(
        self, correlation_window_minutes: int = 10, analysis_days: int = 14
    ) -> list[dict[str, Any]]:
        """Analyze correlations between configured entities and other device trackers.

        Finds device_tracker entities that frequently arrive/leave at similar times
        as the configured entities.

        Args:
            correlation_window_minutes: Time window in minutes to consider transitions as correlated
            analysis_days: Number of days of history to analyze

        Returns:
            List of suggested entities with correlation data, sorted by correlation score
        """
        configured_entity_ids = self._entity_ids
        if not configured_entity_ids:
            return []

        # Get all potential device tracker entities
        def entity_filter(state: State) -> bool:
            """Return if entity should be included in analysis."""
            if state.domain not in (BS_DOMAIN, DT_DOMAIN):
                # Check if it's a sensor with GPS data
                attributes = state.attributes
                if ATTR_GPS_ACCURACY not in attributes and ATTR_ACC not in attributes:
                    return False
                if ATTR_LATITUDE in attributes and ATTR_LONGITUDE in attributes:
                    return True
                return ATTR_LAT in attributes and ATTR_LON in attributes
            return True

        # Get all potential entities, excluding already configured ones
        all_potential_entities = {
            state.entity_id
            for state in filter(entity_filter, self.hass.states.async_all())
            if state.entity_id not in configured_entity_ids
        }

        if not all_potential_entities:
            return []

        now = dt_util.utcnow()
        start_time = now - timedelta(days=analysis_days)
        correlation_window = timedelta(minutes=correlation_window_minutes)

        # Get state transitions for configured entities
        configured_transitions: list[tuple[datetime, str]] = []

        try:
            for entity_id in configured_entity_ids:
                states = await get_instance(self.hass).async_add_executor_job(
                    history.state_changes_during_period,
                    self.hass,
                    start_time,
                    now,
                    entity_id
                )

                if states and entity_id in states:
                    entity_states = states[entity_id]
                    for i in range(1, len(entity_states)):
                        prev_state = entity_states[i-1]
                        curr_state = entity_states[i]

                        # Track significant state transitions
                        if prev_state.state != curr_state.state:
                            # Home/away transitions
                            if curr_state.state in ('home', 'not_home', 'away'):
                                configured_transitions.append(
                                    (curr_state.last_updated, curr_state.state)
                                )
                        else:
                            # GPS movement detection
                            prev_lat = prev_state.attributes.get(ATTR_LATITUDE)
                            curr_lat = curr_state.attributes.get(ATTR_LATITUDE)
                            prev_lon = prev_state.attributes.get(ATTR_LONGITUDE)
                            curr_lon = curr_state.attributes.get(ATTR_LONGITUDE)

                            if all([prev_lat, curr_lat, prev_lon, curr_lon]):
                                # Significant location change (rough approximation)
                                lat_diff = abs(curr_lat - prev_lat)
                                lon_diff = abs(curr_lon - prev_lon)
                                if lat_diff > 0.001 or lon_diff > 0.001:  # ~100m change
                                    configured_transitions.append(
                                        (curr_state.last_updated, 'movement')
                                    )

        except Exception as err:
            _LOGGER.error("Error analyzing configured entity transitions: %s", err)
            return []

        if not configured_transitions:
            _LOGGER.debug("No transitions found in configured entities")
            return []

        # Analyze each potential entity for correlation
        suggestions: list[dict[str, Any]] = []

        for candidate_id in all_potential_entities:
            try:
                states = await get_instance(self.hass).async_add_executor_job(
                    history.state_changes_during_period,
                    self.hass,
                    start_time,
                    now,
                    candidate_id
                )

                if not states or candidate_id not in states:
                    continue

                candidate_states = states[candidate_id]
                if len(candidate_states) < 5:  # Require minimum activity
                    continue

                # Extract transitions for candidate
                candidate_transitions: list[tuple[datetime, str]] = []

                for i in range(1, len(candidate_states)):
                    prev_state = candidate_states[i-1]
                    curr_state = candidate_states[i]

                    if prev_state.state != curr_state.state:
                        if curr_state.state in ('home', 'not_home', 'away'):
                            candidate_transitions.append(
                                (curr_state.last_updated, curr_state.state)
                            )
                    else:
                        # GPS movement
                        prev_lat = prev_state.attributes.get(ATTR_LATITUDE)
                        curr_lat = curr_state.attributes.get(ATTR_LATITUDE)
                        prev_lon = prev_state.attributes.get(ATTR_LONGITUDE)
                        curr_lon = curr_state.attributes.get(ATTR_LONGITUDE)

                        if all([prev_lat, curr_lat, prev_lon, curr_lon]):
                            lat_diff = abs(curr_lat - prev_lat)
                            lon_diff = abs(curr_lon - prev_lon)
                            if lat_diff > 0.001 or lon_diff > 0.001:
                                candidate_transitions.append(
                                    (curr_state.last_updated, 'movement')
                                )

                if not candidate_transitions:
                    continue

                # Calculate correlation
                correlated_count = 0
                total_configured = len(configured_transitions)

                # For each configured transition, check if candidate had a similar transition
                for config_time, config_type in configured_transitions:
                    for cand_time, cand_type in candidate_transitions:
                        time_diff = abs((cand_time - config_time).total_seconds())
                        if time_diff <= correlation_window.total_seconds():
                            # Bonus points for matching transition type
                            if config_type == cand_type:
                                correlated_count += 1
                            else:
                                correlated_count += 0.5
                            break  # Only count first match

                # Calculate correlation percentage
                correlation_score = (correlated_count / total_configured * 100) if total_configured > 0 else 0

                # Only include if correlation is significant
                if correlation_score >= 30:  # 30% minimum correlation
                    # Calculate additional metrics
                    update_count = len(candidate_states)
                    last_update = candidate_states[-1].last_updated
                    time_since_update = (now - last_update).total_seconds() / 3600  # hours

                    # Calculate average interval
                    intervals = []
                    for i in range(1, len(candidate_states)):
                        interval = (candidate_states[i].last_updated - candidate_states[i-1].last_updated).total_seconds()
                        intervals.append(interval)
                    avg_interval = sum(intervals) / len(intervals) if intervals else 0

                    # Format interval
                    if avg_interval < 60:
                        avg_interval_text = f"{int(avg_interval)}s"
                    elif avg_interval < 3600:
                        avg_interval_text = f"{int(avg_interval / 60)}m"
                    else:
                        avg_interval_text = f"{avg_interval / 3600:.1f}h"

                    # Determine quality warnings
                    warnings = []
                    if time_since_update > 72:
                        warnings.append("Currently stale")
                    elif time_since_update > 24:
                        warnings.append("Slow to update")
                    if avg_interval > 3600:
                        warnings.append("Infrequent updates")
                    if update_count < 20:
                        warnings.append("Limited data")

                    # Get friendly name
                    friendly_name = candidate_id
                    if state := self.hass.states.get(candidate_id):
                        friendly_name = state.attributes.get("friendly_name", candidate_id)

                    suggestions.append({
                        "entity_id": candidate_id,
                        "friendly_name": friendly_name,
                        "correlation_score": correlation_score,
                        "correlated_events": int(correlated_count),
                        "total_events": total_configured,
                        "update_count": update_count,
                        "avg_interval": avg_interval_text,
                        "time_since_update_hours": time_since_update,
                        "warnings": warnings
                    })

            except Exception as err:
                _LOGGER.debug("Error analyzing candidate %s: %s", candidate_id, err)
                continue

        # Sort by correlation score (descending)
        suggestions.sort(key=lambda x: x["correlation_score"], reverse=True)

        # Return top 10
        return suggestions[:10]

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
              vol.Optional(CONF_ENTITY): SelectSelector(
                  SelectSelectorConfig(
                      options=include_entities,
                      mode=SelectSelectorMode.DROPDOWN,
                  )
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
                )
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

        # Create options with entity_id and friendly name
        entity_options = []
        for entity_id in self._entity_ids:
            if state := self.hass.states.get(entity_id):
                friendly = state.attributes.get("friendly_name", entity_id)
                entity_options.append({
                    "value": entity_id,
                    "label": f"{friendly} ({entity_id})"
                })
            else:
                entity_options.append({
                    "value": entity_id,
                    "label": entity_id
                })

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_ENTITY): SelectSelector(
                    SelectSelectorConfig(
                        options=entity_options,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
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
