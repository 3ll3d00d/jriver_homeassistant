"""Common fixtures for the JRiver Media Center tests."""
from collections.abc import Generator
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

from hamcws import MediaServer, MediaServerInfo
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock, None, None]:
    """Override async_setup_entry."""
    with patch(
        "custom_components.jriver.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        yield mock_setup_entry


@pytest.fixture
def media_server() -> MediaServer:
    """Mock a MediaServer."""
    ms = AsyncMock(MediaServer)
    type(ms).host = PropertyMock(return_value="localhost")
    type(ms).port = PropertyMock(return_value=52199)
    msi = Mock(MediaServerInfo)
    type(ms).media_server_info = PropertyMock(return_value=msi)
    type(msi).name = PropertyMock(return_value="localhost")
    return ms
