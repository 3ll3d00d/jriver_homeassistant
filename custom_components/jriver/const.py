"""Constants for the JRiver Media Center integration."""

CONF_BROWSE_PATHS = "browse_paths"
CONF_DEVICE_PER_ZONE = "per_zone"
CONF_DEVICE_ZONES = "device_zones"
CONF_EXTRA_FIELDS = "extra_fields"
CONF_USE_WOL = "use_wol"

DOMAIN = "jriver"
DEFAULT_PORT = 52199
DEFAULT_SSL = False
DEFAULT_TIMEOUT = 5
DEFAULT_DEVICE_PER_ZONE = False
DEFAULT_BROWSE_PATHS = [
    "Audio,Artist|Album Artist (auto),Album",
    "Audio,Album|Album",
    "Audio,Recent|Album",
    "Audio,Genre|Genre,Album Artist (auto),Album",
    "Audio,Composer|Composer,Album",
    "Audio,Podcast",
    "Video,Movies",
    "Video,Shows|Series,Season",
    "Video,Music|Artist,Album",
]
DATA_EXTRA_FIELDS = "extra_fields"
DATA_MEDIA_SERVER = "media_server"
DATA_REMOVE_STOP_LISTENER = "remove_listener"
DATA_REMOVE_UPDATE_LISTENER = "remove_update_listener"
DATA_BROWSE_PATHS = "browse_paths"
DATA_COORDINATOR = "coordinator"
DATA_ZONES = "zones"
DATA_SERVER_NAME = "server_name"
DATA_MAC_ADDRESSES = "mac_addresses"

MC_FIELD_TO_HA_MEDIATYPE: dict[str, str] = {
    "Audio": "MUSIC",
    "Artist": "ARTIST",
    "Album": "ALBUM",
    "Album Artist (auto)": "ARTIST",
    "Composer": "ARTIST",
    "Video": "VIDEO",
    "Images": "IMAGE",
    "Playlists": "PLAYLIST",
    "Shows": "TVSHOW",
    "Genre": "GENRE",
    "Podcast": "PODCAST",
}

MC_FIELD_TO_HA_MEDIACLASS: dict[str, str] = {
    "Audio": "MUSIC",
    "Artist": "ARTIST",
    "Album": "ALBUM",
    "Album Artist (auto)": "ARTIST",
    "Composer": "ARTIST",
    "Video": "VIDEO",
    "Images": "IMAGE",
    "Playlists": "PLAYLIST",
    "Shows": "TV_SHOW",
    "Genre": "GENRE",
    "Podcast": "PODCAST",
}
