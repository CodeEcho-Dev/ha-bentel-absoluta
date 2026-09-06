"""Diagnostics support - redacts the PIN and clientId before returning
anything. See docs/phase5-implementation-guide.md sub-step 4: this
integration handles a real secret (the panel PIN) plus a persistent
clientId, and AGENTS.md is explicit about never leaking credentials.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import BentelAbsolutaConfigEntry
from .const import CONF_CLIENT_ID, CONF_PIN

TO_REDACT = {CONF_PIN, CONF_CLIENT_ID}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: BentelAbsolutaConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data.coordinator
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": dict(entry.options),
        "coordinator_data": coordinator.data,
        "tier1_failures": coordinator.tier1_failures,
        "tier2_failures": coordinator.tier2_failures,
    }
