"""Refresh UniFi device specs from upstream sources.

Primary source:
- netbox-community/devicetype-library (Ubiquiti device-types)

Optional enrichment:
- UniFi Store product pages (technicalSpecification) for extra metadata

The resulting structure matches data/ubiquiti_device_specs.json:
{
  "by_part": {"PART-NUMBER": {...}},
  "by_model": {"Model Name": {...}}
}
"""

from __future__ import annotations

import io
import json
import re
import tarfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import yaml


DEVICETYPE_LIBRARY_TARBALL_URL = (
    "https://codeload.github.com/netbox-community/devicetype-library/tar.gz/refs/heads/master"
)
UNIFI_STORE_PRODUCT_URL_TEMPLATE = "https://store.ui.com/us/en/products/{slug}"


def _log(logger: Any, level: str, message: str) -> None:
    if logger is None:
        return
    fn = getattr(logger, level, None)
    if callable(fn):
        fn(message)


def _to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_html_text(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    return "\n".join(part.strip() for part in text.splitlines() if part.strip())


def _parse_first_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    m = re.search(r"(\d+)", str(value))
    if not m:
        return None
    return int(m.group(1))


def _parse_weight_kg(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).replace(",", ".")
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*kg", text, flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _parse_u_height(value: Any) -> Optional[int]:
    if value is None:
        return None
    m = re.search(r"(\d+)\s*U", str(value), flags=re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1))


def _normalize_match_token(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _slug_candidates(raw: Any) -> List[str]:
    token = _clean_text(raw)
    if not token:
        return []
    base = token.lower().strip()
    variants = {
        base,
        base.replace(" ", "-"),
        base.replace("_", "-"),
        base.replace("+", "-plus"),
    }
    normalized = set()
    for candidate in variants:
        candidate = re.sub(r"[^a-z0-9-]", "-", candidate)
        candidate = re.sub(r"-+", "-", candidate).strip("-")
        if candidate:
            normalized.add(candidate)
    return sorted(normalized)


def _normalize_store_product(page_props: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    current_product_id = page_props.get("currentProductId")
    products = (page_props.get("collection") or {}).get("products") or []
    if not isinstance(products, list) or not products:
        return None

    for product in products:
        if isinstance(product, dict) and product.get("id") == current_product_id:
            return product
    first = products[0]
    return first if isinstance(first, dict) else None


def _extract_features_from_technical_spec(product: Dict[str, Any]) -> Dict[str, str]:
    features: Dict[str, str] = {}
    technical = product.get("technicalSpecification")
    if not isinstance(technical, dict):
        return features

    for section in technical.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for entry in section.get("features") or []:
            if not isinstance(entry, dict):
                continue
            label = ((entry.get("feature") or {}).get("label") or "").strip()
            if not label:
                continue
            value = entry.get("value")
            if value in (None, ""):
                value = entry.get("flag")
            if value in (None, "", "Empty"):
                continue
            features[label.lower()] = str(value).strip()
    return features


def _extract_features_from_datasheet(product: Dict[str, Any]) -> Dict[str, str]:
    features: Dict[str, str] = {}
    datasheet = product.get("datasheet") or {}
    html = datasheet.get("html") if isinstance(datasheet, dict) else None
    if not html:
        return features

    pattern = re.compile(
        r"<tr>\s*<td[^>]*class=\"key\"[^>]*>(.*?)</td>\s*<td[^>]*class=\"value\"[^>]*>(.*?)</td>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for key_html, value_html in pattern.findall(html):
        key = _clean_html_text(key_html).lower()
        value = _clean_html_text(value_html)
        if key and value:
            features[key] = value
    return features


def _interfaces_from_store_features(features: Dict[str, str]) -> List[Dict[str, Any]]:
    interfaces: List[Dict[str, Any]] = []

    # Keep port numbering contiguous for copper interfaces.
    copper_layout = [
        ("1 gbe rj45", "1000base-t"),
        ("2.5 gbe rj45", "2.5gbase-t"),
        ("5 gbe rj45", "5gbase-t"),
        ("10 gbe rj45", "10gbase-t"),
    ]
    port_idx = 0
    for label, iface_type in copper_layout:
        count = _parse_first_int(features.get(label)) or 0
        for _ in range(count):
            port_idx += 1
            interfaces.append({"name": f"Port {port_idx}", "type": iface_type})

    fiber_layout = [
        ("1g sfp", "SFP", "1000base-x-sfp"),
        ("10g sfp+", "SFP+", "10gbase-x-sfpp"),
        ("25g sfp28", "SFP28", "25gbase-x-sfp28"),
        ("100g qsfp28", "QSFP28", "100gbase-x-qsfp28"),
    ]
    for label, prefix, iface_type in fiber_layout:
        count = _parse_first_int(features.get(label)) or 0
        for idx in range(1, count + 1):
            interfaces.append({"name": f"{prefix} {idx}", "type": iface_type})

    return interfaces


def extract_store_spec(product: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert UniFi Store product payload into the bundle spec format."""
    part_number = _clean_text(product.get("name"))
    model_name = _clean_text(product.get("title")) or _clean_text(product.get("shortTitle"))
    slug = _clean_text(product.get("slug"))

    if not part_number and not model_name:
        return None

    features = _extract_features_from_technical_spec(product)
    if not features:
        features = _extract_features_from_datasheet(product)

    interfaces = _interfaces_from_store_features(features)

    poe_budget = None
    for key in ("total poe availability", "total available poe", "total poe"):
        poe_budget = _parse_first_int(features.get(key))
        if poe_budget is not None:
            break

    u_height = _parse_u_height(features.get("form factor"))

    weight = _parse_weight_kg(features.get("weight"))

    spec: Dict[str, Any] = {
        "manufacturer": "Ubiquiti",
        "model": model_name or part_number,
        "part_number": part_number or model_name,
        "slug": f"ubiquiti-{slug}" if slug and not str(slug).startswith("ubiquiti-") else slug,
        "interfaces": interfaces,
        "poe_budget": poe_budget,
        "u_height": u_height,
        "weight": weight,
        "weight_unit": "kg" if weight is not None else None,
    }

    # Drop empty fields to keep output compact and deterministic.
    return {
        key: value
        for key, value in spec.items()
        if value not in (None, "", [], {})
    }


def _normalize_devicetype_doc(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    model = _clean_text(doc.get("model"))
    part_number = _clean_text(doc.get("part_number")) or model
    if not part_number:
        return None

    entry: Dict[str, Any] = {
        "manufacturer": _clean_text(doc.get("manufacturer")) or "Ubiquiti",
        "model": model or part_number,
        "part_number": part_number,
        "slug": _clean_text(doc.get("slug")),
        "u_height": doc.get("u_height"),
        "is_full_depth": doc.get("is_full_depth"),
        "airflow": _clean_text(doc.get("airflow")),
        "weight": doc.get("weight"),
        "weight_unit": _clean_text(doc.get("weight_unit")),
    }

    interfaces = []
    for iface in doc.get("interfaces") or []:
        if not isinstance(iface, dict) or not iface.get("name"):
            continue
        normalized = {
            "name": iface.get("name"),
            "type": iface.get("type", "1000base-t"),
        }
        for optional in ("mgmt_only", "poe_mode", "poe_type"):
            if iface.get(optional) is not None:
                normalized[optional] = iface.get(optional)
        interfaces.append(normalized)
    if interfaces:
        entry["interfaces"] = interfaces

    console_ports = []
    for cp in doc.get("console-ports") or []:
        if not isinstance(cp, dict) or not cp.get("name"):
            continue
        console_ports.append({"name": cp.get("name"), "type": cp.get("type", "rj-45")})
    if console_ports:
        entry["console_ports"] = console_ports

    power_ports = []
    for pp in doc.get("power-ports") or []:
        if not isinstance(pp, dict) or not pp.get("name"):
            continue
        normalized = {"name": pp.get("name"), "type": pp.get("type", "iec-60320-c14")}
        for optional in ("maximum_draw", "allocated_draw"):
            if pp.get(optional) is not None:
                normalized[optional] = pp.get(optional)
        power_ports.append(normalized)
    if power_ports:
        entry["power_ports"] = power_ports

    return {
        key: value
        for key, value in entry.items()
        if value not in (None, "", [], {})
    }


def build_bundle_from_devicetype_docs(docs: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_part: Dict[str, Dict[str, Any]] = {}
    by_model: Dict[str, Dict[str, Any]] = {}

    for raw in docs:
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_devicetype_doc(raw)
        if not normalized:
            continue

        part_number = normalized.get("part_number")
        model = normalized.get("model")
        if part_number and part_number not in by_part:
            by_part[part_number] = normalized
        elif part_number:
            by_part[part_number] = {**by_part[part_number], **normalized}

        if model:
            by_model[model] = normalized

    return {"by_part": by_part, "by_model": by_model}


def fetch_devicetype_library_bundle(timeout: int = 45, logger: Any = None) -> Dict[str, Dict[str, Any]]:
    """Fetch Ubiquiti device-types from netbox-community/devicetype-library tarball."""
    _log(logger, "info", "Fetching Device Type Library tarball...")
    response = requests.get(DEVICETYPE_LIBRARY_TARBALL_URL, timeout=timeout)
    response.raise_for_status()

    docs: List[Dict[str, Any]] = []
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        for member in archive.getmembers():
            name = member.name
            if "/device-types/Ubiquiti/" not in name or not name.endswith(".yaml"):
                continue
            fh = archive.extractfile(member)
            if fh is None:
                continue
            payload = fh.read().decode("utf-8", errors="replace")
            loaded = yaml.safe_load(payload)
            if isinstance(loaded, dict):
                docs.append(loaded)

    bundle = build_bundle_from_devicetype_docs(docs)
    _log(
        logger,
        "info",
        (
            "Built bundle from Device Type Library: "
            f"{len(bundle['by_part'])} by part, {len(bundle['by_model'])} by model"
        ),
    )
    return bundle


def _fetch_store_product(slug: str, timeout: int = 15) -> Optional[Dict[str, Any]]:
    url = UNIFI_STORE_PRODUCT_URL_TEMPLATE.format(slug=slug)
    response = requests.get(url, timeout=timeout)
    if response.status_code != 200:
        return None

    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        response.text,
        flags=re.DOTALL,
    )
    if not match:
        return None

    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

    if payload.get("page") != "/[store]/[language]/products/[product]":
        return None

    page_props = ((payload.get("props") or {}).get("pageProps") or {})
    return _normalize_store_product(page_props)


def _merge_specs(base: Optional[Dict[str, Any]], extra: Dict[str, Any]) -> Dict[str, Any]:
    if not base:
        return dict(extra)

    merged = dict(base)
    for key, value in extra.items():
        if value in (None, "", [], {}):
            continue
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
    return merged


def _find_part_key_case_insensitive(by_part: Dict[str, Any], token: str) -> Optional[str]:
    token_norm = _normalize_match_token(token)
    if not token_norm:
        return None
    for key in by_part.keys():
        if _normalize_match_token(key) == token_norm:
            return key
    return None


def _product_matches(part_number: str, model: Optional[str], product: Dict[str, Any]) -> bool:
    candidates = [_normalize_match_token(part_number), _normalize_match_token(model)]
    candidates = [c for c in candidates if c]
    values = [
        _normalize_match_token(product.get("name")),
        _normalize_match_token(product.get("slug")),
        _normalize_match_token(product.get("title")),
    ]
    values = [v for v in values if v]

    if not candidates or not values:
        return False

    for c in candidates:
        for v in values:
            if c == v or c in v or v in c:
                return True
    return False


def augment_bundle_with_store_specs(
    bundle: Dict[str, Dict[str, Any]],
    *,
    timeout: int = 15,
    max_workers: int = 8,
    logger: Any = None,
) -> Dict[str, Dict[str, Any]]:
    """Best-effort enrichment from UniFi Store specs.

    Attempts product lookup using part_number/model-derived slug candidates.
    Existing Device Type Library values are kept as primary source; store values
    only fill missing metadata (e.g., poe_budget, u_height, weight, extra aliases).
    """
    by_part = dict(bundle.get("by_part") or {})
    by_model = dict(bundle.get("by_model") or {})

    work_items: List[Tuple[str, Dict[str, Any], List[str]]] = []
    for part_key, spec in by_part.items():
        candidates = []
        candidates.extend(_slug_candidates(part_key))
        candidates.extend(_slug_candidates((spec or {}).get("part_number")))
        candidates.extend(_slug_candidates((spec or {}).get("model")))
        unique = list(dict.fromkeys(candidates))
        if unique:
            work_items.append((part_key, spec, unique))

    _log(logger, "info", f"Store enrichment: probing {len(work_items)} part numbers")

    def resolve_one(part_key: str, spec: Dict[str, Any], slugs: List[str]) -> Optional[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        model = (spec or {}).get("model")
        for slug in slugs:
            product = _fetch_store_product(slug, timeout=timeout)
            if not product:
                continue
            if not _product_matches(part_key, model, product):
                continue
            store_spec = extract_store_spec(product)
            if not store_spec:
                continue
            return part_key, product, store_spec
        return None

    hits = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(resolve_one, part_key, spec, slugs)
            for part_key, spec, slugs in work_items
        ]
        for future in as_completed(futures):
            result = future.result()
            if not result:
                continue

            part_key, product, store_spec = result
            hits += 1
            merged = _merge_specs(by_part.get(part_key), store_spec)
            by_part[part_key] = merged

            # Add model aliases from store naming to improve model-key lookups.
            for alias in {
                product.get("title"),
                product.get("shortTitle"),
                product.get("name"),
                merged.get("model"),
            }:
                alias_clean = _clean_text(alias)
                if alias_clean:
                    by_model[alias_clean] = merged

            # Store part-number may differ by casing or separators.
            product_part = _clean_text(product.get("name"))
            if product_part:
                existing_key = _find_part_key_case_insensitive(by_part, product_part)
                if existing_key is None:
                    by_part[product_part] = merged

    _log(logger, "info", f"Store enrichment completed: matched {hits} products")
    return {"by_part": by_part, "by_model": by_model}


def refresh_specs_bundle(
    *,
    include_store: bool = False,
    library_timeout: int = 45,
    store_timeout: int = 15,
    store_max_workers: int = 8,
    logger: Any = None,
) -> Dict[str, Dict[str, Any]]:
    """Build a fresh specs bundle from upstream source(s)."""
    bundle = fetch_devicetype_library_bundle(timeout=library_timeout, logger=logger)
    if include_store:
        bundle = augment_bundle_with_store_specs(
            bundle,
            timeout=store_timeout,
            max_workers=store_max_workers,
            logger=logger,
        )
    return bundle


def write_specs_bundle(path: str, bundle: Dict[str, Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=2, sort_keys=True)
        fh.write("\n")


__all__ = [
    "DEVICETYPE_LIBRARY_TARBALL_URL",
    "UNIFI_STORE_PRODUCT_URL_TEMPLATE",
    "augment_bundle_with_store_specs",
    "build_bundle_from_devicetype_docs",
    "extract_store_spec",
    "fetch_devicetype_library_bundle",
    "refresh_specs_bundle",
    "write_specs_bundle",
]
