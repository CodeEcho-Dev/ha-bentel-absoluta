"""Constants for the Bentel Absoluta integration."""

from __future__ import annotations

DOMAIN = "bentel_absoluta"

# ---- Config entry data keys ----
CONF_NAME = "name"
CONF_SERIAL = "serial"
CONF_PIN = "pin"
CONF_CLIENT_ID = "client_id"

# ---- Options keys / defaults ----
# See docs/phase5-implementation-guide.md sub-step 2/3: Tier 1 has no
# session-contention risk so no floor is enforced; Tier 2 opens a real
# session so a floor protects against configuring away the "don't fight
# the real app for the session" tradeoff the whole design is built around.
CONF_SYNC_INTERVAL = "sync_interval"
CONF_SNAPSHOT_INTERVAL = "snapshot_interval"
DEFAULT_SYNC_INTERVAL = 30  # seconds
DEFAULT_SNAPSHOT_INTERVAL = 600  # seconds (10 minutes)
MIN_SNAPSHOT_INTERVAL = 120  # seconds (2 minutes) - enforced floor

# ---- API constants (from docs/protocol.md) ----
API_BASE = "https://mobile.absoluta.info"
APP_VERSION = "3.3.0"
APP_NAME = "com.imavis.absolutafree"
# Only value ever observed (docs/protocol.md) - unclear if it varies for a
# panel connected via GPRS/3G rather than LAN/Wi-Fi. See
# docs/phase5-implementation-guide.md sub-step 3's open items.
PANEL_TYPE = "LAN"

# ---- Timing (docs/phase5-implementation-guide.md sub-step 5) ----
# Bounded wait for the initial pageReady burst (main/settings/status/
# scenario) after credentials are submitted.
BURST_TIMEOUT = 10  # seconds
# Bounded wait for a command's confirming pageChanged diff. Not receiving
# one within this window is NOT an error - the command already succeeded
# per the API's fire-and-forget contract (see "Command failure semantics").
COMMAND_CONFIRM_TIMEOUT = 5  # seconds
# Defensive hard ceiling on total session lifetime, independent of the
# per-step timeouts above - see sub-step 5's "Defensive session-duration
# ceiling". Real observed session lifetimes before an involuntary drop
# ranged 50s-214s (docs/captured-messages-log.md Rounds 4-6), so these
# ceilings sit comfortably inside that range.
SESSION_CEILING_SNAPSHOT = 30  # seconds
SESSION_CEILING_COMMAND = 15  # seconds
# Config Flow's UNREACHABLE retry (sub-step 3's "Error mapping" - this
# code can mean either a wrong serial or a transiently-unreachable real
# panel, e.g. right after another client's session was evicted).
UNREACHABLE_RETRY_DELAY = 3  # seconds

# ---- Consecutive-failure thresholds before the coordinator raises
# UpdateFailed (sub-step 5 "Retry & availability") ----
TIER1_FAILURE_THRESHOLD = 5
TIER2_FAILURE_THRESHOLD = 3

# ---- partitionArming request enum (docs/protocol.md, confirmed as a
# direct 1:1 passthrough into status.armingStatus[i]) ----
ARMING_DISARM = 0
ARMING_STAY = 1
ARMING_AWAY = 2
ARMING_NO_DELAY = 3

# ---- Status value meanings (docs/protocol.md) ----
ZONE_STATUS_OK = 0
ZONE_STATUS_OPEN = 5
ZONE_STATUS_ALARM = 1
ZONE_STATUS_BYPASSED = 4
