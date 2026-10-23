"""Audit every local pixel block of a frozen, one-scene-per-product MGRS pilot.

Source checks independently map target pixel centres into the original raster;
they do not call the production writer or WarpedVRT. Only small source windows
are read. Full local decoding is exhaustive; agreement with the source is sampled.
Run after ingestion has stopped. Reports are acceptance evidence, not test data.
"""

# Standalone repository diagnostic.
# ruff: noqa: E402
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import numpy as np
from pyproj import Transformer
import rasterio
from rasterio.windows import Window
from shapely.geometry import box, shape
from shapely.ops import transform as transform_geometry
import zarr

from landsat_grits.access import local_asset_index, raster_access, resolve_item_assets
from landsat_grits.bands import PRODUCTS, product_for_item, source_band_for_item
from landsat_grits.grid import GridSpec, load_grid
from landsat_grits.storage import atomic_json, open_group
from landsat_grits.workflow import ingest, load_manifest, store_for, verify


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_error(exc):
    message = re.sub(r"https?://[^\s\"'<>]+", "<source-url-redacted>", str(exc))
    message = re.sub(
        r"(?i)(sig|token|credential|x-amz-signature)=[^&\s]+", r"\1=<redacted>", message
    )
    return {"type": type(exc).__name__, "message": message}


def scan_band(group, bi, band):
    data, valid = group["data"], group["valid"]
    width, height = data.shape[2:]
    hashes = {name: hashlib.sha256() for name in ("data", "valid")}
    total_valid = zero_valid = chunks = 0
    extrema = [None, None]
    present = bool(group["present"][0, bi])
    # One valid position in each third of the tile gives spatially spread samples.
    valid_positions = {}
    for x in range(0, width, data.chunks[2]):
        for y in range(0, height, data.chunks[3]):
            selection = (0, bi, slice(x, min(width, x + data.chunks[2])),
                         slice(y, min(height, y + data.chunks[3])))
            values, mask = data[selection], valid[selection]
            expected_shape = (selection[2].stop - x, selection[3].stop - y)
            if values.shape != expected_shape or mask.shape != expected_shape:
                raise AssertionError("Decoded chunk has an unexpected shape")
            if values.dtype != data.dtype or mask.dtype != np.dtype("bool"):
                raise AssertionError("Decoded chunk has an unexpected dtype")
            fill = (0 if band.nodata is None else band.nodata) if present else data.fill_value
            if np.any(values[~mask] != fill):
                raise AssertionError("Invalid pixels differ from the band's fill value")
            if band.nodata is not None and np.any(values[mask] == band.nodata):
                raise AssertionError("NoData was marked valid")
            count = int(np.count_nonzero(mask))
            total_valid += count
            zero_valid += int(np.count_nonzero(mask & (values == 0)))
            if count:
                present_values = values[mask]
                low, high = int(present_values.min()), int(present_values.max())
                extrema[0] = low if extrema[0] is None else min(extrema[0], low)
                extrema[1] = high if extrema[1] is None else max(extrema[1], high)
                local_x, local_y = np.argwhere(mask)[count // 2]
                position = (x + int(local_x), y + int(local_y))
                key = (min(2, position[0] * 3 // width), min(2, position[1] * 3 // height))
                valid_positions.setdefault(key, position)
            # Explicit framing makes the hash independent of ambiguous block boundaries.
            frame = json.dumps([x, y, *expected_shape], separators=(",", ":")).encode()
            for name, array in (("data", values), ("valid", mask)):
                hashes[name].update(frame)
                hashes[name].update(np.ascontiguousarray(array).tobytes())
            chunks += 1
    if not present and total_valid:
        raise AssertionError("An absent band contains valid pixels")
    return {
        "band": band.name, "asset": band.asset_key, "present": present,
        "pixels_decoded": width * height, "chunk_positions_decoded": chunks,
        "valid_pixels": total_valid, "missing_pixels": width * height - total_valid,
        "valid_fraction_of_full_grid": total_valid / (width * height),
        "valid_zero_pixels": zero_valid, "valid_dn_range": extrema,
        "decoded_sha256": {key: value.hexdigest() for key, value in hashes.items()},
        "hash_order": "x chunks then y chunks; JSON [x,y,width,height] then C-order bytes",
        "valid_sample_positions_xy": [list(p) for p in valid_positions.values()],
    }


def nearest_source_index(coordinate, size):
    # Match GDAL's containing-pixel rule, including its floating-point epsilon.
    # https://github.com/OSGeo/gdal/blob/v3.12.2/alg/gdalwarpkernel.cpp#L5139-L5194
    # Integer coordinates select the pixel to the right/below the shared edge.
    if not math.isfinite(coordinate) or coordinate < 0 or coordinate + 1e-10 > size:
        return None
    return min(int(coordinate + 1e-10), size - 1)


def compare_samples(group, bi, band, grid, asset, access, record):
    width, height = grid.width, grid.height
    centres = {(x, y) for x in (0, width // 2, width - 1)
               for y in (0, height // 2, height - 1)}
    centres.update(tuple(p) for p in record["valid_sample_positions_xy"])
    positions = sorted({(x + dx, y + dy) for x, y in centres
                        for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                        if 0 <= x + dx < width and 0 <= y + dy < height})
    compared = valid_compared = outside = boundary_checked = 0
    details = []
    with raster_access(asset, access) as href, rasterio.open(href) as source:
        if source.count != 1 or source.crs is None:
            raise AssertionError("Expected one georeferenced source band")
        transform = Transformer.from_crs(grid.crs, source.crs, always_xy=True)
        source_inverse = ~source.transform
        effective_nodata = source.nodata if source.nodata is not None else band.nodata
        fill = 0 if band.nodata is None else band.nodata
        for x, y in positions:
            target_x = grid.transform.c + grid.transform.a * (x + 0.5)
            target_y = grid.transform.f + grid.transform.e * (y + 0.5)
            source_x, source_y = transform.transform(target_x, target_y)
            source_col = (source_inverse.a * source_x + source_inverse.b * source_y
                          + source_inverse.c)
            source_row = (source_inverse.d * source_x + source_inverse.e * source_y
                          + source_inverse.f)
            if min(
                abs(source_col - round(source_col)), abs(source_row - round(source_row))
            ) < 1e-8:
                boundary_checked += 1
            col = nearest_source_index(source_col, source.width)
            row = nearest_source_index(source_row, source.height)
            expected_valid, expected_value = False, fill
            if col is not None and row is not None:
                window = Window(col, row, 1, 1)
                expected_value = int(source.read(1, window=window)[0, 0])
                expected_valid = bool(source.read_masks(1, window=window)[0, 0])
                if effective_nodata is not None:
                    expected_valid &= expected_value != effective_nodata
                if not expected_valid:
                    expected_value = fill
            else:
                outside += 1
            stored_value = int(group["data"][0, bi, x, y])
            stored_valid = bool(group["valid"][0, bi, x, y])
            if stored_valid != expected_valid or stored_value != expected_value:
                raise AssertionError(
                    f"Independent source sample differs: {band.asset_key} at ({x},{y}); "
                    f"stored=({stored_value},{stored_valid}), "
                    f"source=({expected_value},{expected_valid})"
                )
            compared += 1
            valid_compared += int(expected_valid)
            details.append({"target_xy": [x, y], "source_col_row": [col, row],
                            "dn": stored_value, "valid": stored_valid})
        if not compared or (record["valid_pixels"] and not valid_compared):
            raise AssertionError("No valid source comparison was obtained")
        return {
            "method": "Independent exact target-centre transform and original TIFF pixel/mask read",
            "source_shape_yx": [source.height, source.width],
            "source_crs": str(source.crs), "source_dtype": source.dtypes[0],
            "source_nodata": source.nodata,
            "compared_pixels": compared, "compared_valid_pixels": valid_compared,
            "outside_source_extent_pixels": outside,
            "source_pixel_boundary_samples_checked": boundary_checked,
            "source_samples_excluded": 0,
            "source_coordinate_epsilon_pixels": 1e-10,
            "nearest_index_rule": "GDAL containing source pixel: int(coordinate + 1e-10)",
            "all_compared_values_and_masks_equal": True, "samples": details,
        }


def geometry_coverage(grid, item):
    project = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True).transform
    tile = transform_geometry(project, shape(grid.geometry))
    scene = transform_geometry(project, shape(item["geometry"]))
    left, top = grid.transform * (0, 0)
    right, bottom = grid.transform * (grid.width, grid.height)
    extent = box(left, bottom, right, top)
    return {
        "scene_fraction_of_mgrs_polygon": scene.intersection(tile).area / tile.area,
        "scene_fraction_of_snapped_grid_extent": scene.intersection(extent).area / extent.area,
        "note": "STAC geometry overlap, not a claim of cloud-free or valid pixel coverage",
    }


def audit_store(manifest, tile, product, item, *, source_checks, save, record):
    store = store_for(manifest, tile, product)
    record.update({"product": product.key, "scene_id": item["id"],
                   "platform": item["properties"]["platform"],
                   "acquired_at": item["properties"]["datetime"],
                   "output_path": str(store.path), "groups": {}})
    # Keep writers out throughout the exhaustive scan, including source sampling.
    with store.lock:
        root, commits = store._open()
        if len(commits) != 1:
            raise AssertionError("Each pilot archive must contain exactly one committed scene")
        assets = resolve_item_assets(
            item, store.access,
            asset_keys=[b.asset_key for bands in store.groups.values() for b in bands],
        ) if source_checks else {}
        record["source_geometry_coverage"] = geometry_coverage(store.grid, item)
        for name, bands in store.groups.items():
            group, grid = root[name], store.group_grids[name]
            if group["data"].shape != (1, len(bands), grid.width, grid.height):
                raise AssertionError("Expected the complete MGRS grid and exactly one time step")
            np.testing.assert_array_equal(group["x"][:], grid.x)
            np.testing.assert_array_equal(group["y"][:], grid.y)
            np.testing.assert_array_equal(group["scale"][0], [b.scale for b in bands])
            np.testing.assert_array_equal(group["offset"][0], [b.offset for b in bands])
            np.testing.assert_array_equal(
                group["source_band"][0], [source_band_for_item(b, item) for b in bands]
            )
            result = {"shape_time_bands_x_y": list(group["data"].shape),
                      "dimensions": group["data"].attrs["_ARRAY_DIMENSIONS"],
                      "chunks": list(group["data"].chunks), "dtype": str(group["data"].dtype),
                      "crs": grid.crs, "resolution_m": grid.resolution,
                      "full_mgrs_grid": True, "bands": []}
            record["groups"][name] = result
            for bi, band in enumerate(bands):
                print(f"Scan {product.key}/{name}/{band.name}", flush=True)
                band_record = scan_band(group, bi, band)
                result["bands"].append(band_record)
                if band_record["present"] != (band.asset_key in item["assets"]):
                    raise AssertionError("Band presence differs from the frozen source assets")
                if source_checks and band_record["present"]:
                    if band.asset_key not in assets:
                        raise AssertionError("Present band has no source asset")
                    band_record["source_comparison"] = compare_samples(
                        group, bi, band, grid, assets[band.asset_key], store.access, band_record
                    )
                save()
        if not any(b["valid_pixels"] for b in record["groups"]["optical"]["bands"]):
            raise AssertionError("The pilot scene contains no valid optical pixels")
        # open_group uses the same file lock; release our lock before its API check.
    for name in store.groups:
        with open_group(store.path, name) as dataset:
            if dataset.data.dims != ("time", "bands", "x", "y") or dataset.sizes["time"] != 1:
                raise AssertionError("xarray reader dimensions differ")
            record["groups"][name]["xarray_dimensions_verified"] = True
            record["groups"][name]["decoded_time"] = str(dataset.time.values[0])
    record["status"] = "passed"
    save()


def scan_credentials(output):
    inspected, found = 0, []
    pattern = re.compile(
        r'(?i)(?:[?&](?:sig|x-amz-signature)=|"(?:token|credential)"\s*:)'
    )
    for path in output.rglob("*"):
        if path.is_file() and (path.suffix == ".json" or path.name in {
            ".zattrs", ".zarray", ".zgroup", ".zmetadata"
        }):
            inspected += 1
            if pattern.search(path.read_text(encoding="utf-8")):
                found.append(str(path.relative_to(output)))
    return {"files_checked": inspected, "files_with_credentials": found}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Frozen manifest used for the completed ingest")
    parser.add_argument("--report", type=Path, required=True, help="Persistent JSON acceptance report")
    parser.add_argument("--tile", default="17MPU", help="Expected single MGRS tile (default: 17MPU)")
    parser.add_argument("--local-only", action="store_true", help="Skip independent source comparisons")
    parser.add_argument("--check-resume", action="store_true", help="Re-run ingest and require all scenes skipped")
    args = parser.parse_args()
    started = time.monotonic()
    report = {
        "schema_version": 1, "status": "running", "started_at_utc": utc_now(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "manifest_path": str(args.manifest.resolve()), "source_checks": not args.local_only,
        "scope": "All local data/valid blocks decoded; independent source comparison is sampled",
        "network_bytes_measured": False, "stores": [],
        "software": {"python": sys.version.split()[0], "numpy": np.__version__,
                     "rasterio": rasterio.__version__, "gdal": rasterio.__gdal_version__,
                     "zarr": zarr.__version__},
    }

    def save():
        atomic_json(args.report, report)

    try:
        manifest = load_manifest(args.manifest)
        report["manifest_sha256"] = manifest["manifest_sha256"]
        tiles = manifest["tiles"]
        if len(tiles) != 1 or tiles[0]["tile"] != args.tile:
            raise ValueError("Manifest must contain exactly the requested MGRS tile")
        tile = tiles[0]
        expected = set(manifest["config"]["products"])
        if set(tile["scene_counts"]) != expected or any(
            count != 1 for count in tile["scene_counts"].values()
        ):
            raise ValueError("Manifest must select exactly one scene for every configured product")
        by_product = {}
        for item in tile["items"]:
            key = product_for_item(item).key
            if key in by_product:
                raise ValueError("Manifest contains duplicate product selections")
            by_product[key] = item
        if set(by_product) != expected:
            raise ValueError("Manifest scene counts disagree with the selected items")
        original_grids = {}
        grid_records = list(tile["grids"].values())
        grid_records += [record for groups in tile.get("group_grids", {}).values()
                         for record in groups.values()]
        for grid_record in grid_records:
            grid = GridSpec.from_dict(grid_record)
            cache_key = (grid.source_path, grid.resolution)
            if cache_key not in original_grids:
                original_grids[cache_key] = load_grid(
                    grid.source_path, args.tile, grid.resolution
                ).to_dict()
            if grid.to_dict() != original_grids[cache_key]:
                raise AssertionError("Manifest grid is cropped or differs from the MGRS source")
        report["full_grid_matches_original_mgrs_source"] = True
        report["structural_verification"] = verify(manifest)
        if not report["structural_verification"]["manifest_satisfied"]:
            raise ValueError("Ingestion is incomplete; finish it before full-tile verification")
        save()
        with local_asset_index(manifest["config"].get("access")):
            for key, item in by_product.items():
                record = {"status": "running"}
                report["stores"].append(record)
                audit_store(manifest, tile, PRODUCTS[key], item,
                            source_checks=not args.local_only, save=save, record=record)
        if args.check_resume:
            before = {key: store_for(manifest, tile, PRODUCTS[key]).commits_path.read_bytes()
                      for key in by_product}
            resumed = ingest(manifest)
            if resumed["written"] != 0 or resumed["already_committed"] != len(by_product):
                raise AssertionError("Repeat ingest did not skip every selected scene")
            for key, content in before.items():
                if store_for(manifest, tile, PRODUCTS[key]).commits_path.read_bytes() != content:
                    raise AssertionError("Repeat ingest changed a commit ledger")
            report["repeat_ingest"] = resumed
        report["credential_scan"] = scan_credentials(Path(manifest["config"]["output"]))
        if report["credential_scan"]["files_with_credentials"]:
            raise AssertionError("Credentials found in persisted archive metadata")
        report["status"] = "passed"
    except Exception as exc:
        report["status"], report["error"] = "failed", safe_error(exc)
        print(json.dumps(report["error"], ensure_ascii=False), flush=True)
    report["finished_at_utc"] = utc_now()
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    save()
    print(f"Full tile verification {report['status']}: {args.report}", flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
