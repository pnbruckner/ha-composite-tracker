# Composite Device Tracker
This platform creates a composite device tracker from one or more other device trackers and/or binary sensors. It will update whenever one of the watched entities updates, taking the last_seen/last_updated (and possibly GPS and battery) data from the changing entity. The result can be a more accurate and up-to-date device tracker if the "input" device tracker's update irregularly.

Currently device_tracker's with a source_type of bluetooth, bluetooth_le, gps or router are supported, as well as binary_sensor's.
## Installation
See [Installing and Updating](custom_updater.md) to use Custom Updater. The name of this `"element"` is `"device_tracker.composite"`.

Alternatively, place a copy of:

[`composite/__init__.py`](../custom_components/composite/__init__.py) at `<config>/custom_components/composite/__init__.py`  
[`composite/device_tracker.py`](../custom_components/composite/device_tracker.py) at `<config>/custom_components/composite/device_tracker.py`  
[`composite/manifest.json`](../custom_components/composite/manifest.json) at `<config>/custom_components/composite/manifest.json`

where `<config>` is your Home Assistant configuration directory.

>__NOTE__: Do not download the file by using the link above directly. Rather, click on it, then on the page that comes up use the `Raw` button.

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
### Home Assistant before 0.86
For manual installation, place a copy of:

[`composite/device_tracker.py`](../custom_components/composite/device_tracker.py) at `<config>/custom_components/device_tracker/composite.py`
### numpy on Raspberry Pi
To determine time zone from GPS coordinates (see `time_as` configuration variable below) the package [timezonefinderL](https://pypi.org/project/timezonefinderL/) is used. That package requires the package [numpy](https://pypi.org/project/numpy/). These will both be installed automatically by HA. Note, however, that numpy on Pi _usually_ requires libatlas to be installed. (See [this web page](https://www.raspberrypi.org/forums/viewtopic.php?t=207058) for more details.) It can be installed using this command:
```
sudo apt install libatlas3-base
```
>Note: This is the same step that would be required if using a standard HA component that uses numpy (such as the [Trend Binary Sensor](https://www.home-assistant.io/components/binary_sensor.trend/)), and is only required if you use `device_or_utc` or `device_or_local` for `time_as`.
## Configuration variables
- **name**: Object ID (i.e., part of entity ID after the dot) of composite device. For example, `NAME` would result in an entity ID of `device_tracker.NAME`.
- **time_as** (*Optional*): One of `utc`, `local`, `device_or_utc` or `device_or_local`. Default is `utc` which shows time attributes in UTC. `local` shows time attributes per HA's `time_zone` configuration. `device_or_utc` and `device_or_local` attempt to determine the time zone in which the device is located based on its GPS coordinates. The name of the time zone (or `unknown`) will be shown in a new attribute named `time_zone`. If the time zone can be determined, then time attributes will be shown in that time zone. If the time zone cannot be determined, then time attributes will be shown in UTC if `device_or_utc` is selected, or in HA's local time zone if `device_or_local` is selected.
- **entity_id**: Entity IDs of watched device tracker devices. Can be a single entity ID, a list of entity IDs, or a string containing multiple entity IDs separated by commas.
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
## Release Notes
Date | Version | Notes
-|:-:|-
20180907 | [1.0.0](https://github.com/pnbruckner/homeassistant-config/blob/d767bcce0fdff0c9298dc7a010d27af88817eac2/custom_components/device_tracker/composite.py) | Initial support for Custom Updater.
20180920 | [1.0.1](https://github.com/pnbruckner/homeassistant-config/blob/d5dd426bbf28a8f7bd5241bfe0603e67bc29f951/custom_components/device_tracker/composite.py) | Add thread locking to protect against multiple entities updating too close together.
20180925 | [1.1.0](https://github.com/pnbruckner/homeassistant-config/blob/d57cc5bdae4eeee98d0eebb6cba493243e20c0cd/custom_components/device_tracker/composite.py) | Add support for network-based (aka router) device trackers.
20180926 | [1.2.0](https://github.com/pnbruckner/homeassistant-config/blob/67ca1774af55c9d1b84672160ad07a7a34fbbf4c/custom_components/device_tracker/composite.py) | Add support for bluetooth device trackers and binary sensors.
20180926 | [1.3.0](https://github.com/pnbruckner/homeassistant-config/blob/ed9bab69ea9cdd2bb2a892cf3a1b23f930119f0b/custom_components/device_tracker/composite.py) | Add entity_id and last_entity_id attributes. Fix bug in 1.2.0 that affected state of binary_sensors in state machine.
20181016 | [1.4.0](https://github.com/pnbruckner/homeassistant-config/blob/42faa22bdc3cc7cc63df3744a81bc235507996b6/custom_components/device_tracker/composite.py) | Make sure name is valid object ID.
20181019 | [1.5.0](https://github.com/pnbruckner/homeassistant-config/blob/d1fffc42d5c309bc6a99ff74d81469c00a4fa71b/custom_components/device_tracker/composite.py) | Remove initialization delay and update immediately according to current state of entities.
20181022 | [1.5.1](https://github.com/pnbruckner/homeassistant-config/blob/111ce69063dfeda57f4c62a5207cce7d605c5928/custom_components/device_tracker/composite.py) | Log, but otherwise ignore, invalid states of watched entities during init. Improve "skipping" debug message.
20181102 | [1.5.2](https://github.com/pnbruckner/homeassistant-config/blob/f29b3db134b15bf6ea30034b4dd5bc7bee281def/custom_components/device_tracker/composite.py) | Slugify name in schema instead of during setup to catch any errors earlier.
20181109 | [1.6.0](https://github.com/pnbruckner/homeassistant-config/blob/a38ddea71232053e6f8824822e31cd5060801334/custom_components/device_tracker/composite.py) | In addition to 'battery' attribute, also accept 'battery_level' attribute, and use for 'battery' attribute. Accept either 'battery_charging' or 'charging' attribute and use for new 'battery_charging' attribute. 
20190123 | [1.7.0](https://github.com/pnbruckner/homeassistant-config/blob/0f0254c1137255662e1fe53e0d08a8bbf4e2f1b2/custom_components/device_tracker/composite.py) | Add `time_as` option.
20190419 | [1.7.1](https://github.com/pnbruckner/homeassistant-config/blob/4f638d1ac9abd12f7bc1e8080763b545fd2385fa/custom_components/composite/device_tracker.py) | Add manifest.json required by 0.92.
20190419 | [1.7.2](https://github.com/pnbruckner/homeassistant-config/blob/6da2f09b5cf49db3b02403958e41763231c0a7ec/custom_components/composite) | ... and apparently custom_updater needs an `__init__.py` file, too.
20190419 | [1.7.3](https://github.com/pnbruckner/homeassistant-config/blob/7be70ac6f3445a755c4f3060bfc00cc3019a33b3/custom_components/composite) | ... and older HA versions still need REQUIREMENTS & DEPENDENCIES!
20190422 | [1.7.4](https://github.com/pnbruckner/homeassistant-config/blob/e8e87ca3019ed98cb54f119032266fd72e86b3be/custom_components/composite) | ... and they need to be in the right place!!
20190606 | [1.7.5](https://github.com/pnbruckner/homeassistant-config/blob/e4ec3996840d4303957af18e01b4e380d20df7df/custom_components/composite) | Adjust to changes in HA 0.94.
