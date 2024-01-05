"""Config flow for Composite integration."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_ENTITY_ID, CONF_ID, CONF_NAME
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_REQ_MOVEMENT, DOMAIN


def split_conf(conf: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return pieces of configuration data."""
    return {
        kw: {k: v for k, v in conf.items() if k in ks}
        for kw, ks in (
            ("data", (CONF_NAME, CONF_ID)),
            ("options", (CONF_ENTITY_ID, CONF_REQ_MOVEMENT)),
        )
    }


class CompositeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Composite config flow."""

    VERSION = 1

    async def async_step_import(self, data: dict[str, Any]) -> FlowResult:
        """Import config entry from configuration."""
        await self.async_set_unique_id(data[CONF_ID])
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"{data[CONF_NAME]} (from configuration)",
            **split_conf(data),  # type: ignore[arg-type]
        )
