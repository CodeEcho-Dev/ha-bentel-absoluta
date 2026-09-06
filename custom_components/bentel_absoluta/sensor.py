"""Panel warning-count sensor.

Raw count only - not split into tamper/battery/mains-loss, since
`settings.enabledSubtypes`/`warningNum`'s per-condition mapping isn't
confirmed yet.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BentelAbsolutaConfigEntry
from .const import CONF_NAME, CONF_SERIAL
from .coordinator import BentelAbsolutaCoordinator
from .entity import BentelAbsolutaEntity, panel_device_info


async def async_setup_entry(hass: HomeAssistant, entry: BentelAbsolutaConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: BentelAbsolutaCoordinator = entry.runtime_data.coordinator
    async_add_entities([BentelAbsolutaWarningCount(coordinator, entry.data[CONF_SERIAL], entry.data[CONF_NAME])])


class BentelAbsolutaWarningCount(BentelAbsolutaEntity, SensorEntity):
    """`main.warningNum`."""

    _attr_name = "Warning count"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: BentelAbsolutaCoordinator, serial: str, name: str) -> None:
        super().__init__(coordinator, f"{serial}:warning_count")
        self._attr_device_info = panel_device_info(serial, name)

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.get("main", {}).get("warningNum")
