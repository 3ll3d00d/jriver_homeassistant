# JRiver Media Center Integration 

Home Assistant integration for interacting with your JRiver Media Center.

## Installation

### Automated installation through HACS

You can install this component through [HACS](https://hacs.xyz/) so make sure HACS is installed. 

Next, visit the HACS Integrations pane and go to *Custom Repositories* via the advanced menu (the 3 dots in the top right). Add `https://github.com/3ll3d00d/jriver_homeassistant.git` and set category to `Integration`.

Finally, click this link to complete the installation.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=3ll3d00d&repository=jriver_homeassistant)

### Manual installation

Copy the complete `custom_components/jriver/` directory (including all subdirectory content) from this repository to your `config/custom_components/` directory.

## Configuration

After installation is complete, restart Home Assistant:

[![Open your Home Assistant instance and show the system dashboard.](https://my.home-assistant.io/badges/system_dashboard.svg)](https://my.home-assistant.io/redirect/system_dashboard/)

and add the integration:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=jriver)

A number of configuration screens will be presented:

### Server Location

Enter the host or IP that MC is running on along with the port specified in its configuration. The host must be accessible from the Home Assistant host and MC must be running to continue.

![Server Location](img/config_1.png?raw=true "Server Location")

### Authentication

This screen is only displayed if authentication is enabled, enter the username and password to continue.

![Authentication](img/config_2.png?raw=true "Authentication")

### Browse Paths

The media player integration can show the remote views configured in Media Center in the Home Assistant UI however it will only show the configured views. There is no way to access this configuration via the [JRiver Web Service Interface](https://wiki.jriver.com/index.php/Web_Service_Interface) so it has to be entered manually.

A number of the views that are configured in MC out of the box are automatically populated in this list.

![Browse Paths](img/config_3.png?raw=true "Browse Paths")

The format of each entry is a pipe delimited pair of comma separated values. These 2 values are:

* the list of path names
* the list of categories (field names)

The example illustrates, the path names are on the left and the category list is on the right. The complete text for this example would be `Audio,Artist|Album Artist (auto),Album`.

### Zones

These screens are only shown if multiple zones are configured in Media Center.

There are 2 modes of integration

1) a single media player device for the entire Media Center, zone control is handled externally (i.e. either manually by the user or using [Zone Switch](https://wiki.jriver.com/index.php/Zones#ZoneSwitch))
2) a device for each zone

If the "device per zone" option is chosen, the zones to include as devices can also be configured.

![SingleOrMulti](img/config_4.png?raw=true)

![Zone Selection](img/config_5.png?raw=true "Zone Selection")

## Options

The Configure option allows for reconfiguration of the browse paths at any time.

## Platforms

### Media Player

The [media player](https://www.home-assistant.io/integrations/media_player/) supports all [listed features](https://www.home-assistant.io/integrations/media_player/#media-control-services) excluding the following:

* select_source
* select_sound_mode
* join
* unjoin

Browsing is supported and provides access to

* any Media Center [remote view](https://wiki.jriver.com/index.php/Customize_Views_for_Gizmo,_WebRemote,_and_DLNA) specified in the [#BrowsePaths] configuration
* any Home Assistant [media source](https://www.home-assistant.io/integrations/media_source/) that is exposed as a URL 

`turn_on` and `turn_off` services function as per the equivalent [#Remote Control] services. 

If the "expose each zone as a separate device" option is selected then a separate media player entity is created for each zone to allow for direct control over that specified zone.

### Remote Control

A single [Remote](https://www.home-assistant.io/integrations/remote/) entity is registered which maps the [remote.send_command](https://www.home-assistant.io/integrations/remote/) service to `MCWS/v1/Control/Key`.

Each service call can accept a list of values. A value that matches one of the mentioned "special keys" is sent as is, other values are treated as individual key presses.  

### Sensor

A single [Sensor](https://www.home-assistant.io/integrations/sensor) entity is registered which exposes the currently active zone as its state. This is accompanied by the zone id which is exposed as an attribute.

### Additional Services

A number of additional services are provided.

#### jriver.add_to_playlist

Targets the `media_player` entity.

[![Open your Home Assistant instance and show your service developer tools.](https://my.home-assistant.io/badges/developer_call_service.svg)](https://my.home-assistant.io/redirect/developer_call_service/?service=jriver.add_to_playlist)

Requires one of two parameters:

* query: a valid search expression
* playlist_path

#### jriver.relative_seek

Targets the `media_player` entity.

[![Open your Home Assistant instance and show your service developer tools.](https://my.home-assistant.io/badges/developer_call_service.svg)](https://my.home-assistant.io/redirect/developer_call_service/?service=jriver.seek_relative)

Requires a single parameter:

* seek_duration: an amount to seek by in seconds

#### jriver.activate_zone

Targets the `remote` entity.

[![Open your Home Assistant instance and show your service developer tools.](https://my.home-assistant.io/badges/developer_call_service.svg)](https://my.home-assistant.io/redirect/developer_call_service/?service=jriver.activate_zone)

Requires a single parameter

* zone_name

#### jriver.send_mcc

Targets the `remote` entity.

[![Open your Home Assistant instance and show your service developer tools.](https://my.home-assistant.io/badges/developer_call_service.svg)](https://my.home-assistant.io/redirect/developer_call_service/?service=jriver.activate_zone)

Exposes MCWS/v1/Control/MCC as a service and accepts the same parameters, i.e. 

* command
* parameter
* block
* zone_name

Minimally, command is required.

#### jriver.wake

Targets the `remote` entity.

[![Open your Home Assistant instance and show your service developer tools.](https://my.home-assistant.io/badges/developer_call_service.svg)](https://my.home-assistant.io/redirect/developer_call_service/?service=jriver.wake)

Sends a WOL packet to the configured MAC addresses.

Depends on [Wake on LAN](https://www.home-assistant.io/integrations/wake_on_lan/) being enabled and an access key used to connect to Media Server. 

# Dev

Create a virtual environment to install dependencies:

```bash
python -m venv dev-venv
. dev-venv\Scripts\activate
```

You can then install the dependencies that will allow you to develop: 

`pip3 install -r requirements-dev.txt`