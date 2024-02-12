"""The JRiver Media Center (https://jriver.com/) integration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import datetime as dt
import logging
from typing import TypeVar

from awesomeversion import AwesomeVersion
from hamcws import (
    BrowsePath,
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
    convert_browse_rules,
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
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.MEDIA_PLAYER, Platform.REMOTE, Platform.SENSOR]


V = TypeVar("V")


@dataclass(frozen=True, kw_only=True)
class MediaServerData:
    """MediaServer data."""

    server_info: MediaServerInfo | None = None
    playback_info_by_zone: dict[str, PlaybackInfo] = field(default_factory=dict)
    position_updated_at_by_zone: dict[str, dt.datetime] = field(default_factory=dict)
    zones: list[Zone] = field(default_factory=list)
    view_mode: ViewMode = ViewMode.UNKNOWN
    browse_paths: list[BrowsePath] | None = None
    last_path_refresh: dt.datetime | None = None

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
        self._last_path_refresh: dt.datetime | None = None

    async def _refresh_paths_if_necessary(
        self, current_version: str
    ) -> list[BrowsePath]:
        async def _get_paths() -> list[BrowsePath]:
            try:
                return convert_browse_rules(await self._media_server.get_browse_rules())
            finally:
                self._last_path_refresh = dt_util.utcnow()

        if not _can_refresh_paths(self._media_server):
            return []

        if (
            self.data.browse_paths is None
            or self._last_path_refresh is None
            or self.data.server_info is None
        ):
            return await _get_paths()

        since_last = (dt_util.utcnow() - self._last_path_refresh).total_seconds()
        if since_last >= 900:
            _LOGGER.debug("Reloading paths, %d seconds since last refresh", since_last)
            return await _get_paths()

        if self.data.server_info.version != current_version:
            _LOGGER.debug(
                "Reloading paths, version change from %s to %s",
                self.data.server_info.version,
                current_version,
            )
            return await _get_paths()

        return self.data.browse_paths

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
            pos_updated_at = dt_util.utcnow()

            for i, task in enumerate(zone_tasks):
                zone_name = zones[i].name
                playback_info: PlaybackInfo = task.result()
                playback_info_by_zone[zone_name] = playback_info
                last_info = self.data.playback_info_by_zone.get(zone_name, None)
                if last_info and last_info.position_ms != playback_info.position_ms:
                    _LOGGER.debug(
                        "[%s] Updated %s position: %d",
                        self._media_server.media_server_info.name,
                        zone_name,
                        playback_info.position_ms,
                    )
                    position_updated_at_by_zone[zone_name] = pos_updated_at

            new_data = MediaServerData(
                server_info=server_info,
                playback_info_by_zone=playback_info_by_zone,
                position_updated_at_by_zone=position_updated_at_by_zone,
                zones=zones,
                view_mode=view_mode,
                browse_paths=await self._refresh_paths_if_necessary(
                    server_info.version
                ),
            )

            last_zone = self.data.get_active_zone_name()
            new_zone = new_data.get_active_zone_name()

            if last_zone != new_zone:
                _LOGGER.debug(
                    '[%s] Active zone change "%s" -> "%s"',
                    self._media_server.media_server_info.name,
                    last_zone,
                    new_zone,
                )

            return new_data
        except InvalidAuthError as err:
            raise ConfigEntryAuthFailed from err
        except (CannotConnectError, MediaServerError, InvalidRequestError) as err:
            raise UpdateFailed from err


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up mcws from a config entry."""
    ms = get_ms(hass, entry)

    extra_fields: list[str] | None = (
        entry.options[CONF_EXTRA_FIELDS]
        if CONF_EXTRA_FIELDS in entry.options
        else entry.data[CONF_EXTRA_FIELDS]
    )

    coordinator = MediaServerUpdateCoordinator(hass, ms, extra_fields)
    await coordinator.async_config_entry_first_refresh()

    async def _close(event):
        _LOGGER.debug("[%s] Closing media server connection", event.entry_id)
        await ms.close()

    remove_stop_listener = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _close)
    remove_update_listener = entry.add_update_listener(reconfigure_entry)

    hass.data.setdefault(DOMAIN, {})
    browse_paths = (
        entry.options[CONF_BROWSE_PATHS]
        if CONF_BROWSE_PATHS in entry.options
        else entry.data[CONF_BROWSE_PATHS]
    )

    mac_addresses = []
    if CONF_MAC in entry.data:
        mac_addresses = entry.data[CONF_MAC]
    if CONF_MAC in entry.options:
        _LOGGER.debug("[%s] Using MAC addresses from options", entry.entry_id)
        mac_addresses = entry.options[CONF_MAC]

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_MEDIA_SERVER: ms,
        DATA_REMOVE_STOP_LISTENER: remove_stop_listener,
        DATA_REMOVE_UPDATE_LISTENER: remove_update_listener,
        DATA_ZONES: entry.data[CONF_DEVICE_ZONES],
        DATA_BROWSE_PATHS: browse_paths,
        DATA_COORDINATOR: coordinator,
        DATA_SERVER_NAME: entry.data.get(CONF_NAME, ms.media_server_info.name),
        DATA_EXTRA_FIELDS: extra_fields,
        DATA_MAC_ADDRESSES: mac_addresses,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def get_ms(hass: HomeAssistant, entry: ConfigEntry) -> MediaServer:
    """Get a MediaServer instance."""
    conn = get_mcws_connection(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        ssl=entry.data[CONF_SSL],
        session=async_get_clientsession(hass),
    )
    ms = MediaServer(conn)
    return ms


async def reconfigure_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


def _translate_to_media_type(
    media_type: mc_MediaType | str | None,
    media_sub_type: mc_MediaSubType | str | None,
    single: bool = False,
) -> MediaType | str:
    """Convert JRiver MediaType/SubType to HA MediaType."""
    if media_type == mc_MediaType.VIDEO:
        if media_sub_type == mc_MediaSubType.MOVIE:
            return MediaType.MOVIE
        if media_sub_type == mc_MediaSubType.TV_SHOW:
            return MediaType.EPISODE if single else MediaType.TVSHOW
        return MediaType.VIDEO

    if media_type == mc_MediaType.AUDIO:
        if single:
            return MediaType.TRACK
        return MediaType.MUSIC

    if media_type == mc_MediaType.TV:
        if single:
            return MediaType.CHANNEL
        return MediaType.TVSHOW

    if media_type == mc_MediaType.IMAGE:
        return MediaType.IMAGE

    if media_type == mc_MediaType.PLAYLIST:
        return MediaType.PLAYLIST

    if not media_type:
        if media_sub_type == mc_MediaSubType.MOVIE:
            return MediaType.MOVIE
        if media_sub_type == mc_MediaSubType.TV_SHOW:
            return MediaType.EPISODE if single else MediaType.TVSHOW
        if media_sub_type == mc_MediaSubType.MUSIC:
            return MediaType.TRACK if single else MediaType.MUSIC

    return ""


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and DOMAIN in hass.data:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data[DATA_MEDIA_SERVER].close()
        data[DATA_REMOVE_STOP_LISTENER]()
        data[DATA_REMOVE_UPDATE_LISTENER]()

    return unload_ok


def _translate_to_media_class(
    media_type: mc_MediaType | str | None,
    media_sub_type: mc_MediaSubType | str | None,
    single: bool = False,
) -> MediaClass | str:
    """Convert JRiver MediaType/SubType to HA MediaClass."""
    if media_type == mc_MediaType.VIDEO:
        if media_sub_type == mc_MediaSubType.MOVIE:
            return MediaClass.MOVIE
        if media_sub_type == mc_MediaSubType.TV_SHOW:
            return MediaClass.EPISODE if single else MediaClass.TV_SHOW
        return MediaClass.VIDEO

    if media_type == mc_MediaType.AUDIO:
        if single:
            return MediaClass.TRACK
        return MediaClass.MUSIC

    if media_type == mc_MediaType.TV:
        return MediaClass.CHANNEL

    if media_type == mc_MediaType.IMAGE:
        return MediaClass.IMAGE

    if media_type == mc_MediaType.PLAYLIST:
        return MediaClass.PLAYLIST

    if not media_type:
        if media_sub_type == mc_MediaSubType.MOVIE:
            return MediaClass.MOVIE
        if media_sub_type == mc_MediaSubType.TV_SHOW:
            return MediaClass.EPISODE if single else MediaClass.TV_SHOW
        if media_sub_type == mc_MediaSubType.MUSIC:
            return MediaClass.TRACK if single else MediaClass.MUSIC

    return ""


def _can_refresh_paths(ms: MediaServer) -> bool:
    """Show if the server version supports reload."""
    v = ms.media_server_info.version
    return v and v != "Unknown" and AwesomeVersion(v) >= "32.0.6"
