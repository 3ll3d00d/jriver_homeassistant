"""A DataUpdateCoordinator for J River."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import datetime as dt
import logging
from typing import TypeVar

from hamcws import (
    BrowsePath,
    CannotConnectError,
    InvalidAuthError,
    InvalidRequestError,
    MediaServer,
    MediaServerError,
    MediaServerInfo,
    PlaybackInfo,
    ViewMode,
    Zone,
    convert_browse_rules,
)

from homeassistant.components.media_player import MediaType
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, _can_refresh_paths

V = TypeVar("V")


_LOGGER = logging.getLogger(__name__)


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
            return vals.get(target_zone)
        active_zone = next((z for z in self.zones if z.active), None)
        if not active_zone and self.zones:
            active_zone = self.zones[0]
        if active_zone:
            return vals.get(active_zone.name, None)
        return None


class MediaServerUpdateCoordinator(DataUpdateCoordinator[MediaServerData]):
    """Updates MediaServer data."""

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
                browse_rules = convert_browse_rules(
                    await self._media_server.get_browse_rules()
                )
                playlist_path = BrowsePath("Playlists")
                playlist_path.media_types.append(MediaType.PLAYLIST)
                browse_rules.append(playlist_path)
                return browse_rules
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
                if playback_info.position_ms:
                    delta = (
                        playback_info.position_ms - last_info.position_ms
                        if last_info
                        else 0
                    )
                    _LOGGER.debug(
                        "[%s] Updated %s position by %d to %d",
                        self._media_server.media_server_info.name,
                        zone_name,
                        delta,
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

        except InvalidAuthError as err:
            raise ConfigEntryAuthFailed from err
        except (CannotConnectError, MediaServerError, InvalidRequestError) as err:
            if _LOGGER.isEnabledFor(logging.DEBUG):
                n = (
                    self._media_server.media_server_info.name
                    if self._media_server.media_server_info
                    else "Unknown"
                )
                formatted = str(err)
                detail = f" - {formatted}" if formatted else ""
                _LOGGER.debug(
                    "[%s] Update failure due to %s%s", n, type(err).__name__, detail
                )
            raise UpdateFailed from err
        else:
            return new_data
