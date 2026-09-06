"""The Bentel Absoluta integration.

See docs/phase5-implementation-guide.md for the full design. This file
stays thin by design: build the API client + coordinator, run the first
refresh, store both on entry.runtime_data, forward to platforms. No
business logic belongs here - see api.py/coordinator.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BentelAbsolutaApiClient
from .const import CONF_CLIENT_ID
from .coordinator import BentelAbsolutaCoordinator

PLATFORMS: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass
class BentelAbsolutaData:
    """Data stored on entry.runtime_data."""

    api: BentelAbsolutaApiClient
    coordinator: BentelAbsolutaCoordinator


type BentelAbsolutaConfigEntry = ConfigEntry[BentelAbsolutaData]


async def async_setup_entry(hass: HomeAssistant, entry: BentelAbsolutaConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    api = BentelAbsolutaApiClient(session, entry.data[CONF_CLIENT_ID])
    coordinator = BentelAbsolutaCoordinator(hass, entry, api)

    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = BentelAbsolutaData(api=api, coordinator=coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BentelAbsolutaConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: BentelAbsolutaConfigEntry) -> None:
    """Options (poll intervals) changed - reload to pick them up."""
    await hass.config_entries.async_reload(entry.entry_id)
