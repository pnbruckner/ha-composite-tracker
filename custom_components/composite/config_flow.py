"""Config flow for Composite integration."""
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_ID, CONF_NAME

from .const import DOMAIN


class CompositeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Composite config flow."""

    VERSION = 1

    async def async_step_import(self, data):
        """Import config entry from configuration."""
        name = data[CONF_NAME]
        id = data[CONF_ID]

        await self.async_set_unique_id(id)
        self._abort_if_unique_id_configured()

        # Versions prior to 2021.6 did not support creating with options, so only save
        # main (name/ID) data here. async_setup in __init__.py will update the entry
        # with the options.
        return self.async_create_entry(
            title=f"{name} (from configuration)", data={CONF_NAME: name, CONF_ID: id}
        )
