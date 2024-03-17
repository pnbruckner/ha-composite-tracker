# <img src="https://brands.home-assistant.io/composite/icon.png" alt="Composite Device Tracker Platform" width="50" height="50"/> Composite Device Tracker

This integration creates a composite `device_tracker` entity from one or more other entities. It will update whenever one of the watched entities updates, taking the "last seen" (and possibly GPS and other) data from the changing entity. The result can be a more accurate and up-to-date device tracker if the "input" entities update irregularly.

It will also create a `sensor` entity that indicates the speed of the device.

Currently any entity that has "GPS" attributes (`gps_accuracy` or `acc`, and either `latitude` & `longitude` or `lat` & `lon`), or any `device_tracker` entity with a `source_type` attribute of `bluetooth`, `bluetooth_le`, `gps` or `router`, or any `binary_sensor` entity, can be used as an input entity.

## Installation

<details>
<summary>With HACS</summary>

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)

You can use HACS to manage the installation and provide update notifications.

1. Add this repo as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/).
   It should then appear as a new integration. Click on it. If necessary, search for "composite".

   ```text
   https://github.com/pnbruckner/ha-composite-tracker
   ```
   Or use this button:
   
   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pnbruckner&repository=ha-composite-tracker&category=integration)


1. Download the integration using the appropriate button.

</details>

<details>
<summary>Manual</summary>

Place a copy of the files from [`custom_components/composite`](custom_components/composite)
in `<config>/custom_components/composite`,
where `<config>` is your Home Assistant configuration directory.

>__NOTE__: When downloading, make sure to use the `Raw` button from each file's page.

</details>

After it has been downloaded you will need to restart Home Assistant.

### Versions

This custom integration supports HomeAssistant versions 2023.7 or newer.

## Configuration

Composite entities can be created via the UI on the Integrations page or by YAML entries.

To create a Composite entity via the UI you can use this My Button:

[![add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=composite)

Alternatively, go to Settings -> Devices & services and click the **`+ ADD INTEGRATION`** button.
Find or search for "Composite", click on it, then follow the prompts.

The remainder of this section describes YAML configuration.
Here is an example YAML configuration:

```yaml
composite:
  trackers:
    - name: Me
      entity_id:
        - entity: device_tracker.platform1_me
          use_picture: true
        - device_tracker.platform2_me
        - binary_sensor.i_am_home
```

- **default_options** (*Optional*): Defines default values for corresponding options under **trackers**.
  - **require_movement** (*Optional*): Default is `false`.
  - **driving_speed** (*Optional*)

- **trackers**: The list of composite trackers to create. For each entry see [Tracker entries](#tracker-entries).

### Tracker entries

- **entity_id**: Specifies the watched entities. Can be an entity ID, a dictionary (see [Entity Dictionary](#entity-dictionary)), or a list containing any combination of these.
- **name**: Friendly name of composite device.
- **id** (*Optional*): Object ID (i.e., part of entity ID after the dot) of composite device. If not supplied, then object ID will be generated from the `name` variable. For example, `My Name` would result in a tracker entity ID of `device_tracker.my_name`. The speed sensor's object ID will be the same as for the device tracker, but with a suffix of "`_speed`" added (e.g., `sensor.my_name_speed`.)
- **require_movement** (*Optional*): `true` or `false`. If `true`, will skip update from a GPS-based tracker if it has not moved. Specifically, if circle defined by new GPS coordinates and accuracy overlaps circle defined by previous GPS coordinates and accuracy then update will be ignored.
- **driving_speed** (*Optional*): Defines a driving speed threshold (in MPH or KPH, depending on general unit system setting.) If set, and current speed is at or above this value, and tracker is not in a zone, then the state of the tracker will be set to `driving`.
- **entity_picture** (*Optional*): Specifies image to use for entity. Can be an URL or a file in "/local". Note that /local is used by the frontend to access files in `<config_path>/www` (which is typically `/config/www`.) You can specify file names with or without the "/local" prefix. If this option is used, then `use_picture` cannot be used.

#### Entity Dictionary

- **entity**: Entity ID of an entity to watch.
- **all_states** (*Optional*): `true` or `false`. Default is `false`. If `true`, use all states of the entity. If `false`, only use the "Home" state. NOTE: This option is ignored for entities whose `source_type` is `gps` for which all states are always used.
- **use_picture** (*Optional*): `true` or `false`. Default is `false`. If `true`, use the entity's picture for the composite. Can only be `true` for at most one of the entities. If `entity_picture` is used, then this option cannot be used.

## Watched device notes
### Used states

For watched non-GPS-based devices, which states are used and whether any GPS data (if present) is used depends on several factors. E.g., if GPS-based devices are in use then the 'not_home'/'off' state of non-GPS-based devices will be ignored (unless `all_states` was specified as `true` for that entity.) If only non-GPS-based devices are in use, then the composite device will be 'home' if any of the watched devices are 'home'/'on', and will be 'not_home' only when _all_ the watched devices are 'not_home'/'off'.

### Last seen

If a watched device has a "last seen" attribute (i.e. `last_seen` or `last_timestamp`), that will be used in the composite device. If not, then `last_updated` from the entity's [state object](https://www.home-assistant.io/docs/configuration/state_object/) will be used instead.

The "last seen" attribute can be in any one of these formats:

Python type | description
-|-
aware `datetime` | In any time zone
naive `datetime` | Assumed to be in the system's time zone (Settings -> System -> General)
`float`, `int`, `str` | A POSIX timestamp (anything accepted by `homeassistant.util.dt.utc_from_timestamp(float(x))`
`str` | A date & time, aware or naive (anything accepted by `homeassistant.util.dt.parse_datetime`)

* See [Aware and Naive Objects](https://docs.python.org/3/library/datetime.html#aware-and-naive-objects)

Integrations known to provide a supported "last seen" attribute:

- Google Maps (`last_seen`, [built-in](https://www.home-assistant.io/integrations/google_maps/) or [enhanced custom](https://github.com/pnbruckner/ha-google-maps))
- [Enhanced GPSLogger](https://github.com/pnbruckner/ha-gpslogger) (`last_seen`)
- [iCould3](https://github.com/gcobb321/icloud3) (`last_timestamp`)

### Miscellaneous

If a watched device has a `battery_level` or `battery` attribute, that will be used to update the composite device's `battery_level` attribute. If it has a `battery_charging` or `charging` attribute, that will be used to udpate the composite device's `battery_charging` attribute.

## `device_tracker` Attributes

Attribute | Description
-|-
battery_level | Battery level (in percent, if available.)
battery_charging | Battery charging status (True/False, if available.)
entities | IDs of entities that have contributed to the state of the composite device.
entity_picture | Picture to use for composite (if configured and available.)
gps_accuracy | GPS accuracy radius (in meters, if available.)
last_entity_id | ID of the last entity to update the composite device.
last_seen | Date and time when current location information was last updated.
latitude | Latitude of current location (if available.)
longitude | Longitude of current location (if available.)
source_type | Source of current location information: `binary_sensor`, `bluetooth`, `bluetooth_le`, `gps` or `router`.

## Speed `sensor` Attributes

Attribute | Description
-|-
angle | Angle of movement direction (in degrees, if moving.)
direction | Compass heading of movement direction (if moving.)

## Examples
### Example Full Config
```yaml
composite:
  default_options:
    require_movement: true
    driving_speed: 15
  trackers:
    - name: Me
      driving_speed: 20
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
      entity_picture: /local/wife.jpg
      entity_id: device_tracker.platform_wife
```
