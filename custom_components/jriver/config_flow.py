"""Config flow for JRiver integration."""
from __future__ import annotations

import logging
import re
from typing import Any

from aiohttp import ClientSession
from hamcws import (
    CannotConnectError,
    InvalidAuthError,
    InvalidRequestError,
    MediaServer,
    MediaServerError,
    ServerAddress,
    get_mcws_connection,
    resolve_access_key,
)
import voluptuous as vol

from homeassistant import config_entries, core, exceptions
from homeassistant.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_TIMEOUT,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow, FlowResult
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
    CONF_EXTRA_FIELDS,
    CONF_USE_WOL,
    DEFAULT_BROWSE_PATHS,
    DEFAULT_DEVICE_PER_ZONE,
    DEFAULT_PORT,
    DEFAULT_SSL,
    DEFAULT_TIMEOUT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _invalid_mac(mac: str) -> bool:
    """Validate the MAC address."""
    return not re.match(
        "[0-9a-f]{2}([-:]?)[0-9a-f]{2}(\\1[0-9a-f]{2}){4}$", mac.lower()
    )


async def try_connect(
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    session: ClientSession,
    ssl: bool = False,
    timeout: int = 5,
) -> MediaServer:
    """Try to connect to the given host/port."""
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
        await ms.alive()
    except CannotConnectError as error:
        raise CannotConnect from error
    except InvalidAuthError as error:
        raise InvalidAuth from error
    except InvalidRequestError as error:
        raise InvalidRequest from error
    except MediaServerError as error:
        raise InternalError from error
    return ms


async def validate_http(
    hass: core.HomeAssistant, data
) -> tuple[MediaServer, list[str]]:
    """Validate the user input allows us to connect over HTTP."""

    access_key = data.get(CONF_API_KEY, "")
    host = data[CONF_HOST]
    port = data[CONF_PORT]
    username = data.get(CONF_USERNAME)
    password = data.get(CONF_PASSWORD)
    timeout = data.get(CONF_TIMEOUT)
    ssl = data.get(CONF_SSL)
    session = async_get_clientsession(hass)

    if access_key:
        _LOGGER.debug("Looking up access key %s", access_key)
        server_info: ServerAddress | None = await resolve_access_key(
            access_key, session
        )
        if server_info:
            for ip in server_info.local_ip_list:
                try:
                    ms = await try_connect(
                        ip,
                        server_info.https_port if ssl else server_info.http_port,
                        username,
                        password,
                        session,
                        ssl=ssl,
                        timeout=timeout,
                    )
                except CannotConnectError:
                    continue
                except InvalidAuthError as error:
                    raise InvalidAuth from error
                except InvalidRequestError as error:
                    raise InvalidRequest from error
                except MediaServerError as error:
                    raise InternalError from error
                if ms:
                    _LOGGER.debug(
                        "Access key %s resolved to %s:%s",
                        access_key,
                        ip,
                        server_info.port,
                    )
                    return ms, server_info.mac_address_list
        else:
            raise InvalidAccessKey()
    ms = await try_connect(
        host, port, username, password, session, ssl=ssl, timeout=timeout
    )
    return ms, []


class JRiverConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for JRiver."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow."""
        self._access_key: str = ""
        self._host: str = ""
        self._port: int = DEFAULT_PORT
        self._friendly_name: str = ""
        self._mac_addresses: list[str] = []
        self._username: str | None = None
        self._password: str | None = None
        self._ssl: bool | None = DEFAULT_SSL
        self._device_per_zone: bool | None = DEFAULT_DEVICE_PER_ZONE
        self._browse_paths: list[str] | None = DEFAULT_BROWSE_PATHS
        self._device_zones: list[str] | None = None
        self._extra_fields: list[str] = []
        self._ms: MediaServer | None = None
        self._zone_names: list[str] = []
        self._library_fields: list[str] | None = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            self._access_key = user_input.get(CONF_API_KEY, "")
            self._host = user_input.get(CONF_HOST, "")
            self._port = user_input[CONF_PORT]
            self._ssl = user_input[CONF_SSL]
            self._friendly_name = user_input.get(CONF_NAME, "")

            try:
                unique_id = self._access_key or self._friendly_name or self._host
                if unique_id:
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()
                self._ms, self._mac_addresses = await validate_http(
                    self.hass, self._get_data()
                )
                self._host = self._ms.host
                self._port = self._ms.port
                if not self._friendly_name:
                    self._friendly_name = self._ms.media_server_info.name
            except InvalidAccessKey:
                errors["base"] = "invalid_access_key"
            except InvalidAuth:
                return await self.async_step_credentials()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except TimeoutError:
                errors["base"] = "timeout_connect"
            except (InvalidRequest, InternalError):
                errors["base"] = "unknown"
            except AbortFlow as e:
                raise e
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return await self.async_step_macs()

        return self._show_user_form(errors)

    async def async_step_credentials(self, user_input=None):
        """Handle username and password input."""
        errors = {}

        if user_input is not None:
            self._username = user_input.get(CONF_USERNAME)
            self._password = user_input.get(CONF_PASSWORD)

            try:
                self._ms, self._mac_addresses = await validate_http(
                    self.hass, self._get_data()
                )
                self._host = self._ms.host
                self._port = self._ms.port
                if not self._friendly_name:
                    self._friendly_name = self._ms.media_server_info.name
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return await self.async_step_macs()

        return self._show_credentials_form(errors)

    async def async_step_macs(self, user_input=None):
        """Handle mac address input."""
        errors = {}
        if user_input is not None:
            macs = user_input[CONF_MAC]
            use_wol = user_input[CONF_USE_WOL] is True
            if not macs and use_wol:
                errors["base"] = "no_mac_addresses"
                return self._show_macs_form(errors)

            self._mac_addresses = macs if use_wol else []
            if any(_invalid_mac(m) for m in self._mac_addresses):
                errors["base"] = "invalid_mac"
                return self._show_macs_form(errors)
            self._mac_addresses = [m.replace("-", ":") for m in macs]

            return await self.async_step_paths()

        return self._show_macs_form(errors)

    async def async_step_paths(self, user_input=None):
        """Handle paths input."""
        errors = {}

        if user_input is not None:
            self._browse_paths = user_input[CONF_BROWSE_PATHS]

            if not self._browse_paths:
                errors["base"] = "no_paths"
            else:
                self._zone_names = [z.name for z in await self._ms.get_zones()]
                if len(self._zone_names) > 1:
                    return await self.async_step_zones()
                return await self.async_step_select_playback_fields()

        return self._show_paths_form(errors)

    async def async_step_zones(self, user_input=None):
        """Handle zones input."""
        errors = {}

        if user_input is not None:
            self._device_per_zone = user_input[CONF_DEVICE_PER_ZONE]

            if self._device_per_zone is False:
                return await self.async_step_select_playback_fields()
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

    async def async_step_select_playback_fields(self, user_input=None):
        """Handle fields input."""
        errors = {}

        if user_input is not None:
            self._extra_fields = user_input[CONF_EXTRA_FIELDS]
            return self._create_entry()

        if not self._library_fields:
            self._library_fields = sorted(
                [f.name for f in await self._ms.get_library_fields()]
            )

        return self._show_select_playback_fields_form(errors)

    async def async_step_import(self, data):
        """Handle import from YAML."""
        try:
            await validate_http(self.hass, data)
        except InvalidAccessKey:
            reason = "invalid_access_key"
        except InvalidAuth:
            reason = "invalid_auth"
        except CannotConnect:
            reason = "cannot_connect"
        except TimeoutError:
            reason = "timeout_connect"
        except (InvalidRequest, InternalError):
            reason = "unknown"
        except AbortFlow as e:
            raise e
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
                vol.Optional(CONF_API_KEY, default=self._access_key): str,
                vol.Optional(CONF_HOST, default=self._host): str,
                vol.Optional(CONF_PORT, default=default_port): int,
                vol.Optional(CONF_NAME, default=self._friendly_name): str,
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
    def _show_macs_form(self, errors=None):
        schema = vol.Schema(
            {
                vol.Required(CONF_USE_WOL, default=True): bool,
                vol.Optional(CONF_MAC, default=self._mac_addresses): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, multiple=True)
                ),
            }
        )

        return self.async_show_form(
            step_id="macs", data_schema=schema, errors=errors or {}
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
    def _show_select_playback_fields_form(self, errors=None):
        extra_fields = self._extra_fields
        schema = vol.Schema(
            {
                vol.Required(CONF_EXTRA_FIELDS, default=extra_fields): SelectSelector(
                    SelectSelectorConfig(multiple=True, options=self._library_fields)
                ),
            }
        )

        return self.async_show_form(
            step_id="select_playback_fields", data_schema=schema, errors=errors or {}
        )

    @callback
    def _create_entry(self):
        return self.async_create_entry(
            title=self._access_key or self._host,
            data=self._get_data(),
        )

    @callback
    def _get_data(self):
        data = {
            CONF_API_KEY: self._access_key,
            CONF_NAME: self._friendly_name,
            CONF_HOST: self._host,
            CONF_PORT: self._port,
            CONF_MAC: self._mac_addresses,
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_SSL: self._ssl,
            CONF_TIMEOUT: DEFAULT_TIMEOUT,
            CONF_BROWSE_PATHS: self._browse_paths,
            CONF_DEVICE_PER_ZONE: self._device_per_zone,
            CONF_DEVICE_ZONES: self._device_zones,
            CONF_EXTRA_FIELDS: self._extra_fields,
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
        self._library_fields: list[str] = []
        self._browse_paths: list[str] = self._get_existing(CONF_BROWSE_PATHS, [])
        self._extra_fields: list[str] = self._get_existing(CONF_EXTRA_FIELDS, [])
        self._mac_addresses: list[str] = self._get_existing(CONF_MAC, [])
        self._use_wol: bool = self._get_existing(CONF_USE_WOL, True)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors = {}
        if user_input is not None:
            self._browse_paths = user_input.get(CONF_BROWSE_PATHS, [])
            if self._browse_paths:
                return await self.async_step_macs()
            errors["base"] = "no_paths"

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BROWSE_PATHS,
                        default=self._browse_paths,
                    ): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.TEXT, multiline=True, multiple=True
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def _ensure_library_fields(self) -> dict[str, str]:
        """Load the library fields from MediaServer if necessary."""
        errors = {}
        if not self._library_fields:
            try:
                ms, _ = await validate_http(self.hass, self.config_entry.data)
            except InvalidAccessKey:
                errors["base"] = "invalid_access_key"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except TimeoutError:
                errors["base"] = "timeout_connect"
            except (InvalidRequest, InternalError):
                errors["base"] = "unknown"
            except AbortFlow as e:
                raise e
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                self._library_fields = sorted(
                    [f.name for f in await ms.get_library_fields()]
                )
        return errors

    async def async_step_fields(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the extra fields."""
        if user_input is not None:
            self._extra_fields = user_input.get(CONF_EXTRA_FIELDS, [])
            return self.async_create_entry(title="", data=self._get_data())

        errors = await self._ensure_library_fields()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_EXTRA_FIELDS,
                    default=self._extra_fields,
                ): SelectSelector(
                    SelectSelectorConfig(multiple=True, options=self._library_fields)
                ),
            }
        )

        return self.async_show_form(step_id="fields", data_schema=schema, errors=errors)

    async def async_step_macs(self, user_input=None):
        """Handle mac address input."""
        schema = vol.Schema(
            {
                vol.Required(CONF_USE_WOL, default=True): bool,
                vol.Optional(
                    CONF_MAC,
                    default=self._mac_addresses,
                ): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, multiple=True)
                ),
            }
        )

        errors = {}
        if user_input is not None:
            macs = user_input[CONF_MAC]
            use_wol = user_input[CONF_USE_WOL] is True
            if not macs and use_wol:
                errors["base"] = "no_mac_addresses"
                return self.async_show_form(
                    step_id="macs", data_schema=schema, errors=errors
                )

            self._mac_addresses = macs if use_wol else []
            if any(_invalid_mac(m) for m in self._mac_addresses):
                errors["base"] = "invalid_mac"
                return self.async_show_form(
                    step_id="macs", data_schema=schema, errors=errors
                )

            self._mac_addresses = [m.replace("-", ":") for m in macs]

            return await self.async_step_fields()

        return self.async_show_form(step_id="macs", data_schema=schema, errors=errors)

    @callback
    def _get_data(self):
        data = {
            CONF_BROWSE_PATHS: self._browse_paths,
            CONF_EXTRA_FIELDS: self._extra_fields,
            CONF_MAC: self._mac_addresses,
            CONF_USE_WOL: self._use_wol,
        }

        return data

    def _get_existing(self, key: str, default_value: Any = None) -> Any:
        if key in self.config_entry.options:
            return self.config_entry.options[key]
        if key in self.config_entry.data:
            return self.config_entry.data[key]
        return default_value


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""


class InvalidRequest(exceptions.HomeAssistantError):
    """Error to indicate an invalid request was made."""


class InternalError(exceptions.HomeAssistantError):
    """Error to indicate an invalid request was made."""


class InvalidAccessKey(exceptions.HomeAssistantError):
    """Errors to indicate the access key is invalid."""
