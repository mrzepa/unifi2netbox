from __future__ import annotations

import json
from dotenv import load_dotenv
from slugify import slugify
import os
import re
import requests
import warnings
import logging
import pynetbox
import ipaddress
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib3.exceptions import InsecureRequestWarning
# Import the unifi module instead of defining the Unifi class
from sync import ipam as ipam_helpers
from sync import vrf as vrf_helpers
from sync.ipam import (
    _get_network_info_for_ip,
    extract_dhcp_ranges_from_unifi,
    find_available_static_ip,
    is_ip_in_dhcp_range,
    set_unifi_device_static_ip,
)
from sync.runtime_config import (
    _netbox_verify_ssl,
    _parse_env_bool,
    _read_env_int,
    _sync_interval_seconds,
    load_runtime_config,
)
from sync.runtime_config import _unifi_verify_ssl  # noqa: F401
from sync.log_sanitizer import SensitiveDataFormatter
from sync.vrf import get_or_create_vrf, get_vrf_for_site  # noqa: F401
from unifi.unifi import Unifi
from unifi.model_specs import UNIFI_MODEL_SPECS
from unifi.spec_refresh import refresh_specs_bundle, write_specs_bundle
# Suppress only the InsecureRequestWarning
warnings.simplefilter("ignore", InsecureRequestWarning)

load_dotenv()
logger = logging.getLogger(__name__)

# Threading limits (configurable via env vars)
# Use guarded parsing to avoid startup crashes on invalid env values.
MAX_CONTROLLER_THREADS = _read_env_int("MAX_CONTROLLER_THREADS", default=5, minimum=1)
MAX_SITE_THREADS = _read_env_int("MAX_SITE_THREADS", default=8, minimum=1)
MAX_DEVICE_THREADS = _read_env_int("MAX_DEVICE_THREADS", default=8, minimum=1)

# Populated at runtime from NETBOX roles in environment variables
netbox_device_roles = {}
postable_fields_cache = {}
postable_fields_lock = threading.Lock()
vrf_cache = vrf_helpers.vrf_cache
vrf_cache_lock = vrf_helpers.vrf_cache_lock
vrf_locks = vrf_helpers.vrf_locks
vrf_locks_lock = vrf_helpers.vrf_locks_lock
# Caches for custom fields, tags, VLANs (thread-safe)
_custom_field_cache = {}
_custom_field_lock = threading.Lock()
_tag_cache = {}
_tag_lock = threading.Lock()
_vlan_cache = {}
_vlan_lock = threading.Lock()
_cable_lock = threading.Lock()
_dhcp_ranges_cache = ipam_helpers._dhcp_ranges_cache
_dhcp_ranges_lock = ipam_helpers._dhcp_ranges_lock
_assigned_static_ips = ipam_helpers._assigned_static_ips
_assigned_static_ips_lock = ipam_helpers._assigned_static_ips_lock
_exhausted_static_prefixes = ipam_helpers._exhausted_static_prefixes
_exhausted_static_prefixes_lock = ipam_helpers._exhausted_static_prefixes_lock
_static_prefix_locks = ipam_helpers._static_prefix_locks
_static_prefix_locks_lock = ipam_helpers._static_prefix_locks_lock
_unifi_dhcp_ranges = ipam_helpers._unifi_dhcp_ranges          # site_id -> list of IPv4Network
_unifi_dhcp_ranges_lock = ipam_helpers._unifi_dhcp_ranges_lock
_unifi_network_info = ipam_helpers._unifi_network_info         # site_id -> list of dicts: {network, gateway, dns}
_unifi_network_info_lock = ipam_helpers._unifi_network_info_lock
_cleanup_serials_by_site = {}          # site_id -> set of UniFi serials (for cleanup)
_cleanup_serials_lock = threading.Lock()
_site_mapping_cache = {}
_site_mapping_cache_lock = threading.Lock()

_ASSET_TAG_RE = re.compile(r"[-_]?(A?ID\d+)$", re.IGNORECASE)
_MAC_WITH_SEP_RE = re.compile(r"(?i)([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$")
_MAC_PLAIN_RE = re.compile(r"(?i)[0-9a-f]{12}$")
_NON_HEX_RE = re.compile(r"[^0-9A-Fa-f]")

def get_postable_fields(base_url, token, url_path):
    """
    Retrieves the POST-able fields for NetBox path.
    """
    normalized_base = base_url.rstrip("/")
    normalized_path = url_path.strip("/")
    cache_key = (normalized_base, normalized_path)
    with postable_fields_lock:
        cached_fields = postable_fields_cache.get(cache_key)
    if cached_fields is not None:
        logger.debug(f"Using cached POST-able fields for NetBox path: {normalized_path}")
        return cached_fields

    url = f"{normalized_base}/api/{normalized_path}/"
    logger.debug(f"Retrieving POST-able fields from NetBox API: {url}")
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
    }
    response = requests.options(
        url,
        headers=headers,
        verify=_netbox_verify_ssl(),
        timeout=15,
    )
    response.raise_for_status()  # Raise an error if the response is not successful

    # Extract the available POST fields from the API schema
    fields = response.json().get("actions", {}).get("POST", {})
    with postable_fields_lock:
        postable_fields_cache[cache_key] = fields
    logger.debug(f"Retrieved {len(fields)} POST-able fields from NetBox API")
    return fields

def load_site_mapping(config=None):
    """
    Load site mapping from runtime config (environment-derived).
    Returns a dictionary mapping UniFi site names to NetBox site names.

    :param config: Runtime configuration dictionary
    :return: Dictionary mapping UniFi site names to NetBox site names
    """
    unifi_cfg = config.get("UNIFI", {}) if isinstance(config, dict) else {}
    config_mappings = unifi_cfg.get("SITE_MAPPINGS") if isinstance(unifi_cfg, dict) else None
    normalized_config_items = tuple(
        sorted((str(k), str(v)) for k, v in config_mappings.items())
    ) if isinstance(config_mappings, dict) and config_mappings else ()
    cache_key = normalized_config_items
    with _site_mapping_cache_lock:
        cached_mapping = _site_mapping_cache.get(cache_key)
    if cached_mapping is not None:
        return dict(cached_mapping)

    site_mapping = dict(config_mappings) if isinstance(config_mappings, dict) else {}
    if site_mapping:
        logger.debug(f"Loaded {len(site_mapping)} site mappings from UNIFI_SITE_MAPPINGS.")

    with _site_mapping_cache_lock:
        _site_mapping_cache[cache_key] = dict(site_mapping)

    logger.debug(f"Final site mapping has {len(site_mapping)} entries")
    return site_mapping

def get_netbox_site_name(unifi_site_name, config=None):
    """
    Get NetBox site name from UniFi site name using the mapping table.
    If no mapping exists, return the original name.
    
    :param unifi_site_name: The UniFi site name to look up
    :param config: Runtime configuration dictionary
    :return: The corresponding NetBox site name or the original name if no mapping exists
    """
    site_mapping = load_site_mapping(config)
    mapped_name = site_mapping.get(unifi_site_name, unifi_site_name)
    if mapped_name != unifi_site_name:
        logger.debug(f"Mapped UniFi site '{unifi_site_name}' to NetBox site '{mapped_name}'")
    return mapped_name

def prepare_netbox_sites(netbox_sites):
    """
    Pre-process NetBox sites for lookup.

    :param netbox_sites: List of NetBox site objects.
    :return: A dictionary mapping NetBox site names to the original NetBox site objects.
    """
    return {netbox_site.name: netbox_site for netbox_site in netbox_sites}

def match_sites_to_netbox(ubiquity_desc, netbox_sites_dict, config=None):
    """
    Match Ubiquity site to NetBox site using the site mapping configuration.

    :param ubiquity_desc: The description of the Ubiquity site.
    :param netbox_sites_dict: A dictionary mapping NetBox site names to site objects.
    :param config: Runtime configuration dictionary
    :return: The matched NetBox site, or None if no match is found.
    """
    # Get the corresponding NetBox site name from the mapping
    netbox_site_name = get_netbox_site_name(ubiquity_desc, config)
    logger.debug(f'Mapping Ubiquity site: "{ubiquity_desc}" -> "{netbox_site_name}"')
    
    # Look for exact match in NetBox sites
    if netbox_site_name in netbox_sites_dict:
        netbox_site = netbox_sites_dict[netbox_site_name]
        logger.debug(f'Matched Ubiquity site "{ubiquity_desc}" to NetBox site "{netbox_site.name}"')
        return netbox_site
    
    # If site mapping exists but no match found, provide more helpful message
    if config and 'UNIFI' in config and ('USE_SITE_MAPPING' in config['UNIFI'] and config['UNIFI']['USE_SITE_MAPPING'] or 
                                        'SITE_MAPPINGS' in config['UNIFI'] and config['UNIFI']['SITE_MAPPINGS']):
        logger.debug(f'No match found for Ubiquity site "{ubiquity_desc}". Add mapping in UNIFI_SITE_MAPPINGS.')
    else:
        logger.debug(f'No match found for Ubiquity site "{ubiquity_desc}". Set UNIFI_SITE_MAPPINGS in .env if needed.')
    return None

def setup_logging(min_log_level=logging.INFO):
    """
    Sets up logging to separate files for each log level.
    Only logs from the specified `min_log_level` and above are saved in their respective files.
    Includes console logging for the same log levels.

    :param min_log_level: Minimum log level to log. Defaults to logging.INFO.
    """
    logs_dir = "logs"
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)

    if not os.access(logs_dir, os.W_OK):
        raise PermissionError(f"Cannot write to log directory: {logs_dir}")

    # Log files for each level
    log_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }

    # Create the root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Capture all log levels

    # Define a log format
    log_format = SensitiveDataFormatter("%(asctime)s - %(levelname)s - %(message)s")

    # Set up file handlers for each log level
    for level_name, level_value in log_levels.items():
        if level_value >= min_log_level:
            log_file = os.path.join(logs_dir, f"{level_name.lower()}.log")
            handler = logging.FileHandler(log_file)
            handler.setLevel(level_value)
            handler.setFormatter(log_format)

            # Add a filter so only logs of this specific level are captured
            handler.addFilter(lambda record, lv=level_value: record.levelno == lv)
            logger.addHandler(handler)

    # Set up console handler for logs at `min_log_level` and above
    console_handler = logging.StreamHandler()
    console_handler.setLevel(min_log_level)
    console_handler.setFormatter(log_format)
    logger.addHandler(console_handler)

    logging.info(f"Logging is set up. Minimum log level: {logging.getLevelName(min_log_level)}")

def get_device_name(device: dict) -> str:
    return (
        device.get("name")
        or device.get("hostname")
        or device.get("macAddress")
        or device.get("mac")
        or device.get("id")
        or "unknown-device"
    )

def extract_asset_tag(device_name: str | None) -> str | None:
    """Extract ID or AID tag from device name, e.g. 'IT-AULA-AP02-ID3006' -> 'ID3006'."""
    if not device_name:
        return None
    match = _ASSET_TAG_RE.search(device_name)
    if match:
        return match.group(1).upper()
    return None


def get_device_mac(device: dict) -> str | None:
    return device.get("mac") or device.get("macAddress")

def get_device_ip(device: dict) -> str | None:
    return device.get("ip") or device.get("ipAddress")

def get_device_serial(device: dict) -> str | None:
    """
    Determine what to put in NetBox's `serial` field.

    Controlled by env:
      - NETBOX_SERIAL_MODE=mac   (default): use device.serial, else MAC, else id
      - NETBOX_SERIAL_MODE=unifi: only use device.serial (no fallback)
      - NETBOX_SERIAL_MODE=id    : use device.serial, else id
      - NETBOX_SERIAL_MODE=none  : do not set serial in NetBox
    """
    def _normalize_serial(value):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        # If this is a MAC address (with separators) or 12 hex characters, normalize to compact uppercase.
        if _MAC_WITH_SEP_RE.fullmatch(text) or _MAC_PLAIN_RE.fullmatch(text):
            return _NON_HEX_RE.sub("", text).upper()
        return text

    mode = (os.getenv("NETBOX_SERIAL_MODE") or "mac").strip().lower()
    if mode == "none":
        return None
    if mode in {"unifi", "serial"}:
        return _normalize_serial(device.get("serial"))
    if mode == "id":
        return _normalize_serial(device.get("serial") or device.get("id"))
    # default: mac
    return _normalize_serial(device.get("serial") or get_device_mac(device) or device.get("id"))

def is_access_point_device(device: dict) -> bool:
    ap_flag = device.get("is_access_point")
    if isinstance(ap_flag, bool):
        return ap_flag
    features = device.get("features")
    if isinstance(features, list):
        return "accessPoint" in features
    if isinstance(features, dict):
        return "accessPoint" in features
    interfaces = device.get("interfaces")
    if isinstance(interfaces, dict):
        return bool(interfaces.get("radios"))
    if isinstance(interfaces, list):
        # Check if any item in the list looks like a radio
        return any(
            isinstance(iface, dict) and (
                iface.get("radio") is not None
                or iface.get("band")
                or iface.get("channel")
                or (iface.get("name") or "").lower().startswith("radio")
            )
            for iface in interfaces
        )
    return False

def ensure_custom_field(nb, name, cf_type="text", content_types=None, label=None):
    """Ensure a custom field exists in NetBox. Create if missing. Returns the CF object."""
    with _custom_field_lock:
        if name in _custom_field_cache:
            return _custom_field_cache[name]
    cf = None
    try:
        cfs = list(nb.extras.custom_fields.filter(name=name))
        if cfs:
            cf = cfs[0]
        else:
            try:
                cf = nb.extras.custom_fields.create({
                    "name": name,
                    "type": cf_type,
                    "object_types": content_types or ["dcim.device"],
                    "label": label or name.replace("_", " ").title(),
                    "filter_logic": "loose",
                })
                if cf:
                    logger.info(f"Created custom field '{name}' in NetBox.")
            except Exception:
                # Race condition: another thread created it; retry filter
                cfs = list(nb.extras.custom_fields.filter(name=name))
                if cfs:
                    cf = cfs[0]
    except Exception as e:
        logger.warning(f"Could not ensure custom field '{name}': {e}")
    with _custom_field_lock:
        _custom_field_cache[name] = cf
    return cf


def ensure_tag(nb, name, slug=None, color=None):
    """Ensure a tag exists in NetBox. Returns the tag object.

    Uses double-check locking to prevent duplicate tag creation
    when multiple threads request the same tag concurrently.
    """
    slug = slug or slugify(name)
    # Fast path: check cache without blocking
    with _tag_lock:
        if slug in _tag_cache:
            return _tag_cache[slug]

    # Slow path: hold lock for the entire get-or-create to close TOCTOU window
    with _tag_lock:
        # Double-check after acquiring lock
        if slug in _tag_cache:
            return _tag_cache[slug]

        tag = None
        try:
            tag = nb.extras.tags.get(slug=slug)
            if not tag:
                payload = {"name": name, "slug": slug}
                if color:
                    payload["color"] = color
                tag = nb.extras.tags.create(payload)
                if tag:
                    logger.info(f"Created tag '{name}' in NetBox.")
        except pynetbox.core.query.RequestError:
            # Race condition: another thread created it between get and create
            tag = nb.extras.tags.get(slug=slug)

        if tag:
            _tag_cache[slug] = tag
        else:
            logger.warning(f"Could not ensure tag '{name}' in NetBox")
        return tag


def sync_device_state(nb, nb_device, device):
    """Sync UniFi device state to NetBox device status (active/offline)."""
    state = (device.get("state") or device.get("status") or "").upper()
    if state in ("ONLINE", "CONNECTED", "1"):
        desired = "active"
    elif state in ("OFFLINE", "DISCONNECTED", "0"):
        desired = "offline"
    else:
        return  # Unknown state, don't change

    current = None
    if nb_device.status:
        current = nb_device.status.value if isinstance(nb_device.status, dict) or hasattr(nb_device.status, 'value') else str(nb_device.status)
        if hasattr(nb_device.status, 'value'):
            current = nb_device.status.value
    if current != desired:
        nb_device.status = desired
        nb_device.save()
        logger.info(f"Updated {nb_device.name} status: {current} -> {desired}")


def sync_device_custom_fields(nb, nb_device, device):
    """Sync firmware version, uptime, MAC, and last seen from UniFi to NetBox custom fields."""
    # Ensure custom fields exist
    ensure_custom_field(nb, "unifi_firmware", cf_type="text", label="UniFi Firmware")
    ensure_custom_field(nb, "unifi_uptime", cf_type="integer", label="UniFi Uptime (sec)")
    ensure_custom_field(nb, "unifi_mac", cf_type="text", label="UniFi MAC")
    ensure_custom_field(nb, "unifi_last_seen", cf_type="text", label="UniFi Last Seen")

    firmware = device.get("firmwareVersion") or device.get("version") or device.get("fw_version")
    uptime = device.get("uptimeSec") or device.get("uptime") or device.get("_uptime")
    mac = device.get("macAddress") or device.get("mac")
    last_seen = device.get("lastSeen") or device.get("last_seen")

    cf = dict(nb_device.custom_fields or {})
    changed = False

    if firmware and cf.get("unifi_firmware") != firmware:
        cf["unifi_firmware"] = firmware
        changed = True
    if uptime is not None:
        try:
            uptime_int = int(uptime)
            if cf.get("unifi_uptime") != uptime_int:
                cf["unifi_uptime"] = uptime_int
                changed = True
        except (ValueError, TypeError):
            pass
    if mac and cf.get("unifi_mac") != mac:
        cf["unifi_mac"] = mac
        changed = True
    # Last seen: store as ISO timestamp or raw value
    if last_seen:
        last_seen_str = str(last_seen)
        # Convert epoch seconds to readable format
        if last_seen_str.isdigit() and len(last_seen_str) >= 10:
            try:
                from datetime import datetime, timezone
                last_seen_str = datetime.fromtimestamp(int(last_seen_str), tz=timezone.utc).isoformat()
            except (ValueError, OSError):
                pass
        if cf.get("unifi_last_seen") != last_seen_str:
            cf["unifi_last_seen"] = last_seen_str
            changed = True

    if changed:
        nb_device.custom_fields = cf
        nb_device.save()
        logger.debug(f"Updated custom fields for {nb_device.name}")


def sync_uplink_cable(nb, nb_device, device, all_nb_devices_by_mac):
    """Create cable between device uplink port and upstream device if both exist in NetBox.
    For offline devices: remove existing cables instead of creating new ones."""
    device_name = get_device_name(device)

    # Check if device is offline — remove cables and skip
    device_state = (device.get("state") or device.get("status") or "").upper()
    if device_state in ("OFFLINE", "DISCONNECTED", "0"):
        # Remove all cables from this device's interfaces
        try:
            ifaces = list(nb.dcim.interfaces.filter(device_id=nb_device.id))
            for iface in ifaces:
                if iface.cable:
                    try:
                        cable_id = iface.cable.id if hasattr(iface.cable, 'id') else iface.cable
                        cable_obj = nb.dcim.cables.get(cable_id)
                        if cable_obj:
                            cable_obj.delete()
                            logger.info(f"Removed cable from offline device {device_name}:{iface.name}")
                    except Exception as e:
                        logger.debug(f"Could not remove cable from {device_name}:{iface.name}: {e}")
        except Exception as e:
            logger.debug(f"Could not check cables for offline device {device_name}: {e}")
        return

    # Integration API: uplink.deviceId; Legacy: uplink_mac or uplink.mac
    # Prefer _detail_uplink (from device detail API) over the list-level uplink
    uplink = device.get("_detail_uplink") or device.get("uplink") or {}
    logger.debug(f"Cable sync for {device_name}: uplink keys={list(uplink.keys()) if uplink else 'none'}, uplink={uplink}")
    upstream_device_id = uplink.get("deviceId") or uplink.get("device_id")
    upstream_mac = uplink.get("uplink_mac") or uplink.get("mac") or uplink.get("macAddress")
    uplink_port_name = uplink.get("name") or uplink.get("uplink_port") or uplink.get("port_name") or uplink.get("portName")
    upstream_port_name = uplink.get("uplink_remote_port") or uplink.get("remotePort") or uplink.get("port_name")

    if not upstream_device_id and not upstream_mac:
        logger.debug(f"Cable sync for {device_name}: no upstream deviceId or MAC in uplink data")
        return

    logger.debug(f"Cable sync for {device_name}: upstream_device_id={upstream_device_id}, upstream_mac={upstream_mac}, uplink_port={uplink_port_name}, upstream_port={upstream_port_name}")

    # Find upstream device in NetBox (O(1) lookup via dict)
    upstream_nb = None
    if upstream_mac:
        normalized_mac = upstream_mac.upper().replace(":", "").replace("-", "")
        upstream_nb = all_nb_devices_by_mac.get(normalized_mac)
        if not upstream_nb:
            logger.debug(f"Cable sync for {device_name}: upstream MAC {normalized_mac} not found in lookup (keys: {list(all_nb_devices_by_mac.keys())[:5]}...)")
    if not upstream_nb and upstream_device_id:
        # Try UUID-based lookup (Integration API stores device UUIDs)
        upstream_nb = all_nb_devices_by_mac.get(str(upstream_device_id))
        if not upstream_nb:
            logger.debug(f"Cable sync for {device_name}: upstream UUID {upstream_device_id} not found in lookup")

    if not upstream_nb:
        logger.debug(f"Cable sync for {device_name}: upstream device not found in NetBox")
        return

    logger.debug(f"Cable sync for {device_name}: found upstream device {upstream_nb.name}")

    # Find the uplink interface on our device
    our_iface = None
    if uplink_port_name:
        our_iface = nb.dcim.interfaces.get(device_id=nb_device.id, name=uplink_port_name)
    if not our_iface:
        # Try to find any interface marked as uplink
        all_ifaces = list(nb.dcim.interfaces.filter(device_id=nb_device.id))
        # Filter to only cabled (ethernet/physical) interfaces — exclude wireless types
        iface_types = [(i.name, str(i.type.value) if i.type else "none") for i in all_ifaces]
        logger.debug(f"Cable sync for {device_name}: all interfaces: {iface_types}")
        wired_ifaces = [i for i in all_ifaces if i.type and i.type.value not in ("virtual", "lag") and not str(i.type.value).startswith("ieee802.11")]
        for iface in wired_ifaces:
            if iface.description and "uplink" in iface.description.lower():
                our_iface = iface
                break
        # Last resort: use the last physical port (commonly uplink on switches)
        if not our_iface and wired_ifaces:
            physical_ifaces = [i for i in wired_ifaces if i.type and i.type.value not in ("virtual", "lag")]
            if physical_ifaces:
                our_iface = physical_ifaces[-1]
        # For APs with no wired interfaces, create an eth0 interface for uplink
        if not our_iface and not wired_ifaces:
            try:
                our_iface = nb.dcim.interfaces.create({
                    "device": nb_device.id,
                    "name": "eth0",
                    "type": "1000base-t",
                    "description": "Uplink (auto-created)",
                })
                if our_iface:
                    logger.info(f"Created eth0 uplink interface for {device_name}")
            except pynetbox.core.query.RequestError as e:
                logger.debug(f"Could not create eth0 for {device_name}: {e}")

    if not our_iface:
        logger.debug(f"Cable sync for {device_name}: no suitable uplink interface found on device (port_name={uplink_port_name})")
        return

    # Check if cable already exists on this interface
    if our_iface.cable:
        logger.debug(f"Cable sync for {device_name}: cable already exists on {our_iface.name}")
        return

    # Find a port on upstream device to connect to
    upstream_iface = None
    upstream_ifaces = list(nb.dcim.interfaces.filter(device_id=upstream_nb.id))
    # Prefer matching the remote port name from uplink data
    if upstream_port_name:
        for iface in upstream_ifaces:
            if iface.name == upstream_port_name and not iface.cable:
                upstream_iface = iface
                break
    # Fallback: any unconnected physical port
    if not upstream_iface:
        for iface in upstream_ifaces:
            if not iface.cable and iface.type and iface.type.value not in ("virtual", "lag"):
                upstream_iface = iface
                break

    if not upstream_iface:
        logger.debug(f"Cable sync for {device_name}: no available interface on upstream device {upstream_nb.name} (upstream_port={upstream_port_name})")
        return

    with _cable_lock:
        try:
            cable = nb.dcim.cables.create({
                "a_terminations": [{"object_type": "dcim.interface", "object_id": our_iface.id}],
                "b_terminations": [{"object_type": "dcim.interface", "object_id": upstream_iface.id}],
                "status": "connected",
            })
            if cable:
                logger.info(f"Created cable: {device_name}:{our_iface.name} <-> {upstream_nb.name}:{upstream_iface.name}")
        except pynetbox.core.query.RequestError as e:
            logger.warning(f"Could not create cable for {device_name}: {e}")


def sync_site_vlans(nb, site_obj, nb_site, tenant):
    """Sync VLANs from UniFi network configs to NetBox."""
    try:
        networks = site_obj.network_conf.all()
    except Exception as e:
        logger.warning(f"Could not fetch networks for site {nb_site.name}: {e}")
        return

    if not networks:
        return

    # Ensure a VLAN group exists for the site
    vlan_group = None
    try:
        vlan_group = nb.ipam.vlan_groups.get(slug=slugify(nb_site.name))
        if not vlan_group:
            vlan_group = nb.ipam.vlan_groups.create({
                "name": nb_site.name,
                "slug": slugify(nb_site.name),
                "scope_type": "dcim.site",
                "scope_id": nb_site.id,
            })
            if vlan_group:
                logger.info(f"Created VLAN group '{nb_site.name}' for site.")
    except pynetbox.core.query.RequestError as e:
        logger.warning(f"Could not create VLAN group for {nb_site.name}: {e}")
        # Try without scope (older NetBox)
        try:
            vlan_group = nb.ipam.vlan_groups.get(slug=slugify(nb_site.name))
            if not vlan_group:
                vlan_group = nb.ipam.vlan_groups.create({
                    "name": nb_site.name,
                    "slug": slugify(nb_site.name),
                })
        except Exception as e:
            logger.debug(f"VLAN group fallback for {nb_site.name}: {e}")

    for net in networks:
        vlan_id = net.get("vlanId") or net.get("vlan") or net.get("vlan_id")
        net_name = net.get("name") or net.get("purpose") or "Unknown"
        enabled = net.get("enabled", True)

        if not vlan_id:
            continue

        try:
            vlan_id = int(vlan_id)
        except (ValueError, TypeError):
            continue

        vlan_key = f"{nb_site.id}_{vlan_id}"
        with _vlan_lock:
            if vlan_key in _vlan_cache:
                continue

        # Check if VLAN exists
        vlan_filters = {"vid": vlan_id, "site_id": nb_site.id}
        existing = nb.ipam.vlans.get(**vlan_filters)
        if not existing and vlan_group:
            vlan_filters = {"vid": vlan_id, "group_id": vlan_group.id}
            existing = nb.ipam.vlans.get(**vlan_filters)

        if not existing:
            try:
                vlan_payload = {
                    "name": net_name,
                    "vid": vlan_id,
                    "site": nb_site.id,
                    "tenant": tenant.id,
                    "status": "active" if enabled else "reserved",
                }
                if vlan_group:
                    vlan_payload["group"] = vlan_group.id
                new_vlan = nb.ipam.vlans.create(vlan_payload)
                if new_vlan:
                    logger.info(f"Created VLAN {vlan_id} ({net_name}) at site {nb_site.name}")
                    with _vlan_lock:
                        _vlan_cache[vlan_key] = new_vlan
            except pynetbox.core.query.RequestError as e:
                logger.warning(f"Could not create VLAN {vlan_id} ({net_name}): {e}")
        else:
            with _vlan_lock:
                _vlan_cache[vlan_key] = existing
            # Update name if changed
            if existing.name != net_name:
                try:
                    existing.name = net_name
                    existing.save()
                    logger.debug(f"Updated VLAN {vlan_id} name to '{net_name}'")
                except Exception as e:
                    logger.warning(f"Failed to update VLAN {vlan_id} name: {e}")


def sync_site_wlans(nb, site_obj, nb_site, tenant):
    """Sync WiFi SSIDs from UniFi to NetBox wireless LANs."""
    try:
        wlans = site_obj.wlan_conf.all()
    except Exception as e:
        logger.warning(f"Could not fetch WLANs for site {nb_site.name}: {e}")
        return

    if not wlans:
        return

    # Ensure a wireless LAN group for the site
    wlan_group = None
    try:
        wlan_group = nb.wireless.wireless_lan_groups.get(slug=slugify(nb_site.name))
        if not wlan_group:
            wlan_group = nb.wireless.wireless_lan_groups.create({
                "name": nb_site.name,
                "slug": slugify(nb_site.name),
            })
            if wlan_group:
                logger.info(f"Created wireless LAN group '{nb_site.name}'.")
    except Exception as e:
        logger.debug(f"Wireless LAN groups not available: {e}")

    for wlan in wlans:
        ssid = wlan.get("name") or wlan.get("x_passphrase") or "Unknown"
        enabled = wlan.get("enabled", True)
        security = wlan.get("security") or wlan.get("wpa_mode") or ""
        # Integration API: securityConfiguration.type
        sec_config = wlan.get("securityConfiguration") or {}
        if isinstance(sec_config, dict):
            security = security or sec_config.get("type") or ""

        # Map security to NetBox auth_type (NetBox 4.x: open, wep, wpa-personal, wpa-enterprise)
        sec_lower = str(security).lower()
        if "enterprise" in sec_lower:
            auth_type = "wpa-enterprise"
        elif "wpa" in sec_lower or "sae" in sec_lower or "psk" in sec_lower:
            auth_type = "wpa-personal"
        elif "wep" in sec_lower:
            auth_type = "wep"
        elif "open" in sec_lower or "none" in sec_lower:
            auth_type = "open"
        else:
            auth_type = "wpa-personal"

        # Check if wireless LAN exists for this group (site)
        existing = None
        try:
            filters = {"ssid": ssid}
            if wlan_group:
                filters["group_id"] = wlan_group.id
            matches = list(nb.wireless.wireless_lans.filter(**filters))
            if matches:
                existing = matches[0]
        except Exception as e:
            logger.debug(f"Could not check existing wireless LAN '{ssid}': {e}")

        if not existing:
            try:
                wlan_payload = {
                    "ssid": ssid,
                    "status": "active" if enabled else "disabled",
                    "auth_type": auth_type,
                    "tenant": tenant.id,
                }
                if wlan_group:
                    wlan_payload["group"] = wlan_group.id
                new_wlan = nb.wireless.wireless_lans.create(wlan_payload)
                if new_wlan:
                    logger.info(f"Created wireless LAN '{ssid}' at site {nb_site.name}")
            except pynetbox.core.query.RequestError as e:
                logger.warning(f"Could not create wireless LAN '{ssid}': {e}")
        else:
            # Update if changed
            changed = False
            desired_status = "active" if enabled else "disabled"
            if hasattr(existing, 'status') and existing.status:
                current_status = existing.status.value if hasattr(existing.status, 'value') else str(existing.status)
                if current_status != desired_status:
                    existing.status = desired_status
                    changed = True
            if changed:
                try:
                    existing.save()
                    logger.debug(f"Updated wireless LAN '{ssid}'")
                except Exception as e:
                    logger.warning(f"Failed to update wireless LAN '{ssid}': {e}")


def map_unifi_port_to_netbox_type(port, api_style="integration"):
    """Map a UniFi port dict to a NetBox interface type string."""
    if api_style == "legacy":
        media = (port.get("media") or "").upper()
        speed = port.get("speed", 0) or 0
        if media == "SFP+":
            return "10gbase-x-sfpp"
        if media == "SFP":
            return "1000base-x-sfp"
        if speed >= 10000:
            return "10gbase-t"
        if speed >= 2500:
            return "2.5gbase-t"
        return "1000base-t"
    # Integration API
    max_speed = port.get("maxSpeed") or port.get("speed") or 0
    connector = (port.get("connector") or "").lower()
    if "sfp" in connector:
        if max_speed >= 10000:
            return "10gbase-x-sfpp"
        return "1000base-x-sfp"
    if max_speed >= 10000:
        return "10gbase-t"
    if max_speed >= 2500:
        return "2.5gbase-t"
    return "1000base-t"


def map_unifi_radio_to_netbox_type(radio):
    """Map a UniFi radio to a NetBox wireless interface type."""
    band = str(radio.get("band") or radio.get("radio") or "").lower()
    if "6e" in band or "6ghz" in band or "6g" in band:
        return "ieee802.11ax"
    if "5g" in band or "na" in band:
        return "ieee802.11ac"
    if "2g" in band or "ng" in band:
        return "ieee802.11n"
    return "ieee802.11ax"


def normalize_port_data(device, api_style="integration"):
    """Extract and normalize port data from device dict into a common format."""
    ports = []
    if api_style == "integration":
        interfaces = device.get("interfaces")
        if isinstance(interfaces, dict):
            raw_ports = interfaces.get("ports") or []
        elif isinstance(interfaces, list):
            # Integration API v1 may return interfaces as a flat list;
            # filter to port-type entries (non-radio, or entries with portIdx/connector)
            raw_ports = [
                iface for iface in interfaces
                if isinstance(iface, dict) and (
                    iface.get("portIdx") is not None
                    or iface.get("connector")
                    or iface.get("maxSpeed")
                    or iface.get("type", "").lower() in ("ethernet", "sfp", "sfp+", "port")
                    or (iface.get("name") or "").lower().startswith("port")
                )
            ]
        else:
            raw_ports = []
    else:
        raw_ports = device.get("port_table") or []

    for port in raw_ports:
        if api_style == "integration":
            name = port.get("name") or f"Port {port.get('portIdx', '?')}"
            speed_mbps = port.get("maxSpeed") or port.get("speed") or 0
            enabled = port.get("enabled", True) if "enabled" in port else port.get("up", True)
            poe = port.get("poeMode") or port.get("poe_mode")
            mac = port.get("macAddress") or port.get("mac")
            is_uplink = port.get("isUplink", False)
        else:
            name = port.get("name") or f"Port {port.get('port_idx', '?')}"
            speed_mbps = port.get("speed") or 0
            enabled = port.get("up", True)
            poe = port.get("poe_mode")
            mac = port.get("mac")
            is_uplink = port.get("is_uplink", False)

        # Skip ports without real data (missing index/name)
        if "?" in name:
            continue

        nb_type = map_unifi_port_to_netbox_type(port, api_style)
        speed_kbps = int(speed_mbps) * 1000 if speed_mbps else None

        nb_poe_mode = None
        if poe:
            poe_str = str(poe).lower()
            if poe_str in ("auto", "pasv24", "passthrough", "on"):
                nb_poe_mode = "pse"

        ports.append({
            "name": name,
            "type": nb_type,
            "speed_kbps": speed_kbps,
            "enabled": bool(enabled),
            "poe_mode": nb_poe_mode,
            "mac_address": mac,
            "is_uplink": bool(is_uplink),
            "description": "Uplink" if is_uplink else "",
        })
    return ports


def normalize_radio_data(device, api_style="integration"):
    """Extract and normalize radio data from device dict."""
    radios = []
    if api_style == "integration":
        interfaces = device.get("interfaces")
        if isinstance(interfaces, dict):
            raw_radios = interfaces.get("radios") or []
        elif isinstance(interfaces, list):
            # Integration API v1 may return interfaces as a flat list;
            # filter to radio-type entries
            raw_radios = [
                iface for iface in interfaces
                if isinstance(iface, dict) and (
                    iface.get("radio") is not None
                    or iface.get("band")
                    or iface.get("channel")
                    or iface.get("type", "").lower() in ("radio", "wireless")
                    or (iface.get("name") or "").lower().startswith("radio")
                )
            ]
        else:
            raw_radios = []
    else:
        raw_radios = device.get("radio_table") or []

    for radio in raw_radios:
        name = radio.get("name") or f"radio{radio.get('radio', '?')}"
        # Skip radios without real data (missing index/name)
        if "?" in name:
            continue
        nb_type = map_unifi_radio_to_netbox_type(radio)
        # Build rich description: band, channel, tx power
        desc_parts = []
        band = radio.get("band") or radio.get("radio") or ""
        if band:
            desc_parts.append(str(band).upper())
        channel = radio.get("channel")
        if channel:
            desc_parts.append(f"Ch {channel}")
        tx_power = radio.get("txPower") or radio.get("tx_power") or radio.get("tx_power_mode")
        if tx_power:
            desc_parts.append(f"TX {tx_power}dBm" if str(tx_power).isdigit() else f"TX {tx_power}")
        radios.append({
            "name": name,
            "type": nb_type,
            "enabled": True,
            "description": " | ".join(desc_parts) if desc_parts else "",
        })
    return radios


def _fetch_integration_device_detail(unifi, site_obj, device_id):
    """Fetch full device detail via Integration API /devices/{id} which includes port_table."""
    try:
        site_api_id = getattr(site_obj, "api_id", site_obj.name)
        url = f"/sites/{site_api_id}/devices/{device_id}"
        response = unifi.make_request(url, "GET")
        logger.debug(f"Device detail response type: {type(response)}, "
                     f"keys: {list(response.keys()) if isinstance(response, dict) else 'N/A'}")
        if isinstance(response, dict):
            # Detect error responses from the API
            if "statusCode" in response:
                status = int(response.get("statusCode", 0))
                if status >= 400:
                    logger.debug(f"Device detail API error for {device_id}: "
                                 f"{response.get('message', 'unknown error')} (status {status})")
                    return None
            data = response.get("data", response)
            if isinstance(data, dict):
                # Also check if data itself is an error response
                if "statusCode" in data and int(data.get("statusCode", 0)) >= 400:
                    return None
                return data
            if isinstance(data, list) and data:
                return data[0]
        return None
    except Exception as e:
        logger.debug(f"Could not fetch device detail for {device_id}: {e}")
    return None


def sync_device_interfaces(nb, nb_device, device, api_style="integration", unifi=None, site_obj=None):
    """
    Sync physical port and radio interfaces from UniFi device data to NetBox.
    Upsert: match by device_id + interface name, create if missing, update if changed.
    """
    if os.getenv("SYNC_INTERFACES", "true").strip().lower() not in ("true", "1", "yes"):
        return

    device_name = get_device_name(device)

    # Integration API v1: device list only returns interfaces: ["ports"]/["radios"]
    # as metadata strings. We need to fetch actual port data via a separate API call.
    original_device = device  # Keep reference to original for uplink merge
    interfaces = device.get("interfaces")
    if api_style == "integration" and isinstance(interfaces, list) and unifi and site_obj:
        device_id = device.get("id")
        if device_id:
            detail = _fetch_integration_device_detail(unifi, site_obj, device_id)
            if detail and isinstance(detail, dict):
                detail_ifaces = detail.get("interfaces")
                port_table = detail.get("port_table")
                radio_table = detail.get("radio_table")
                logger.debug(f"Device {device_name} detail: interfaces type={type(detail_ifaces)}, "
                             f"has port_table={port_table is not None}, has radio_table={radio_table is not None}, "
                             f"detail keys={list(detail.keys())[:15]}")
                # If the detail has richer interface data, use it
                if isinstance(detail_ifaces, dict):
                    device = dict(device)
                    device["interfaces"] = detail_ifaces
                elif port_table or radio_table:
                    device = dict(device)
                    if port_table:
                        device["port_table"] = port_table
                    if radio_table:
                        device["radio_table"] = radio_table
                    # Switch to legacy-style parsing since we have port_table/radio_table
                    api_style = "legacy"
                # Merge uplink data from detail into ORIGINAL device dict for cable sync
                detail_uplink = detail.get("uplink")
                if detail_uplink and isinstance(detail_uplink, dict):
                    original_device["_detail_uplink"] = detail_uplink
                    logger.debug(f"Stored uplink detail for {device_name}: {list(detail_uplink.keys())}")
            else:
                logger.debug(f"No detail data returned for {device_name} from Integration API")

    # Fetch all existing interfaces for this device in one call
    existing_interfaces = {
        iface.name: iface
        for iface in nb.dcim.interfaces.filter(device_id=nb_device.id)
    }

    # --- Physical Ports ---
    ports = normalize_port_data(device, api_style)
    for port in ports:
        iface_name = port["name"]
        existing = existing_interfaces.get(iface_name)

        iface_data = {
            "device": nb_device.id,
            "name": iface_name,
            "type": port["type"],
            "enabled": port["enabled"],
        }
        if port.get("speed_kbps"):
            iface_data["speed"] = port["speed_kbps"]
        if port.get("poe_mode"):
            iface_data["poe_mode"] = port["poe_mode"]
        if port.get("description"):
            iface_data["description"] = port["description"]
        if port.get("mac_address"):
            iface_data["mac_address"] = port["mac_address"]

        if existing:
            needs_update = False
            for key, value in iface_data.items():
                if key == "device":
                    continue
                current_val = getattr(existing, key, None)
                if isinstance(current_val, dict):
                    current_val = current_val.get("value")
                if str(current_val) != str(value):
                    needs_update = True
                    break
            if needs_update:
                try:
                    for key, value in iface_data.items():
                        if key != "device":
                            setattr(existing, key, value)
                    existing.save()
                    logger.debug(f"Updated interface {iface_name} on {device_name}")
                except pynetbox.core.query.RequestError as e:
                    logger.warning(f"Failed to update interface {iface_name} on {device_name}: {e}")
        else:
            try:
                new_iface = nb.dcim.interfaces.create(iface_data)
                if new_iface:
                    logger.info(f"Created interface {iface_name} (ID {new_iface.id}) on {device_name}")
            except pynetbox.core.query.RequestError as e:
                logger.warning(f"Failed to create interface {iface_name} on {device_name}: {e}")

    # --- Radio Interfaces (APs only) ---
    if is_access_point_device(device):
        radios = normalize_radio_data(device, api_style)
        for radio in radios:
            iface_name = radio["name"]
            existing = existing_interfaces.get(iface_name)

            iface_data = {
                "device": nb_device.id,
                "name": iface_name,
                "type": radio["type"],
                "enabled": radio["enabled"],
            }
            if radio.get("description"):
                iface_data["description"] = radio["description"]

            if existing:
                needs_update = False
                for key, value in iface_data.items():
                    if key == "device":
                        continue
                    current_val = getattr(existing, key, None)
                    if isinstance(current_val, dict):
                        current_val = current_val.get("value")
                    if str(current_val) != str(value):
                        needs_update = True
                        break
                if needs_update:
                    try:
                        for key, value in iface_data.items():
                            if key != "device":
                                setattr(existing, key, value)
                        existing.save()
                        logger.debug(f"Updated radio {iface_name} on {device_name}")
                    except pynetbox.core.query.RequestError as e:
                        logger.warning(f"Failed to update radio {iface_name} on {device_name}: {e}")
            else:
                try:
                    new_iface = nb.dcim.interfaces.create(iface_data)
                    if new_iface:
                        logger.info(f"Created radio {iface_name} (ID {new_iface.id}) on {device_name}")
                except pynetbox.core.query.RequestError as e:
                    logger.warning(f"Failed to create radio {iface_name} on {device_name}: {e}")

    # Clean up interfaces with '?' in name (leftover from previous runs with missing data)
    for iface_name, iface_obj in existing_interfaces.items():
        if "?" in iface_name:
            try:
                iface_obj.delete()
                logger.info(f"Deleted invalid interface '{iface_name}' from {device_name}")
            except Exception as e:
                logger.warning(f"Failed to delete interface '{iface_name}' from {device_name}: {e}")


def get_device_features(device):
    """Normalize feature information from legacy and integration payloads."""
    features = device.get("features")
    if isinstance(features, list):
        return {str(item) for item in features}
    if isinstance(features, dict):
        return set(features.keys())
    return set()

def infer_role_key_for_device(device):
    """
    Infer a role key from device capabilities/model.
    Supported keys: WIRELESS, LAN, GATEWAY, ROUTER, UNKNOWN.
    """
    if is_access_point_device(device):
        return "WIRELESS"

    features = get_device_features(device)
    model = str(device.get("model", "")).upper()

    if (
        {"gateway", "securityGateway", "routing", "wan"} & features
        or model.startswith(("USG", "UXG", "UDM", "UCG", "UDR", "UX", "UGW"))
        or "GATEWAY" in model
    ):
        return "GATEWAY"

    if "routing" in features or "ROUTER" in model:
        return "ROUTER"

    if {"switching", "switch", "ports"} & features:
        return "LAN"

    return "UNKNOWN"

def select_netbox_role_for_device(device):
    """
    Pick a NetBox role object based on inferred role key and configured fallback order.
    """
    if not netbox_device_roles:
        raise ValueError("No device roles loaded from NETBOX.ROLES")

    inferred_key = infer_role_key_for_device(device)
    if inferred_key in netbox_device_roles:
        return netbox_device_roles[inferred_key], inferred_key

    for fallback_key in ("LAN", "WIRELESS", "GATEWAY", "ROUTER", "UNKNOWN"):
        if fallback_key in netbox_device_roles:
            return netbox_device_roles[fallback_key], fallback_key

    # Final fallback: first configured role
    first_key = next(iter(netbox_device_roles))
    return netbox_device_roles[first_key], first_key


_device_type_specs_done = set()
_device_type_specs_lock = threading.Lock()

# ---------------------------------------------------------------------------
#  Community device-type library (netbox-community/devicetype-library)
# ---------------------------------------------------------------------------
_community_specs = None


def _load_community_specs():
    """Load community device specs from bundled JSON file (lazy, cached)."""
    global _community_specs
    if _community_specs is not None:
        return _community_specs
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ubiquiti_device_specs.json")
    if not os.path.exists(json_path):
        logger.warning(f"Community device specs file not found: {json_path}")
        _community_specs = {"by_part": {}, "by_model": {}}
        return _community_specs
    try:
        with open(json_path, "r") as fh:
            _community_specs = json.load(fh)
        logger.info(f"Loaded community device specs: {len(_community_specs.get('by_part', {}))} by part, "
                     f"{len(_community_specs.get('by_model', {}))} by model")
    except Exception as e:
        logger.warning(f"Failed to load community device specs: {e}")
        _community_specs = {"by_part": {}, "by_model": {}}

    auto_refresh = _parse_env_bool(os.getenv("UNIFI_SPECS_AUTO_REFRESH"), default=False)
    if auto_refresh:
        include_store = _parse_env_bool(os.getenv("UNIFI_SPECS_INCLUDE_STORE"), default=False)
        library_timeout = _read_env_int("UNIFI_SPECS_REFRESH_TIMEOUT", default=45, minimum=5)
        store_timeout = _read_env_int("UNIFI_SPECS_STORE_TIMEOUT", default=15, minimum=5)
        store_workers = _read_env_int("UNIFI_SPECS_STORE_MAX_WORKERS", default=8, minimum=1)
        write_cache = _parse_env_bool(os.getenv("UNIFI_SPECS_WRITE_CACHE"), default=False)
        try:
            refreshed = refresh_specs_bundle(
                include_store=include_store,
                library_timeout=library_timeout,
                store_timeout=store_timeout,
                store_max_workers=store_workers,
                logger=logger,
            )
            if refreshed.get("by_part"):
                _community_specs = refreshed
                logger.info(
                    "Auto-refreshed community device specs: "
                    f"{len(_community_specs.get('by_part', {}))} by part, "
                    f"{len(_community_specs.get('by_model', {}))} by model"
                )
                if write_cache:
                    try:
                        write_specs_bundle(json_path, _community_specs)
                        logger.info(f"Wrote refreshed community specs cache to {json_path}")
                    except Exception as cache_err:
                        logger.warning(f"Failed to write refreshed community specs cache: {cache_err}")
            else:
                logger.warning("Auto-refresh returned empty device specs bundle; keeping bundled specs.")
        except Exception as refresh_err:
            logger.warning(f"Auto-refresh of community device specs failed: {refresh_err}")
    return _community_specs


def _lookup_community_specs(part_number=None, model=None):
    """Look up community specs by part number or model name (case-insensitive)."""
    specs = _load_community_specs()
    # Try part_number first
    if part_number:
        hit = specs["by_part"].get(part_number)
        if hit:
            return hit
        # Case-insensitive fallback
        pn_upper = part_number.upper()
        for key, val in specs["by_part"].items():
            if key.upper() == pn_upper:
                return val
    # Try model name
    if model:
        hit = specs["by_model"].get(model)
        if hit:
            return hit
        model_upper = model.upper()
        for key, val in specs["by_model"].items():
            if key.upper() == model_upper:
                return val
    return None


def _resolve_device_specs(model):
    """Resolve full device specs by merging UNIFI_MODEL_SPECS with community library.

    Hardcoded specs overlay community data so manual overrides always win.
    Returns merged dict or None if neither source has data.
    """
    hardcoded = UNIFI_MODEL_SPECS.get(model)
    part_number = hardcoded.get("part_number") if hardcoded else None

    # Look up community specs by part_number or model name
    community = _lookup_community_specs(part_number=part_number, model=model)
    # Fallback: try model code as part_number (e.g. "US48PRO" might match)
    if not community and not part_number:
        community = _lookup_community_specs(part_number=model)

    if not community and not hardcoded:
        return None

    # Merge: community base, hardcoded overlay
    merged = {}
    if community:
        merged.update(community)
    if hardcoded:
        merged.update(hardcoded)
    return merged


# ---------------------------------------------------------------------------
#  Generic template sync (interfaces, console ports, power ports)
# ---------------------------------------------------------------------------

def _sync_templates(nb, nb_device_type, model, template_endpoint, expected, label):
    """Generic sync for interface/console-port/power-port templates.

    *expected* is a list of dicts, each with at least 'name' and 'type'.
    *template_endpoint* is the pynetbox endpoint (e.g. nb.dcim.interface_templates).
    *label* is used for log messages (e.g. "interface", "console-port").
    """
    dt_id = int(nb_device_type.id)
    existing_templates = list(template_endpoint.filter(device_type_id=dt_id))

    # De-duplicate existing
    existing_by_name = {}
    for tmpl in existing_templates:
        key = tmpl.name
        if key not in existing_by_name:
            existing_by_name[key] = tmpl
        else:
            try:
                tmpl.delete()
                logger.debug(f"Deleted duplicate {label} template '{key}' from {model}")
            except Exception as err:
                logger.debug(
                    f"Failed deleting duplicate {label} template '{key}' from {model}: {err}"
                )

    # Build comparison sets
    expected_set = set()
    for e in expected:
        expected_set.add((e["name"], e.get("type", "")))

    existing_set = set()
    for name, tmpl in existing_by_name.items():
        tmpl_type = tmpl.type.value if tmpl.type else ""
        existing_set.add((name, tmpl_type))

    if expected_set == existing_set:
        logger.debug(f"{label.capitalize()} templates for {model} already correct ({len(expected_set)})")
        return

    logger.debug(f"{label.capitalize()} template mismatch for {model}: "
                 f"expected={len(expected_set)}, existing={len(existing_set)}")

    # Delete all and recreate
    for tmpl in existing_by_name.values():
        try:
            tmpl.delete()
        except Exception as err:
            logger.debug(
                f"Failed deleting existing {label} template '{tmpl.name}' from {model}: {err}"
            )

    for entry in expected:
        create_data = {
            "device_type": dt_id,
            "name": entry["name"],
            "type": entry.get("type", ""),
        }
        # Pass through optional fields
        for opt_field in ("mgmt_only", "poe_mode", "poe_type", "label",
                          "maximum_draw", "allocated_draw"):
            if opt_field in entry and entry[opt_field] is not None:
                create_data[opt_field] = entry[opt_field]
        try:
            template_endpoint.create(create_data)
        except pynetbox.core.query.RequestError as err:
            logger.warning(
                f"Failed to create {label} template '{entry['name']}' for {model}: {err}"
            )
    logger.info(f"Synced {len(expected)} {label} templates for {model}")


def ensure_device_type_specs(nb, nb_device_type, model):
    """Ensure a device type has correct specs (part number, u_height, interface templates)
    based on merged UNIFI_MODEL_SPECS + community library. Also syncs console/power port templates."""
    specs = _resolve_device_specs(model)
    if not specs:
        return

    # Serialize all template operations to prevent concurrent API races
    with _device_type_specs_lock:
        if nb_device_type.id in _device_type_specs_done:
            return
        _device_type_specs_done.add(nb_device_type.id)

        _ensure_device_type_specs_inner(nb, nb_device_type, model, specs)


def _ensure_device_type_specs_inner(nb, nb_device_type, model, specs):
    """Inner implementation of device type spec sync (called under lock)."""
    changed = False
    # Update part number and u_height if missing/wrong
    if specs.get("part_number") and (nb_device_type.part_number or "") != specs["part_number"]:
        nb_device_type.part_number = specs["part_number"]
        changed = True
    if specs.get("u_height") is not None and nb_device_type.u_height != specs["u_height"]:
        nb_device_type.u_height = specs["u_height"]
        changed = True
    # is_full_depth
    if specs.get("is_full_depth") is not None and getattr(nb_device_type, "is_full_depth", None) != specs["is_full_depth"]:
        nb_device_type.is_full_depth = specs["is_full_depth"]
        changed = True
    # airflow
    if specs.get("airflow") and getattr(nb_device_type, "airflow", None) != specs["airflow"]:
        nb_device_type.airflow = specs["airflow"]
        changed = True
    # weight
    if specs.get("weight") is not None:
        try:
            w = float(specs["weight"])
            if getattr(nb_device_type, "weight", None) != w:
                nb_device_type.weight = w
                nb_device_type.weight_unit = specs.get("weight_unit", "kg")
                changed = True
        except (ValueError, TypeError):
            pass
    # Add PoE budget as comment if available
    poe = specs.get("poe_budget", 0)
    expected_comments = f"PoE budget: {poe}W" if poe else ""
    if expected_comments and (nb_device_type.comments or "") != expected_comments:
        nb_device_type.comments = expected_comments
        changed = True
    if changed:
        try:
            nb_device_type.save()
            logger.info(f"Updated device type specs for {model}: part#={specs.get('part_number')}, "
                        f"u_height={specs.get('u_height')}, PoE={poe}W")
        except Exception as e:
            logger.warning(f"Failed to update device type specs for {model}: {e}")

    # --- Sync interface templates ---
    expected_ifaces = []
    # Prefer community 'interfaces' list (richer: poe_mode, poe_type, mgmt_only)
    if specs.get("interfaces"):
        for iface in specs["interfaces"]:
            entry = {"name": iface["name"], "type": iface.get("type", "1000base-t")}
            if iface.get("mgmt_only"):
                entry["mgmt_only"] = True
            if iface.get("poe_mode"):
                entry["poe_mode"] = iface["poe_mode"]
            if iface.get("poe_type"):
                entry["poe_type"] = iface["poe_type"]
            expected_ifaces.append(entry)
    elif specs.get("ports"):
        # Fallback to hardcoded ports tuple format
        for port_spec in specs["ports"]:
            pattern, port_type, count = port_spec
            if count == 1 and "{n}" not in pattern and "{n+" not in pattern:
                expected_ifaces.append({"name": pattern, "type": port_type})
            elif "{n+" in pattern:
                import re as _re
                m = _re.search(r'\{n\+(\d+)\}', pattern)
                offset = int(m.group(1)) if m else 0
                base_pattern = _re.sub(r'\{n\+\d+\}', '{}', pattern)
                for i in range(1, count + 1):
                    expected_ifaces.append({"name": base_pattern.format(offset + i), "type": port_type})
            else:
                for i in range(1, count + 1):
                    expected_ifaces.append({"name": pattern.replace("{n}", str(i)), "type": port_type})

    if expected_ifaces:
        _sync_templates(nb, nb_device_type, model, nb.dcim.interface_templates, expected_ifaces, "interface")

    # --- Sync console port templates ---
    if specs.get("console_ports"):
        expected_console = []
        for cp in specs["console_ports"]:
            expected_console.append({"name": cp["name"], "type": cp.get("type", "rj-45")})
        _sync_templates(nb, nb_device_type, model, nb.dcim.console_port_templates, expected_console, "console-port")

    # --- Sync power port templates ---
    if specs.get("power_ports"):
        expected_power = []
        for pp in specs["power_ports"]:
            entry = {"name": pp["name"], "type": pp.get("type", "iec-60320-c14")}
            if pp.get("maximum_draw") is not None:
                entry["maximum_draw"] = pp["maximum_draw"]
            if pp.get("allocated_draw") is not None:
                entry["allocated_draw"] = pp["allocated_draw"]
            expected_power.append(entry)
        _sync_templates(nb, nb_device_type, model, nb.dcim.power_port_templates, expected_power, "power-port")


def process_device(unifi, nb, site, device, nb_ubiquity, tenant, unifi_device_ips=None, unifi_site_obj=None):
    """Process a device and add it to NetBox."""
    try:
        device_name = get_device_name(device)
        device_model = device.get("model") or "Unknown Model"
        device_mac = get_device_mac(device)
        device_ip = get_device_ip(device)
        device_serial = get_device_serial(device)

        # Skip offline/disconnected devices
        device_state = (device.get("state") or device.get("status") or "").upper()
        if device_state in ("OFFLINE", "DISCONNECTED", "0"):
            logger.debug(f"Skipping offline device {device_name}")
            return

        logger.info(f"Processing device {device_name} at site {site}...")
        logger.debug(f"Device details: Model={device_model}, MAC={device_mac}, IP={device_ip}, Serial={device_serial}")

        # Determine device role from configured NETBOX.ROLES mapping
        nb_device_role, selected_role_key = select_netbox_role_for_device(device)
        logger.debug(f"Using role '{selected_role_key}' ({nb_device_role.name}) for device {device_name}")

        if not device_serial:
            logger.warning(f"Missing serial/mac/id for device {device_name}. Skipping...")
            return

        # VRF handling (env-controlled). Default: do not create VRFs.
        vrf, vrf_mode = get_vrf_for_site(nb, site.name)
        if vrf:
            logger.debug(f"Using VRF {vrf.name} (ID {vrf.id}) for site {site.name} (mode={vrf_mode})")
        else:
            logger.debug(f"Running without VRF for site {site.name} (mode={vrf_mode})")

        # Device Type creation
        logger.debug(f"Checking for existing device type: {device_model} (manufacturer ID: {nb_ubiquity.id})")
        nb_device_type = nb.dcim.device_types.get(model=device_model, manufacturer_id=nb_ubiquity.id)
        if not nb_device_type:
            # Pre-populate from community specs when creating a new device type
            specs = _resolve_device_specs(device_model)
            create_data = {
                "manufacturer": nb_ubiquity.id,
                "model": device_model,
                "slug": (specs or {}).get("slug") or slugify(f'{nb_ubiquity.name}-{device_model}'),
            }
            if specs:
                if specs.get("part_number"):
                    create_data["part_number"] = specs["part_number"]
                if specs.get("u_height") is not None:
                    create_data["u_height"] = specs["u_height"]
                if specs.get("is_full_depth") is not None:
                    create_data["is_full_depth"] = specs["is_full_depth"]
                if specs.get("airflow"):
                    create_data["airflow"] = specs["airflow"]
                if specs.get("weight") is not None:
                    try:
                        create_data["weight"] = float(specs["weight"])
                        create_data["weight_unit"] = specs.get("weight_unit", "kg")
                    except (ValueError, TypeError):
                        pass
            try:
                nb_device_type = nb.dcim.device_types.create(create_data)
                if nb_device_type:
                    logger.info(f"Device type {device_model} with ID {nb_device_type.id} successfully added to NetBox.")
            except pynetbox.core.query.RequestError as e:
                error_message = str(e).lower()
                if "duplicate key value violates unique constraint" in error_message:
                    # Race condition guard: another worker may have created the same type just before us.
                    nb_device_type = nb.dcim.device_types.get(model=device_model, manufacturer_id=nb_ubiquity.id)
                    if not nb_device_type and create_data.get("part_number"):
                        nb_device_type = nb.dcim.device_types.get(
                            part_number=create_data["part_number"], manufacturer_id=nb_ubiquity.id
                        )
                    if nb_device_type:
                        logger.debug(
                            f"Device type {device_model} already exists after duplicate create error; reusing ID {nb_device_type.id}."
                        )
                    else:
                        logger.error(f"Failed to recover duplicate device type for {device_name} at site {site}: {e}")
                        return
                else:
                    logger.error(f"Failed to create device type for {device_name} at site {site}: {e}")
                    return
        # Ensure device type has correct specs (ports, PoE, part number, etc.)
        ensure_device_type_specs(nb, nb_device_type, device_model)

        # Check for existing device
        logger.debug(f"Checking if device already exists: {device_name} (serial: {device_serial})")
        nb_device = nb.dcim.devices.get(site_id=site.id, serial=device_serial)
        if nb_device:
            logger.info(f"Device {device_name} with serial {device_serial} already exists. Checking IP...")
            # Update device name if changed in UniFi
            if nb_device.name != device_name:
                old_name = nb_device.name
                nb_device.name = device_name
                try:
                    nb_device.save()
                    logger.info(f"Updated device name from '{old_name}' to '{device_name}'")
                except pynetbox.core.query.RequestError as e:
                    logger.warning(f"Failed to update device name to '{device_name}': {e}")
                    nb_device.name = old_name  # Revert on failure
            # Update device type if model changed
            current_type_id = nb_device.device_type.id if nb_device.device_type else None
            if nb_device_type and current_type_id != nb_device_type.id:
                nb_device.device_type = nb_device_type.id
                try:
                    nb_device.save()
                    logger.info(f"Updated device type for {device_name} to {device_model}")
                except pynetbox.core.query.RequestError as e:
                    logger.warning(f"Failed to update device type for {device_name}: {e}")
            # Update asset tag from device name (ID/AID suffix)
            asset_tag = extract_asset_tag(device_name)
            if asset_tag and getattr(nb_device, 'asset_tag', None) != asset_tag:
                nb_device.asset_tag = asset_tag
                try:
                    nb_device.save()
                    logger.info(f"Updated asset tag for {device_name} to {asset_tag}")
                except pynetbox.core.query.RequestError as e:
                    logger.warning("Failed to update asset tag for %s: %s", device_name, e)
        else:
            # Create NetBox Device
            try:
                device_data = {
                        'name': device_name,
                        'device_type': nb_device_type.id,
                        'tenant': tenant.id,
                        'site': site.id,
                        'serial': device_serial
                    }
                asset_tag = extract_asset_tag(device_name)
                if asset_tag:
                    device_data['asset_tag'] = asset_tag

                logger.debug("Getting postable fields for NetBox API")
                available_fields = get_postable_fields(netbox_url, netbox_token, 'dcim/devices')
                logger.debug(f"Available NetBox API fields: {list(available_fields.keys())}")
                if 'role' in available_fields:
                    logger.debug(f"Using 'role' field for device role (ID: {nb_device_role.id})")
                    device_data['role'] = nb_device_role.id
                elif 'device_role' in available_fields:
                    logger.debug(f"Using 'device_role' field for device role (ID: {nb_device_role.id})")
                    device_data['device_role'] = nb_device_role.id
                else:
                    logger.error(f'Could not determine the syntax for the role. Skipping device {device_name}, '
                                    f'{device_serial}.')
                    return None

                # Device status on create (default: offline)
                desired_status = (os.getenv("NETBOX_DEVICE_STATUS") or "offline").strip().lower()
                if desired_status and "status" in available_fields:
                    device_data["status"] = desired_status

                # Add the device to Netbox
                logger.debug(f"Creating device in NetBox with data: {device_data}")
                nb_device = nb.dcim.devices.create(device_data)

                if nb_device:
                    logger.info(f"Device {device_name} serial {device_serial} with ID {nb_device.id} successfully added to NetBox.")
            except pynetbox.core.query.RequestError as e:
                error_message = str(e)
                if "Device name must be unique per site" in error_message:
                    logger.warning(f"Device name {device_name} already exists at site {site}. "
                                   f"Trying with name {device_name}_{device_serial}.")
                    try:
                        device_data['name'] = f"{device_name}_{device_serial}"
                        nb_device = nb.dcim.devices.create(device_data)
                        if nb_device:
                            logger.info(f"Device {device_name} with ID {nb_device.id} successfully added to NetBox.")
                    except pynetbox.core.query.RequestError as e2:
                        logger.exception(f"Failed to create device {device_name} serial {device_serial} at site {site}: {e2}")
                        return
                else:
                    logger.exception(f"Failed to create device {device_name} serial {device_serial} at site {site}: {e}")
                    return

        if nb_device:
            # Ensure "zabbix" tag is present
            zabbix_tag = ensure_tag(nb, "zabbix")
            if zabbix_tag:
                current_tags = [t.id for t in (nb_device.tags or [])]
                if zabbix_tag.id not in current_tags:
                    current_tags.append(zabbix_tag.id)
                    nb_device.tags = current_tags
                    nb_device.save()
                    logger.info(f"Added 'zabbix' tag to device {device_name}.")

            # Sync device state (ONLINE/OFFLINE -> active/offline)
            try:
                sync_device_state(nb, nb_device, device)
            except Exception as e:
                logger.warning(f"Failed to sync state for {device_name}: {e}")

            # Sync custom fields (firmware, uptime, mac)
            try:
                sync_device_custom_fields(nb, nb_device, device)
            except Exception as e:
                logger.warning(f"Failed to sync custom fields for {device_name}: {e}")

            # Sync physical interfaces from UniFi to NetBox
            try:
                api_style = getattr(unifi, "api_style", "legacy") or "legacy"
                sync_device_interfaces(nb, nb_device, device, api_style, unifi=unifi, site_obj=unifi_site_obj)
            except Exception as e:
                logger.warning(f"Failed to sync interfaces for {device_name}: {e}")

        # Add primary IP if available — skip routers/gateways (they manage their own IPs)
        role_key = infer_role_key_for_device(device)
        if role_key in ("GATEWAY", "ROUTER"):
            logger.debug(f"Skipping IP assignment for {device_name} — device is a {role_key}")
            return

        if not device_ip:
            logger.warning(f"Missing IP for device {device_name}. Skipping IP assignment...")
            return
        try:
            ipaddress.ip_address(device_ip)
        except ValueError:
            logger.warning(f"Invalid IP {device_ip} for device {device_name}. Skipping...")
            return

        # --- DHCP-to-static IP reassignment ---
        if is_ip_in_dhcp_range(device_ip):
            # Skip routers/gateways — they manage their own IPs
            role_key = infer_role_key_for_device(device)
            if role_key in ("GATEWAY", "ROUTER"):
                logger.debug(f"Skipping DHCP-to-static for {device_name} — device is a {role_key}")
            else:
                # If device already has a static IP in NetBox, keep it
                if nb_device and nb_device.primary_ip4:
                    existing_ip_obj = nb.ipam.ip_addresses.get(id=nb_device.primary_ip4.id)
                    if existing_ip_obj:
                        existing_ip_str = str(existing_ip_obj.address).split("/")[0]
                        if not is_ip_in_dhcp_range(existing_ip_str):
                            logger.debug(
                                f"Device {device_name} reports DHCP IP {device_ip} but NetBox "
                                f"already has static IP {existing_ip_str}. Keeping existing."
                            )
                            return

                logger.info(f"Device {device_name} has DHCP IP {device_ip}. Finding static IP...")
                # Find prefix containing the DHCP IP
                if vrf:
                    dhcp_prefixes = list(nb.ipam.prefixes.filter(contains=device_ip, vrf_id=vrf.id))
                else:
                    dhcp_prefixes = list(nb.ipam.prefixes.filter(contains=device_ip))

                if dhcp_prefixes:
                    target_prefix = dhcp_prefixes[0]
                    static_ip = find_available_static_ip(nb, target_prefix, vrf, tenant, unifi_device_ips=unifi_device_ips)
                    if static_ip:
                        logger.info(f"Reassigning {device_name} from DHCP {device_ip} to static {static_ip}")
                        new_ip = static_ip.split("/")[0]
                        # Set static IP on UniFi device with gateway + DNS from network config
                        if unifi_site_obj:
                            subnet_mask_bits = int(static_ip.split("/")[1])
                            subnet_mask = str(ipaddress.IPv4Network(f"0.0.0.0/{subnet_mask_bits}").netmask)
                            gw, dns = _get_network_info_for_ip(new_ip)
                            set_unifi_device_static_ip(
                                unifi, unifi_site_obj, device, new_ip,
                                subnet_mask=subnet_mask, gateway=gw, dns_servers=dns
                            )
                        device_ip = new_ip
                    else:
                        logger.info(f"No available static IP for {device_name}. Keeping DHCP IP {device_ip}.")
                else:
                    logger.info(f"No prefix found for DHCP IP {device_ip}. Keeping DHCP IP.")
        # --- End DHCP-to-static ---

        # get the prefix that this IP address belongs to
        if vrf:
            prefixes = nb.ipam.prefixes.filter(contains=device_ip, vrf_id=vrf.id)
        else:
            prefixes = nb.ipam.prefixes.filter(contains=device_ip)
        if not prefixes:
            logger.warning(f"No prefix found for IP {device_ip} for device {device_name}. Skipping...")
            return
        for prefix in prefixes:
            # Extract the prefix length (mask) from the prefix
            subnet_mask = prefix.prefix.split('/')[1]
            ip = f'{device_ip}/{subnet_mask}'
            break
        if nb_device:
            # Check if the IP has changed compared to what NetBox has
            old_ip_str = None
            if nb_device.primary_ip4:
                old_ip_obj = nb.ipam.ip_addresses.get(id=nb_device.primary_ip4.id)
                if old_ip_obj:
                    old_ip_str = str(old_ip_obj.address).split("/")[0]
            if old_ip_str and old_ip_str != device_ip:
                logger.info(f"Device {device_name} IP changed: {old_ip_str} -> {device_ip}. Updating NetBox.")
                # Remove old IP assignment
                try:
                    old_ip_obj.delete()
                    logger.info(f"Deleted old IP {old_ip_str} for device {device_name}.")
                except Exception as e:
                    logger.warning(f"Could not delete old IP {old_ip_str} for device {device_name}: {e}")
                nb_device.primary_ip4 = None
                nb_device.save()
            elif old_ip_str and old_ip_str == device_ip:
                logger.debug(f"Device {device_name} IP unchanged ({device_ip}). Skipping IP update.")
                return

            interface = nb.dcim.interfaces.get(device_id=nb_device.id, name="vlan.1")
            if not interface:
                try:
                    iface_payload = {
                        "device": nb_device.id,
                        "name": "vlan.1",
                        "type": "virtual",
                        "enabled": True,
                    }
                    if vrf:
                        iface_payload["vrf_id"] = vrf.id
                    interface = nb.dcim.interfaces.create(**iface_payload)
                    if interface:
                        logger.info(
                            f"Interface vlan.1 for device {device_name} with ID {interface.id} successfully added to NetBox.")
                except pynetbox.core.query.RequestError as e:
                    logger.exception(
                        f"Failed to create interface vlan.1 for device {device_name} at site {site}: {e}")
                    return
            ip_get_filters = {"address": ip, "tenant_id": tenant.id}
            if vrf:
                ip_get_filters["vrf_id"] = vrf.id
            nb_ip = nb.ipam.ip_addresses.get(**ip_get_filters)
            if not nb_ip:
                try:
                    ip_payload = {
                        "assigned_object_id": interface.id,
                        "assigned_object_type": 'dcim.interface',
                        "address": ip,
                        "tenant_id": tenant.id,
                        "status": "active",
                    }
                    if vrf:
                        ip_payload["vrf_id"] = vrf.id
                    nb_ip = nb.ipam.ip_addresses.create(ip_payload)
                    if nb_ip:
                        logger.info(f"IP address {ip} with ID {nb_ip.id} successfully added to NetBox.")
                except pynetbox.core.query.RequestError as e:
                    logger.exception(f"Failed to create IP address {ip} for device {device_name} at site {site}: {e}")
                    return
            if nb_ip:
                nb_device.primary_ip4 = nb_ip.id
                nb_device.save()
                logger.info(f"Device {device_name} primary IP set to {ip}.")

    except Exception as e:
        logger.exception(f"Failed to process device {get_device_name(device)} at site {site}: {e}")

def process_site(unifi, nb, site_obj, site_display_name, nb_site, nb_ubiquity, tenant):
    """
    Process devices for a given site and add them to NetBox.
    Also syncs VLANs, WiFi SSIDs, and uplink cables.
    """
    logger.debug(f"Processing site {site_display_name}...")
    try:
        if site_obj:
            # Sync VLANs from UniFi networks
            if os.getenv("SYNC_VLANS", "true").strip().lower() in ("true", "1", "yes"):
                try:
                    sync_site_vlans(nb, site_obj, nb_site, tenant)
                except Exception as e:
                    logger.warning(f"Failed to sync VLANs for site {site_display_name}: {e}")

            # Sync WiFi SSIDs
            if os.getenv("SYNC_WLANS", "true").strip().lower() in ("true", "1", "yes"):
                try:
                    sync_site_wlans(nb, site_obj, nb_site, tenant)
                except Exception as e:
                    logger.warning(f"Failed to sync WLANs for site {site_display_name}: {e}")

            # Auto-discover DHCP ranges from UniFi network configs
            if os.getenv("DHCP_AUTO_DISCOVER", "true").strip().lower() in ("true", "1", "yes"):
                try:
                    site_dhcp_ranges = extract_dhcp_ranges_from_unifi(site_obj, unifi=unifi)
                    if site_dhcp_ranges:
                        with _unifi_dhcp_ranges_lock:
                            _unifi_dhcp_ranges[nb_site.id] = site_dhcp_ranges
                        logger.info(
                            f"Discovered {len(site_dhcp_ranges)} DHCP range(s) from UniFi "
                            f"for site {site_display_name}: {[str(n) for n in site_dhcp_ranges]}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to extract DHCP ranges for site {site_display_name}: {e}")

            logger.debug(f"Fetching devices for site: {site_display_name}")
            devices = site_obj.device.all()
            logger.debug(f"Found {len(devices)} devices for site {site_display_name}")

            # Collect all UniFi device IPs for DHCP-to-static checks
            unifi_device_ips = set()
            # Also collect serials for cleanup phase
            site_serials = set()
            for d in devices:
                dip = get_device_ip(d)
                if dip:
                    unifi_device_ips.add(dip)
                ds = get_device_serial(d)
                if ds:
                    site_serials.add(ds)
            # Store serials for cleanup
            with _cleanup_serials_lock:
                _cleanup_serials_by_site[nb_site.id] = site_serials

            with ThreadPoolExecutor(max_workers=MAX_DEVICE_THREADS) as executor:
                futures = []
                for device in devices:
                    futures.append(executor.submit(process_device, unifi, nb, nb_site, device, nb_ubiquity, tenant, unifi_device_ips=unifi_device_ips, unifi_site_obj=site_obj))

                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Error processing a device at site {site_display_name}: {e}")

            # Sync uplink cables after all devices are processed
            if os.getenv("SYNC_CABLES", "true").strip().lower() in ("true", "1", "yes"):
                try:
                    # Build a lookup of all NetBox devices by MAC/serial/UUID for this site
                    nb_devices_at_site = nb.dcim.devices.filter(site_id=nb_site.id, tenant_id=tenant.id)
                    all_nb_devices_by_mac = {}
                    for d in nb_devices_at_site:
                        serial = str(d.serial or "").upper().replace(":", "")
                        if serial:
                            all_nb_devices_by_mac[serial] = d
                        # Also index by custom field MAC if available
                        cf = dict(d.custom_fields or {})
                        cf_mac = (cf.get("unifi_mac") or "").upper().replace(":", "")
                        if cf_mac:
                            all_nb_devices_by_mac[cf_mac] = d
                    # Index UniFi device UUIDs for O(1) upstream lookup
                    for unifi_dev in devices:
                        dev_id = unifi_dev.get("id")
                        dev_serial = get_device_serial(unifi_dev)
                        if dev_id and dev_serial and dev_serial in all_nb_devices_by_mac:
                            all_nb_devices_by_mac[str(dev_id)] = all_nb_devices_by_mac[dev_serial]

                    # Ensure all devices have uplink data from device detail API
                    # (sync_device_interfaces only fetches detail for devices with list-type interfaces)
                    api_style = getattr(unifi, "api_style", "legacy") or "legacy"
                    if api_style == "integration":
                        for device in devices:
                            if not device.get("_detail_uplink"):
                                device_id = device.get("id")
                                if device_id:
                                    detail = _fetch_integration_device_detail(unifi, site_obj, device_id)
                                    if detail and isinstance(detail, dict):
                                        detail_uplink = detail.get("uplink")
                                        if detail_uplink and isinstance(detail_uplink, dict):
                                            device["_detail_uplink"] = detail_uplink

                    for device in devices:
                        device_serial = get_device_serial(device)
                        if not device_serial:
                            continue
                        # Use the already-built lookup instead of an extra API call
                        nb_device = all_nb_devices_by_mac.get(device_serial)
                        if nb_device:
                            try:
                                sync_uplink_cable(nb, nb_device, device, all_nb_devices_by_mac)
                            except Exception as e:
                                logger.debug(f"Could not sync uplink cable for {get_device_name(device)}: {e}")
                except Exception as e:
                    logger.warning(f"Failed to sync uplink cables for site {site_display_name}: {e}")

            # Mark stale devices (in NetBox but no longer in UniFi) as offline
            if os.getenv("SYNC_STALE_CLEANUP", "true").strip().lower() in ("true", "1", "yes"):
                try:
                    unifi_serials = set()
                    for d in devices:
                        s = get_device_serial(d)
                        if s:
                            unifi_serials.add(s)
                    nb_devices_at_site = list(nb.dcim.devices.filter(site_id=nb_site.id, tenant_id=tenant.id))
                    for nb_dev in nb_devices_at_site:
                        if nb_dev.serial and nb_dev.serial not in unifi_serials:
                            current_status = nb_dev.status.value if hasattr(nb_dev.status, 'value') else str(nb_dev.status)
                            if current_status != "offline":
                                nb_dev.status = "offline"
                                nb_dev.save()
                                logger.info(f"Marked stale device '{nb_dev.name}' as offline (not in UniFi)")
                except Exception as e:
                    logger.warning(f"Failed to clean up stale devices for site {site_display_name}: {e}")
        else:
            logger.error(f"Site {site_display_name} not found")
    except Exception as e:
        logger.error(f"Failed to process site {site_display_name}: {e}")

def process_controller(unifi_url, unifi_username, unifi_password, unifi_mfa_secret, unifi_api_key, unifi_api_key_header, nb, nb_ubiquity, tenant,
                       netbox_sites_dict, config=None):
    """
    Process all sites and devices for a specific UniFi controller.
    """
    logger.info(f"Processing controller {unifi_url}...")
    logger.debug(f"Initializing UniFi connection to: {unifi_url}")

    try:
        # Create a Unifi instance and authenticate
        unifi = Unifi(
            unifi_url,
            unifi_username,
            unifi_password,
            unifi_mfa_secret,
            api_key=unifi_api_key,
            api_key_header=unifi_api_key_header,
        )
        logger.debug(f"UniFi connection established to: {unifi_url}")
        
        # Get all sites from the controller
        logger.debug(f"Fetching sites from controller: {unifi_url}")
        sites = unifi.sites
        logger.debug(f"Found {len(sites)} sites on controller: {unifi_url}")
        logger.info(f"Found {len(sites)} sites for controller {unifi_url}")

        with ThreadPoolExecutor(max_workers=MAX_SITE_THREADS) as executor:
            futures = []
            for site_name, site_obj in sites.items():
                logger.info(f"Processing site {site_name}...")
                nb_site = match_sites_to_netbox(site_name, netbox_sites_dict, config)

                if not nb_site:
                    logger.warning(f"No match found for Ubiquity site: {site_name}. Skipping...")
                    continue

                futures.append(executor.submit(process_site, unifi, nb, site_obj, site_name, nb_site, nb_ubiquity, tenant))

            # Wait for all site-processing threads to complete
            for future in as_completed(futures):
                future.result()
    except Exception as e:
        logger.error(f"Error processing controller {unifi_url}: {e}")

def process_all_controllers(unifi_url_list, unifi_username, unifi_password, unifi_mfa_secret, unifi_api_key, unifi_api_key_header, nb, nb_ubiquity, tenant,
                            netbox_sites_dict, config=None):
    """
    Process all UniFi controllers in parallel.
    """
    with ThreadPoolExecutor(max_workers=MAX_CONTROLLER_THREADS) as executor:
        future_to_url = {}
        for url in unifi_url_list:
            future = executor.submit(
                process_controller,
                url,
                unifi_username,
                unifi_password,
                unifi_mfa_secret,
                unifi_api_key,
                unifi_api_key_header,
                nb,
                nb_ubiquity,
                tenant,
                netbox_sites_dict,
                config,
            )
            future_to_url[future] = url

        # Wait for all controller-processing threads to complete
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                future.result()
            except Exception as e:
                logger.exception(f"Error processing one of the UniFi controllers {url}: {e}")
                continue

# ---------------------------------------------------------------------------
#  NetBox Cleanup Functions
# ---------------------------------------------------------------------------

def _is_cleanup_enabled() -> bool:
    """Check if cleanup is enabled via NETBOX_CLEANUP env var."""
    return _parse_env_bool(os.getenv("NETBOX_CLEANUP"), default=False)


def _cleanup_stale_days() -> int:
    """Get the stale device grace period in days."""
    return _read_env_int("CLEANUP_STALE_DAYS", default=30, minimum=0)


def cleanup_stale_devices(nb, nb_site, tenant, unifi_serials):
    """Delete devices at a site that are no longer present in UniFi.

    Only deletes devices that have been offline for longer than CLEANUP_STALE_DAYS.
    When CLEANUP_STALE_DAYS=0, all stale devices are deleted immediately.
    """
    grace_days = _cleanup_stale_days()
    nb_devices = list(nb.dcim.devices.filter(site_id=nb_site.id, tenant_id=tenant.id))
    deleted = 0
    for dev in nb_devices:
        serial = str(dev.serial or "").upper().replace(":", "")
        if not serial:
            continue
        if serial in unifi_serials:
            continue
        # Device not found in UniFi — check grace period
        if grace_days > 0:
            # Use last_updated as proxy for "last seen"
            import datetime
            last_updated = getattr(dev, "last_updated", None)
            if last_updated:
                try:
                    if isinstance(last_updated, str):
                        lu = datetime.datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                    else:
                        lu = last_updated
                    now = datetime.datetime.now(datetime.timezone.utc)
                    age_days = (now - lu).days
                    if age_days < grace_days:
                        logger.debug(f"Stale device {dev.name} ({serial}) last updated {age_days}d ago, "
                                     f"grace={grace_days}d — skipping")
                        continue
                except Exception as err:
                    logger.debug(
                        f"Could not parse last_updated for stale-check on {dev.name} ({serial}): {err}"
                    )
        # Delete the device and its interfaces/IPs
        try:
            dev.delete()
            deleted += 1
            logger.info(f"Cleanup: deleted stale device {dev.name} ({serial}) from site {nb_site.name}")
        except Exception as e:
            logger.warning(f"Cleanup: failed to delete stale device {dev.name}: {e}")
    if deleted:
        logger.info(f"Cleanup: deleted {deleted} stale device(s) from site {nb_site.name}")
    return deleted


def cleanup_orphan_interfaces(nb, nb_site, tenant):
    """Delete garbage interfaces (names containing '?') at a site."""
    nb_devices = list(nb.dcim.devices.filter(site_id=nb_site.id, tenant_id=tenant.id))
    deleted = 0
    for dev in nb_devices:
        ifaces = list(nb.dcim.interfaces.filter(device_id=dev.id))
        for iface in ifaces:
            if "?" in (iface.name or ""):
                try:
                    iface.delete()
                    deleted += 1
                    logger.debug(f"Cleanup: deleted garbage interface '{iface.name}' on {dev.name}")
                except Exception as e:
                    logger.warning(f"Cleanup: failed to delete interface '{iface.name}' on {dev.name}: {e}")
    if deleted:
        logger.info(f"Cleanup: deleted {deleted} garbage interface(s) from site {nb_site.name}")
    return deleted


def cleanup_orphan_ips(nb, tenant):
    """Delete IP addresses that have no assigned object (orphaned)."""
    all_ips = list(nb.ipam.ip_addresses.filter(tenant_id=tenant.id))
    deleted = 0
    for ip in all_ips:
        if ip.assigned_object is None and ip.assigned_object_id is None:
            try:
                ip.delete()
                deleted += 1
                logger.debug(f"Cleanup: deleted orphan IP {ip.address}")
            except Exception as e:
                logger.warning(f"Cleanup: failed to delete orphan IP {ip.address}: {e}")
    if deleted:
        logger.info(f"Cleanup: deleted {deleted} orphan IP(s)")
    return deleted


def cleanup_orphan_cables(nb, nb_site):
    """Delete cables at a site where one or both terminations are missing."""
    try:
        cables = list(nb.dcim.cables.filter(site_id=nb_site.id))
    except Exception:
        cables = list(nb.dcim.cables.all())
    deleted = 0
    for cable in cables:
        a_ok = getattr(cable, "a_terminations", None)
        b_ok = getattr(cable, "b_terminations", None)
        if not a_ok or not b_ok:
            try:
                cable.delete()
                deleted += 1
                logger.debug(f"Cleanup: deleted orphan cable {cable.id}")
            except Exception as e:
                logger.warning(f"Cleanup: failed to delete orphan cable {cable.id}: {e}")
    if deleted:
        logger.info(f"Cleanup: deleted {deleted} orphan cable(s) from site {nb_site.name}")
    return deleted


def cleanup_device_types(nb, nb_ubiquity):
    """Refresh device type specs and delete unused device types (device_count == 0)."""
    all_types = list(nb.dcim.device_types.filter(manufacturer_id=nb_ubiquity.id))
    refreshed = 0
    deleted = 0
    for dt in all_types:
        # Refresh specs from community + hardcoded
        model = dt.model
        specs = _resolve_device_specs(model)
        if specs:
            try:
                _ensure_device_type_specs_inner(nb, dt, model, specs)
                refreshed += 1
            except Exception as e:
                logger.warning(f"Cleanup: failed to refresh specs for device type {model}: {e}")
        # Delete unused device types
        device_count = getattr(dt, "device_count", None)
        if device_count is not None and device_count == 0:
            try:
                dt.delete()
                deleted += 1
                logger.info(f"Cleanup: deleted unused device type {model}")
            except Exception as e:
                logger.warning(f"Cleanup: failed to delete unused device type {model}: {e}")
    logger.info(f"Cleanup: refreshed {refreshed} device type(s), deleted {deleted} unused device type(s)")
    return deleted


def run_netbox_cleanup(nb, nb_ubiquity, tenant, netbox_sites_dict, all_unifi_serials_by_site):
    """Orchestrate all cleanup functions."""
    if not _is_cleanup_enabled():
        logger.debug("NetBox cleanup is disabled (NETBOX_CLEANUP != true)")
        return

    logger.info("=== Starting NetBox cleanup ===")

    # Per-site cleanup
    for site_name, nb_site in netbox_sites_dict.items():
        site_serials = all_unifi_serials_by_site.get(nb_site.id, set())
        try:
            cleanup_stale_devices(nb, nb_site, tenant, site_serials)
        except Exception as e:
            logger.warning(f"Cleanup error (stale devices) at site {site_name}: {e}")
        try:
            cleanup_orphan_interfaces(nb, nb_site, tenant)
        except Exception as e:
            logger.warning(f"Cleanup error (orphan interfaces) at site {site_name}: {e}")
        try:
            cleanup_orphan_cables(nb, nb_site)
        except Exception as e:
            logger.warning(f"Cleanup error (orphan cables) at site {site_name}: {e}")

    # Global cleanup (not per-site)
    try:
        cleanup_orphan_ips(nb, tenant)
    except Exception as e:
        logger.warning(f"Cleanup error (orphan IPs): {e}")

    try:
        cleanup_device_types(nb, nb_ubiquity)
    except Exception as e:
        logger.warning(f"Cleanup error (device types): {e}")

    logger.info("=== NetBox cleanup complete ===")


if __name__ == "__main__":
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='Sync UniFi devices to NetBox')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose (debug) logging')
    args = parser.parse_args()
    
    # Configure logging with appropriate level based on verbose flag
    log_level = logging.DEBUG if args.verbose else logging.INFO
    setup_logging(log_level)
    
    if args.verbose:
        logger.debug("Verbose logging enabled")
    logger.debug("Loading runtime configuration from environment variables")
    try:
        config = load_runtime_config()
    except Exception as e:
        logger.exception(f"Failed to load runtime configuration: {e}")
        raise SystemExit(1)
    logger.debug("Runtime configuration loaded successfully")
    try:
        unifi_url_list = config['UNIFI']['URLS']
    except (KeyError, TypeError):
        logger.error("UniFi URL list is missing. Set UNIFI_URLS in .env.")
        raise SystemExit(1)
    if not unifi_url_list:
        logger.error("UniFi URL list is empty. Set UNIFI_URLS in .env (comma-separated or JSON array).")
        raise SystemExit(1)

    unifi_username = os.getenv('UNIFI_USERNAME')
    unifi_password = os.getenv('UNIFI_PASSWORD')
    unifi_mfa_secret = os.getenv('UNIFI_MFA_SECRET')
    unifi_api_key = os.getenv('UNIFI_API_KEY')
    unifi_api_key_header = os.getenv('UNIFI_API_KEY_HEADER')

    if not unifi_api_key and not (unifi_username and unifi_password):
        logger.error("Missing UniFi credentials. Set UNIFI_API_KEY or UNIFI_USERNAME + UNIFI_PASSWORD.")
        raise SystemExit(1)

    # Connect to Netbox
    try:
        netbox_url = config['NETBOX']['URL']
    except (KeyError, TypeError):
        logger.error("NetBox URL is missing. Set NETBOX_URL in .env.")
        raise SystemExit(1)
    if not netbox_url:
        logger.error("NetBox URL is empty. Set NETBOX_URL in .env.")
        raise SystemExit(1)
    netbox_token = os.getenv('NETBOX_TOKEN')
    if not netbox_token:
        logger.error("Netbox token is missing from environment variables.")
        raise SystemExit(1)

    # Create a custom HTTP session as this script will often exceed the default pool size of 10
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50)

    # Adjust connection pool size
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.verify = _netbox_verify_ssl()

    logger.debug(f"Initializing NetBox API connection to: {netbox_url}")
    nb = pynetbox.api(netbox_url, token=netbox_token, threading=True)
    nb.http_session = session  # Attach the custom session
    logger.debug("NetBox API connection established")

    nb_ubiquity = nb.dcim.manufacturers.get(slug='ubiquity')
    try:
        tenant_name = config['NETBOX']['TENANT']
    except (KeyError, TypeError):
        logger.error(
            "NetBox tenant is missing. Set NETBOX_IMPORT_TENANT or NETBOX_TENANT in .env, "
            "and ensure the tenant exists in NetBox."
        )
        raise SystemExit(1)
    if not tenant_name:
        logger.error("NetBox tenant is empty. Set NETBOX_IMPORT_TENANT or NETBOX_TENANT in .env.")
        raise SystemExit(1)

    tenant = nb.tenancy.tenants.get(name=tenant_name)
    if not tenant:
        logger.error(
            f"NetBox tenant '{tenant_name}' was not found. "
            "Create it in NetBox or update NETBOX_IMPORT_TENANT/NETBOX_TENANT."
        )
        raise SystemExit(1)

    roles_config = config.get('NETBOX', {}).get('ROLES')
    if not isinstance(roles_config, dict) or not roles_config:
        logger.error(
            "NETBOX.ROLES is missing. Set NETBOX_ROLES JSON in .env "
            "or NETBOX_ROLE_<KEY> variables (e.g. NETBOX_ROLE_WIRELESS=AP)."
        )
        raise SystemExit(1)

    netbox_device_roles.clear()
    for role_key, role_name in roles_config.items():
        if not role_name:
            continue
        normalized_key = str(role_key).upper()
        role_slug = slugify(role_name)
        role_obj = None
        try:
            role_obj = nb.dcim.device_roles.get(slug=role_slug)
        except ValueError:
            # If multiple roles match (unexpected), just pick the first.
            role_obj = next(iter(nb.dcim.device_roles.filter(slug=role_slug)), None)
        if not role_obj:
            try:
                role_obj = nb.dcim.device_roles.get(name=role_name)
            except ValueError:
                role_obj = next(iter(nb.dcim.device_roles.filter(name=role_name)), None)
        if not role_obj:
            try:
                role_obj = nb.dcim.device_roles.create({'name': role_name, 'slug': role_slug})
                if role_obj:
                    logger.info(f"Role {normalized_key} ({role_name}) with ID {role_obj.id} successfully added to NetBox.")
            except pynetbox.core.query.RequestError as e:
                # Another process might have created it, or name/slug might already exist.
                logger.warning(f"Failed to create role {normalized_key} ({role_name}): {e}. Trying to fetch existing role.")
                try:
                    role_obj = nb.dcim.device_roles.get(slug=role_slug) or nb.dcim.device_roles.get(name=role_name)
                except ValueError:
                    role_obj = None
        if role_obj:
            netbox_device_roles[normalized_key] = role_obj

    if not netbox_device_roles:
        logger.error("Could not load or create any roles from NETBOX roles configuration.")
        raise SystemExit(1)

    logger.debug("Fetching all NetBox sites")
    netbox_sites = nb.dcim.sites.all()
    logger.debug(f"Found {len(netbox_sites)} sites in NetBox")

    # Preprocess NetBox sites
    logger.debug("Preparing NetBox sites dictionary")
    netbox_sites_dict = prepare_netbox_sites(netbox_sites)
    logger.debug(f"Prepared {len(netbox_sites_dict)} NetBox sites for mapping")

    if not nb_ubiquity:
        nb_ubiquity = nb.dcim.manufacturers.create({'name': 'Ubiquity Networks', 'slug': 'ubiquity'})
        if nb_ubiquity:
            logger.info(f"Ubiquity manufacturer with ID {nb_ubiquity.id} successfully added to Netbox.")

    # Sync loop — run once or continuously based on SYNC_INTERVAL
    sync_interval = _sync_interval_seconds()
    import time as _time
    run_count = 0
    while True:
        run_count += 1
        # Clear per-run caches on subsequent runs
        if run_count > 1:
            _device_type_specs_done.clear()
            _cleanup_serials_by_site.clear()
            _assigned_static_ips.clear()
            _unifi_dhcp_ranges.clear()
            _unifi_network_info.clear()
            _exhausted_static_prefixes.clear()
            with _static_prefix_locks_lock:
                _static_prefix_locks.clear()

        logger.info(f"=== Sync run #{run_count} starting ===")

        # Process all UniFi controllers in parallel
        process_all_controllers(unifi_url_list, unifi_username, unifi_password, unifi_mfa_secret,
                                unifi_api_key, unifi_api_key_header, nb, nb_ubiquity,
                                tenant, netbox_sites_dict, config)

        # Run cleanup after sync
        run_netbox_cleanup(nb, nb_ubiquity, tenant, netbox_sites_dict, _cleanup_serials_by_site)

        logger.info(f"=== Sync run #{run_count} complete ===")

        if sync_interval <= 0:
            break
        logger.info(f"Sleeping {sync_interval} seconds until next sync...")
        _time.sleep(sync_interval)
