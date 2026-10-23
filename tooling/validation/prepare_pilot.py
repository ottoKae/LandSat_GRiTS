"""Freeze one complete-band public scene per product on the full 17MPU grid.

Run products separately: a limit on their shared STAC collection is not a limit
per sensor. This is a bounded acceptance selection, never a historical inventory.
"""

# ruff: noqa: E402
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timezone
import math
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import numpy as np
from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import transform
import yaml

from landsat_grits.bands import PRODUCTS
from landsat_grits.grid import GridSpec
from landsat_grits.storage import atomic_json, band_groups
from landsat_grits.workflow import load_config, load_manifest, manifest_hash, plan

PERIODS = {
    "mss-l1": ("1972-07-23", "1995-12-31"),
    "tm-l2": ("2000-01-01", "2011-12-31"),
    "etm-l2": ("1999-07-01", "2003-05-30"),
    "oli-l2": ("2023-01-01", "2025-12-31"),
}


def select_one(manifest, key):
    result = deepcopy(manifest)
    tile = result["tiles"][0]
    grid = GridSpec.from_dict(tile["grids"][key])
    product = PRODUCTS[key]
    target = box(grid.transform.c, grid.transform.f - grid.height * grid.resolution,
                 grid.transform.c + grid.width * grid.resolution, grid.transform.f)
    project = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True).transform
    required = {b.asset_key for bands in band_groups(product).values() for b in bands}
    candidates = []
    for item in tile["items"]:
        if not required.issubset(item["assets"]):
            continue
        coverage = transform(project, shape(item["geometry"])).intersection(target).area / target.area
        cloud = item["properties"].get("eo:cloud_cover")
        cloud = float(cloud) if isinstance(cloud, (int, float)) else 100.0
        candidates.append((item, coverage, cloud))
    if not candidates:
        raise ValueError(f"No complete-band scene found for {key}; do not substitute another product")
    # At 0.1% coverage precision, use lower cloud cover as the tie breaker.
    item, coverage, cloud = min(
        candidates, key=lambda c: (-round(c[1], 3), c[2], c[0]["id"])
    )
    tile["items"], tile["scene_counts"] = [item], {key: 1}
    total_bytes = total_chunks = 0
    for bands in band_groups(product).values():
        total_bytes += grid.width * grid.height * len(bands) * (
            np.result_type(*[b.dtype for b in bands]).itemsize + 1
        )
        total_chunks += (math.ceil(grid.width / result["config"]["chunk"])
                         * math.ceil(grid.height / result["config"]["chunk"]) * len(bands) * 2)
    result["estimated_uncompressed_array_bytes"] = total_bytes
    result["estimated_data_and_valid_chunks_upper_bound"] = total_chunks
    result["selection"] = "pilot"
    result["search_complete"] = False
    result["pilot_selection"] = {
        "tile_extent": "full source MGRS snapped UTM rectangle; no crop",
        "purpose": "one complete-band scene per sensor family, not full history",
        "candidates_returned": len(manifest["tiles"][0]["items"]),
        "complete_band_candidates": len(candidates),
        "ranking": "descending geometric coverage rounded to 0.1%, ascending scene cloud cover",
        "selected_scene": item["id"],
        "scene_footprint_fraction_of_output_rectangle": coverage,
        "scene_cloud_cover_percent": cloud,
        "note": "Footprint overlap does not guarantee valid or cloud-free pixels",
    }
    result["manifest_sha256"] = manifest_hash(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("products", nargs="+", choices=list(PERIODS))
    parser.add_argument("--candidate-limit", type=int, default=64)
    args = parser.parse_args()
    evidence = REPO / "logs" / date.today().isoformat() / "17mpu-pilot"
    configs = evidence / "config"
    evidence.mkdir(parents=True, exist_ok=True)
    configs.mkdir(parents=True, exist_ok=True)
    for key in args.products:
        destination = evidence / f"{key}-manifest.json"
        if destination.exists():
            frozen = load_manifest(destination)
            print(f"Existing frozen selection: {key} {frozen['pilot_selection']['selected_scene']}",
                  flush=True)
            continue
        start, end = PERIODS[key]
        config = {
            "mgrs_path": str(REPO.parent / "S1-GRiTS/src/s1grits/data/mgrs.parquet"),
            "tiles": ["17MPU"], "start": start, "end": end,
            "missions": [int(p.split("-")[1]) for p in PRODUCTS[key].platforms],
            "products": [key], "max_cloud_cover": 60, "chunk": 512,
            "output": str(REPO / "outputs/pilots/17MPU-20261021"),
            "access": {"mode": "auto"},
        }
        config_path = configs / f"{key}.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        print(f"{datetime.now(timezone.utc).isoformat()} Searching {key}", flush=True)
        candidates = plan(load_config(config_path), limit_per_search=args.candidate_limit)
        atomic_json(evidence / f"{key}-candidates.json", candidates)
        frozen = select_one(candidates, key)
        atomic_json(destination, frozen)
        print(f"Frozen {key}: {frozen['pilot_selection']} -> {destination}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
