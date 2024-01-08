# JRiver Media Center Integration 

Home Assistant integration for interacting with your JRiver Media Center.

This integration 

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
![Zone Selection](img/config_4.png?raw=true "Zone Selection")

## Options

The Configure option allows for reconfiguration of the browse paths at any time.

## Platforms

### Media Player

Coming soon

### Remote Control

Coming soon

### Sensor

Coming soon

# Dev

Create a virtual environment to install dependencies:

```bash
python -m venv dev-venv
. dev-venv\Scripts\activate
```

You can then install the dependencies that will allow you to develop: 

`pip3 install -r requirements-dev.txt`