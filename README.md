# Composite Device Tracker

This platform creates a composite device tracker from one or more other device trackers and/or binary sensors. It will update whenever one of the watched entities updates, taking the last_seen/last_updated (and possibly GPS and battery) data from the changing entity. The result can be a more accurate and up-to-date device tracker if the "input" device tracker's update irregularly.

Currently device_tracker's with a source_type of bluetooth, bluetooth_le, gps or router are supported, as well as binary_sensor's.

## Installation

Follow either the HACS or manual installation instructions below.
Then add the desired configuration. Here is an example of a typical configuration:

```yaml
device_tracker:
  - platform: composite
    name: me
    time_as: device_or_local
    entity_id:
      - device_tracker.platform1_me
      - device_tracker.platform2_me
```

### HACS

See [HACS](https://github.com/custom-components/hacs), especially the **Add custom repositories** section on [this page](https://custom-components.github.io/hacs/usage/settings/).

### Manual

Alternatively, place a copy of:

[`__init__.py`](custom_components/composite/__init__.py) at `<config>/custom_components/composite/__init__.py`  
[`device_tracker.py`](custom_components/composite/device_tracker.py) at `<config>/custom_components/composite/device_tracker.py`  
[`manifest.json`](custom_components/composite/manifest.json) at `<config>/custom_components/composite/manifest.json`

where `<config>` is your Home Assistant configuration directory.

>__NOTE__: Do not download the file by using the link above directly. Rather, click on it, then on the page that comes up use the `Raw` button.

### numpy on Raspberry Pi

To determine time zone from GPS coordinates (see `time_as` configuration variable below) the package [timezonefinderL](https://pypi.org/project/timezonefinderL/) is used. That package requires the package [numpy](https://pypi.org/project/numpy/). These will both be installed automatically by HA. Note, however, that numpy on Pi _usually_ requires libatlas to be installed. (See [this web page](https://www.raspberrypi.org/forums/viewtopic.php?t=207058) for more details.) It can be installed using this command:
```
sudo apt install libatlas3-base
```
>Note: This is the same step that would be required if using a standard HA component that uses numpy (such as the [Trend Binary Sensor](https://www.home-assistant.io/components/binary_sensor.trend/)), and is only required if you use `device_or_utc` or `device_or_local` for `time_as`.

## Configuration variables

- **entity_id**: Entity IDs of watched device tracker devices. Can be a single entity ID, a list of entity IDs, or a string containing multiple entity IDs separated by commas.
- **name**: Object ID (i.e., part of entity ID after the dot) of composite device. For example, `NAME` would result in an entity ID of `device_tracker.NAME`.
- **require_movement** (*Optional*): `true` or `false`. If `true`, will skip update from a GPS-based tracker if it has not moved. Specifically, if circle defined by new GPS coordinates and accuracy overlaps circle defined by previous GPS coordinates and accuracy then update will be ignored.
- **time_as** (*Optional*): One of `utc`, `local`, `device_or_utc` or `device_or_local`. Default is `utc` which shows time attributes in UTC. `local` shows time attributes per HA's `time_zone` configuration. `device_or_utc` and `device_or_local` attempt to determine the time zone in which the device is located based on its GPS coordinates. The name of the time zone (or `unknown`) will be shown in a new attribute named `time_zone`. If the time zone can be determined, then time attributes will be shown in that time zone. If the time zone cannot be determined, then time attributes will be shown in UTC if `device_or_utc` is selected, or in HA's local time zone if `device_or_local` is selected.

## Watched device notes

Watched GPS-based devices must have, at a minimum, the following attributes: `latitude`, `longitude` and `gps_accuracy`. If they don't they will not be used.

For watched non-GPS-based devices, which states are used and whether any GPS data (if present) is used depends on several factors. E.g., if GPS-based devices are in use then the 'not_home'/'off' state of non-GPS-based devices will be ignored. If only non-GPS-based devices are in use, then the composite device will be 'home' if any of the watched devices are 'home'/'on', and will be 'not_home' only when _all_ the watched devices are 'not_home'/'off'.

If a watched device has a `last_seen` attribute, that will be used in the composite device. If not, then `last_updated` from the entity's state will be used instead.

If a watched device has a `battery` or `battery_level` attribute, that will be used to update the composite device's `battery` attribute. If it has a `battery_charging` or `charging` attribute, that will be used to udpate the composite device's `battery_charging` attribute.

## known_devices.yaml

The watched devices, and the composite device, should all have `track` set to `true`.

It's recommended, as well, to set `hide_if_away` to `true` for the watched devices (but leave it set to `false` for the composite device.) This way the map will only show the composite device (of course when it is out of the home zone.) **NOTE:** The downside to hiding the watched devices, though, is that their history (other than when they're home) will not get recorded and hence will not be available in history views. (In history views they will appear to always be home.) Also they are hidden *everywhere* in the UI when not home (not just the map.)

Lastly, it is also recommended to _not_ use the native merge feature of the device tracker component (i.e., do not add the MAC address from network-based trackers to a GPS-based tracker. See more details in the [Device Tracker doc page](https://www.home-assistant.io/components/device_tracker/#using-gps-device-trackers-with-local-network-device-trackers).)

## Attributes

Attribute | Description
-|-
battery | Battery level (in percent, if available.)
battery_charging | Battery charging status (True/False, if available.)
entity_id | IDs of entities that have contributed to the state of the composite device.
gps_accuracy | GPS accuracy radius (in meters, if available.)
last_entity_id | ID of the last entity to update the composite device.
last_seen | Date and time when current location information was last updated.
latitude | Latitude of current location (if available.)
longitude | Longitude of current location (if available.)
source_type | Source of current location information: `binary_sensor`, `bluetooth`, `bluetooth_le`, `gps` or `router`.
time_zone | The name of the time zone in which the device is located, or `unknown` if it cannot be determined. Only exists if `device_or_utc` or `device_or_local` is chosen for `time_as`.

## Examples
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
