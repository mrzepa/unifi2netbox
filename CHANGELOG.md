# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added
- Gateway and DNS are now read from UniFi network config (`gateway_ip`, `dhcpd_dns_1-4`) for DHCP-to-static IP conversion.
- Fallback env vars `DEFAULT_GATEWAY` and `DEFAULT_DNS` when UniFi network config lacks gateway/DNS.
- 20 new unit tests covering `_get_network_info_for_ip`, `extract_dhcp_ranges_from_unifi` network info, and `is_ip_in_dhcp_range`.
- TLS verification configuration flags:
  - `UNIFI_VERIFY_SSL` (default: `true`)
  - `NETBOX_VERIFY_SSL` (default: `true`)
- UniFi session cache control:
  - `UNIFI_PERSIST_SESSION` (default: `true`)
- Robust integer parsing helper for runtime env vars (used for sync interval and cleanup grace period).

### Changed
- Runtime startup validation logs now use `logger.error(...)` for fail-fast config checks (instead of `logger.exception(...)` outside `except` blocks).
- NetBox HTTP session verify behavior is now driven by `NETBOX_VERIFY_SSL`.
- UniFi request verify behavior is now driven by `UNIFI_VERIFY_SSL`.
- `README.md`, `docs/configuration.md`, `docs/architecture.md`, and `docs/troubleshooting.md` updated to match current TLS/session behavior.
- Docker image metadata source URL corrected to the active repository.

### Security
- UniFi session cache file writes now enforce restrictive permissions (`0600`).
- Integration API auth headers are no longer persisted to session cache on disk.

### Removed
- Raw auto-generated git-log changelog format replaced by structured release notes.

## 2026-02-16

### Changed
- Repository cleanup and documentation alignment with current implementation.
- CI workflow updated to install `pytest` explicitly while keeping runtime dependencies minimal.
- Dockerfile aligned with current runtime files.
- LXC scripts updated for current repository URL and simplified install flow.

### Removed
- Unused standalone files: `unifi_client.py`, `config.py`, `exceptions.py`, `utils.py`.
- Dead code and unused imports across core modules and tests.

## 2025-02-12

### Added
- Unit test suite and CI pipeline.
- Thread limits configurable via environment variables.

### Removed
- `README-old.md` (obsolete).

### Fixed
- `.gitignore` updated with key file ignores.

## 2025-02-11

### Added
- Community device specs bundle integration.
- Generic template sync for interface/console/power templates.
- NetBox cleanup workflow.
- Auto-create device types from discovered models.
- Continuous sync loop via `SYNC_INTERVAL`.

### Fixed
- Case-insensitive part number lookup behavior.

## 2025-02-10

### Added
- DHCP auto-discovery from UniFi network configuration.
- Merge of discovered DHCP ranges with manual `DHCP_RANGES`.
- `DHCP_AUTO_DISCOVER` toggle.

## 2025-02-09

### Added
- Built-in UniFi model specs and interface template sync.
- Device type enrichment (part number, U height, PoE budget).

### Changed
- Concurrency/race condition hardening for tagging paths.

## 2025-02-08

### Added
- Cable sync and stale/offline device handling.

### Improved
- Reliability improvements in concurrent controller processing.
