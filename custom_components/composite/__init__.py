"""Composite Device Tracker."""
from __future__ import annotations

import logging
from typing import Any, cast

import voluptuous as vol

from homeassistant.components.device_tracker import DOMAIN as DT_DOMAIN
from homeassistant.components.device_tracker.legacy import YAML_DEVICES
from homeassistant.components.persistent_notification import (
    async_create as pn_async_create,
)
from homeassistant.config import load_yaml_config_file
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_ID, CONF_NAME, CONF_PLATFORM, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.requirements import RequirementsNotFound, async_process_requirements
from homeassistant.util import slugify

from .config_flow import split_conf
from .const import (
    CONF_DEFAULT_OPTIONS,
    CONF_REQ_MOVEMENT,
    CONF_TIME_AS,
    CONF_TRACKERS,
    DATA_LEGACY_WARNED,
    DATA_TF,
    DEF_REQ_MOVEMENT,
    DEF_TIME_AS,
    DOMAIN,
    TIME_AS_OPTS,
    TZ_DEVICE_LOCAL,
    TZ_DEVICE_UTC,
)
from .device_tracker import COMPOSITE_TRACKER

CONF_TZ_FINDER = "tz_finder"
DEFAULT_TZ_FINDER = "timezonefinder==5.2.0"
CONF_TZ_FINDER_CLASS = "tz_finder_class"
PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR]
TZ_FINDER_CLASS_OPTS = ["TimezoneFinder", "TimezoneFinderL"]
TRACKER = COMPOSITE_TRACKER.copy()
TRACKER.update({vol.Required(CONF_NAME): cv.string, vol.Optional(CONF_ID): cv.slugify})


def _tracker_ids(
    value: list[dict[vol.Required | vol.Optional, Any]]
) -> list[dict[vol.Required | vol.Optional, Any]]:
    """Determine tracker ID."""
    ids: list[str] = []
    for conf in value:
        if CONF_ID not in conf:
            name: str = conf[CONF_NAME]
            if name == slugify(name):
                conf[CONF_ID] = name
                conf[CONF_NAME] = name.replace("_", " ").title()
            else:
                conf[CONF_ID] = cv.slugify(conf[CONF_NAME])
        ids.append(cast(str, conf[CONF_ID]))
    if len(ids) != len(set(ids)):
        raise vol.Invalid("id's must be unique")
    return value


def _defaults(config: dict) -> dict:
    """Apply default options to trackers."""
    def_time_as = config[CONF_DEFAULT_OPTIONS][CONF_TIME_AS]
    def_req_mv = config[CONF_DEFAULT_OPTIONS][CONF_REQ_MOVEMENT]
    for tracker in config[CONF_TRACKERS]:
        tracker[CONF_TIME_AS] = tracker.get(CONF_TIME_AS, def_time_as)
        tracker[CONF_REQ_MOVEMENT] = tracker.get(CONF_REQ_MOVEMENT, def_req_mv)
    return config


CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN, default=dict): vol.All(
            vol.Schema(
                {
                    vol.Optional(CONF_TZ_FINDER, default=DEFAULT_TZ_FINDER): cv.string,
                    vol.Optional(
                        CONF_TZ_FINDER_CLASS, default=TZ_FINDER_CLASS_OPTS[1]
                    ): vol.In(TZ_FINDER_CLASS_OPTS),
                    vol.Optional(CONF_DEFAULT_OPTIONS, default=dict): vol.Schema(
                        {
                            vol.Optional(CONF_TIME_AS, default=DEF_TIME_AS): vol.In(
                                TIME_AS_OPTS
                            ),
                            vol.Optional(
                                CONF_REQ_MOVEMENT, default=DEF_REQ_MOVEMENT
                            ): cv.boolean,
                        }
                    ),
                    vol.Optional(CONF_TRACKERS, default=list): vol.All(
                        cv.ensure_list, [TRACKER], _tracker_ids
                    ),
                }
            ),
            _defaults,
        )
    },
    extra=vol.ALLOW_EXTRA,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up composite integration."""
    hass.data[DOMAIN] = {DATA_LEGACY_WARNED: False}

    # Get a list of all the object IDs in known_devices.yaml to see if any were created
    # when this integration was a legacy device tracker, or would otherwise conflict
    # with IDs in our config.
    try:
        legacy_devices: dict[str, dict] = await hass.async_add_executor_job(
            load_yaml_config_file, hass.config.path(YAML_DEVICES)
        )
    except (HomeAssistantError, FileNotFoundError):
        legacy_devices = {}
    try:
        legacy_ids = [
            cv.slugify(obj_id)
            for obj_id, dev in legacy_devices.items()
            if cv.boolean(dev.get("track", False))
        ]
    except vol.Invalid:
        legacy_ids = []

    # Get all existing composite config entries.
    cfg_entries = {
        cast(str, entry.data[CONF_ID]): entry
        for entry in hass.config_entries.async_entries(DOMAIN)
    }

    # For each tracker config, see if it conflicts with a known_devices.yaml entry.
    # If not, update the config entry if one already exists for it in case the config
    # has changed, or create a new config entry if one did not already exist.
    tracker_configs: list[dict[str, Any]] = config[DOMAIN][CONF_TRACKERS]
    conflict_ids: list[str] = []
    for conf in tracker_configs:
        obj_id: str = conf[CONF_ID]

        if obj_id in legacy_ids:
            conflict_ids.append(obj_id)
        elif obj_id in cfg_entries:
            hass.config_entries.async_update_entry(
                cfg_entries[obj_id], **split_conf(conf)  # type: ignore[arg-type]
            )
        else:
            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
                )
            )

    if conflict_ids:
        _LOGGER.warning("%s in %s: skipping", ", ".join(conflict_ids), YAML_DEVICES)
        if len(conflict_ids) == 1:
            msg1 = "ID was"
            msg2 = "conflicts"
        else:
            msg1 = "IDs were"
            msg2 = "conflict"
        pn_async_create(
            hass,
            title="Conflicting IDs",
            message=f"The following {msg1} found in {YAML_DEVICES}"
            f" which {msg2} with the configuration of the {DOMAIN} integration."
            " Please remove from one or the other."
            f"\n\n{', '.join(conflict_ids)}",
        )

    legacy_configs = [
        conf
        for conf in cast(list[dict[str, Any]], config.get(DT_DOMAIN) or [])
        if conf[CONF_PLATFORM] == DOMAIN
    ]
    # Note that CONF_TIME_AS may not be in legacy configs.
    if any(
        conf.get(CONF_TIME_AS, DEF_TIME_AS) in (TZ_DEVICE_UTC, TZ_DEVICE_LOCAL)
        for conf in tracker_configs + legacy_configs
    ):
        pkg: str = config[DOMAIN][CONF_TZ_FINDER]
        try:
            await async_process_requirements(hass, f"{DOMAIN}.{DT_DOMAIN}", [pkg])
        except RequirementsNotFound:
            _LOGGER.debug("Process requirements failed: %s", pkg)
            return False
        _LOGGER.debug("Process requirements suceeded: %s", pkg)

        def create_timefinder() -> None:
            """Create timefinder object."""

            # This must be done in an executor since the timefinder constructor
            # does file I/O.

            if pkg.split("==")[0].strip().endswith("L"):
                from timezonefinderL import (  # pylint: disable=import-outside-toplevel
                    TimezoneFinder,
                )

                tf = TimezoneFinder()
            elif config[DOMAIN][CONF_TZ_FINDER_CLASS] == "TimezoneFinder":
                from timezonefinder import (  # pylint: disable=import-outside-toplevel
                    TimezoneFinder,
                )

                tf = TimezoneFinder()
            else:
                from timezonefinder import (  # pylint: disable=import-outside-toplevel
                    TimezoneFinderL,
                )

                tf = TimezoneFinderL()
            hass.data[DOMAIN][DATA_TF] = tf

        await hass.async_add_executor_job(create_timefinder)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
