"""Config flow for JRiver integration."""
from __future__ import annotations

import logging
from typing import Any

from hamcws import (
    CannotConnectError,
    InvalidAuthError,
    MediaServer,
    get_mcws_connection,
)
import voluptuous as vol

from homeassistant import config_entries, core, exceptions
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_TIMEOUT,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_BROWSE_PATHS,
    CONF_DEVICE_PER_ZONE,
    CONF_DEVICE_ZONES,
    DATA_BROWSE_PATHS,
    DEFAULT_BROWSE_PATHS,
    DEFAULT_DEVICE_PER_ZONE,
    DEFAULT_PORT,
    DEFAULT_SSL,
    DEFAULT_TIMEOUT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def validate_http(hass: core.HomeAssistant, data) -> MediaServer:
    """Validate the user input allows us to connect over HTTP."""

    host = data[CONF_HOST]
    port = data[CONF_PORT]
    username = data.get(CONF_USERNAME)
    password = data.get(CONF_PASSWORD)
    timeout = data.get(CONF_TIMEOUT)
    ssl = data.get(CONF_SSL)
    session = async_get_clientsession(hass)

    _LOGGER.debug("Connecting to %s:%s", host, port)
    conn = get_mcws_connection(
        host,
        port,
        username=username,
        password=password,
        ssl=ssl,
        timeout=timeout,
        session=session,
    )
    ms = MediaServer(conn)
    try:
        if not await ms.get_auth_token():
            raise CannotConnect("Unexpected response")
    except CannotConnectError as error:
        raise CannotConnect from error
    except InvalidAuthError as error:
        raise InvalidAuth from error
    return ms


class JRiverConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for JRiver."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow."""
        self._host: str | None = None
        self._port: int | None = DEFAULT_PORT
        self._username: str | None = None
        self._password: str | None = None
        self._ssl: bool | None = DEFAULT_SSL
        self._device_per_zone: bool | None = DEFAULT_DEVICE_PER_ZONE
        self._browse_paths: list[str] | None = DEFAULT_BROWSE_PATHS
        self._device_zones: list[str] | None = None
        self._ms: MediaServer | None = None
        self._zone_names: list[str] = []

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]
            self._ssl = user_input[CONF_SSL]

            try:
                self._ms = await validate_http(self.hass, self._get_data())
            except InvalidAuth:
                return await self.async_step_credentials()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return await self.async_step_paths()

        return self._show_user_form(errors)

    async def async_step_credentials(self, user_input=None):
        """Handle username and password input."""
        errors = {}

        if user_input is not None:
            self._username = user_input.get(CONF_USERNAME)
            self._password = user_input.get(CONF_PASSWORD)

            try:
                self._ms = await validate_http(self.hass, self._get_data())
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return await self.async_step_paths()

        return self._show_credentials_form(errors)

    async def async_step_paths(self, user_input=None):
        """Handle paths input."""
        errors = {}

        if user_input is not None:
            self._browse_paths = user_input[CONF_BROWSE_PATHS]

            if not self._browse_paths:
                errors["base"] = "invalid_paths"
            else:
                self._zone_names = [
                    z.name for z in await self._ms.get_zones() if z.is_dlna is False
                ]
                if len(self._zone_names) > 1:
                    return await self.async_step_zones()
                return self._create_entry()

        return self._show_paths_form(errors)

    async def async_step_zones(self, user_input=None):
        """Handle zones input."""
        errors = {}

        if user_input is not None:
            self._device_per_zone = user_input[CONF_DEVICE_PER_ZONE]

            if self._device_per_zone is False:
                return self._create_entry()
            return await self.async_step_select_zones()

        return self._show_zones_form(errors)

    async def async_step_select_zones(self, user_input=None):
        """Handle zones input."""
        errors = {}

        if user_input is not None:
            self._device_zones = user_input[CONF_DEVICE_ZONES]

            if not self._device_zones:
                errors["base"] = "no_zones"
            else:
                return self._create_entry()

        return self._show_select_zones_form(errors)

    async def async_step_import(self, data):
        """Handle import from YAML."""
        reason = None
        try:
            await validate_http(self.hass, data)
        except InvalidAuth:
            _LOGGER.exception("Invalid MCWS credentials")
            reason = "invalid_auth"
        except CannotConnect:
            _LOGGER.exception("Cannot connect to MCWS")
            reason = "cannot_connect"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            reason = "unknown"
        else:
            return self.async_create_entry(title=data[CONF_HOST], data=data)

        return self.async_abort(reason=reason)

    @callback
    def _show_user_form(self, errors=None):
        default_port = self._port or DEFAULT_PORT
        default_ssl = self._ssl or DEFAULT_SSL

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._host): str,
                vol.Required(CONF_PORT, default=default_port): int,
                vol.Required(CONF_SSL, default=default_ssl): bool,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors or {}
        )

    @callback
    def _show_credentials_form(self, errors=None):
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_USERNAME, description={"suggested_value": self._username}
                ): str,
                vol.Optional(
                    CONF_PASSWORD, description={"suggested_value": self._password}
                ): str,
            }
        )

        return self.async_show_form(
            step_id="credentials", data_schema=schema, errors=errors or {}
        )

    @callback
    def _show_paths_form(self, errors=None):
        default_browse_paths = self._browse_paths or DEFAULT_BROWSE_PATHS
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BROWSE_PATHS, default=default_browse_paths
                ): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.TEXT, multiline=True, multiple=True
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="paths", data_schema=schema, errors=errors or {}
        )

    @callback
    def _show_zones_form(self, errors=None):
        default_device_per_zone = self._device_per_zone or DEFAULT_DEVICE_PER_ZONE
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEVICE_PER_ZONE, default=default_device_per_zone
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="zones", data_schema=schema, errors=errors or {}
        )

    @callback
    def _show_select_zones_form(self, errors=None):
        zones = self._device_zones or self._zone_names
        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ZONES, default=zones): SelectSelector(
                    SelectSelectorConfig(multiple=True, options=self._zone_names)
                ),
            }
        )

        return self.async_show_form(
            step_id="select_zones", data_schema=schema, errors=errors or {}
        )

    @callback
    def _create_entry(self):
        return self.async_create_entry(
            title=self._host,
            data=self._get_data(),
        )

    @callback
    def _get_data(self):
        data = {
            CONF_HOST: self._host,
            CONF_PORT: self._port,
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_SSL: self._ssl,
            CONF_TIMEOUT: DEFAULT_TIMEOUT,
            CONF_BROWSE_PATHS: self._browse_paths,
            CONF_DEVICE_PER_ZONE: self._device_per_zone,
            CONF_DEVICE_ZONES: self._device_zones,
        }

        return data

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> JRiverOptionsFlowHandler:
        """Get the options flow for this handler."""
        return JRiverOptionsFlowHandler(config_entry)


class JRiverOptionsFlowHandler(config_entries.OptionsFlow):
    """Allow reconfiguration of the browse paths."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BROWSE_PATHS,
                        default=self.config_entry.options[DATA_BROWSE_PATHS]
                        if DATA_BROWSE_PATHS in self.config_entry.options
                        else self.config_entry.data[DATA_BROWSE_PATHS],
                    ): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.TEXT, multiline=True, multiple=True
                        )
                    ),
                }
            ),
        )


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""
