# unifi2netbox

Synchronizes devices, interfaces, VLANs, WLANs, uplink cables, and
device type specifications from one or more UniFi Controllers into
NetBox.

NetBox is kept as the authoritative inventory while UniFi provides the
operational data.

Synchronization direction: **UniFi → NetBox** (no data is written back
to UniFi).

------------------------------------------------------------------------

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Quick Start](#quick-start)
- [Configuration Reference](#configuration-reference)
  - [Environment Variables](#environment-variables)
  - [YAML Configuration](#yaml-configuration)
  - [Site Mapping](#site-mapping)
- [Feature Reference](#feature-reference)
  - [Device Synchronization](#device-synchronization)
  - [Device Type Specs Sync](#device-type-specs-sync)
  - [Interface Synchronization](#interface-synchronization)
  - [IP Address Handling](#ip-address-handling)
  - [DHCP-to-Static IP Conversion](#dhcp-to-static-ip-conversion)
  - [VRF Handling](#vrf-handling)
  - [VLAN / WLAN Synchronization](#vlan--wlan-synchronization)
  - [Uplink Cable Synchronization](#uplink-cable-synchronization)
  - [Stale Device Cleanup](#stale-device-cleanup)
- [Supported Hardware](#supported-hardware)
- [Threading Model](#threading-model)
- [Logging](#logging)
- [Data Safety Considerations](#data-safety-considerations)
- [Deployment Recommendations](#deployment-recommendations)

------------------------------------------------------------------------

## Architecture Overview

```
UniFi Controller API (Integration or Legacy)
  ↓
UniFi API wrapper (unifi/)
  ↓
Normalization / Mapping Layer (main.py)
  ↓
NetBox API (pynetbox)
```

Core components:

| File / Directory | Purpose |
|---|---|
| `main.py` | Orchestration, device/cable/interface sync, device type specs |
| `unifi/` | UniFi API abstraction (supports Integration and Legacy API) |
| `config/` | YAML configuration and site mapping |
| `.env` | Environment variables |
| `docker-compose.yml` | Container deployment |

------------------------------------------------------------------------

## Quick Start

1. Copy `.env.example` to `.env` and fill in your credentials.
2. Optionally edit `config/site_mapping.yaml` for site name mapping.
3. Run:

```bash
docker compose up --build
```

The container runs continuously and syncs on each cycle. Verbose
logging is enabled by default (`-v` flag in docker-compose.yml).

------------------------------------------------------------------------

## Configuration Reference

Configuration comes from two sources (environment variables override
YAML values):

1. Environment variables (`.env`)
2. YAML configuration (`config/config.yaml`)

### Environment Variables

#### NetBox Connection

| Variable | Required | Default | Description |
|---|---|---|---|
| `NETBOX_URL` | Yes | — | NetBox base URL (e.g. `https://netbox.example.com`) |
| `NETBOX_TOKEN` | Yes | — | API token with write access to DCIM, IPAM, Tenancy |
| `NETBOX_TENANT` | Yes | — | Tenant name for device/IP assignment |
| `NETBOX_DEVICE_STATUS` | No | `offline` | Status for newly created devices |

#### UniFi Connection

| Variable | Required | Default | Description |
|---|---|---|---|
| `UNIFI_URLS` | Yes | — | Comma-separated or JSON array of controller URLs |
| `UNIFI_API_KEY` | * | — | API key authentication (Integration API) |
| `UNIFI_API_KEY_HEADER` | No | `X-API-KEY` | Custom header name for API key |
| `UNIFI_USERNAME` | * | — | Username authentication (Legacy API) |
| `UNIFI_PASSWORD` | * | — | Password for username auth |
| `UNIFI_MFA_SECRET` | No | — | MFA/TOTP secret for 2FA login |

\* Either `UNIFI_API_KEY` or `UNIFI_USERNAME`/`UNIFI_PASSWORD` is required.

#### Serial Number Handling

| Variable | Required | Default | Description |
|---|---|---|---|
| `NETBOX_SERIAL_MODE` | No | `mac` | How device serial is determined |

Modes:

- `mac` — Use UniFi serial, fallback to MAC (uppercase, no colons),
  then device ID
- `unifi` — Only use serial from UniFi (may be empty)
- `id` — Use UniFi serial, fallback to device ID
- `none` — Do not set serial field

#### VRF Handling

| Variable | Required | Default | Description |
|---|---|---|---|
| `NETBOX_VRF_MODE` | No | `existing` | How VRFs are managed |

Modes:

- `none` / `disabled` / `off` — No VRF; all IPs in default routing
- `existing` / `get` — Use VRF if it exists (named after site), never create
- `create` / `site` — Create VRF named after site if missing

#### Device Role Mapping

Roles can be set individually or as a JSON map:

| Variable | Default | Description |
|---|---|---|
| `NETBOX_ROLE_WIRELESS` | `Wireless AP` | Role for access points |
| `NETBOX_ROLE_LAN` | `Switch` | Role for switches |
| `NETBOX_ROLE_GATEWAY` | `Gateway Firewall` | Role for gateways/UDM |
| `NETBOX_ROLE_ROUTER` | `Router` | Role for routers |
| `NETBOX_ROLE_UNKNOWN` | `Network Device` | Fallback role |

Or as JSON (overrides individual vars):

```
NETBOX_ROLES={"WIRELESS":"Wireless AP","LAN":"Switch","GATEWAY":"Gateway Firewall"}
```

Roles are auto-created in NetBox if they do not exist.

#### Site Mapping

| Variable | Required | Default | Description |
|---|---|---|---|
| `UNIFI_USE_SITE_MAPPING` | No | `false` | Enable site name mapping |
| `UNIFI_SITE_MAPPINGS` | No | — | JSON map of UniFi→NetBox site names |

#### Feature Toggles

| Variable | Default | Description |
|---|---|---|
| `SYNC_INTERFACES` | `true` | Sync physical ports and radio interfaces |
| `SYNC_VLANS` | `true` | Sync VLANs from UniFi networks |
| `SYNC_WLANS` | `true` | Sync wireless network definitions |
| `SYNC_CABLES` | `true` | Sync uplink cables between devices |
| `SYNC_STALE_CLEANUP` | `true` | Mark missing devices as offline |

#### DHCP Range Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `DHCP_RANGES` | No | — | Comma-separated or JSON array of CIDR ranges |

Example:

```
DHCP_RANGES=10.88.91.0/24,10.100.0.0/16
```

Devices with IPs in these ranges are assigned a static IP from the
same prefix (outside the DHCP pool). The candidate IP is verified via
ping before assignment.

#### HTTP Tuning

| Variable | Default | Description |
|---|---|---|
| `UNIFI_REQUEST_TIMEOUT` | `15` | HTTP request timeout in seconds |
| `UNIFI_HTTP_RETRIES` | `3` | Number of retry attempts |
| `UNIFI_RETRY_BACKOFF_BASE` | `1.0` | Initial retry delay (seconds) |
| `UNIFI_RETRY_BACKOFF_MAX` | `30.0` | Maximum retry delay (seconds) |

------------------------------------------------------------------------

### YAML Configuration

File: `config/config.yaml`

```yaml
UNIFI:
  URLS:
    - "https://unifi1.example.com"
    - "https://unifi2.example.com"
  USE_SITE_MAPPING: false
  SITE_MAPPINGS:
    "Default": "Main Office"

NETBOX:
  URL: "https://netbox.example.com"
  TENANT: "My Organization"
  ROLES:
    GATEWAY: "Gateway"
    ROUTER: "Router"
    LAN: "Switch"
    WIRELESS: "Access Point"
    UNKNOWN: "Other"
```

Environment variables take precedence over YAML values.

------------------------------------------------------------------------

### Site Mapping

File: `config/site_mapping.yaml`

Maps UniFi site names to NetBox site names when they differ:

```yaml
"UniFi Site Name": "NetBox Site Name"
"Corporate Office": "HQ"
"Remote Branch": "Branch-01"
```

Enable with `UNIFI_USE_SITE_MAPPING=true`.

------------------------------------------------------------------------

## Feature Reference

### Device Synchronization

For each online UniFi device:

1. Extract serial, MAC, model, firmware, IP, and site
2. Look up existing device in NetBox by serial (primary) or custom
   field `unifi_mac` (secondary)
3. If found → update fields (name, firmware, IP, etc.)
4. If not found → create new device

Custom fields synced:

- `unifi_mac` — Device MAC address
- `unifi_firmware` — Firmware version
- `unifi_uptime` — Uptime in seconds
- `unifi_last_seen` — Last seen timestamp

Offline/disconnected devices are skipped during creation but existing
devices remain in NetBox (see [Stale Device Cleanup](#stale-device-cleanup)).

Manufacturer is auto-created as "Ubiquiti" if missing.

------------------------------------------------------------------------

### Device Type Specs Sync

Every device type is checked against a built-in hardware database
(`UNIFI_MODEL_SPECS`) containing ~40 UniFi models with:

- **Part number** (e.g. `USW-Pro-48-PoE`)
- **U height** (0 for desktop/wall-mount, 1 for rack-mount)
- **Port definitions** with correct interface types and counts
- **PoE budget** (stored as a comment)

On each run:

1. Device type metadata (part number, u_height, PoE comment) is
   updated if incorrect
2. Interface templates are compared against specs
3. If templates don't match → all existing templates are deleted and
   recreated from specs
4. Duplicate templates are cleaned up automatically
5. Each device type is processed only once per run (thread-safe)

This ensures NetBox device types accurately reflect the physical
hardware (port count, port types like 1000base-t, 10gbase-x-sfpp,
25gbase-x-sfp28, etc.).

------------------------------------------------------------------------

### Interface Synchronization

When `SYNC_INTERFACES=true`:

**Physical ports:**

- Syncs port name, enabled state, speed, PoE mode, MAC, and
  description
- Creates new interfaces if missing; updates existing if properties
  changed
- Cleans up invalid interfaces (names containing "?")

**Radio interfaces (APs only):**

- Syncs radio name, enabled state, and description
- Only processed for devices detected as access points

**API compatibility:**

- Integration API: fetches detailed port/radio data via device-detail
  endpoint
- Legacy API: uses `port_table` / `radio_table` from device response

------------------------------------------------------------------------

### IP Address Handling

1. Look up prefix in NetBox matching the device IP
2. If VRF is configured → filter by VRF
3. Derive subnet mask from matched prefix
4. If IP changed:
   - Delete old IP object
   - Create new IP with correct CIDR notation
5. Assign to management interface
6. Set as `primary_ip4`

------------------------------------------------------------------------

### DHCP-to-Static IP Conversion

When `DHCP_RANGES` is configured:

1. If device IP falls within a DHCP range:
   - Check if device already has a static IP in NetBox → keep it
   - Otherwise, find an available static IP from the same prefix but
     outside the DHCP pool
   - Candidate IP is verified via ping (must not respond)
   - Candidate must not be used by other UniFi devices
   - Candidate must not already exist in NetBox
   - Up to 10 candidates are checked
2. Routers/gateways are exempt (they manage their own IPs)

------------------------------------------------------------------------

### VRF Handling

VRF names match site names. Behavior depends on `NETBOX_VRF_MODE`:

| Mode | VRF exists | VRF missing |
|---|---|---|
| `none` | Not used | Not used |
| `existing` (default) | Used | Ignored (no VRF) |
| `create` | Used | Created automatically |

VRFs are cached per site to avoid duplicate API calls. Thread-safe
with per-VRF locking to prevent duplicate creation.

------------------------------------------------------------------------

### VLAN / WLAN Synchronization

When `SYNC_VLANS=true`:

- UniFi networks are mapped to NetBox VLAN objects
- Existing VLANs are looked up before creation
- No destructive deletion

When `SYNC_WLANS=true`:

- UniFi WLAN definitions are imported into NetBox

------------------------------------------------------------------------

### Uplink Cable Synchronization

When `SYNC_CABLES=true`, after all devices are synced:

**Online devices:**

1. Read uplink data from UniFi device detail API
2. Find upstream device by UUID or MAC
3. Match interfaces on both sides:
   - Source: uplink port name, or interface marked "uplink", or last
     physical port
   - Target: remote port name if provided, or first unconnected
     physical port
4. Create cable in NetBox (status: connected)
5. For APs with no physical ports: auto-creates `eth0` (1000base-t)
   interface

**Offline devices:**

- All existing cables are removed from the device's interfaces
- No new cables are created

Cable sync is thread-safe using a dedicated lock.

------------------------------------------------------------------------

### Stale Device Cleanup

When `SYNC_STALE_CLEANUP=true`:

- Compares NetBox devices against current UniFi inventory per site
- Devices present in NetBox but missing from UniFi are marked as
  `status: offline`
- **No devices are deleted** — they can recover if they come back
  online

------------------------------------------------------------------------

## Supported Hardware

The built-in hardware database includes interface templates and specs
for:

**Switches:**

| Model Code | Part Number | Ports |
|---|---|---|
| US8P60 | US-8-60W | 8× GbE, PoE 60W |
| US8P150 | US-8-150W | 8× GbE + 2× SFP, PoE 150W |
| US16P150 | US-16-150W | 16× GbE + 2× SFP, PoE 150W |
| US24P250 | US-24-250W | 24× GbE + 2× SFP, PoE 250W |
| USW Pro 48 PoE | USW-Pro-48-PoE | 48× GbE + 4× SFP+, PoE 600W |
| USW Pro 8 PoE | USW-Pro-8-PoE | 8× GbE + 2× SFP+, PoE 120W |
| USPM16P | USW-Pro-Max-16-PoE | 16× 2.5GbE + 2× SFP+, PoE 180W |
| USPM24P | USW-Pro-Max-24-PoE | 16× GbE + 8× 2.5GbE + 2× SFP+, PoE 400W |
| USW Aggregation | USW-Aggregation | 8× SFP+ |
| USW Pro Aggregation | USW-Pro-Aggregation | 28× SFP+ + 4× SFP28 |
| USW Flex Mini | USW-Flex-Mini | 5× GbE |
| USW Enterprise 8 PoE | USW-Enterprise-8-PoE | 8× 2.5GbE + 2× SFP+, PoE 120W |
| US XG 16 | US-XG-16 | 4× 10GbE + 12× SFP+ |

**Gateways:**

| Model Code | Part Number | Ports |
|---|---|---|
| UXGPRO / Gateway Pro | UXG-Pro | 2× GbE WAN + 2× SFP+ LAN |

**Access Points:**

| Model Code | Part Number | Ports |
|---|---|---|
| U7LT / UAP-AC-Lite | UAP-AC-Lite | 1× GbE (eth0) |
| U7MSH / AC Mesh | UAP-AC-M | 1× GbE (eth0) |
| UAL6 / U6 Lite | U6-Lite | 1× GbE (eth0) |
| UFLHD / FlexHD | UAP-FlexHD | 1× GbE (eth0) |

**UISP / airMAX:**

| Model Code | Part Number | Ports |
|---|---|---|
| Rocket Prism 5AC Gen2 | RP-5AC-Gen2 | 1× GbE (eth0) |
| LiteAP AC | LAP-120 | 1× GbE (eth0) |
| LiteBeam 5AC Gen2 | LBE-5AC-Gen2 | 1× GbE (eth0) |
| Nanostation 5AC | NS-5AC | 1× GbE (eth0) |

Devices not in the database are still synced — they just won't have
pre-configured interface templates.

------------------------------------------------------------------------

## Threading Model

```
Controller Thread Pool (MAX_CONTROLLER_THREADS=5)
  └── Site Thread Pool (MAX_SITE_THREADS=8)
        └── Device Thread Pool (MAX_DEVICE_THREADS=8)
```

HTTP connection pool: 50 connections.

NetBox client initialized with `threading=True` for concurrent API
calls.

Thread-safe resources with dedicated locks:

- VRF cache and creation
- Custom fields, tags, VLANs
- Cable creation
- DHCP ranges and static IP assignments
- Device type specs sync
- Postable fields cache

------------------------------------------------------------------------

## Logging

Default log level: **INFO** (verbose mode with `-v` flag).

Logs include:

- Device creation/updates
- Interface and template sync
- VLAN/WLAN sync status
- Cable creation/removal
- Stale device marking
- Device type spec updates
- API errors and warnings

Run with verbose logging:

```bash
python main.py -v
```

Or via Docker (default in docker-compose.yml):

```bash
docker compose up
```

------------------------------------------------------------------------

## Data Safety Considerations

- Old IP objects are deleted when IP changes
- Stale devices are marked offline, **never deleted**
- Cables on offline devices are removed automatically
- No rollback if an API call partially fails
- SSL verification is disabled by default
- Script assumes valid prefix structure in NetBox

------------------------------------------------------------------------

## Deployment Recommendations

- Run as a scheduled container (`restart: unless-stopped`)
- Use a staging NetBox instance for initial testing
- Monitor NetBox API response times
- Start with lower thread counts for large environments (>1000
  devices)
- Enable SSL verification in production
- Review `DHCP_RANGES` carefully before enabling

------------------------------------------------------------------------

## Disclaimer

This script performs direct write operations in NetBox via API.
Validate configuration and test thoroughly before production use.
