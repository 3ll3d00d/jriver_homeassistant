"""The JRiver Media Center (https://jriver.com/) integration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import datetime as dt
import logging
from typing import TypeVar

from hamcws import (
    CannotConnectError,
    InvalidAuthError,
    InvalidRequestError,
    MediaServer,
    MediaServerError,
    MediaServerInfo,
    MediaSubType as mc_MediaSubType,
    MediaType as mc_MediaType,
    PlaybackInfo,
    ViewMode,
    Zone,
    get_mcws_connection,
)

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .const import (
    CONF_BROWSE_PATHS,
    CONF_DEVICE_ZONES,
    CONF_EXTRA_FIELDS,
    DATA_BROWSE_PATHS,
    DATA_COORDINATOR,
    DATA_EXTRA_FIELDS,
    DATA_MAC_ADDRESSES,
    DATA_MEDIA_SERVER,
    DATA_REMOVE_STOP_LISTENER,
    DATA_REMOVE_UPDATE_LISTENER,
    DATA_SERVER_NAME,
    DATA_ZONES,
    DOMAIN,
    MC_FIELD_TO_HA_MEDIACLASS,
    MC_FIELD_TO_HA_MEDIATYPE,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.MEDIA_PLAYER, Platform.REMOTE, Platform.SENSOR]


class BrowsePath:
    """representation of a single path through the Media Center browse tree.

    Each path is split into 2 parts, 1 or more named nodes which are followed by 0 to n library field names.
    """

    def __init__(self, entry: str) -> None:
        """Create new instance from a config entry."""
        self.__entry = entry
        vals = entry.split("|", 2)
        self._names = vals[0].split(",")
        self._tags = vals[1].split(",") if len(vals) == 2 else []

    def contains(self, entry: list[str]) -> bool:
        """Test if the given entry is part of this path, matches again name tags only."""
        if len(entry) > len(self):
            return False
        for i in range(min(len(entry), len(self.names))):
            if entry[i] != self[i]:
                return False
        return True

    def get_media_classification(
        self, entry: list[str]
    ) -> tuple[MediaClass, MediaType] | None:
        """Get the MediaClass and MediaType for the given entry."""
        if not entry:
            return None
        if not self.contains(entry):
            return None
        if len(entry) > len(self):
            return None
        if len(entry) > len(self.names):
            library_field_name = self[len(entry) - 1]
            mc = MC_FIELD_TO_HA_MEDIACLASS.get(library_field_name, None)
            mt = MC_FIELD_TO_HA_MEDIATYPE.get(library_field_name, None)
            if mc and mt:
                return MediaClass[mc], MediaType[mt]
        for i in reversed(range(min(len(self.names), len(entry)))):
            path_token = self[i]
            mc = MC_FIELD_TO_HA_MEDIACLASS.get(path_token, None)
            mt = MC_FIELD_TO_HA_MEDIATYPE.get(path_token, None)
            if mc and mt:
                return MediaClass[mc], MediaType[mt]
        return None

    @property
    def names(self) -> list[str]:
        """Get the name portion of the entry."""
        return [] + self._names

    @property
    def tags(self) -> list[str]:
        """Get the tags portion of the entry."""
        return [] + self._tags

    def __str__(self):
        """Get str representation."""
        return self.__entry

    def __len__(self):
        """Total number of nodes in the path."""
        return len(self._names) + len(self._tags)

    def __getitem__(self, index) -> str:
        """Get the item at the specified index."""
        if index < 0:
            raise IndexError()
        if index < len(self.names):
            return self.names[index]
        if index < len(self):
            return self.tags[index - len(self.names)]
        raise IndexError()


V = TypeVar("V")


@dataclass(frozen=True, kw_only=True)
class MediaServerData:
    """MediaServer data."""

    server_info: MediaServerInfo | None = None
    playback_info_by_zone: dict[str, PlaybackInfo] = field(default_factory=dict)
    position_updated_at_by_zone: dict[str, dt.datetime] = field(default_factory=dict)
    zones: list[Zone] = field(default_factory=list)
    view_mode: ViewMode = ViewMode.UNKNOWN

    def get_active_zone_name(self) -> str | None:
        """Get the current active zone name."""
        return next((z.name for z in self.zones if z.active), None)

    def get_active_zone_id(self) -> int | None:
        """Get the current active zone id."""
        return next((z.id for z in self.zones if z.active), None)

    def get_playback_info(self, target_zone: str | None) -> PlaybackInfo | None:
        """Get PlaybackInfo for the given zone if provided or the currently active zone."""
        return self._get_val_for_zone(self.playback_info_by_zone, target_zone)

    def get_position_updated_at(self, target_zone: str | None) -> dt.datetime | None:
        """Get last position updated at for the given zone if provided or the currently active zone."""
        return self._get_val_for_zone(self.position_updated_at_by_zone, target_zone)

    def _get_val_for_zone(
        self, vals: dict[str, V], target_zone: str | None
    ) -> V | None:
        if target_zone:
            return vals.get(target_zone, None)
        active_zone = next((z for z in self.zones if z.active), None)
        if not active_zone and self.zones:
            active_zone = self.zones[0]
        if active_zone:
            return vals.get(active_zone.name, None)
        return None


class MediaServerUpdateCoordinator(DataUpdateCoordinator[MediaServerData]):
    """Store MediaServer data."""

    def __init__(
        self,
        hass: HomeAssistant,
        media_server: MediaServer,
        extra_fields: list[str] | None,
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=dt.timedelta(seconds=1),
        )
        self._media_server = media_server
        self.data = MediaServerData()
        self._extra_fields = extra_fields

    async def _async_update_data(self) -> MediaServerData:
        """Fetch the latest status."""
        try:
            server_info, zones, view_mode = await asyncio.gather(
                self._media_server.alive(),
                self._media_server.get_zones(),
                self._media_server.get_view_mode(),
            )
            zone_tasks: list[asyncio.Task]
            async with asyncio.TaskGroup() as tg:
                zone_tasks = [
                    tg.create_task(
                        self._media_server.get_playback_info(
                            zone, extra_fields=self._extra_fields
                        )
                    )
                    for zone in zones
                ]

            playback_info_by_zone: dict[str, PlaybackInfo] = {}
            position_updated_at_by_zone: dict[str, dt.datetime] = {}
            for i, task in enumerate(zone_tasks):
                zone_name = zones[i].name
                playback_info: PlaybackInfo = task.result()
                playback_info_by_zone[zone_name] = playback_info
                last_info = self.data.playback_info_by_zone.get(zone_name, None)
                if last_info and last_info.position_ms != playback_info.position_ms:
                    position_updated_at_by_zone[zone_name] = dt_util.utcnow()

            return MediaServerData(
                server_info=server_info,
                playback_info_by_zone=playback_info_by_zone,
                position_updated_at_by_zone=position_updated_at_by_zone,
                zones=zones,
                view_mode=view_mode,
            )
        except InvalidAuthError as err:
            raise ConfigEntryAuthFailed from err
        except (CannotConnectError, MediaServerError, InvalidRequestError) as err:
            raise UpdateFailed from err


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up mcws from a config entry."""
    conn = get_mcws_connection(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        ssl=entry.data[CONF_SSL],
        session=async_get_clientsession(hass),
    )

    ms = MediaServer(conn)

    extra_fields: list[str] | None = (
        entry.options[CONF_EXTRA_FIELDS]
        if CONF_EXTRA_FIELDS in entry.options
        else entry.data[CONF_EXTRA_FIELDS]
    )

    coordinator = MediaServerUpdateCoordinator(hass, ms, extra_fields)
    await coordinator.async_config_entry_first_refresh()

    async def _close(event):
        await conn.close()

    remove_stop_listener = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _close)
    remove_update_listener = entry.add_update_listener(reconfigure_entry)

    hass.data.setdefault(DOMAIN, {})
    browse_paths = (
        entry.options[CONF_BROWSE_PATHS]
        if CONF_BROWSE_PATHS in entry.options
        else entry.data[CONF_BROWSE_PATHS]
    )
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_MEDIA_SERVER: ms,
        DATA_REMOVE_STOP_LISTENER: remove_stop_listener,
        DATA_REMOVE_UPDATE_LISTENER: remove_update_listener,
        DATA_ZONES: entry.data[CONF_DEVICE_ZONES],
        DATA_BROWSE_PATHS: browse_paths,
        DATA_COORDINATOR: coordinator,
        DATA_SERVER_NAME: entry.data.get(CONF_NAME, ms.media_server_info.name),
        DATA_EXTRA_FIELDS: extra_fields,
        DATA_MAC_ADDRESSES: entry.data[CONF_MAC],
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def reconfigure_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data[DATA_MEDIA_SERVER].close()
        data[DATA_REMOVE_STOP_LISTENER]()
        data[DATA_REMOVE_UPDATE_LISTENER]()

    return unload_ok


def _translate_to_media_type(
    media_type: mc_MediaType | str | None,
    media_sub_type: mc_MediaSubType | str | None,
    single: bool = False,
) -> MediaType | str:
    """Convert JRiver MediaType/SubType to HA MediaType."""
    if not mc_MediaType or not mc_MediaSubType:
        return ""

    if media_type == mc_MediaType.VIDEO:
        if media_sub_type == mc_MediaSubType.MOVIE:
            return MediaType.MOVIE
        if media_sub_type == mc_MediaSubType.TV_SHOW:
            return MediaType.EPISODE if single else MediaType.TVSHOW
        return MediaType.VIDEO

    if media_type == mc_MediaType.AUDIO:
        return MediaType.TRACK

    if media_type == mc_MediaType.TV:
        return MediaType.CHANNEL

    if media_type == mc_MediaType.IMAGE:
        return MediaType.IMAGE

    if media_type == mc_MediaType.PLAYLIST:
        return MediaType.PLAYLIST

    return ""


def _translate_to_media_class(
    media_type: mc_MediaType | str | None,
    media_sub_type: mc_MediaSubType | str | None,
    single: bool = False,
) -> MediaClass | str:
    """Convert JRiver MediaType/SubType to HA MediaClass."""
    if not mc_MediaType or not mc_MediaSubType:
        return ""

    if media_type == mc_MediaType.VIDEO:
        if media_sub_type == mc_MediaSubType.MOVIE:
            return MediaClass.MOVIE
        if media_sub_type == mc_MediaSubType.TV_SHOW:
            return MediaClass.EPISODE if single else MediaType.TVSHOW
        return MediaClass.VIDEO

    if media_type == mc_MediaType.AUDIO:
        return MediaClass.TRACK

    if media_type == mc_MediaType.TV:
        return MediaClass.CHANNEL

    if media_type == mc_MediaType.IMAGE:
        return MediaClass.IMAGE

    if media_type == mc_MediaType.PLAYLIST:
        return MediaClass.PLAYLIST

    return ""
