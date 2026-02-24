# Configuration Reference

Runtime config is built from environment variables (`.env`) only.

## Required Settings

| Variable | Required | Default in code | Notes |
|---|---|---|---|
| `UNIFI_URLS` | Yes | — | Comma-separated list or JSON array |
| `NETBOX_URL` | Yes | — | NetBox base URL |
| `NETBOX_TOKEN` | Yes | — | NetBox API token |
| `NETBOX_IMPORT_TENANT` or `NETBOX_TENANT` | Yes | — | Existing tenant name (`NETBOX_IMPORT_TENANT` takes precedence) |
| `UNIFI_API_KEY` | * | — | Preferred auth mode |
| `UNIFI_USERNAME` + `UNIFI_PASSWORD` | * | — | Fallback auth mode |

\* Provide either API key or username/password.

Note: `unifi.ui.com` cloud API keys are not equivalent to local UniFi Network Integration API keys.

## UniFi API Settings

| Variable | Required | Default in code | Description |
|---|---|---|---|
| `UNIFI_API_KEY_HEADER` | No | auto-probe | Custom API key header; if omitted, standard headers are probed |
| `UNIFI_MFA_SECRET` | No | unset | Optional TOTP for session login |
| `UNIFI_VERIFY_SSL` | No | `true` | Verify UniFi TLS certificates |
| `UNIFI_PERSIST_SESSION` | No | `true` | Persist UniFi session cache to `~/.unifi_session.json` (file mode enforced to `0600`, and tightened automatically on load if too open) |
| `UNIFI_REQUEST_TIMEOUT` | No | `15` | Request timeout in seconds |
| `UNIFI_HTTP_RETRIES` | No | `3` | Retry attempts for transient failures |
| `UNIFI_RETRY_BACKOFF_BASE` | No | `1.0` | Exponential backoff base delay (seconds) |
| `UNIFI_RETRY_BACKOFF_MAX` | No | `30.0` | Max backoff delay (seconds) |

### URL format examples

Integration API:
```bash
UNIFI_URLS=https://controller.example.com/proxy/network/integration/v1
```

Integration API (alternate path):
```bash
UNIFI_URLS=https://controller.example.com/integration/v1
```

Base URL (integration base is auto-probed):
```bash
UNIFI_URLS=https://controller.example.com
```

Legacy/session login:
```bash
UNIFI_URLS=https://controller.example.com:8443
```

Multiple controllers:
```bash
UNIFI_URLS=https://ctrl1.example.com/proxy/network/integration/v1,https://ctrl2.example.com:8443
```

If Integration API is unavailable, use local controller base URL + `UNIFI_USERNAME`/`UNIFI_PASSWORD`.

## NetBox Settings

| Variable | Required | Default in code | Description |
|---|---|---|---|
| `NETBOX_DEVICE_STATUS` | No | `offline` | Status for newly created devices |
| `NETBOX_VERIFY_SSL` | No | `true` | Verify NetBox TLS certificates |
| `NETBOX_SERIAL_MODE` | No | `mac` | `mac`, `unifi`, `id`, `none` |
| `NETBOX_VRF_MODE` | No | `existing` | `none`, `existing`, `create` |
| `NETBOX_DEFAULT_VRF` | No | empty | If set, use this VRF name for all imported IPs instead of site-based VRF names |

### Device roles

Configure either:
- individual vars:
  - `NETBOX_ROLE_WIRELESS`
  - `NETBOX_ROLE_LAN`
  - `NETBOX_ROLE_GATEWAY`
  - `NETBOX_ROLE_ROUTER`
  - `NETBOX_ROLE_UNKNOWN`
- or JSON mapping:
  - `NETBOX_ROLES={"WIRELESS":"Wireless AP","LAN":"Switch",...}`

`NETBOX_ROLES` overrides individual role vars.

## Site Mapping

| Variable | Required | Default in code | Description |
|---|---|---|---|
| `UNIFI_USE_SITE_MAPPING` | No | `false` | Optional legacy toggle (kept for compatibility) |
| `UNIFI_SITE_MAPPINGS` | No | unset | UniFi->NetBox name mapping (`JSON` or `key=value` pairs) |

## Device Specs Auto-Refresh

| Variable | Required | Default in code | Description |
|---|---|---|---|
| `UNIFI_SPECS_AUTO_REFRESH` | No | `false` | Refresh bundled specs from upstream Device Type Library on startup |
| `UNIFI_SPECS_INCLUDE_STORE` | No | `false` | Also enrich from UniFi Store technical specs (slower) |
| `UNIFI_SPECS_REFRESH_TIMEOUT` | No | `45` | Timeout (seconds) for Device Type Library tarball fetch |
| `UNIFI_SPECS_STORE_TIMEOUT` | No | `15` | Timeout (seconds) per UniFi Store product request |
| `UNIFI_SPECS_STORE_MAX_WORKERS` | No | `8` | Parallel workers for UniFi Store enrichment |
| `UNIFI_SPECS_WRITE_CACHE` | No | `false` | Write refreshed bundle back to `data/ubiquiti_device_specs.json` |

Notes:
- This is optional and disabled by default.
- Runtime precedence is still: hardcoded `UNIFI_MODEL_SPECS` overrides community/store data.
- For one-off/manual refresh, use `python3 tools/refresh_unifi_specs.py`.

## DHCP / Static IP Behavior

| Variable | Required | Default in code | Description |
|---|---|---|---|
| `DHCP_AUTO_DISCOVER` | No | `true` | Discover DHCP ranges from UniFi network configs |
| `DHCP_RANGES` | No | empty | Manual CIDRs, merged with discovered ranges |
| `DEFAULT_GATEWAY` | No | empty | Fallback gateway if UniFi network config lacks one |
| `DEFAULT_DNS` | No | empty | Fallback DNS servers (comma-separated) if UniFi lacks them |

When a device IP is in a DHCP range, static replacement logic assigns a free IP from the same prefix (except gateways). Gateway and DNS are read from UniFi's network config (`gateway_ip`, `dhcpd_dns_1-4`). If unavailable, `DEFAULT_GATEWAY` and `DEFAULT_DNS` env vars are used as fallback.

Important: DHCP-to-static conversion also updates the device IP configuration in UniFi (writeback for that specific flow).
To avoid UniFi writeback entirely, disable DHCP conversion inputs:
- `DHCP_AUTO_DISCOVER=false`
- leave `DHCP_RANGES` unset/empty

## Feature Toggles

| Variable | Default in code | Description |
|---|---|---|
| `SYNC_INTERFACES` | `true` | Sync physical ports and radios |
| `SYNC_VLANS` | `true` | Sync VLANs |
| `SYNC_WLANS` | `true` | Sync WLANs |
| `SYNC_CABLES` | `true` | Sync uplink cables |
| `SYNC_STALE_CLEANUP` | `true` | Mark missing devices offline |

## Threading

| Variable | Default in code |
|---|---|
| `MAX_CONTROLLER_THREADS` | `5` |
| `MAX_SITE_THREADS` | `8` |
| `MAX_DEVICE_THREADS` | `8` |

## Cleanup

| Variable | Default in code | Description |
|---|---|---|
| `NETBOX_CLEANUP` | `false` | Enable destructive cleanup phase |
| `CLEANUP_STALE_DAYS` | `30` | Grace period before stale device deletion |

## Sync Interval

| Variable | Default in code | Description |
|---|---|---|
| `SYNC_INTERVAL` | `0` | `0` = run once and exit; `>0` = continuous loop |

Note: `.env.example` sets `SYNC_INTERVAL=600` as an operational default for Docker deployments.

## `.env` Example

```bash
UNIFI_URLS=https://controller.example.com/proxy/network/integration/v1
UNIFI_SITE_MAPPINGS={"Default":"Main Office"}

NETBOX_URL=https://netbox.example.com
NETBOX_IMPORT_TENANT=My Organization
NETBOX_ROLES={"WIRELESS":"Wireless AP","LAN":"Switch","GATEWAY":"Gateway Firewall","ROUTER":"Router","UNKNOWN":"Network Device"}
```
