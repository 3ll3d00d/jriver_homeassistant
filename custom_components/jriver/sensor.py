"""Sensor platform for the jriver integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from hamcws import MediaServer

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MediaServerUpdateCoordinator
from .const import (
    DATA_COORDINATOR,
    DATA_EXTRA_FIELDS,
    DATA_MEDIA_SERVER,
    DATA_SERVER_NAME,
    DOMAIN,
    DATA_ZONES,
)
from .entity import MediaServerEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the JRiver Media Center sensor platform from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    extra_fields = data[DATA_EXTRA_FIELDS]
    name = data[DATA_SERVER_NAME]
    allowed_zones = data[DATA_ZONES]
    ms: MediaServer = data[DATA_MEDIA_SERVER]
    uid_prefix = config_entry.unique_id or config_entry.entry_id

    entities: list[SensorEntity] = [
        JRiverActiveZoneSensor(
            data[DATA_COORDINATOR], f"{uid_prefix}_activezone", f"{name} (Active Zone)"
        ),
        JRiverUISensor(
            data[DATA_COORDINATOR], f"{uid_prefix}_uimode", f"{name} (UI Mode)"
        )
    ]

    zones = await ms.get_zones()
    for z in zones:
        if z in allowed_zones or allowed_zones is None:
            entities.extend(
                [
                    JRiverPlayingNowSensor(
                        data[DATA_COORDINATOR],
                        f"{uid_prefix}_{z}_playingnow",
                        f"{name} - {z} (Playing Now)",
                        z.name,
                        extra_fields,
                    ),
                    JRiverAudioPlayingDirectSensor(
                        data[DATA_COORDINATOR],
                        f"{uid_prefix}_{z}_audiodirect",
                        f"{name} - {z} (Audio Is Direct)",
                        z.name,
                    ),
                    JRiverPlaylistSensor(
                        data[DATA_COORDINATOR],
                        f"{uid_prefix}_{z}_playlist",
                        f"{name} - {z} (Playlist)",
                        z.name,
                    ),
                ]
            )

    async_add_entities(entities)


class JRiverActiveZoneSensor(MediaServerEntity, SensorEntity):
    """Exposes current active zone."""

    _attr_name = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self.coordinator.data.get_active_zone_name()
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return the state attributes."""
        return {"id": self.coordinator.data.get_active_zone_id()}


class JRiverPlayingNowSensor(MediaServerEntity, SensorEntity):
    """Exposes detailed information about what is playing in a given zone."""

    _attr_name = None

    def __init__(
            self,
            coordinator: MediaServerUpdateCoordinator,
            unique_id: str,
            name: str,
            zone_name: str,
            extra_fields: list[str],
    ) -> None:
        """Init the sensor."""
        super().__init__(coordinator, unique_id, name)
        self._zone_name = zone_name
        self._extra_fields = extra_fields

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        info = self.coordinator.data.get_playback_info(self._zone_name)
        if not info:
            return
        self._attr_native_value = info.name
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return the state attributes."""
        info = self.coordinator.data.get_playback_info(self._zone_name)
        if not info:
            return {}
        return {
            "is_active": self.coordinator.data.get_active_zone_name()
                         == self._zone_name,
            **info.as_dict(),
        }


class JRiverAudioPlayingDirectSensor(MediaServerEntity, SensorEntity):
    """Exposes whether the given zone is playing direct."""

    _attr_name = None

    def __init__(
            self,
            coordinator: MediaServerUpdateCoordinator,
            unique_id: str,
            name: str,
            zone_name: str,
    ) -> None:
        """Init the sensor."""
        super().__init__(coordinator, unique_id, name)
        self._zone_name = zone_name

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        info = self.coordinator.data.get_audio_path_is_direct(self._zone_name)
        if not info:
            return
        self._attr_native_value = info.is_direct
        self.async_write_ha_state()


class JRiverPlaylistSensor(MediaServerEntity, SensorEntity):
    """Exposes the playlist in a given zone."""

    _attr_name = None

    def __init__(
            self,
            coordinator: MediaServerUpdateCoordinator,
            unique_id: str,
            name: str,
            zone_name: str,
    ) -> None:
        """Init the sensor."""
        super().__init__(coordinator, unique_id, name)
        self._zone_name = zone_name

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        info = self.coordinator.data.get_playlist(self._zone_name)
        if not info:
            self._attr_native_value = False
            return
        self._attr_native_value = True
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return the state attributes."""
        info = self.coordinator.data.get_playlist(self._zone_name)
        if not info:
            return {}
        return {"entries": info}


class JRiverUISensor(MediaServerEntity, SensorEntity):
    """Exposes the state of the UI."""

    _attr_name = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self.coordinator.data.view_mode.name
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return the state attributes."""
        return {"id": self.coordinator.data.view_mode.value}
