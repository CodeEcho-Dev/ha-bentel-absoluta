"""Shared device-identity helpers and base entity.

Centralizing DeviceInfo construction here (rather than inline in every
platform file) avoids the classic bug of a typo'd `identifiers` tuple
silently creating a duplicate device for something that should be one -
see docs/phase5-implementation-guide.md sub-step 4.
"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BentelAbsolutaCoordinator


def panel_device_info(serial: str, name: str) -> DeviceInfo:
    """The root device for the whole panel.

    `name` is the user-chosen name from Config Flow - the serial is never
    used as, or folded into, any display name (see sub-step 3's
    "Naming"). It only appears here as the internal `identifiers` value.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=name,
        manufacturer="Bentel",
        model="Absoluta",
    )


def partition_device_info(serial: str, partition_id: int, label: str) -> DeviceInfo:
    """One device per partition, parented to the panel device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{serial}:partition:{partition_id}")},
        name=label,
        via_device=(DOMAIN, serial),
    )


def zone_device_info(serial: str, zone_id: int, label: str) -> DeviceInfo:
    """One device per unique zoneId, parented directly to the panel (not
    to a partition) - a zoneId can appear in more than one partition's
    zones[] entry (confirmed: a shared fire zone repeats in every
    partition), so a zone must be a single device keyed by zoneId, not
    duplicated per-partition. See sub-step 1's "Why zones are their own
    devices"."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{serial}:zone:{zone_id}")},
        name=label,
        via_device=(DOMAIN, serial),
    )


def all_zone_ids(data: dict[str, Any]) -> dict[int, str]:
    """zoneId -> label, deduplicated across every partition's zones[]
    group - a shared zone (e.g. a fire detector) repeats in each
    partition's group, but must resolve to exactly one HA device/entity
    set. See sub-step 1's "Why zones are their own devices"."""
    result: dict[int, str] = {}
    for group in data.get("status", {}).get("zones", []):
        for zone_id, label in zip(group.get("zoneIds", []), group.get("zoneLabels", []), strict=False):
            result.setdefault(zone_id, label)
    return result


def find_zone(data: dict[str, Any], zone_id: int) -> dict[str, Any] | None:
    """Look up one zone's current status/bypass/bypassable/label by id,
    in whichever partition group happens to list it first."""
    for group in data.get("status", {}).get("zones", []):
        ids = group.get("zoneIds", [])
        if zone_id in ids:
            idx = ids.index(zone_id)
            return {
                "label": group["zoneLabels"][idx],
                "status": group["zoneStatus"][idx],
                "bypass": group["zoneBypass"][idx],
                "bypassable": group["zoneBypassable"][idx],
            }
    return None


class BentelAbsolutaEntity(CoordinatorEntity[BentelAbsolutaCoordinator]):
    """Base entity - shared unique_id-prefix/availability logic."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BentelAbsolutaCoordinator, unique_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
