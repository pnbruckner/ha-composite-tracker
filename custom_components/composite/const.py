"""Constants for Composite Integration."""
DOMAIN = "composite"

PICTURE_SUFFIXES = ("bmp", "jpg", "png")
MIME_TO_SUFFIX = {"image/bmp": "bmp", "image/jpeg": "jpg", "image/png": "png"}

DEF_REQ_MOVEMENT = False

MIN_SPEED_SECONDS = 3
MIN_ANGLE_SPEED = 1  # meters / second

SIG_COMPOSITE_SPEED = "composite_speed"

CONF_ALL_STATES = "all_states"
CONF_DEFAULT_OPTIONS = "default_options"
CONF_DRIVING_SPEED = "driving_speed"
CONF_END_DRIVING_DELAY = "end_driving_delay"
CONF_ENTITY = "entity"
CONF_ENTITY_PICTURE = "entity_picture"
CONF_MAX_SPEED_AGE = "max_speed_age"
CONF_REQ_MOVEMENT = "require_movement"
CONF_SHOW_UNKNOWN_AS_0 = "show_unknown_as_0"
CONF_TIME_AS = "time_as"
CONF_TRACKERS = "trackers"
CONF_USE_PICTURE = "use_picture"

ATTR_ACC = "acc"
ATTR_ANGLE = "angle"
ATTR_CHARGING = "charging"
ATTR_DIRECTION = "direction"
ATTR_ENTITIES = "entities"
ATTR_LAST_SEEN = "last_seen"
ATTR_LAST_TIMESTAMP = "last_timestamp"
ATTR_LAST_ENTITY_ID = "last_entity_id"
ATTR_LAT = "lat"
ATTR_LON = "lon"

STATE_DRIVING = "driving"
