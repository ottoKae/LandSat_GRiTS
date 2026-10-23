"""Freeze a source inventory before downloading pixels."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import yaml

from .access import local_asset_index
from .bands import PRODUCTS, group_resolutions, product_for_item
from .catalog import item_datetime, scene_fingerprint, scene_key, search_items
from .grid import GridSpec, load_grid
from .storage import SceneStore, atomic_json, band_groups, read_json


def _bound(value, *, end=False):
    text = str(value)
    if len(text) == 10:
        day = datetime.fromisoformat(text).date()
        result = datetime.combine(day, time.max if end else time.min, tzinfo=timezone.utc)
    else:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("Datetime must include a timezone, or use YYYY-MM-DD")
    return result.astimezone(timezone.utc)


def load_config(path: str | Path) -> dict:
    path = Path(path).resolve()
    with path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a YAML mapping")
    allowed = {"mgrs_path", "tiles", "start", "end", "missions", "products", "output",
               "max_cloud_cover", "chunk", "resolutions", "access"}
    unknown = set(config) - allowed
    if unknown:
        raise ValueError(f"Unknown configuration keys: {sorted(unknown)}")
    for required in ("mgrs_path", "tiles", "start", "end", "output"):
        if required not in config:
            raise ValueError(f"Required configuration key: {required}")
    for key in ("mgrs_path", "output"):
        config[key] = str((path.parent / config[key]).resolve())
    tiles = config["tiles"]
    if not isinstance(tiles, list) or not tiles or not all(isinstance(t, str) for t in tiles):
        raise ValueError("tiles must be a nonempty list of MGRS IDs")
    config["tiles"] = sorted(set(t.strip().upper() for t in tiles))
    start, end = _bound(config["start"]), _bound(config["end"], end=True)
    if start > end:
        raise ValueError("start must not be after end")
    config["start"], config["end"] = start.isoformat(), end.isoformat()
    missions = config.setdefault("missions", list(range(1, 10)))
    if not isinstance(missions, list) or not missions or any(
        type(m) is not int or m not in range(1, 10) for m in missions
    ):
        raise ValueError("missions must contain integers from 1 through 9")
    products = config.setdefault("products", ["mss-l1", "tm-l1", "etm-l1", "oli-l1"])
    if not isinstance(products, list) or not products or any(p not in PRODUCTS for p in products):
        raise ValueError(f"products must be selected from {list(PRODUCTS)}")
    cloud = config.get("max_cloud_cover")
    if cloud is not None and (type(cloud) not in (float, int) or not 0 <= cloud <= 100):
        raise ValueError("max_cloud_cover must be null or a percentage from 0 to 100")
    chunk = config.setdefault("chunk", 512)
    if type(chunk) is not int or not 16 <= chunk <= 2048:
        raise ValueError("chunk must be an integer between 16 and 2048")
    resolutions = config.setdefault("resolutions", {})
    if not isinstance(resolutions, dict) or set(resolutions) - set(PRODUCTS):
        raise ValueError("resolutions must map product keys to metres per pixel")
    for value in resolutions.values():
        if type(value) not in (int, float) or not np.isfinite(value) or value <= 0:
            raise ValueError("Resolution must be finite and positive")
    for key in products:
        if PRODUCTS[key].source == "usgs" and resolutions.get(key, 30) != 30:
            raise ValueError("Native Level-1 keeps published 30 m data and separate 15 m pan grids")
    access = config.setdefault("access", {"mode": "auto"})
    if not isinstance(access, dict) or set(access) - {"mode", "directory", "aws_profile"}:
        raise ValueError("access accepts mode, directory and aws_profile; keep credentials outside YAML")
    mode = access.setdefault("mode", "auto")
    if mode not in {"auto", "local", "aws-requester-pays"}:
        raise ValueError("access.mode must be auto, local or aws-requester-pays")
    if mode == "local":
        if not isinstance(access.get("directory"), str) or not access["directory"]:
            raise ValueError("Local Level-1 access requires a directory")
        access["directory"] = str((path.parent / access["directory"]).resolve())
    if "aws_profile" in access and (not isinstance(access["aws_profile"], str)
                                  or not access["aws_profile"]):
        raise ValueError("aws_profile must be a nonempty profile name")
    if "aws_profile" in access and mode != "aws-requester-pays":
        raise ValueError("aws_profile requires aws-requester-pays mode")
    if "directory" in access and mode != "local":
        raise ValueError("access.directory requires local mode")
    return config


def manifest_hash(manifest: dict) -> str:
    payload = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def plan(config: dict, *, limit_per_search: int | None = None) -> dict:
    """All pixels remain untouched. A limit explicitly creates a pilot selection."""
    if limit_per_search is not None and limit_per_search < 1:
        raise ValueError("limit_per_search must be positive")
    plans = []
    total_raw_bytes = 0
    total_pixel_chunks = 0
    selected_missions = {f"landsat-{m}" for m in config["missions"] if m != 6}
    for tile in config["tiles"]:
        by_collection = defaultdict(list)
        grids = {}
        per_group_grids = {}
        tile_grids = {}
        for key in config["products"]:
            product = PRODUCTS[key]
            if not (selected_missions & set(product.platforms)):
                continue
            resolution = config["resolutions"].get(key, product.resolution)
            if resolution not in tile_grids:
                tile_grids[resolution] = load_grid(config["mgrs_path"], tile, resolution)
            grid = tile_grids[resolution]
            grids[key] = grid
            if product.source == "usgs":
                # Build each resolution independently from the source polygon.
                # 15 m dimensions/origins need not be exactly double the 30 m grid.
                per_group_grids[key] = {}
                for group, resolution in group_resolutions(product).items():
                    if resolution not in tile_grids:
                        tile_grids[resolution] = load_grid(config["mgrs_path"], tile, resolution)
                    per_group_grids[key][group] = tile_grids[resolution]
            by_collection[(product.source, product.collection)].append(key)
        tile_items = {}
        for (source, collection), keys in by_collection.items():
            platforms = tuple(sorted(selected_missions & {
                platform for key in keys for platform in PRODUCTS[key].platforms
            }))
            options = {}
            if source != "planetary-computer":
                instrument_names = {"tm-l1": ("TM",), "etm-l1": ("ETM",),
                                    "oli-l1": ("OLI", "TIRS")}
                options = {"source": source, "instruments": tuple(sorted({
                    value for key in keys for value in instrument_names[key]
                }))}
            items = search_items(
                collection, grids[keys[0]].geometry, config["start"], config["end"],
                platforms, max_cloud_cover=config.get("max_cloud_cover"),
                limit=limit_per_search, **options,
            )
            for item in items:
                product = product_for_item(item)
                if product.key in keys:
                    tile_items[scene_key(item)] = item
        items = sorted(tile_items.values(), key=lambda i: (item_datetime(i), scene_key(i)))
        counts = {key: 0 for key in grids}
        for item in items:
            key = product_for_item(item).key
            counts[key] += 1
            # Includes raw data and one-byte valid masks, not compression or source COG transfer.
            for group, bands in band_groups(PRODUCTS[key]).items():
                grid = per_group_grids.get(key, {}).get(group, grids[key])
                total_raw_bytes += (grid.width * grid.height
                                    * (np.result_type(*[b.dtype for b in bands]).itemsize + 1)
                                    * len(bands))
                total_pixel_chunks += (
                    math.ceil(grid.width / config["chunk"])
                    * math.ceil(grid.height / config["chunk"]) * len(bands) * 2
                )
        plans.append({"tile": tile, "grids": {key: g.to_dict() for key, g in grids.items()},
                      "group_grids": {
                          key: {name: g.to_dict() for name, g in grouped.items()}
                          for key, grouped in per_group_grids.items()
                      },
                      "scene_counts": counts, "items": items})
    result = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "per-product source",
        "source_endpoints": {
            "planetary-computer": "https://planetarycomputer.microsoft.com/api/stac/v1",
            "usgs": "https://landsatlook.usgs.gov/stac-server",
        },
        "config": config, "selection": "pilot" if limit_per_search else "all_matching_catalog_items",
        "search_complete": limit_per_search is None,
        "limit_per_search": limit_per_search,
        "mission_notes": {"6": "No imagery: Landsat 6 failed to achieve orbit"},
        "estimated_uncompressed_array_bytes": total_raw_bytes,
        "estimate_note": "Raw data + valid masks, before compression; not source transfer or free-space requirement",
        "estimated_data_and_valid_chunks_upper_bound": total_pixel_chunks,
        "chunk_estimate_note": "Data and valid chunks only; fill chunks may be omitted; excludes coordinates/metadata",
        "tiles": plans,
    }
    result["manifest_sha256"] = manifest_hash(result)
    return result


def load_manifest(path: str | Path) -> dict:
    result = read_json(Path(path))
    if result.get("schema_version") != 1 or result.get("manifest_sha256") != manifest_hash(result):
        raise ValueError("Invalid or modified manifest; regenerate it with plan")
    return result


def store_for(manifest, tile, product):
    grid = GridSpec.from_dict(tile["grids"][product.key])
    path = Path(manifest["config"]["output"]) / tile["tile"] / product.key / f"{grid.resolution:g}m.zarr"
    groups = {name: GridSpec.from_dict(data)
              for name, data in tile.get("group_grids", {}).get(product.key, {}).items()}
    options = {}
    if product.source == "usgs":
        options = {"group_grids": groups, "access": manifest["config"].get("access")}
    return SceneStore(path, product, grid, manifest["config"]["chunk"], **options)


def ingest(manifest: dict) -> dict:
    needs_l1_access = any(product_for_item(item).source == "usgs"
                          for tile in manifest["tiles"] for item in tile["items"])
    access = manifest["config"].get("access", {"mode": "auto"})
    output = Path(manifest["config"]["output"])
    if needs_l1_access and access.get("mode", "auto") == "auto":
        raise ValueError(
            "Modern Level-1 pixels require access.mode=local or explicit aws-requester-pays. "
            "Set the backend in YAML and create a new plan; no pixels have been downloaded."
        )
    if needs_l1_access and access.get("mode") == "local":
        if output.resolve().is_relative_to(Path(access["directory"]).resolve()):
            raise ValueError("Archive output must be outside the immutable local source directory")
    written = skipped = 0
    with local_asset_index(access):
        for tile in manifest["tiles"]:
            for item in tile["items"]:
                product = product_for_item(item)
                store = store_for(manifest, tile, product)
                if store.append(item):
                    written += 1
                else:
                    skipped += 1
    report = verify(manifest)
    report.update({"written": written, "already_committed": skipped})
    atomic_json(output / f"receipt-{manifest['manifest_sha256'][:16]}.json", report)
    return report


def verify(manifest: dict) -> dict:
    reports, missing, no_scenes = [], [], []
    for tile in manifest["tiles"]:
        for key, count in tile["scene_counts"].items():
            if count == 0:
                no_scenes.append({"tile": tile["tile"], "product": key})
                continue
            product = PRODUCTS[key]
            expected = {scene_key(i): scene_fingerprint(i)
                        for i in tile["items"] if product_for_item(i).key == key}
            store = store_for(manifest, tile, product)
            if not store.path.exists():
                missing.extend(f"{tile['tile']}/{s}" for s in expected)
                continue
            result = store.audit()
            missing.extend(f"{tile['tile']}/{s}" for s in set(expected) - set(result["keys"]))
            for identity in set(expected) & set(result["keys"]):
                if result["fingerprints"][identity] != expected[identity]:
                    raise ValueError(f"Committed scene differs from frozen manifest: {identity}")
            reports.append(result)
    return {
        "manifest_sha256": manifest["manifest_sha256"], "selection": manifest["selection"],
        "search_complete": manifest["search_complete"], "manifest_satisfied": not missing,
        "missing_scenes": sorted(missing), "no_matching_catalog_scenes": no_scenes, "stores": reports,
        "note": "This verifies the frozen selection; it does not prove complete USGS/global coverage.",
    }
