# Composite Device Tracker

This integration creates a composite `device_tracker` entity from one or more other device trackers and/or binary sensors. It will update whenever one of the watched entities updates, taking the `last_seen`/`last_updated` (and possibly GPS and battery) data from the changing entity. The result can be a more accurate and up-to-date device tracker if the "input" entities update irregularly.

Currently `device_tracker` entities with a `source_type` of `bluetooth`, `bluetooth_le`, `gps` or `router` are supported, as well as `binary_sensor` entities.

It will also create a `sensor` entity that indicates the speed of the device.

For now configuration is done strictly in YAML and will be imported into the Integrations and Entities pages in the UI.
