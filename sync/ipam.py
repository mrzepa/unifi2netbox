"""DHCP/IPAM helpers and UniFi static-IP writeback logic."""

from __future__ import annotations

import ipaddress
import logging
import os
import subprocess  # nosec B404
import threading

import requests

from sync.runtime_config import _netbox_verify_ssl, _parse_env_list, _unifi_verify_ssl

logger = logging.getLogger(__name__)

_dhcp_ranges_cache = None
_dhcp_ranges_lock = threading.Lock()
_assigned_static_ips = set()
_assigned_static_ips_lock = threading.Lock()
_exhausted_static_prefixes = set()  # Prefix IDs exhausted for static selection in current sync run
_exhausted_static_prefixes_lock = threading.Lock()
_static_prefix_locks = {}
_static_prefix_locks_lock = threading.Lock()
_unifi_dhcp_ranges = {}  # site_id -> list of IPv4Network
_unifi_dhcp_ranges_lock = threading.Lock()
_unifi_network_info = {}  # site_id -> list of dicts: {network, gateway, dns}
_unifi_network_info_lock = threading.Lock()


def _get_static_prefix_lock(prefix_key) -> threading.Lock:
    with _static_prefix_locks_lock:
        lock = _static_prefix_locks.get(prefix_key)
        if lock is None:
            lock = threading.Lock()
            _static_prefix_locks[prefix_key] = lock
    return lock


def _parse_env_dhcp_ranges():
    """Parse DHCP_RANGES env var into a list of ipaddress.IPv4Network objects. Cached."""
    global _dhcp_ranges_cache
    with _dhcp_ranges_lock:
        if _dhcp_ranges_cache is not None:
            return _dhcp_ranges_cache

    raw_ranges = _parse_env_list("DHCP_RANGES")
    if not raw_ranges:
        with _dhcp_ranges_lock:
            _dhcp_ranges_cache = []
        return []

    networks = []
    for r in raw_ranges:
        r = r.strip()
        try:
            networks.append(ipaddress.ip_network(r, strict=False))
        except ValueError:
            logger.warning(f"Invalid DHCP range '{r}' in DHCP_RANGES. Skipping.")

    with _dhcp_ranges_lock:
        _dhcp_ranges_cache = networks
    logger.debug(f"Parsed {len(networks)} env DHCP ranges: {[str(n) for n in networks]}")
    return networks


def _fetch_legacy_networkconf(unifi, site_obj):
    """Fetch network configs via Legacy API (has DHCP fields)."""
    site_code = (
        getattr(site_obj, "internal_reference", None)
        or getattr(site_obj, "name", None)
        or "default"
    )
    base = unifi.base_url
    if "/proxy/network/integration" in base:
        base = base.split("/proxy/network/integration")[0]
    elif "/integration/" in base:
        base = base.split("/integration/")[0]
    url = f"{base}/proxy/network/api/s/{site_code}/rest/networkconf"

    try:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        auth_headers = getattr(unifi, "integration_auth_headers", None) or {}
        headers.update(auth_headers)

        resp = unifi.session.get(
            url,
            headers=headers,
            verify=getattr(unifi, "verify_ssl", _unifi_verify_ssl()),
            timeout=unifi.request_timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            if isinstance(data, list):
                return data
        else:
            logger.debug(f"Legacy networkconf returned HTTP {resp.status_code} for site {site_code}")
    except Exception as e:
        logger.debug(f"Legacy networkconf fallback failed for site {site_code}: {e}")
    return None


def extract_dhcp_ranges_from_unifi(site_obj, unifi=None) -> list[ipaddress.IPv4Network]:
    """Extract DHCP ranges from UniFi network configs for a site."""
    networks_result = []
    try:
        net_configs = site_obj.network_conf.all()
    except Exception as e:
        logger.warning(f"Could not fetch network configs for DHCP range extraction: {e}")
        return networks_result

    if net_configs and "dhcpd_enabled" not in net_configs[0] and unifi:
        logger.debug("Integration API lacks DHCP fields, falling back to Legacy API")
        legacy_configs = _fetch_legacy_networkconf(unifi, site_obj)
        if legacy_configs:
            net_configs = legacy_configs
        else:
            logger.debug("Legacy API fallback returned no data")
            return networks_result

    network_info_list = []

    for net in net_configs:
        net_name = net.get("name") or net.get("purpose") or "unknown"
        dhcp_enabled = net.get("dhcpd_enabled") or net.get("dhcpdEnabled") or False
        if not dhcp_enabled:
            continue

        subnet = net.get("ip_subnet") or net.get("subnet")
        if not subnet:
            continue

        try:
            network = ipaddress.ip_network(subnet, strict=False)
            networks_result.append(network)
            logger.debug(f"Found DHCP-enabled network '{net_name}': {subnet}")

            gateway = net.get("gateway_ip") or net.get("gateway") or None
            dns_servers = []
            for key in (
                "dhcpd_dns_1",
                "dhcpd_dns_2",
                "dhcpd_dns_3",
                "dhcpd_dns_4",
                "dhcpdDns1",
                "dhcpdDns2",
                "dhcpdDns3",
                "dhcpdDns4",
            ):
                val = net.get(key)
                if val and str(val).strip():
                    dns_servers.append(str(val).strip())

            seen_dns = set()
            unique_dns = []
            for d in dns_servers:
                if d not in seen_dns:
                    seen_dns.add(d)
                    unique_dns.append(d)

            network_info_list.append(
                {
                    "network": network,
                    "gateway": gateway,
                    "dns": unique_dns,
                    "name": net_name,
                }
            )
            if gateway or unique_dns:
                logger.debug(f"Network '{net_name}': gateway={gateway}, dns={unique_dns}")

        except ValueError:
            logger.warning(f"Invalid subnet '{subnet}' in UniFi network config. Skipping.")

    site_id = getattr(site_obj, "id", None) or getattr(site_obj, "_id", None)
    if site_id and network_info_list:
        with _unifi_network_info_lock:
            _unifi_network_info[site_id] = network_info_list

    return networks_result


def get_all_dhcp_ranges() -> list[ipaddress.IPv4Network]:
    """Return merged DHCP ranges from env var + all discovered UniFi sites."""
    env_ranges = _parse_env_dhcp_ranges()
    with _unifi_dhcp_ranges_lock:
        unifi_ranges = []
        for ranges in _unifi_dhcp_ranges.values():
            unifi_ranges.extend(ranges)

    seen = set()
    merged = []
    for net in env_ranges + unifi_ranges:
        key = str(net)
        if key not in seen:
            seen.add(key)
            merged.append(net)
    return merged


def is_ip_in_dhcp_range(ip_str: str) -> bool:
    """Return True if the given IP string falls within any configured/discovered DHCP range."""
    dhcp_ranges = get_all_dhcp_ranges()
    if not dhcp_ranges:
        return False
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(addr in network for network in dhcp_ranges)


def _get_network_info_for_ip(ip_str: str) -> tuple[str | None, list[str]]:
    """Look up gateway and DNS servers for a given IP from cached UniFi network configs."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return None, []

    with _unifi_network_info_lock:
        for _site_id, info_list in _unifi_network_info.items():
            for info in info_list:
                if addr in info["network"]:
                    return info.get("gateway"), info.get("dns", [])

    env_gw = os.getenv("DEFAULT_GATEWAY", "").strip() or None
    env_dns_raw = os.getenv("DEFAULT_DNS", "").strip()
    env_dns = [d.strip() for d in env_dns_raw.split(",") if d.strip()] if env_dns_raw else []
    if env_gw or env_dns:
        logger.debug(f"Using env fallback for {ip_str}: gateway={env_gw}, dns={env_dns}")
    return env_gw, env_dns


def ping_ip(ip_str: str, count: int = 2, timeout: int = 1) -> bool:
    """Ping an IP address. Returns True if host responds (IP in use), False if not."""
    try:
        target_ip = str(ipaddress.ip_address(ip_str))
        safe_count = max(1, min(5, int(count)))
        safe_timeout = max(1, min(5, int(timeout)))
        cmd = ["ping", "-c", str(safe_count), "-W", str(safe_timeout), target_ip]
        result = subprocess.run(  # nosec B603
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=safe_count * safe_timeout + 5,
            check=False,
        )
        return result.returncode == 0
    except (ValueError, TypeError, subprocess.TimeoutExpired, Exception) as e:
        logger.debug(f"Ping to {ip_str} failed/timed out: {e}")
        return False


def find_available_static_ip(
    nb,
    prefix_obj,
    vrf,
    tenant,
    unifi_device_ips: set[str] | None = None,
    max_attempts: int = 10,
) -> str | None:
    """
    Find an available static IP in the given NetBox prefix.
    Returns IP string with mask (e.g. '192.168.1.5/24') or None.
    """
    dhcp_ranges = get_all_dhcp_ranges()
    subnet_mask = prefix_obj.prefix.split("/")[1]
    prefix_id = prefix_obj.id
    vrf_id = getattr(vrf, "id", None) if vrf is not None else None
    prefix_key = f"{prefix_obj.prefix}|vrf:{vrf_id if vrf_id is not None else 'none'}"
    unifi_ips = unifi_device_ips or set()

    with _get_static_prefix_lock(prefix_key):
        # Avoid repeated expensive checks and warning spam for the same exhausted prefix within one run.
        with _exhausted_static_prefixes_lock:
            if prefix_key in _exhausted_static_prefixes:
                logger.debug(
                    f"Static IP candidate search already exhausted for prefix {prefix_obj.prefix} in this run."
                )
                return None

        netbox_url = os.getenv("NETBOX_URL", "").rstrip("/")
        netbox_token = os.getenv("NETBOX_TOKEN", "")
        url = f"{netbox_url}/api/ipam/prefixes/{prefix_id}/available-ips/"
        headers = {"Authorization": f"Token {netbox_token}", "Accept": "application/json"}

        try:
            resp = requests.get(
                url,
                headers=headers,
                params={"limit": max_attempts * 5},
                verify=_netbox_verify_ssl(),
                timeout=10,
            )
            resp.raise_for_status()
            candidates = resp.json()
        except Exception as e:
            logger.error(f"Failed to query available IPs for prefix {prefix_obj.prefix}: {e}")
            return None

        if not isinstance(candidates, list):
            logger.error(f"Unexpected response from available-ips endpoint: {type(candidates)}")
            return None

        attempts = 0
        evaluated_candidates = 0
        skipped_dhcp = 0
        skipped_assigned = 0
        skipped_unifi = 0
        skipped_ping = 0
        for candidate in candidates:
            if attempts >= max_attempts:
                break

            candidate_addr = candidate.get("address", "")
            candidate_ip = candidate_addr.split("/")[0]

            try:
                addr = ipaddress.ip_address(candidate_ip)
            except ValueError:
                continue

            evaluated_candidates += 1

            if any(addr in net for net in dhcp_ranges):
                skipped_dhcp += 1
                continue

            with _assigned_static_ips_lock:
                if candidate_ip in _assigned_static_ips:
                    logger.debug(f"Skipping {candidate_ip} — already being assigned this run")
                    skipped_assigned += 1
                    continue

            if candidate_ip in unifi_ips:
                logger.debug(f"Skipping {candidate_ip} — already in use by a UniFi device")
                skipped_unifi += 1
                continue

            attempts += 1

            if ping_ip(candidate_ip):
                logger.warning(f"Candidate IP {candidate_ip} responds to ping — in use, skipping")
                skipped_ping += 1
                continue

            with _assigned_static_ips_lock:
                if candidate_ip in _assigned_static_ips:
                    continue
                _assigned_static_ips.add(candidate_ip)

            logger.info(f"Found available static IP: {candidate_ip}/{subnet_mask}")
            return f"{candidate_ip}/{subnet_mask}"

        with _exhausted_static_prefixes_lock:
            _exhausted_static_prefixes.add(prefix_key)

        if evaluated_candidates > 0 and attempts == 0 and skipped_dhcp == evaluated_candidates:
            logger.info(
                f"No static-IP candidates outside DHCP ranges for {prefix_obj.prefix} "
                f"(evaluated={evaluated_candidates}). Keeping DHCP assignment."
            )
        else:
            logger.warning(
                f"Could not find available static IP in {prefix_obj.prefix} "
                f"after {attempts} assignment attempts (evaluated {evaluated_candidates}, "
                f"skipped_dhcp={skipped_dhcp}, skipped_assigned={skipped_assigned}, "
                f"skipped_unifi={skipped_unifi}, skipped_ping={skipped_ping})"
            )
        return None


def set_unifi_device_static_ip(
    unifi,
    site_obj,
    device: dict,
    static_ip: str,
    subnet_mask: str = "255.255.252.0",
    gateway: str | None = None,
    dns_servers: list[str] | None = None,
) -> bool:
    """
    Set a static IP on a UniFi device via the controller API.
    For Integration API: PATCH /sites/{siteId}/devices/{deviceId}
    For Legacy API: PUT /api/s/{site}/rest/device/{id}
    """
    device_id = device.get("id") or device.get("_id")
    device_name = (
        device.get("name")
        or device.get("hostname")
        or device.get("macAddress")
        or device.get("mac")
        or device.get("id")
        or "unknown-device"
    )
    if not device_id:
        logger.warning(f"Cannot set static IP on {device_name}: no device ID")
        return False

    site_api_id = getattr(site_obj, "api_id", None) or getattr(site_obj, "_id", None)
    if not site_api_id:
        logger.warning(f"Cannot set static IP on {device_name}: no site API ID")
        return False

    if not gateway:
        try:
            network = ipaddress.ip_network(f"{static_ip}/{subnet_mask}", strict=False)
            gateway = str(list(network.hosts())[0])
        except Exception as e:
            gateway = static_ip.rsplit(".", 1)[0] + ".1"
            logger.debug(f"Could not compute gateway from prefix, using fallback {gateway}: {e}")

    api_style = getattr(unifi, "api_style", "legacy")
    if api_style == "integration":
        url = f"/sites/{site_api_id}/devices/{device_id}"
        ip_config = {
            "mode": "static",
            "ip": static_ip,
            "subnetMask": subnet_mask,
            "gateway": gateway,
        }
        if dns_servers:
            if len(dns_servers) >= 1:
                ip_config["preferredDns"] = dns_servers[0]
            if len(dns_servers) >= 2:
                ip_config["alternateDns"] = dns_servers[1]
        payload = {"ipConfig": ip_config}
        try:
            response = unifi.make_request(url, "PATCH", data=payload)
            if isinstance(response, dict):
                status = response.get("statusCode") or response.get("status")
                if status and int(status) >= 400:
                    logger.warning(
                        f"Failed to set static IP on {device_name} via Integration API: "
                        f"{response.get('message', response)}"
                    )
                    return False
            logger.info(f"Set static IP {static_ip} (gw={gateway}, dns={dns_servers}) on UniFi device {device_name}")
            return True
        except Exception as e:
            logger.warning(f"Failed to set static IP on {device_name}: {e}")
            return False

    config_network = {
        "type": "static",
        "ip": static_ip,
        "netmask": subnet_mask,
        "gateway": gateway,
    }
    if dns_servers:
        if len(dns_servers) >= 1:
            config_network["dns1"] = dns_servers[0]
        if len(dns_servers) >= 2:
            config_network["dns2"] = dns_servers[1]
    payload = {"config_network": config_network}
    try:
        site_name = getattr(site_obj, "name", "default")
        url = f"/api/s/{site_name}/rest/device/{device_id}"
        response = unifi.make_request(url, "PUT", data=payload)
        if isinstance(response, dict):
            meta = response.get("meta", {})
            if isinstance(meta, dict) and meta.get("rc") == "ok":
                logger.info(f"Set static IP {static_ip} (gw={gateway}, dns={dns_servers}) on UniFi device {device_name}")
                return True
            logger.warning(f"Failed to set static IP on {device_name} via legacy API: {response}")
            return False
        return False
    except Exception as e:
        logger.warning(f"Failed to set static IP on {device_name}: {e}")
        return False
