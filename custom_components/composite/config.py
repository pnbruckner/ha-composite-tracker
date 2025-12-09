"""Composite config validation."""
from __future__ import annotations

from contextlib import suppress
from datetime import timedelta
import logging
from pathlib import Path
from typing import Any, cast

import voluptuous as vol

from homeassistant.const import CONF_ENTITY_ID, CONF_ID, CONF_NAME
from homeassistant.core import HomeAssistant, async_get_hass
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify

from .const import (
    CONF_ALL_STATES,
    CONF_DEFAULT_OPTIONS,
    CONF_DRIVING_SPEED,
    CONF_END_DRIVING_DELAY,
    CONF_ENTITY,
    CONF_ENTITY_PICTURE,
    CONF_MAX_SPEED_AGE,
    CONF_REQ_MOVEMENT,
    CONF_SHOW_UNKNOWN_AS_0,
    CONF_TIME_AS,
    CONF_TRACKERS,
    CONF_USE_PICTURE,
    DEF_REQ_MOVEMENT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PACKAGE_MERGE_HINT = "dict"

CONF_TZ_FINDER = "tz_finder"
CONF_TZ_FINDER_CLASS = "tz_finder_class"


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


def _time_period_to_dict(delay: timedelta) -> dict[str, float]:
    """Return timedelta as a dict."""
    result: dict[str, float] = {}
    if delay.days:
        result["days"] = delay.days
    result["hours"] = delay.seconds // 3600
    result["minutes"] = (delay.seconds // 60) % 60
    result["seconds"] = delay.seconds % 60
    return result


def _entity_picture(entity_picture: str) -> str:
    """Validate entity picture.

    Must be run in an executor since it might do file I/O.

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


def _trackers(trackers: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
    def_shu_az = config[CONF_DEFAULT_OPTIONS].get(CONF_SHOW_UNKNOWN_AS_0)
    def_max_sa = config[CONF_DEFAULT_OPTIONS].get(CONF_MAX_SPEED_AGE)
    def_drv_sp = config[CONF_DEFAULT_OPTIONS].get(CONF_DRIVING_SPEED)
    def_end_dd = config[CONF_DEFAULT_OPTIONS].get(CONF_END_DRIVING_DELAY)
    end_dd_but_no_drv_sp = False
    for tracker in config[CONF_TRACKERS]:
        if tracker.pop(CONF_TIME_AS, None):
            unsupported_cfgs.add(CONF_TIME_AS)
        tracker[CONF_REQ_MOVEMENT] = tracker.get(CONF_REQ_MOVEMENT, def_req_mv)
        if CONF_SHOW_UNKNOWN_AS_0 not in tracker and def_shu_az is not None:
            tracker[CONF_SHOW_UNKNOWN_AS_0] = def_shu_az
        if CONF_MAX_SPEED_AGE not in tracker and def_max_sa is not None:
            tracker[CONF_MAX_SPEED_AGE] = def_max_sa
        if CONF_DRIVING_SPEED not in tracker and def_drv_sp is not None:
            tracker[CONF_DRIVING_SPEED] = def_drv_sp
        if CONF_END_DRIVING_DELAY not in tracker and def_end_dd is not None:
            tracker[CONF_END_DRIVING_DELAY] = def_end_dd
        if CONF_END_DRIVING_DELAY in tracker and CONF_DRIVING_SPEED not in tracker:
            end_dd_but_no_drv_sp = True

    if unsupported_cfgs:
        _LOGGER.warning(
            "Your %s configuration contains options that are no longer supported: %s; "
            "Please remove them",
            DOMAIN,
            ", ".join(sorted(unsupported_cfgs)),
        )
    if end_dd_but_no_drv_sp:
        raise vol.Invalid(
            f"using {CONF_END_DRIVING_DELAY}; "
            f"{CONF_DRIVING_SPEED} must also be specified"
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
_POS_TIME_PERIOD = vol.All(cv.positive_time_period, _time_period_to_dict)
_TRACKER = {
    vol.Required(CONF_NAME): cv.string,
    vol.Optional(CONF_ID): cv.slugify,
    vol.Required(CONF_ENTITY_ID): _ENTITIES,
    vol.Optional(CONF_TIME_AS): cv.string,
    vol.Optional(CONF_REQ_MOVEMENT): cv.boolean,
    vol.Optional(CONF_SHOW_UNKNOWN_AS_0): cv.boolean,
    vol.Optional(CONF_MAX_SPEED_AGE): _POS_TIME_PERIOD,
    vol.Optional(CONF_DRIVING_SPEED): vol.Coerce(float),
    vol.Optional(CONF_END_DRIVING_DELAY): _POS_TIME_PERIOD,
    vol.Optional(CONF_ENTITY_PICTURE): vol.All(cv.string, _entity_picture),
}
_CONFIG_SCHEMA = vol.Schema(
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
                            vol.Optional(CONF_SHOW_UNKNOWN_AS_0): cv.boolean,
                            vol.Optional(CONF_MAX_SPEED_AGE): _POS_TIME_PERIOD,
                            vol.Optional(CONF_DRIVING_SPEED): vol.Coerce(float),
                            vol.Optional(CONF_END_DRIVING_DELAY): _POS_TIME_PERIOD,
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


async def async_validate_config(
    hass: HomeAssistant, config: ConfigType
) -> ConfigType | None:
    """Validate configuration."""
    # Perform _CONFIG_SCHEMA validation in executor since it may indirectly invoke
    # _entity_picture which must be run in an executor because it might do file I/O.
    return cast(ConfigType, await hass.async_add_executor_job(_CONFIG_SCHEMA, config))
