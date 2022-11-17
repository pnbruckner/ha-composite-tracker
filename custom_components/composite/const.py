"""Constants for Composite Integration."""
from homeassistant.const import CONF_ENTITY_ID, CONF_ID, CONF_NAME

DOMAIN = "composite"

DATA_LEGACY_WARNED = "legacy_warned"
DATA_TF = "tf"

CONF_ALL_STATES = "all_states"
CONF_ENTITY = "entity"
CONF_REQ_MOVEMENT = "require_movement"
CONF_TIME_AS = "time_as"
CONF_TRACKERS = "trackers"

TZ_UTC = "utc"
TZ_LOCAL = "local"
TZ_DEVICE_UTC = "device_or_utc"
TZ_DEVICE_LOCAL = "device_or_local"
# First item in list is default.
TIME_AS_OPTS = [TZ_UTC, TZ_LOCAL, TZ_DEVICE_UTC, TZ_DEVICE_LOCAL]


def split_conf(conf):
    """Return pieces of configuration data."""
    return {
        kw: {k: v for k, v in conf.items() if k in ks}
        for kw, ks in (
            ("data", (CONF_NAME, CONF_ID)),
            ("options", (CONF_ENTITY_ID, CONF_REQ_MOVEMENT, CONF_TIME_AS)),
        )
    }
