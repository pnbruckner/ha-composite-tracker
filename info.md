# <img src="https://brands.home-assistant.io/composite/icon.png" alt="Composite Device Tracker Platform" width="50" height="50"/> Composite Device Tracker

This integration creates a composite `device_tracker` entity from one or more other entities. It will update whenever one of the watched entities updates, taking the "last seen" (and possibly GPS and other) data from the changing entity. The result can be a more accurate and up-to-date device tracker if the "input" entities update irregularly.

It will also create a `sensor` entity that indicates the speed of the device.

Currently any entity that has "GPS" attributes (`gps_accuracy` or `acc`, and either `latitude` & `longitude` or `lat` & `lon`), or any `device_tracker` entity with a `source_type` attribute of `bluetooth`, `bluetooth_le`, `gps` or `router`, or any `binary_sensor` entity, can be used as an input entity.
