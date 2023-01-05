"""Constants for Composite Integration."""
DOMAIN = "composite"

DATA_LEGACY_WARNED = "legacy_warned"
DATA_TF = "tf"

SIG_COMPOSITE_SPEED = "composite_speed"

CONF_ALL_STATES = "all_states"
CONF_DEFAULT_OPTIONS = "default_options"
CONF_ENTITY = "entity"
CONF_REQ_MOVEMENT = "require_movement"
CONF_TIME_AS = "time_as"
CONF_TRACKERS = "trackers"
CONF_USE_PICTURE = "use_picture"

TZ_UTC = "utc"
TZ_LOCAL = "local"
TZ_DEVICE_UTC = "device_or_utc"
TZ_DEVICE_LOCAL = "device_or_local"
# First item in list is default.
TIME_AS_OPTS = [TZ_UTC, TZ_LOCAL, TZ_DEVICE_UTC, TZ_DEVICE_LOCAL]

DEF_TIME_AS = TIME_AS_OPTS[0]
DEF_REQ_MOVEMENT = False

MIN_SPEED_SECONDS = 3
MIN_ANGLE_SPEED = 1  # meters / second

ATTR_ANGLE = "angle"
ATTR_DIRECTION = "direction"
