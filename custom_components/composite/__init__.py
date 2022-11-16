"""Composite Device Tracker."""
import asyncio
import logging

import voluptuous as vol

from homeassistant.config import load_yaml_config_file
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.requirements import async_process_requirements, RequirementsNotFound
from homeassistant.components.device_tracker import DOMAIN as DT_DOMAIN
from homeassistant.components.device_tracker.legacy import YAML_DEVICES
from homeassistant.components.persistent_notification import (
    async_create as pn_async_create,
)
from homeassistant.const import CONF_ID, CONF_NAME, CONF_PLATFORM

# Platform class did not exist before 2021.12
try:
    from homeassistant.const import Platform

    PLATFORMS = [Platform.DEVICE_TRACKER]
except ImportError:
    PLATFORMS = [DT_DOMAIN]

from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.util import slugify

from .const import (
    CONF_OPTS,
    CONF_TIME_AS,
    CONF_TRACKERS,
    DATA_LEGACY_WARNED,
    DATA_TF,
    DOMAIN,
    TZ_DEVICE_LOCAL,
    TZ_DEVICE_UTC,
)
from .device_tracker import COMPOSITE_TRACKER

CONF_TZ_FINDER = "tz_finder"
DEFAULT_TZ_FINDER = "timezonefinderL==4.0.2"
CONF_TZ_FINDER_CLASS = "tz_finder_class"
TZ_FINDER_CLASS_OPTS = ["TimezoneFinder", "TimezoneFinderL"]
TRACKER = COMPOSITE_TRACKER.copy()
TRACKER.update({vol.Required(CONF_NAME): cv.string, vol.Optional(CONF_ID): cv.slugify})


def _tracker_ids(value):
    """Determine tracker ID."""
    ids = []
    for conf in value:
        if CONF_ID not in conf:
            name = conf[CONF_NAME]
            if name == slugify(name):
                conf[CONF_ID] = name
                conf[CONF_NAME] = name.replace("_", " ").title()
            else:
                conf[CONF_ID] = cv.slugify(conf[CONF_NAME])
        ids.append(conf[CONF_ID])
    if len(ids) != len(set(ids)):
        raise vol.Invalid("id's must be unique")
    return value


CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN, default=dict): vol.Schema(
            {
                vol.Optional(CONF_TZ_FINDER, default=DEFAULT_TZ_FINDER): cv.string,
                vol.Optional(
                    CONF_TZ_FINDER_CLASS, default=TZ_FINDER_CLASS_OPTS[0]
                ): vol.In(TZ_FINDER_CLASS_OPTS),
                vol.Optional(CONF_TRACKERS, default=list): vol.All(
                    cv.ensure_list, [TRACKER], _tracker_ids
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass, config):
    hass.data[DOMAIN] = {DATA_LEGACY_WARNED: False}

    # Get a list of all the object IDs in known_devices.yaml to see if any were created
    # when this integration was a legacy device tracker, or would otherwise conflict
    # with IDs in our config.
    try:
        legacy_devices = await hass.async_add_executor_job(
            load_yaml_config_file, hass.config.path(YAML_DEVICES)
        )
    except (HomeAssistantError, FileNotFoundError):
        legacy_devices = {}
    try:
        legacy_ids = [
            cv.slugify(id)
            for id, dev in legacy_devices.items()
            if cv.boolean(dev.get("track", False))
        ]
    except vol.Invalid:
        legacy_ids = []

    # Get all existing composite config entries.
    cfg_entries = {
        entry.data[CONF_ID]: entry
        for entry in hass.config_entries.async_entries(DOMAIN)
    }

    # For each tracker config, see if it conflicts with a known_devices.yaml entry.
    # If not, update the config entry if one already exists for it in case the config
    # has changed, or create a new config entry if one did not already exist.
    tracker_configs = config[DOMAIN][CONF_TRACKERS]
    conflict_ids = []
    for conf in tracker_configs:
        # These go in the "static" data field.
        id = conf[CONF_ID]
        name = conf[CONF_NAME]
        # These go in the options field, which can be updated via the UI (once support
        # for that is added.)
        options = {k: v for k, v in conf.items() if k in CONF_OPTS}

        if id in legacy_ids:
            conflict_ids.append(id)
        elif id in cfg_entries:
            hass.config_entries.async_update_entry(
                cfg_entries[id], data={CONF_NAME: name, CONF_ID: id}, options=options
            )
        else:

            async def create_config(conf, options):
                """Create new config entry."""
                result = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
                )
                # Versions prior to 2021.6 did not support creating with options, so
                # update the created config entry with the options.
                hass.config_entries.async_update_entry(
                    result["result"], options=options
                )

            hass.async_create_task(create_config(conf, options))

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
        conf for conf in (config.get(DT_DOMAIN) or []) if conf[CONF_PLATFORM] == DOMAIN
    ]
    if any(
        conf[CONF_TIME_AS] in (TZ_DEVICE_UTC, TZ_DEVICE_LOCAL)
        for conf in tracker_configs + legacy_configs
    ):
        pkg = config[DOMAIN][CONF_TZ_FINDER]
        try:
            await async_process_requirements(hass, f"{DOMAIN}.{DT_DOMAIN}", [pkg])
        except RequirementsNotFound:
            _LOGGER.debug("Process requirements failed: %s", pkg)
            return False
        else:
            _LOGGER.debug("Process requirements suceeded: %s", pkg)

        if pkg.split("==")[0].strip().endswith("L"):
            from timezonefinderL import TimezoneFinder

            tf = TimezoneFinder()
        elif config[DOMAIN][CONF_TZ_FINDER_CLASS] == "TimezoneFinder":
            from timezonefinder import TimezoneFinder

            tf = TimezoneFinder()
        else:
            from timezonefinder import TimezoneFinderL

            tf = TimezoneFinderL()
        hass.data[DOMAIN][DATA_TF] = tf

    return True


async def async_setup_entry(hass, entry):
    """Set up config entry."""
    # async_forward_entry_setups was new in 2022.8
    if hasattr(hass.config_entries, "async_forward_entry_setups"):
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # async_setup_platforms was new in 2021.5
    elif hasattr(hass.config_entries, "async_setup_platforms"):
        hass.config_entries.async_setup_platforms(entry, PLATFORMS)
    else:
        for platform in PLATFORMS:
            hass.async_create_task(
                hass.config_entries.async_forward_entry_setup(entry, platform)
            )
    return True


async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    # async_unload_platforms was new in 2021.5
    if hasattr(hass.config_entries, "async_unload_platforms"):
        return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    else:
        return all(
            await asyncio.gather(
                *(
                    hass.config_entries.async_forward_entry_unload(entry, platform)
                    for platform in PLATFORMS
                )
            )
        )
