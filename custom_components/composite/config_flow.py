"""Config flow for Composite integration."""
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_ID, CONF_NAME

from .const import DOMAIN, split_conf


class CompositeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Composite config flow."""

    VERSION = 1

    async def async_step_import(self, data):
        """Import config entry from configuration."""
        await self.async_set_unique_id(data[CONF_ID])
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"{data[CONF_NAME]} (from configuration)", **split_conf(data)
        )
