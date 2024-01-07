"""Sensor platform for the jriver integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DATA_ZONES, DOMAIN
from .entity import MediaServerEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the JRiver Media Center sensor platform from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    if not data[DATA_ZONES]:
        name = config_entry.data[CONF_HOST]
        unique_id = f"{config_entry.unique_id or config_entry.entry_id}_activezone"
        async_add_entities(
            [
                JRiverActiveZoneSensor(
                    data[DATA_COORDINATOR], unique_id, f"{name} Active Zone"
                )
            ]
        )


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
