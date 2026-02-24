"""VRF helpers with thread-safe caching and duplicate avoidance."""

from __future__ import annotations

import logging
import os
import threading

import pynetbox

from sync.runtime_config import _normalize_text_value

logger = logging.getLogger(__name__)

vrf_cache = {}
vrf_cache_lock = threading.Lock()
vrf_locks = {}
vrf_locks_lock = threading.Lock()


def _normalize_vrf_name(vrf_name: str) -> str:
    """
    Normalize VRF names so lookups are stable and do not create near-duplicates.
    """
    if vrf_name is None:
        return ""
    return " ".join(_normalize_text_value(vrf_name).split())


def _vrf_cache_key(vrf_name: str) -> str:
    return _normalize_vrf_name(vrf_name).casefold()


def _find_vrfs_by_name(nb, vrf_name: str):
    """
    Find matching VRFs by name.

    Starts with exact-name API filter for efficiency.
    Falls back to normalized case-insensitive scan to avoid duplicates when
    historical data has casing/whitespace drift.
    """
    normalized = _normalize_vrf_name(vrf_name)
    if not normalized:
        return []

    exact = list(nb.ipam.vrfs.filter(name=normalized))
    if exact:
        return exact

    wanted_key = _vrf_cache_key(normalized)
    matches = []
    try:
        for candidate in nb.ipam.vrfs.all():
            candidate_name = getattr(candidate, "name", "")
            if _vrf_cache_key(candidate_name) == wanted_key:
                matches.append(candidate)
    except Exception as e:
        logger.debug(f"Failed VRF fallback scan for '{normalized}': {e}")

    return matches


def _get_vrf_lock(vrf_name: str) -> threading.Lock:
    cache_key = _vrf_cache_key(vrf_name)
    with vrf_locks_lock:
        lock = vrf_locks.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            vrf_locks[cache_key] = lock
    return lock


def get_or_create_vrf(nb, vrf_name: str):
    """
    Get or create a VRF by name in a concurrency-safe way.

    NetBox does not enforce VRF name uniqueness, so without a lock multiple
    threads can create duplicates when processing devices in parallel.
    """
    normalized_name = _normalize_vrf_name(vrf_name)
    if not normalized_name:
        return None
    cache_key = _vrf_cache_key(normalized_name)

    with vrf_cache_lock:
        cached = vrf_cache.get(cache_key)
    if cached is not None:
        return cached

    with _get_vrf_lock(normalized_name):
        with vrf_cache_lock:
            cached = vrf_cache.get(cache_key)
        if cached is not None:
            return cached

        existing = _find_vrfs_by_name(nb, normalized_name)
        vrf = None
        if existing:
            vrf = sorted(existing, key=lambda item: item.id or 0)[0]
            if len(existing) > 1:
                logger.warning(
                    f"Multiple VRFs with name {normalized_name} found. Using ID {vrf.id}."
                )
        else:
            logger.debug(f"VRF {normalized_name} not found, creating new VRF")
            try:
                vrf = nb.ipam.vrfs.create({"name": normalized_name})
                if vrf is not None:
                    logger.info(f"VRF {normalized_name} with ID {vrf.id} successfully added to NetBox.")
            except pynetbox.core.query.RequestError as e:
                logger.warning(f"Failed to create VRF {normalized_name}: {e}. Trying to refetch.")
                existing = _find_vrfs_by_name(nb, normalized_name)
                if existing:
                    vrf = sorted(existing, key=lambda item: item.id or 0)[0]

        if vrf is not None:
            with vrf_cache_lock:
                vrf_cache[cache_key] = vrf
        return vrf


def get_existing_vrf(nb, vrf_name: str):
    """Get a VRF by name (do not create)."""
    normalized_name = _normalize_vrf_name(vrf_name)
    if not normalized_name:
        return None
    cache_key = _vrf_cache_key(normalized_name)
    with vrf_cache_lock:
        cached = vrf_cache.get(cache_key)
    if cached is not None:
        return cached

    with _get_vrf_lock(normalized_name):
        with vrf_cache_lock:
            cached = vrf_cache.get(cache_key)
        if cached is not None:
            return cached

        existing = _find_vrfs_by_name(nb, normalized_name)
        if not existing:
            return None
        vrf = sorted(existing, key=lambda item: item.id or 0)[0]
        if len(existing) > 1:
            logger.warning(f"Multiple VRFs with name {normalized_name} found. Using ID {vrf.id}.")
        with vrf_cache_lock:
            vrf_cache[cache_key] = vrf
        return vrf


def get_vrf_for_site(nb, site_name: str):
    """
    Decide VRF behavior based on env.

    NETBOX_VRF_MODE:
      - none/disabled/off: do not use VRF at all
      - existing/get: use VRF if it exists, never create
      - create/site (legacy behavior): create VRF if missing

    NETBOX_DEFAULT_VRF:
      - if set, this VRF name is used for all sites instead of site_name
    """
    site_name = _normalize_vrf_name(site_name)
    mode = (os.getenv("NETBOX_VRF_MODE") or "existing").strip().lower()
    if mode in {"none", "disabled", "off"}:
        return None, mode

    default_vrf_name = _normalize_vrf_name(os.getenv("NETBOX_DEFAULT_VRF", ""))
    target_vrf_name = default_vrf_name or site_name

    if not target_vrf_name:
        return None, mode

    if mode in {"create", "site"}:
        vrf = get_or_create_vrf(nb, target_vrf_name)
        return vrf, mode

    if mode in {"existing", "get"}:
        vrf = get_existing_vrf(nb, target_vrf_name)
        return vrf, mode

    logger.warning(f"Unknown NETBOX_VRF_MODE='{mode}'. Falling back to 'existing'.")
    vrf = get_existing_vrf(nb, target_vrf_name)
    return vrf, "existing"

