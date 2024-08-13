"""Composite Device Tracker."""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
import logging
from typing import Any, cast

from homeassistant.components.device_tracker import DOMAIN as DT_DOMAIN
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_ID, CONF_NAME, SERVICE_RELOAD, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.reload import async_integration_yaml_config
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType

from .const import CONF_TRACKERS, DOMAIN

PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR]

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
