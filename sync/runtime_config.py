"""Runtime configuration and environment parsing helpers."""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)


def _normalize_text_value(raw_value) -> str:
    """
    Normalize a free-form text value:
    - trim leading/trailing whitespace
    - strip one pair of matching surrounding quotes ('...' or "...")
    """
    if raw_value is None:
        return ""
    text = str(raw_value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _parse_env_bool(raw_value: str | None, default: bool = False) -> bool:
    if raw_value is None:
        return default
    value = str(raw_value).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    logger.warning(f"Invalid boolean value '{raw_value}'. Using default {default}.")
    return default


def _read_env_int(var_name: str, default: int, minimum: int | None = None) -> int:
    raw_value = os.getenv(var_name)
    if raw_value is None or str(raw_value).strip() == "":
        return default
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        logger.warning(
            f"Invalid integer value for {var_name}: {raw_value}. Using default {default}."
        )
        return default
    if minimum is not None and value < minimum:
        logger.warning(
            f"Value for {var_name} must be >= {minimum}. Using default {default}."
        )
        return default
    return value


def _unifi_verify_ssl() -> bool:
    return _parse_env_bool(os.getenv("UNIFI_VERIFY_SSL"), default=True)


def _netbox_verify_ssl() -> bool:
    return _parse_env_bool(os.getenv("NETBOX_VERIFY_SSL"), default=True)


def _sync_interval_seconds() -> int:
    return _read_env_int("SYNC_INTERVAL", default=0, minimum=0)


def _parse_env_list(var_name: str) -> list[str] | None:
    raw_value = os.getenv(var_name)
    if raw_value is None or not str(raw_value).strip():
        return None

    value = str(raw_value).strip()
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as err:
            raise ValueError(f"{var_name} must be a JSON array or comma-separated list.") from err
        if not isinstance(parsed, list):
            raise ValueError(f"{var_name} JSON value must be an array.")
        return [str(item).strip() for item in parsed if str(item).strip()]

    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_env_mapping(var_name: str) -> dict[str, str] | None:
    raw_value = os.getenv(var_name)
    if raw_value is None or not str(raw_value).strip():
        return None

    value = str(raw_value).strip()
    if value.startswith("{"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as err:
            raise ValueError(f"{var_name} must be a JSON object or key=value pairs.") from err
        if not isinstance(parsed, dict):
            raise ValueError(f"{var_name} JSON value must be an object.")
        return {
            str(key).strip(): str(item).strip()
            for key, item in parsed.items()
            if str(key).strip() and str(item).strip()
        }

    mapping = {}
    for pair in re.split(r"[;,]", value):
        pair = pair.strip()
        if not pair:
            continue
        if "=" in pair:
            key, item = pair.split("=", 1)
        elif ":" in pair:
            key, item = pair.split(":", 1)
        else:
            raise ValueError(
                f"{var_name} pair '{pair}' is invalid. Use key=value pairs separated by ',' or ';'."
            )
        key = key.strip()
        item = item.strip()
        if key and item:
            mapping[key] = item
    return mapping


def _load_roles_from_env():
    roles_from_mapping = _parse_env_mapping("NETBOX_ROLES")
    if roles_from_mapping:
        return {str(key).upper(): value for key, value in roles_from_mapping.items()}

    prefix = "NETBOX_ROLE_"
    roles = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        role_key = key[len(prefix):].strip().upper()
        role_value = str(value).strip()
        if role_key and role_value:
            roles[role_key] = role_value
    return roles or None


def load_config(config_path=None):
    """
    Backward-compatible no-op.

    Runtime configuration is environment-only. This function remains to avoid
    breaking callers that still import or call `load_config`.
    """
    if config_path and os.path.exists(config_path):
        logger.debug(f"Ignoring config file at {config_path}; runtime config is environment-only.")
    return {}


def load_runtime_config(config_path=None):
    """
    Build runtime config from environment variables only.

    `config_path` is accepted for backward compatibility and ignored.
    """
    _ = config_path
    unifi_cfg = {}
    netbox_cfg = {}

    env_unifi_urls = _parse_env_list("UNIFI_URLS")
    if env_unifi_urls is not None:
        unifi_cfg["URLS"] = env_unifi_urls

    env_use_site_mapping = os.getenv("UNIFI_USE_SITE_MAPPING")
    if env_use_site_mapping is not None:
        unifi_cfg["USE_SITE_MAPPING"] = _parse_env_bool(env_use_site_mapping, default=False)

    env_site_mappings = _parse_env_mapping("UNIFI_SITE_MAPPINGS")
    if env_site_mappings is not None:
        unifi_cfg["SITE_MAPPINGS"] = env_site_mappings

    env_netbox_url = os.getenv("NETBOX_URL")
    if env_netbox_url:
        netbox_cfg["URL"] = _normalize_text_value(env_netbox_url)

    env_netbox_import_tenant = _normalize_text_value(os.getenv("NETBOX_IMPORT_TENANT"))
    env_netbox_tenant = _normalize_text_value(os.getenv("NETBOX_TENANT"))
    if env_netbox_import_tenant and env_netbox_tenant:
        if env_netbox_import_tenant != env_netbox_tenant:
            logger.warning(
                "Both NETBOX_IMPORT_TENANT and NETBOX_TENANT are set with different values. "
                "Using NETBOX_IMPORT_TENANT."
            )
    effective_tenant = env_netbox_import_tenant or env_netbox_tenant
    if effective_tenant:
        netbox_cfg["TENANT"] = effective_tenant

    env_netbox_roles = _load_roles_from_env()
    if env_netbox_roles:
        netbox_cfg["ROLES"] = env_netbox_roles

    if isinstance(unifi_cfg.get("URLS"), str):
        unifi_cfg["URLS"] = [item.strip() for item in unifi_cfg["URLS"].split(",") if item.strip()]
    if not isinstance(unifi_cfg.get("URLS"), list):
        unifi_cfg["URLS"] = []
    unifi_cfg["URLS"] = [str(item).strip() for item in unifi_cfg["URLS"] if str(item).strip()]

    site_mappings = unifi_cfg.get("SITE_MAPPINGS")
    if site_mappings is None:
        site_mappings = {}
    if not isinstance(site_mappings, dict):
        raise ValueError("UNIFI.SITE_MAPPINGS must be a mapping.")
    unifi_cfg["SITE_MAPPINGS"] = {
        str(key).strip(): str(value).strip()
        for key, value in site_mappings.items()
        if str(key).strip() and str(value).strip()
    }

    use_site_mapping = unifi_cfg.get("USE_SITE_MAPPING", False)
    if isinstance(use_site_mapping, str):
        use_site_mapping = _parse_env_bool(use_site_mapping, default=False)
    unifi_cfg["USE_SITE_MAPPING"] = bool(use_site_mapping)

    roles_cfg = netbox_cfg.get("ROLES")
    if roles_cfg is None:
        roles_cfg = {}
    if not isinstance(roles_cfg, dict):
        raise ValueError("NETBOX.ROLES must be a mapping.")
    netbox_cfg["ROLES"] = {
        str(key).strip().upper(): str(value).strip()
        for key, value in roles_cfg.items()
        if str(key).strip() and str(value).strip()
    }

    runtime_config = {
        "UNIFI": unifi_cfg,
        "NETBOX": netbox_cfg,
    }
    return runtime_config
