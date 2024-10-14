"""The JRiver Media Center (https://jriver.com/) integration."""

from __future__ import annotations

import asyncio
import logging

from hamcws import (
    MediaServer,
    MediaSubType as mc_MediaSubType,
    MediaType as mc_MediaType,
    get_mcws_connection,
)
import voluptuous as vol

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.wake_on_lan import (
    DOMAIN as WOL_DOMAIN,
    SERVICE_SEND_MAGIC_PACKET,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ENTITY_ID,
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
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

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
    SERVICE_WAKE,
)
from .coordinator import MediaServerUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.MEDIA_PLAYER, Platform.REMOTE, Platform.SENSOR]

PLATFORM_SCHEMA = cv.platform_only_config_schema


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the JRiver Media Center component."""

    async def async_send_wol(call: ServiceCall) -> None:
        """Send WOL packet to each MAC address."""
        entity_id: str | None = call.data.get(CONF_ENTITY_ID)
        if not entity_id:
            _LOGGER.warning("Unable to send WOL, no entity_id specified")
            return

        tokens = entity_id.split(".")
        if len(tokens) != 2:
            _LOGGER.warning(
                "Unable to send WOL, unexpected entity_id format %s", entity_id
            )
            return

        domain_data = next(
            (
                data
                for data in hass.data[DOMAIN].values()
                if data[DATA_SERVER_NAME].casefold() == tokens[1].casefold()
            ),
            None,
        )
        if not domain_data:
            _LOGGER.warning(
                "Unable to send WOL, no such server found in domain %s", entity_id
            )
            return

        if DATA_MAC_ADDRESSES not in domain_data:
            _LOGGER.warning(
                "Unable to send WOL, No MAC addresses found in entity %s", entity_id
            )
            return

        mac_addresses: list[str] = domain_data[DATA_MAC_ADDRESSES]

        if not hass.services.has_service(WOL_DOMAIN, SERVICE_SEND_MAGIC_PACKET):
            _LOGGER.warning(
                "Service wake_on_lan not configured, unable to send WOL to %s for %s",
                str(mac_addresses),
                entity_id,
            )
            return

        _LOGGER.debug("Sending WOL to %s for %s", str(mac_addresses), entity_id)
        await asyncio.gather(
            *[
                hass.services.async_call(
                    WOL_DOMAIN, SERVICE_SEND_MAGIC_PACKET, service_data={"mac": mac}
                )
                for mac in mac_addresses
            ]
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_WAKE,
        async_send_wol,
        vol.Schema({vol.Required(CONF_ENTITY_ID): str}),
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up mcws from a config entry."""
    ms = _get_ms(hass, entry)

    extra_fields: list[str] | None = (
        entry.options[CONF_EXTRA_FIELDS]
        if CONF_EXTRA_FIELDS in entry.options
        else entry.data[CONF_EXTRA_FIELDS]
    )

    ms_coordinator = MediaServerUpdateCoordinator(hass, ms, extra_fields)

    async def _close(event):
        _LOGGER.debug("[%s] Closing media server connection", entry.entry_id)
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
        DATA_COORDINATOR: ms_coordinator,
        DATA_SERVER_NAME: entry.data[CONF_NAME],
        DATA_EXTRA_FIELDS: extra_fields,
        DATA_MAC_ADDRESSES: mac_addresses,
    }

    await ms_coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _get_ms(hass: HomeAssistant, entry: ConfigEntry) -> MediaServer:
    """Get a MediaServer instance."""
    conn = get_mcws_connection(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        ssl=entry.data[CONF_SSL],
        session=async_get_clientsession(hass),
    )
    return MediaServer(conn)


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
