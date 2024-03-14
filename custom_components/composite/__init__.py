"""Composite Device Tracker."""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from contextlib import suppress
import logging
from pathlib import Path
from typing import Any, cast

import voluptuous as vol

from homeassistant.components.device_tracker import DOMAIN as DT_DOMAIN
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    CONF_ENTITY_ID,
    CONF_ID,
    CONF_NAME,
    SERVICE_RELOAD,
    Platform,
)
from homeassistant.core import HomeAssistant, ServiceCall, async_get_hass
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.reload import async_integration_yaml_config
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify

from .const import (
    CONF_ALL_STATES,
    CONF_DEFAULT_OPTIONS,
    CONF_DRIVING_SPEED,
    CONF_ENTITY,
    CONF_ENTITY_PICTURE,
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
    """Convert entity ID to dict of entity, all_states & use_picture.

    Also ensure no more than one entity has use_picture set to true.
    """
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


def _entity_picture(entity_picture: str) -> str:
    """Validate entity picture.

    Can be an URL or a file in "/local".

    file can be "/local/file" or just "file"

    Returns URL or "/local/file"
    """
    with suppress(vol.Invalid):
        return cv.url(entity_picture)

    local_dir = Path("/local")
    local_file = Path(entity_picture)
    with suppress(ValueError):
        local_file = local_file.relative_to(local_dir)
    if not (Path(async_get_hass().config.path("www")) / local_file).is_file():
        raise vol.Invalid(f"{entity_picture} does not exist")
    return str(local_dir / local_file)


def _trackers(
    trackers: list[dict[vol.Required | vol.Optional, Any]]
) -> list[dict[vol.Required | vol.Optional, Any]]:
    """Validate tracker entries.

    Determine tracker IDs and ensure they are unique.
    Also for each tracker, check that no entity has use_picture set if an entity_picture
    file is specified for tracker.
    """
    ids: list[str] = []
    for t_idx, tracker in enumerate(trackers):
        if CONF_ID not in tracker:
            name: str = tracker[CONF_NAME]
            if name == slugify(name):
                tracker[CONF_ID] = name
                tracker[CONF_NAME] = name.replace("_", " ").title()
            else:
                tracker[CONF_ID] = cv.slugify(tracker[CONF_NAME])
        ids.append(cast(str, tracker[CONF_ID]))
        if tracker.get(CONF_ENTITY_PICTURE):
            for e_idx, entity in enumerate(tracker[CONF_ENTITY_ID]):
                if entity[CONF_USE_PICTURE]:
                    raise vol.Invalid(
                        f"{CONF_ENTITY_PICTURE} specified; "
                        f"cannot use {CONF_USE_PICTURE}",
                        path=[t_idx, CONF_ENTITY_ID, e_idx, CONF_USE_PICTURE],
                    )
    if len(ids) != len(set(ids)):
        raise vol.Invalid("id's must be unique")
    return trackers


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
    def_drv_sp = config[CONF_DEFAULT_OPTIONS].get(CONF_DRIVING_SPEED)
    for tracker in config[CONF_TRACKERS]:
        if tracker.pop(CONF_TIME_AS, None):
            unsupported_cfgs.add(CONF_TIME_AS)
        tracker[CONF_REQ_MOVEMENT] = tracker.get(CONF_REQ_MOVEMENT, def_req_mv)
        if CONF_DRIVING_SPEED not in tracker and def_drv_sp is not None:
            tracker[CONF_DRIVING_SPEED] = def_drv_sp

    if unsupported_cfgs:
        _LOGGER.warning(
            "Your %s configuration contains options that are no longer supported: %s; "
            "Please remove them",
            DOMAIN,
            ", ".join(sorted(unsupported_cfgs)),
        )

    del config[CONF_DEFAULT_OPTIONS]
    return config


_ENTITIES = vol.All(
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
_TRACKER = {
    vol.Required(CONF_NAME): cv.string,
    vol.Optional(CONF_ID): cv.slugify,
    vol.Required(CONF_ENTITY_ID): _ENTITIES,
    vol.Optional(CONF_TIME_AS): cv.string,
    vol.Optional(CONF_REQ_MOVEMENT): cv.boolean,
    vol.Optional(CONF_DRIVING_SPEED): vol.Coerce(float),
    vol.Optional(CONF_ENTITY_PICTURE): vol.All(cv.string, _entity_picture),
}
CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN): vol.All(
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
                            vol.Optional(CONF_DRIVING_SPEED): vol.Coerce(float),
                        }
                    ),
                    vol.Required(CONF_TRACKERS, default=list): vol.All(
                        cv.ensure_list, vol.Length(1), [_TRACKER], _trackers
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

    async def process_config(config: ConfigType | None) -> None:
        """Process Composite config."""
        tracker_configs = cast(
            list[dict[str, Any]], (config or {}).get(DOMAIN, {}).get(CONF_TRACKERS, [])
        )
        tracker_ids = [conf[CONF_ID] for conf in tracker_configs]

        for conf in tracker_configs:
            # New config entries and changed existing ones can be processed later and do
            # not need to delay startup.
            hass.async_create_background_task(
                hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
                ),
                "Import YAML config",
            )

        tasks: list[Coroutine[Any, Any, Any]] = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            if (
                entry.source != SOURCE_IMPORT
                or (obj_id := entry.data[CONF_ID]) in tracker_ids
            ):
                continue
            _LOGGER.debug(
                "Removing %s (%s) because it is no longer in YAML configuration",
                entry.data[CONF_NAME],
                f"{DT_DOMAIN}.{obj_id}",
            )
            # Removing config entries needs to happen before entries get a chance to be
            # set up.
            tasks.append(hass.config_entries.async_remove(entry.entry_id))
        if tasks:
            await asyncio.gather(*tasks)

    async def reload_config(_: ServiceCall) -> None:
        """Reload configuration."""
        await process_config(await async_integration_yaml_config(hass, DOMAIN))

    await process_config(config)
    async_register_admin_service(hass, DOMAIN, SERVICE_RELOAD, reload_config)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
