# <img src="https://brands.home-assistant.io/composite/icon.png" alt="Composite Device Tracker Platform" width="50" height="50"/> Composite Device Tracker

This integration creates a composite `device_tracker` entity from one or more other device trackers and/or binary sensors. It will update whenever one of the watched entities updates, taking the `last_seen`, `last_timestamp` or`last_updated` (and possibly GPS and battery) data from the changing entity. The result can be a more accurate and up-to-date device tracker if the "input" entities update irregularly.

Currently `device_tracker` entities with a `source_type` of `bluetooth`, `bluetooth_le`, `gps` or `router` are supported, as well as `binary_sensor` entities.

It will also create a `sensor` entity that indicates the speed of the device.

Follow the [installation](#installation) instructions below.
Then, after restarting Home Assistant, add the desired configuration and restart Home Assistant once more. Here is an example of a typical configuration:

```yaml
composite:
  trackers:
    - name: Me
      time_as: device_or_local
      entity_id:
        - entity: device_tracker.platform1_me
          use_picture: true
        - device_tracker.platform2_me
        - binary_sensor.i_am_home
```

## Legacy vs entity-based implementation

When this integration was originally created the
[Device Tracker](https://www.home-assistant.io/integrations/device_tracker/)
component worked differently than it does today.
That older implementation is now referred to as the "legacy" implementation,
and is the one that creates and uses the `known_devices.yaml` file in HA's configuration folder.

Starting with the 2.4.0 release this integration now uses the newer entity-based implementation.
That implementation stores configuration and entity settings in HA's `.storage` folder,
and supports reconfiguring those items via the Integrations and Entities pages in the UI.
The initial configuration, though, is still done via YAML, and is "imported" and will show up
on the Integrations UI page as such.
In the future the integration will likely allow adding & fully reconfiguring composite trackers
via the UI.

To allow for a smoother transition, the integration currently still supports the older,
legacy implementation as well. If it sees entries under `device_tracker`, it will still create the
entities as before, but it will issue a warning and a persistent notification that the configuration
has changed and suggest how to edit your configuration accordingly.

At some point (i.e., in an upcoming 3.0.0 release) legacy support will be removed.

## Installation
### With HACS
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)

You can use HACS to manage the installation and provide update notifications.

1. Add this repo as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/):

```text
https://github.com/pnbruckner/ha-composite-tracker
```

2. Install the integration using the appropriate button on the HACS Integrations page. Search for "composite".

### Manual

Place a copy of:

[`__init__.py`](custom_components/composite/__init__.py) at `<config>/custom_components/composite/__init__.py`  
[`config_flow.py`](custom_components/composite/config_flow.py) at `<config>/custom_components/composite/config_flow.py`  
[`const.py`](custom_components/composite/const.py) at `<config>/custom_components/composite/const.py`  
[`device_tracker.py`](custom_components/composite/device_tracker.py) at `<config>/custom_components/composite/device_tracker.py`  
[`manifest.json`](custom_components/composite/manifest.json) at `<config>/custom_components/composite/manifest.json`

where `<config>` is your Home Assistant configuration directory.

>__NOTE__: Do not download the file by using the link above directly. Rather, click on it, then on the page that comes up use the `Raw` button.

### Versions

This custom integration supports HomeAssistant versions 2022.11 or newer.

### numpy on Raspberry Pi

To determine time zone from GPS coordinates (see `time_as` configuration variable below) the package [timezonefinderL](https://pypi.org/project/timezonefinderL/) (by default) is used. That package requires the package [numpy](https://pypi.org/project/numpy/). These will both be installed automatically by HA. Note, however, that numpy on Pi _usually_ requires libatlas to be installed. (See [this web page](https://www.raspberrypi.org/forums/viewtopic.php?t=207058) for more details.) It can be installed using this command:
```
sudo apt install libatlas3-base
```
>Note: This is the same step that would be required if using a standard HA component that uses numpy (such as the [Trend Binary Sensor](https://www.home-assistant.io/components/binary_sensor.trend/)), and is only required if you use `device_or_utc` or `device_or_local` for `time_as`.

## Configuration variables

- **default_options** (*Optional*): Defines default values for corresponding options under **trackers**.
  - **require_movement** (*Optional*): Default is `false`.
  - **time_as** (*Optional*): Default is `utc`.

- **trackers** (*Optional*): The list of composite trackers to create. For each entry see [Tracker entries](#tracker-entries).
NOTE: Once legacy support is removed, this variable, with at least one entry, will become required.

- **tz_finder** (*Optional*): Specifies which `timezonefinder` package, and possibly version, to install. Must be formatted as required by `pip`. Default is `timezonefinder==5.2.0`. Other common values:  

`timezonefinder`  
`timezonefinder<5`  
`timezonefinder==4.2.0`  
`timezonefinderL`  
`timezonefinderL==4.0.2`  
`timezonefinderL==2.0.1`

- **tz_finder_class** (*Optional*): Specifies which class to use. Only applies when using `timezonefinder` package. Valid options are `TimezoneFinder` and `TimezoneFinderL`. The default is `TimezoneFinderL`.

>Note: Starting with release 4.4.0 the `timezonefinder` package provides two classes to choose from: the original `TimezoneFinder` class, and a new class named `TimezoneFinderL`, which effectively replaces the functionality of the `timezonefinderL` package.

### Tracker entries

- **entity_id**: Specifies the watched entities. Can be an entity ID, a dictionary (see [Entity Dictionary](#entity-dictionary)), or a list containing any combination of these.
- **name**: Friendly name of composite device.
- **id** (*Optional*): Object ID (i.e., part of entity ID after the dot) of composite device. If not supplied, then object ID will be generated from the `name` variable. For example, `My Name` would result in a tracker entity ID of `device_tracker.my_name`. The speed sensor's object ID will be the same as for the device tracker, but with a suffix of "`_speed`" added (e.g., `sensor.my_name_speed`.)
- **require_movement** (*Optional*): `true` or `false`. If `true`, will skip update from a GPS-based tracker if it has not moved. Specifically, if circle defined by new GPS coordinates and accuracy overlaps circle defined by previous GPS coordinates and accuracy then update will be ignored.
- **time_as** (*Optional*): One of `utc`, `local`, `device_or_utc` or `device_or_local`. `utc` shows time attributes in UTC. `local` shows time attributes per HA's `time_zone` configuration. `device_or_utc` and `device_or_local` attempt to determine the time zone in which the device is located based on its GPS coordinates. The name of the time zone (or `unknown`) will be shown in a new attribute named `time_zone`. If the time zone can be determined, then time attributes will be shown in that time zone. If the time zone cannot be determined, then time attributes will be shown in UTC if `device_or_utc` is selected, or in HA's local time zone if `device_or_local` is selected.

#### Entity Dictionary

- **entity**: Entity ID of an entity to watch.
- **all_states** (*Optional*): `true` or `false`. Default is `false`. If `true`, use all states of the entity. If `false`, only use the "Home" state. NOTE: This option is ignored for entities whose `source_type` is `gps` for which all states are always used.
- **use_picture** (*Optional*): `true` or `false`. Default is `false`. If `true`, use the entity's picture for the composite. Can only be `true` for at most one of the entities.

## Watched device notes

Watched GPS-based devices must have, at a minimum, the following attributes: `latitude`, `longitude` and `gps_accuracy`. If they don't they will not be used.

For watched non-GPS-based devices, which states are used and whether any GPS data (if present) is used depends on several factors. E.g., if GPS-based devices are in use then the 'not_home'/'off' state of non-GPS-based devices will be ignored (unless `all_states` was specified as `true` for that entity.) If only non-GPS-based devices are in use, then the composite device will be 'home' if any of the watched devices are 'home'/'on', and will be 'not_home' only when _all_ the watched devices are 'not_home'/'off'.

If a watched device has a `last_seen` or `last_timestamp` attribute, that will be used in the composite device. If not, then `last_updated` from the entity's state will be used instead.

If a watched device has a `battery` or `battery_level` attribute, that will be used to update the composite device's `battery` attribute. If it has a `battery_charging` or `charging` attribute, that will be used to udpate the composite device's `battery_charging` attribute.

## known_devices.yaml

NOTE: This only applies to "legacy" tracker devices.

The watched devices, and the composite device, should all have `track` set to `true`.

## `device_tracker` Attributes

Attribute | Description
-|-
battery_level | Battery level (in percent, if available.)
battery_charging | Battery charging status (True/False, if available.)
entity_id | IDs of entities that have contributed to the state of the composite device.
entity_picture | Picture to use for composite (if configured and available.)
gps_accuracy | GPS accuracy radius (in meters, if available.)
last_entity_id | ID of the last entity to update the composite device.
last_seen | Date and time when current location information was last updated.
latitude | Latitude of current location (if available.)
longitude | Longitude of current location (if available.)
source_type | Source of current location information: `binary_sensor`, `bluetooth`, `bluetooth_le`, `gps` or `router`.
time_zone | The name of the time zone in which the device is located, or `unknown` if it cannot be determined. Only exists if `device_or_utc` or `device_or_local` is chosen for `time_as`.

## Speed `sensor` Attributes

Attribute | Description
-|-
angle | Angle of movement direction (in degrees, if moving.)
direction | Compass heading of movement direction (if moving.)

## Examples
### Example Full Config
```yaml
composite:
  tz_finder: timezonefinder<6
  tz_finder_class: TimezoneFinderL
  default_options:
    time_as: device_or_local
    require_movement: true
  trackers:
    - name: Me
      time_as: local
      entity_id:
        - entity: device_tracker.platform1_me
          use_picture: true
        - device_tracker.platform2_me
        - device_tracker.router_my_device
        - entity: binary_sensor.i_am_home
          all_states: true
    - name: Better Half
      id: wife
      require_movement: false
      entity_id:
        entity: device_tracker.platform_wife
        use_picture: true
```

### Time zone examples

This example assumes `time_as` is set to `device_or_utc` or `device_or_local`. It determines the difference between the time zone in which the device is located and the `time_zone` in HA's configuration. A positive value means the device's time zone is ahead of (or later than, or east of) the local time zone.
```yaml
sensor:
  - platform: template
    sensors:
      my_tz_offset:
        friendly_name: My time zone offset
        unit_of_measurement: hr
        value_template: >
          {% set state = states.device_tracker.me %}
          {% if state.attributes is defined and
                state.attributes.time_zone is defined and
                state.attributes.time_zone != 'unknown' %}
            {% set n = now() %}
            {{ (n.astimezone(state.attributes.last_seen.tzinfo).utcoffset() -
                n.utcoffset()).total_seconds()/3600 }}
          {% else %}
            unknown
          {% endif %}
```
This example converts a time attribute to the local time zone. It works no matter which time zone the attribute is in.
```yaml
sensor:
  - platform: template
    sensors:
      my_last_seen_local:
        friendly_name: My last_seen time in local time zone
        value_template: >
          {{ state_attr('device_tracker.me', last_seen').astimezone(now().tzinfo) }}
```
