"""Remote platform for the jriver integration."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import logging
from typing import Any

from const import CONF_HOST, STATE_OFF, STATE_ON
from hamcws import KeyCommand, MediaServer, ViewMode

from homeassistant.components.remote import RemoteEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MediaServerUpdateCoordinator
from .const import DATA_COORDINATOR, DATA_MEDIA_SERVER, DOMAIN
from .entity import MediaServerEntity, cmd

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the JRiver Media Center remote platform from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    ms = data[DATA_MEDIA_SERVER]
    name = config_entry.data[CONF_HOST]
    unique_id = f"{config_entry.unique_id or config_entry.entry_id}_remote"
    async_add_entities(
        [JRiverRemote(data[DATA_COORDINATOR], ms, f"{name} - Remote", unique_id)]
    )


class _MixinMeta(type(MediaServerEntity), type(RemoteEntity)):  # type: ignore[misc]
    pass


class JRiverRemote(MediaServerEntity, RemoteEntity, metaclass=_MixinMeta):
    """Control Media Center."""

    _attr_name = None

    def __init__(
        self,
        coordinator: MediaServerUpdateCoordinator,
        media_server: MediaServer,
        name,
        uid: str | None,
    ) -> None:
        """Initialize the MediaServer entity."""
        super().__init__(coordinator, uid, name)
        self._media_server: MediaServer = media_server
        self._key_command_names = [e.name for e in KeyCommand]

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_on = self.coordinator.data.view_mode > ViewMode.NO_UI
        # workaround for apparent multiple inheritance problem where overridden state (in ToggleEntity) is not called
        self._attr_state = STATE_ON if self._attr_is_on else STATE_OFF
        self.async_write_ha_state()

    @cmd
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Show the standard view."""
        await self._media_server.send_mcc(22009, param=0, block=True)

    @cmd
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop all playback and close the display."""
        await asyncio.gather(
            self._media_server.stop_all(),
            self._media_server.send_mcc(22000, param=-2, block=True),
        )

    @cmd
    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send a remote command to the device."""
        commands_to_send: list[KeyCommand | str] = [
            KeyCommand[c] if c in self._key_command_names else c for c in command
        ]
        await self._media_server.send_key_presses(commands_to_send)
