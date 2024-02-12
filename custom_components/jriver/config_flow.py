"""Config flow for JRiver integration."""
from __future__ import annotations

import logging
import re
from typing import Any

import hamcws
from hamcws import (
    BrowsePath,
    BrowseRule,
    CannotConnectError,
    InvalidAccessKeyError,
    InvalidAuthError,
    InvalidRequestError,
    MediaServer,
    MediaServerError,
    parse_browse_paths_from_text,
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
    _can_refresh_paths,
)

_LOGGER = logging.getLogger(__name__)


def _invalid_mac(mac: str) -> bool:
    """Validate the MAC address."""
    return not re.match(
        "[0-9a-f]{2}([-:]?)[0-9a-f]{2}(\\1[0-9a-f]{2}){4}$", mac.lower()
    )


async def connect_to_media_server(
    hass: core.HomeAssistant, data
) -> tuple[MediaServer, list[str]]:
    """Validate the user input allows us to connect over HTTP."""
    try:
        return await hamcws.load_media_server(
            access_key=data.get(CONF_API_KEY, ""),
            host=data.get(CONF_HOST, ""),
            port=data[CONF_PORT],
            username=data.get(CONF_USERNAME, None),
            password=data.get(CONF_PASSWORD, None),
            use_ssl=data.get(CONF_SSL, False),
            session=async_get_clientsession(hass),
            timeout=data.get(CONF_TIMEOUT, 5),
        )
    except InvalidAuthError as error:
        raise InvalidAuth from error
    except CannotConnectError as error:
        raise CannotConnect from error
    except InvalidRequestError as error:
        raise InvalidRequest from error
    except MediaServerError as error:
        raise InternalError from error
    except InvalidAccessKeyError as error:
        raise InvalidAccessKey from error


class JRiverConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for JRiver."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow."""
        self._access_key: str = ""
        self._host: str = ""
        self._port: int = DEFAULT_PORT
        self._friendly_name: str = ""
        self._expect_wol: bool = False
        self._mac_addresses: list[str] = []
        self._username: str | None = None
        self._password: str | None = None
        self._ssl: bool | None = DEFAULT_SSL
        self._device_per_zone: bool | None = DEFAULT_DEVICE_PER_ZONE
        self._browse_paths: list[str] = []
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
                await self._try_connect_to_media_server()
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

    async def _try_connect_to_media_server(self):
        self._ms, self._mac_addresses = await connect_to_media_server(
            self.hass, self._get_data()
        )
        self._host = self._ms.host
        self._port = self._ms.port
        if not self._friendly_name:
            self._friendly_name = self._ms.media_server_info.name
        if self._mac_addresses:
            self._expect_wol = True

    async def async_step_credentials(self, user_input=None):
        """Handle username and password input."""
        errors = {}

        if user_input is not None:
            self._username = user_input.get(CONF_USERNAME)
            self._password = user_input.get(CONF_PASSWORD)

            try:
                await self._try_connect_to_media_server()
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
            self._expect_wol = user_input[CONF_USE_WOL]
            if not macs and self._expect_wol:
                errors["base"] = "no_mac_addresses"
                return self._show_macs_form(errors)

            self._mac_addresses = macs if self._expect_wol else []
            if any(_invalid_mac(m) for m in self._mac_addresses):
                errors["base"] = "invalid_mac"
                return self._show_macs_form(errors)
            self._mac_addresses = [m.replace("-", ":") for m in macs]

            return await self.async_step_paths()

        return self._show_macs_form(errors)

    async def async_step_paths(self, user_input=None):
        """Handle paths input, required for MC <32.0.6."""
        errors = {}

        async def _next_form():
            self._zone_names = [z.name for z in await self._ms.get_zones()]
            if len(self._zone_names) > 1:
                return await self.async_step_zones()
            return await self.async_step_select_playback_fields()

        if _can_refresh_paths(self._ms):
            return await _next_form()

        if not self._browse_paths:
            self._browse_paths = sorted(DEFAULT_BROWSE_PATHS)

        if user_input is not None:
            self._browse_paths = user_input[CONF_BROWSE_PATHS]

            if self._browse_paths:
                if self._browse_paths_are_valid():
                    return await _next_form()
                errors["base"] = "invalid_paths"
            else:
                errors["base"] = "no_paths"

        return self._show_paths_form(errors)

    def _browse_paths_are_valid(self) -> bool:
        return parse_browse_paths_from_text(self._browse_paths) is not None

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
                return await self.async_step_select_playback_fields()

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
            await connect_to_media_server(self.hass, data)
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
                vol.Required(CONF_USE_WOL, default=self._expect_wol): bool,
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
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BROWSE_PATHS, default=self._browse_paths
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
            CONF_USE_WOL: self._expect_wol,
        }

        return data

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> JRiverOptionsFlowHandler:
        """Get the options flow for this handler."""
        return JRiverOptionsFlowHandler(config_entry)


def _format_rule(r: BrowseRule) -> str:
    return f"{','.join(r.get_names())}{'|' if r.get_categories() else ''}{','.join(r.get_categories())}"


class JRiverOptionsFlowHandler(config_entries.OptionsFlow):
    """Allow reconfiguration of the browse paths."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self._library_fields: list[str] = []
        self._browse_paths: list[BrowsePath] = self._get_existing(CONF_BROWSE_PATHS, [])
        self._extra_fields: list[str] = self._get_existing(CONF_EXTRA_FIELDS, [])
        self._mac_addresses: list[str] = self._get_existing(CONF_MAC, [])
        self._use_wol: bool = self._get_existing(CONF_USE_WOL, True)
        self._ms: MediaServer | str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors = {}

        if not self._ms:
            self._ms = await self._reload_ms()
            if isinstance(self._ms, str):
                errors["base"] = self._ms
                self._ms = None

        if self._ms and _can_refresh_paths(self._ms):
            return await self.async_step_macs()

        if not errors and user_input is not None:
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

    async def _ensure_library_fields(self) -> None:
        """Load the library fields from MediaServer if necessary."""
        if not self._library_fields and isinstance(self._ms, MediaServer):
            self._library_fields = sorted(
                [f.name for f in await self._ms.get_library_fields()]
            )

    async def async_step_fields(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the extra fields."""
        if user_input is not None:
            self._extra_fields = user_input.get(CONF_EXTRA_FIELDS, [])
            return self.async_create_entry(title="", data=self._get_data())

        await self._ensure_library_fields()

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

        return self.async_show_form(step_id="fields", data_schema=schema, errors={})

    async def async_step_macs(self, user_input=None):
        """Handle mac address input."""
        schema = vol.Schema(
            {
                vol.Required(CONF_USE_WOL, default=self._use_wol): bool,
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
            self._use_wol = user_input[CONF_USE_WOL]
            if not macs and self._use_wol:
                errors["base"] = "no_mac_addresses"
                return self.async_show_form(
                    step_id="macs", data_schema=schema, errors=errors
                )

            self._mac_addresses = macs if self._use_wol else []
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

    async def _reload_ms(self) -> MediaServer | str:
        try:
            ms, _ = await connect_to_media_server(self.hass, self.config_entry.data)
            return ms
        except InvalidAccessKey:
            return "invalid_access_key"
        except InvalidAuth:
            return "invalid_auth"
        except CannotConnect:
            return "cannot_connect"
        except TimeoutError:
            return "timeout_connect"
        except (InvalidRequest, InternalError):
            return "unknown"
        except AbortFlow as e:
            raise e
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            return "unknown"


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
