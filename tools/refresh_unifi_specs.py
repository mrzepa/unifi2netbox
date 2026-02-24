#!/usr/bin/env python3
"""Refresh data/ubiquiti_device_specs.json from upstream sources."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unifi.spec_refresh import refresh_specs_bundle, write_specs_bundle  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build UniFi device specs bundle from netbox-community/devicetype-library "
            "and optional UniFi Store technical specs."
        )
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "ubiquiti_device_specs.json"),
        help="Output JSON file path",
    )
    parser.add_argument(
        "--skip-store",
        action="store_true",
        help="Skip UniFi Store enrichment",
    )
    parser.add_argument(
        "--library-timeout",
        type=int,
        default=45,
        help="HTTP timeout for Device Type Library tarball fetch",
    )
    parser.add_argument(
        "--store-timeout",
        type=int,
        default=15,
        help="HTTP timeout for each UniFi Store request",
    )
    parser.add_argument(
        "--store-max-workers",
        type=int,
        default=8,
        help="Parallel workers for UniFi Store enrichment",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build bundle and print counts, but do not write output file",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logger = logging.getLogger("spec-refresh")

    bundle = refresh_specs_bundle(
        include_store=not args.skip_store,
        library_timeout=args.library_timeout,
        store_timeout=args.store_timeout,
        store_max_workers=args.store_max_workers,
        logger=logger,
    )

    by_part_count = len(bundle.get("by_part") or {})
    by_model_count = len(bundle.get("by_model") or {})
    logger.info("Bundle ready: %s by part, %s by model", by_part_count, by_model_count)

    if args.dry_run:
        return 0

    output_path = Path(os.path.expanduser(args.output)).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_specs_bundle(str(output_path), bundle)
    logger.info("Wrote %s", output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
