"""Zone open/closed + alarm, panel installer-remote-access/alarm-active,
and partition not-ready binary sensors.

See docs/phase5-implementation-guide.md sub-step 1's entity table.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BentelAbsolutaConfigEntry
from .const import CONF_NAME, CONF_SERIAL, ZONE_STATUS_ALARM, ZONE_STATUS_BYPASSED, ZONE_STATUS_OK
from .coordinator import BentelAbsolutaCoordinator
from .entity import (
    BentelAbsolutaEntity,
    all_zone_ids,
    find_zone,
    panel_device_info,
    partition_device_info,
    zone_device_info,
)


async def async_setup_entry(hass: HomeAssistant, entry: BentelAbsolutaConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: BentelAbsolutaCoordinator = entry.runtime_data.coordinator
    serial = entry.data[CONF_SERIAL]
    name = entry.data[CONF_NAME]

    entities: list[BinarySensorEntity] = [
        BentelAbsolutaInstallerRemoteAccess(coordinator, serial, name),
        BentelAbsolutaAlarmActive(coordinator, serial, name),
    ]

    status = coordinator.data.get("status", {})
    partition_ids = status.get("partitionIds", [])
    partition_labels = status.get("partitionLabels", [])
    for i, partition_id in enumerate(partition_ids):
        label = partition_labels[i] if i < len(partition_labels) else f"Partition {partition_id}"
        entities.append(BentelAbsolutaPartitionNotReady(coordinator, serial, partition_id, label))

    for zone_id, label in all_zone_ids(coordinator.data).items():
        entities.append(BentelAbsolutaZoneOpening(coordinator, serial, zone_id, label))
        entities.append(BentelAbsolutaZoneAlarm(coordinator, serial, zone_id, label))

    async_add_entities(entities)


class BentelAbsolutaInstallerRemoteAccess(BentelAbsolutaEntity, BinarySensorEntity):
    """`main.installerRemoteAccess`."""

    _attr_name = "Installer remote access"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BentelAbsolutaCoordinator, serial: str, name: str) -> None:
        super().__init__(coordinator, f"{serial}:installer_remote_access")
        self._attr_device_info = panel_device_info(serial, name)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get("main", {}).get("installerRemoteAccess")


class BentelAbsolutaAlarmActive(BentelAbsolutaEntity, BinarySensorEntity):
    """Top-level "something is in alarm somewhere" flag - `main.alarmNum`."""

    _attr_name = "Alarm active"
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(self, coordinator: BentelAbsolutaCoordinator, serial: str, name: str) -> None:
        super().__init__(coordinator, f"{serial}:alarm_active")
        self._attr_device_info = panel_device_info(serial, name)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get("main", {}).get("alarmNum", 0) > 0


class BentelAbsolutaPartitionNotReady(BentelAbsolutaEntity, BinarySensorEntity):
    """Mirrors the app's "Open Zones" pill - `status.partitionStatus[i]`.

    Confirmed 2026-09-05 this field tracks zone readiness only, not arm
    state - an armed, all-clear partition also reads 0 here, so this
    sensor stays accurate regardless of the partition's arm state.
    """

    _attr_name = "Not ready"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: BentelAbsolutaCoordinator, serial: str, partition_id: int, label: str
    ) -> None:
        super().__init__(coordinator, f"{serial}:partition:{partition_id}:not_ready")
        self._partition_id = partition_id
        self._attr_device_info = partition_device_info(serial, partition_id, label)

    @property
    def is_on(self) -> bool | None:
        status = self.coordinator.data.get("status", {})
        ids = status.get("partitionIds", [])
        if self._partition_id not in ids:
            return None
        idx = ids.index(self._partition_id)
        partition_status = status.get("partitionStatus", [])
        if idx >= len(partition_status):
            return None
        return partition_status[idx] != ZONE_STATUS_OK


class BentelAbsolutaZoneOpening(BentelAbsolutaEntity, BinarySensorEntity):
    """`zoneStatus` - anything other than closed(0)/bypassed(4) counts as
    open, which also defensively covers alarm(1) and any future/unknown
    value rather than only the confirmed open(5)."""

    _attr_name = "Opening"
    _attr_device_class = BinarySensorDeviceClass.OPENING

    def __init__(self, coordinator: BentelAbsolutaCoordinator, serial: str, zone_id: int, label: str) -> None:
        super().__init__(coordinator, f"{serial}:zone:{zone_id}:opening")
        self._zone_id = zone_id
        self._attr_device_info = zone_device_info(serial, zone_id, label)

    @property
    def is_on(self) -> bool | None:
        zone = find_zone(self.coordinator.data, self._zone_id)
        if zone is None:
            return None
        return zone["status"] not in (ZONE_STATUS_OK, ZONE_STATUS_BYPASSED)


class BentelAbsolutaZoneAlarm(BentelAbsolutaEntity, BinarySensorEntity):
    """`zoneStatus == 1` - confirmed via the one incidental real-alarm capture."""

    _attr_name = "Alarm"
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(self, coordinator: BentelAbsolutaCoordinator, serial: str, zone_id: int, label: str) -> None:
        super().__init__(coordinator, f"{serial}:zone:{zone_id}:alarm")
        self._zone_id = zone_id
        self._attr_device_info = zone_device_info(serial, zone_id, label)

    @property
    def is_on(self) -> bool | None:
        zone = find_zone(self.coordinator.data, self._zone_id)
        if zone is None:
            return None
        return zone["status"] == ZONE_STATUS_ALARM
