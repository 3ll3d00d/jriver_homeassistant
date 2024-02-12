"""Test the JRiver Media Center config flow."""
from unittest.mock import AsyncMock, Mock, patch

from awesomeversion import AwesomeVersion
from hamcws import (
    BrowseRule,
    CannotConnectError,
    InvalidAccessKeyError,
    InvalidAuthError,
    InvalidRequestError,
    LibraryField,
    MediaServer,
    MediaServerError,
    MediaServerInfo,
    Zone,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jriver.coordinator import MediaServerData
from homeassistant import config_entries
from custom_components.jriver.const import (
    CONF_BROWSE_PATHS,
    CONF_DEVICE_PER_ZONE,
    CONF_DEVICE_ZONES,
    CONF_EXTRA_FIELDS,
    CONF_USE_WOL,
    DOMAIN,
)
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
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType


async def test_access_key_is_invalid_errors(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Test bad access key produces error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}

    with patch(
        "hamcws.load_media_server",
    ) as patched:
        patched.side_effect = InvalidAccessKeyError()

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_KEY: "abcdef",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_access_key"}
    assert not mock_setup_entry.mock_calls


@pytest.mark.parametrize(
    ("side_effect", "named_error"),
    [
        (CannotConnectError, "cannot_connect"),
        (TimeoutError, "timeout_connect"),
        (InvalidRequestError, "unknown"),
        (MediaServerError, "unknown"),
        (Exception, "unknown"),
    ],
)
async def test_ip_port_connection_errors(
    hass: HomeAssistant, mock_setup_entry: AsyncMock, side_effect, named_error
) -> None:
    """Test assorted connection error produces error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}

    with patch(
        "hamcws.load_media_server",
    ) as patched:
        patched.side_effect = side_effect()

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "1.1.1.1",
                CONF_PORT: 52199,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": named_error}
    assert not mock_setup_entry.mock_calls


@pytest.mark.parametrize(
    "initial_vals",
    [
        {
            CONF_HOST: "1.1.1.1",
            CONF_PORT: 52199,
        },
        {CONF_API_KEY: "abcdef"},
    ],
)
async def test_invalid_auth_prompts_for_creds(
    hass: HomeAssistant, mock_setup_entry: AsyncMock, initial_vals
) -> None:
    """Test we handle invalid auth."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}

    with patch(
        "hamcws.load_media_server",
    ) as patched:
        patched.side_effect = InvalidAuthError()

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            initial_vals,
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "credentials"
    assert not mock_setup_entry.mock_calls

    # request and supply a invalid user/pass
    with patch(
        "hamcws.load_media_server",
    ) as patched:
        patched.side_effect = InvalidAuthError()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "test-username",
                CONF_PASSWORD: "test-password",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert not mock_setup_entry.mock_calls

    # request and supply a user/pass but connection fails
    with patch(
        "hamcws.load_media_server",
    ) as patched:
        patched.side_effect = CannotConnectError()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "test-username",
                CONF_PASSWORD: "test-password",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert not mock_setup_entry.mock_calls

    # request and supply a user/pass and an unknown exception occurs
    with patch(
        "hamcws.load_media_server",
    ) as patched:
        patched.side_effect = Exception()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "test-username",
                CONF_PASSWORD: "test-password",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
    assert not mock_setup_entry.mock_calls

    # request and supply a user/pass and we can continue
    with patch(
        "hamcws.load_media_server",
        return_value=(Mock(MediaServer), []),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "test-username",
                CONF_PASSWORD: "test-password",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}
    assert result["step_id"] == "macs"
    assert not mock_setup_entry.mock_calls


async def test_connect_via_access_key_provides_mac_address(
    hass: HomeAssistant, mock_setup_entry: AsyncMock, media_server: MediaServer
) -> None:
    """Test no user input is required if api key provides a valid mac address."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.async_block_till_done()

    mac_addresses = ["ab:cd:ef:fe:dc:ba"]
    with patch(
        "hamcws.load_media_server",
        return_value=(media_server, mac_addresses),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: "abcdef"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}
    assert result["step_id"] == "macs"

    # expecting a MAC address to be present
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USE_WOL: True, CONF_MAC: mac_addresses}
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}
    assert (
        result["step_id"] == "paths"
        if media_server.media_server_info.version.startswith("31")
        else "select_playback_fields"
    )


async def test_mac_address_must_be_valid_if_required(
    hass: HomeAssistant, mock_setup_entry: AsyncMock, media_server: MediaServer
) -> None:
    """Test user can supply mac addresses and they get validated."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.async_block_till_done()

    with patch(
        "hamcws.load_media_server",
        return_value=(media_server, []),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "1.1.1.1", CONF_PORT: 52199},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}
    assert result["step_id"] == "macs"

    # expecting a MAC address to be present when required
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USE_WOL: True, CONF_MAC: []}
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "no_mac_addresses"}
    assert result["step_id"] == "macs"

    # expecting a valid MAC address to be present when required
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USE_WOL: True, CONF_MAC: ["abcdefghij"]}
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_mac"}
    assert result["step_id"] == "macs"

    # ignores an invalid mac address if it's not required
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USE_WOL: False, CONF_MAC: ["abcdefghij"]}
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}
    assert (
        result["step_id"] == "paths"
        if media_server.media_server_info.version.startswith("31")
        else "select_playback_fields"
    )


async def test_browse_paths_must_be_supplied(
    hass: HomeAssistant, mock_setup_entry: AsyncMock, media_server: MediaServer
) -> None:
    """Test user must provided some browse paths."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.async_block_till_done()

    with patch(
        "hamcws.load_media_server",
        return_value=(media_server, []),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "1.1.1.1", CONF_PORT: 52199},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}
    assert result["step_id"] == "macs"

    # expecting a MAC address to be present when required
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USE_WOL: True, CONF_MAC: ["ab:cd:ef:fe:dc:ba", "12:34:56:78:90:09"]},
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}
    if media_server.media_server_info.version.startswith("31"):
        assert result["step_id"] == "paths"

        # must provide at least one path
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BROWSE_PATHS: []}
        )
        await hass.async_block_till_done()

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "no_paths"}
        assert result["step_id"] == "paths"

        # require at least one path
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BROWSE_PATHS: ["test,me|out"]}
        )
        await hass.async_block_till_done()

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {}

    assert result["step_id"] == "select_playback_fields"


def _get_zone(id: int, name: str):
    return Zone(
        {
            f"ZoneID{id}": id,
            f"ZoneName{id}": name,
        },
        id,
        0,
    )


@pytest.mark.parametrize(
    "zones",
    [
        [],
        [_get_zone(1, "Player")],
        [_get_zone(1, "Player"), _get_zone(2, "Testing")],
        [_get_zone(1, "Player"), _get_zone(2, "Testing"), _get_zone(3, "Switch")],
    ],
)
async def test_zone_selection_if_multiple_zones(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    zones: list[Zone],
    media_server: MediaServer,
) -> None:
    """Test user can choose to configure an entity per zone."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.async_block_till_done()

    with patch(
        "hamcws.load_media_server",
        return_value=(media_server, []),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "1.1.1.1", CONF_PORT: 52199},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}
    assert result["step_id"] == "macs"

    media_server.get_zones = AsyncMock(return_value=zones)

    # expecting a MAC address to be present when required
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USE_WOL: True, CONF_MAC: ["ab:cd:ef:fe:dc:ba", "12:34:56:78:90:09"]},
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}

    if media_server.media_server_info.version.startswith("31"):
        assert result["step_id"] == "paths"

        # must provide at least one path
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BROWSE_PATHS: ["test,me|out"]}
        )
        await hass.async_block_till_done()

    if len(zones) < 2:
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {}
        assert result["step_id"] == "select_playback_fields"
    else:
        # must review zones
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {}
        assert result["step_id"] == "zones"

        if len(zones) == 2:
            # select per zone
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_DEVICE_PER_ZONE: True}
            )
            await hass.async_block_till_done()
            assert result["type"] == FlowResultType.FORM
            assert result["errors"] == {}
            assert result["step_id"] == "select_zones"

            # must select at least one
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_DEVICE_ZONES: []}
            )

            await hass.async_block_till_done()
            assert result["type"] == FlowResultType.FORM
            assert result["errors"] == {"base": "no_zones"}
            assert result["step_id"] == "select_zones"

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_DEVICE_ZONES: ["Player"]}
            )

            await hass.async_block_till_done()
            assert result["type"] == FlowResultType.FORM
            assert result["errors"] == {}
            assert result["step_id"] == "select_playback_fields"
        else:
            # single player
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_DEVICE_PER_ZONE: False}
            )
            await hass.async_block_till_done()
            assert result["type"] == FlowResultType.FORM
            assert result["errors"] == {}
            assert result["step_id"] == "select_playback_fields"


@pytest.mark.parametrize("add_fields", [True, False])
async def test_can_supply_playback_fields(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    add_fields: bool,
    media_server: MediaServer,
) -> None:
    """Test entry created with extra fields or not."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.async_block_till_done()

    values: list[LibraryField] = [LibraryField("A", "A", "A", "A")]
    media_server.get_library_fields = AsyncMock(return_value=values)
    with patch(
        "hamcws.load_media_server",
        return_value=(media_server, []),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "1.1.1.1", CONF_PORT: 52199},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}
    assert result["step_id"] == "macs"

    # no MAC
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USE_WOL: False, CONF_MAC: []},
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}
    if media_server.media_server_info.version.startswith("31"):
        assert result["step_id"] == "paths"

        # one path
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BROWSE_PATHS: ["test,me|out"]}
        )
        await hass.async_block_till_done()
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {}

    assert result["step_id"] == "select_playback_fields"

    # fields either way
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EXTRA_FIELDS: ["A"] if add_fields is True else []}
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_reconfigure_options(
    hass: HomeAssistant, media_server: MediaServer
) -> None:
    """Can reconfigure options."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="reconfigure_me",
        data={
            CONF_API_KEY: "",
            CONF_NAME: "testme",
            CONF_HOST: "localhost",
            CONF_PORT: 12345,
            CONF_MAC: ["aa:aa:aa:aa:aa:aa"],
            CONF_USERNAME: "",
            CONF_PASSWORD: "",
            CONF_SSL: False,
            CONF_TIMEOUT: 5,
            CONF_BROWSE_PATHS: "a,b|c,d",
            CONF_DEVICE_PER_ZONE: False,
            CONF_DEVICE_ZONES: [],
            CONF_EXTRA_FIELDS: [],
        },
    )
    with (
        patch(
            "custom_components.jriver.MediaServerUpdateCoordinator._async_update_data",
            return_value=MediaServerData(server_info=MediaServerInfo({})),
        ),
        patch("custom_components.jriver._get_ms", return_value=media_server),
        patch(
            "custom_components.jriver.config_flow.JRiverOptionsFlowHandler._reload_ms",
            return_value=media_server,
        ),
    ):
        values: list[BrowseRule] = [
            BrowseRule(r"Audio\Artist", r"Artist\Album", ""),
            BrowseRule(r"Video\Movies", "", ""),
        ]
        media_server.get_browse_rules = AsyncMock(return_value=values)
        media_server.get_library_fields = AsyncMock(return_value={})

        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # show initial form
        result = await hass.config_entries.options.async_init(config_entry.entry_id)

        if media_server.media_server_info.version.startswith("31"):
            # no paths
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], user_input={CONF_BROWSE_PATHS: []}
            )
            assert result["type"] == FlowResultType.FORM
            assert result["errors"] == {"base": "no_paths"}
            assert result["step_id"] == "init"

            # can reload paths
            if AwesomeVersion(media_server.media_server_info.version) >= "32.0.6":
                result = await hass.config_entries.options.async_configure(
                    result["flow_id"], user_input={"refresh_paths": True}
                )
                assert result["type"] == FlowResultType.FORM
                assert result["errors"] == {}
                assert result["step_id"] == "init"

            # some paths
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                user_input={CONF_BROWSE_PATHS: ["a,b|c,d", "1,2|3,4"]},
            )
            assert result["type"] == FlowResultType.FORM
            assert result["errors"] == {}

        assert result["step_id"] == "macs"

        # need a mac
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={CONF_USE_WOL: True, CONF_MAC: []}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "no_mac_addresses"}
        assert result["step_id"] == "macs"

        # need a valid mac
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_USE_WOL: True, CONF_MAC: ["zzzzaaaabbcc"]},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_mac"}
        assert result["step_id"] == "macs"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_USE_WOL: True, CONF_MAC: ["aa:aa:aa:aa:aa:aa"]},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {}
        assert result["step_id"] == "fields"

        # optional extra fields
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_EXTRA_FIELDS: []},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
