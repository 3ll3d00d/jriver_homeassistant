"""Sensor platform for the jriver integration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import CONF_HOST
from homeassistant.helpers.typing import StateType

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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


class _MixinMeta(type(MediaServerEntity), type(SensorEntity)):  # type: ignore[misc]
    pass


class JRiverActiveZoneSensor(SensorEntity, MediaServerEntity, metaclass=_MixinMeta):
    """Exposes current active zone."""

    _attr_name = None

    @property
    def native_value(self) -> StateType | date | datetime | Decimal:
        """Get the native value."""
        return self.coordinator.data.get_active_zone_name()

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return the state attributes."""
        return {"id": self.coordinator.data.get_active_zone_id()}
