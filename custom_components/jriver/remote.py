"""Remote platform for the jriver integration."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import logging
from typing import Any

from hamcws import KeyCommand, MediaServer, ViewMode
import voluptuous as vol

from homeassistant.components.remote import RemoteEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MediaServerUpdateCoordinator
from .const import DATA_COORDINATOR, DATA_MEDIA_SERVER, DATA_SERVER_NAME, DOMAIN
from .entity import MediaServerEntity, cmd

_LOGGER = logging.getLogger(__name__)


SERVICE_ACTIVATE_ZONE = "activate_zone"

ATTR_ZONE_NAME = "zone_name"

MC_ACTIVATE_ZONE_SCHEMA = {
    vol.Required(ATTR_ZONE_NAME): cv.string,
}


SERVICE_SEND_MCC = "send_mcc"

ATTR_MCC_COMMAND = "command"
ATTR_MCC_PARAMETER = "parameter"
ATTR_MCC_BLOCK = "block"
ATTR_ZONE_NAME = "zone_name"

MC_SEND_MCC_SCHEMA = {
    vol.Required(ATTR_MCC_COMMAND): vol.All(
        vol.Coerce(int), vol.Range(min=10000, max=40000)
    ),
    vol.Optional(ATTR_MCC_PARAMETER): vol.Coerce(int),
    vol.Optional(ATTR_MCC_BLOCK): cv.boolean,
    vol.Optional(ATTR_ZONE_NAME): cv.string,
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the JRiver Media Center remote platform from a config entry."""
    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        SERVICE_ACTIVATE_ZONE, MC_ACTIVATE_ZONE_SCHEMA, "async_activate_zone"
    )
    platform.async_register_entity_service(
        SERVICE_SEND_MCC, MC_SEND_MCC_SCHEMA, "async_send_mcc"
    )

    data = hass.data[DOMAIN][config_entry.entry_id]
    ms = data[DATA_MEDIA_SERVER]
    name = data[DATA_SERVER_NAME]

    unique_id = f"{config_entry.unique_id or config_entry.entry_id}_remote"
    async_add_entities(
        [JRiverRemote(data[DATA_COORDINATOR], ms, name, unique_id, hass)]
    )


class JRiverRemote(MediaServerEntity, RemoteEntity):
    """Control Media Center."""

    _attr_name = None

    def __init__(
        self,
        coordinator: MediaServerUpdateCoordinator,
        media_server: MediaServer,
        name,
        uid: str,
        hass: HomeAssistant,
    ) -> None:
        """Initialize the MediaServer entity."""
        super().__init__(coordinator, uid, name)
        self._media_server: MediaServer = media_server
        self._key_command_names = [e.name for e in KeyCommand]
        self._key_command_values = [e.value for e in KeyCommand]
        self._hass = hass

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_on = self.coordinator.data.view_mode > ViewMode.NO_UI
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
            self._media_server.send_mcc(20007, param=0, block=True),
        )

    @cmd
    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send a remote command to the device."""
        commands_to_send: list[KeyCommand | str] = [
            KeyCommand[c]
            if c in self._key_command_names
            else KeyCommand(c)
            if c in self._key_command_values
            else c
            for c in command
        ]
        await self._media_server.send_key_presses(commands_to_send)

    @cmd
    async def async_activate_zone(self, zone_name: str):
        """Activate the named zone."""
        await self._media_server.set_active_zone(zone_name)

    @cmd
    async def async_send_mcc(
        self,
        command: int,
        parameter: int | None = None,
        block: bool = True,
        zone_name: str | None = None,
    ):
        """Send an MCC command."""
        await self._media_server.send_mcc(
            command, param=parameter, block=block, zone=zone_name
        )
