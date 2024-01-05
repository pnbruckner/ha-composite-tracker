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
from homeassistant.const import CONF_ENTITY_ID, CONF_ID, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify

from .config_flow import split_conf
from .const import (
    CONF_ALL_STATES,
    CONF_DEFAULT_OPTIONS,
    CONF_ENTITY,
    CONF_REQ_MOVEMENT,
    CONF_TIME_AS,
    CONF_TRACKERS,
    CONF_USE_PICTURE,
    DEF_REQ_MOVEMENT,
    DOMAIN,
)

CONF_TZ_FINDER = "tz_finder"
CONF_TZ_FINDER_CLASS = "tz_finder_class"
PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR]


def _entities(entities: list[str | dict]) -> list[dict]:
    """Convert entity ID to dict of entity & all_states."""
    result: list[dict] = []
    already_using_picture = False
    for idx, entity in enumerate(entities):
        if isinstance(entity, dict):
            if entity[CONF_USE_PICTURE]:
                if already_using_picture:
                    raise vol.Invalid(
                        f"{CONF_USE_PICTURE} may only be true for one entity per "
                        "composite tracker",
                        path=[idx, CONF_USE_PICTURE],
                    )
                already_using_picture = True
            result.append(entity)
        else:
            result.append(
                {CONF_ENTITY: entity, CONF_ALL_STATES: False, CONF_USE_PICTURE: False}
            )
    return result


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
    """Apply default options to trackers.

    Also warn about options no longer supported.
    """
    unsupported_cfgs = set()
    if config.pop(CONF_TZ_FINDER, None):
        unsupported_cfgs.add(CONF_TZ_FINDER)
    if config.pop(CONF_TZ_FINDER_CLASS, None):
        unsupported_cfgs.add(CONF_TZ_FINDER_CLASS)
    if config[CONF_DEFAULT_OPTIONS].pop(CONF_TIME_AS, None):
        unsupported_cfgs.add(CONF_TIME_AS)

    def_req_mv = config[CONF_DEFAULT_OPTIONS][CONF_REQ_MOVEMENT]
    for tracker in config[CONF_TRACKERS]:
        if tracker.pop(CONF_TIME_AS, None):
            unsupported_cfgs.add(CONF_TIME_AS)
        tracker[CONF_REQ_MOVEMENT] = tracker.get(CONF_REQ_MOVEMENT, def_req_mv)

    if unsupported_cfgs:
        _LOGGER.warning(
            "Your %s configuration contains options that are no longer supported: %s; "
            "Please remove them",
            DOMAIN,
            ", ".join(sorted(unsupported_cfgs)),
        )

    return config


ENTITIES = vol.All(
    cv.ensure_list,
    [
        vol.Any(
            cv.entity_id,
            vol.Schema(
                {
                    vol.Required(CONF_ENTITY): cv.entity_id,
                    vol.Optional(CONF_ALL_STATES, default=False): cv.boolean,
                    vol.Optional(CONF_USE_PICTURE, default=False): cv.boolean,
                }
            ),
        )
    ],
    vol.Length(1),
    _entities,
)
TRACKER = {
    vol.Required(CONF_NAME): cv.string,
    vol.Optional(CONF_ID): cv.slugify,
    vol.Required(CONF_ENTITY_ID): ENTITIES,
    vol.Optional(CONF_TIME_AS): cv.string,
    vol.Optional(CONF_REQ_MOVEMENT): cv.boolean,
}
CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN, default=dict): vol.All(
            vol.Schema(
                {
                    vol.Optional(CONF_TZ_FINDER): cv.string,
                    vol.Optional(CONF_TZ_FINDER_CLASS): cv.string,
                    vol.Optional(CONF_DEFAULT_OPTIONS, default=dict): vol.Schema(
                        {
                            vol.Optional(CONF_TIME_AS): cv.string,
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
    conflict_ids: set[str] = set()
    tracker_ids: set[str] = set()
    for conf in tracker_configs:
        obj_id: str = conf[CONF_ID]
        tracker_ids.add(obj_id)

        if obj_id in legacy_ids:
            conflict_ids.add(obj_id)
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
    for obj_id, entry in cfg_entries.items():
        if entry.source == SOURCE_IMPORT and obj_id not in (tracker_ids - conflict_ids):
            _LOGGER.warning(
                "Removing %s (%s) because it is no longer in YAML configuration",
                entry.data[CONF_NAME],
                f"{DT_DOMAIN}.{entry.data[CONF_ID]}",
            )
            hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))

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

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
