"""Create two tiny synthetic scenes to exercise installation without network access."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from shapely.geometry import box

from landsat_grits.bands import PRODUCTS
from landsat_grits.grid import load_grid
from landsat_grits.storage import atomic_json, band_groups, open_group
from landsat_grits.workflow import ingest, manifest_hash


def create_demo(output):
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = root / "synthetic-tiffs"
    source.mkdir()
    # This deliberately small polygon is a test window, not a real MGRS extent.
    frame = gpd.GeoDataFrame({"mgrs_tile_id": ["31NEA"], "utm_epsg": [32631]},
                            geometry=[box(500011, 1, 501929, 1919)], crs="EPSG:32631")
    frame.to_crs(4326).to_parquet(root / "synthetic-grid.parquet", index=False)
    grid = load_grid(root / "synthetic-grid.parquet", "31NEA", 30)
    product = PRODUCTS["oli-l2"]
    items = []
    y, x = np.indices((grid.height, grid.width))
    for day in (1, 2):
        assets = {}
        for group, bands in band_groups(product).items():
            for bi, band in enumerate(bands):
                if group == "qa":
                    values = np.full(x.shape, 64 if band.asset_key == "qa_pixel" else 0,
                                     dtype=band.dtype)
                else:
                    values = (10000 + x * 50 + y * 30 + bi * 300 + day * 100).astype(band.dtype)
                    values[(x < 8) if day == 1 else (x > 55)] = 0
                path = source / f"demo-{day}-{band.asset_key}.tif"
                with rasterio.open(path, "w", driver="GTiff", width=grid.width,
                                   height=grid.height, count=1, dtype=values.dtype,
                                   crs=grid.crs, transform=grid.transform) as image:
                    image.write(values, 1)
                assets[band.asset_key] = {"href": str(path)}
        items.append({"type": "Feature", "id": f"SYNTHETIC_DEMO_{day}",
                      "collection": product.collection, "geometry": grid.geometry,
                      "properties": {"datetime": f"2025-01-0{day}T03:00:00Z",
                                     "platform": "landsat-9", "landsat:wrs_path": "122",
                                     "landsat:wrs_row": "044"}, "assets": assets})
    manifest = {"schema_version": 1, "selection": "synthetic_offline_demo", "search_complete": False,
                "config": {"output": str(root / "archive"), "chunk": 32},
                "tiles": [{"tile": "31NEA", "grids": {"oli-l2": grid.to_dict()},
                           "scene_counts": {"oli-l2": 2}, "items": items}]}
    manifest["manifest_sha256"] = manifest_hash(manifest)
    atomic_json(root / "manifest.json", manifest)
    report = ingest(manifest)
    assert report["manifest_satisfied"]
    store = root / "archive/31NEA/oli-l2/30m.zarr"
    with open_group(store, chunks=None) as cube:
        assert cube.data.dims == ("time", "bands", "x", "y")
        assert cube.sizes["time"] == 2 and cube.sizes["x"] == cube.sizes["y"] == 64
        assert not cube.valid.sel(bands="red").isel(time=0, x=0, y=20).item()
        assert cube.valid.sel(bands="red").isel(time=1, x=0, y=20).item()
    atomic_json(root / "resolver.json", {
        "cache_dir": "display-cache", "archives": [{
            "stores": ["archive/31NEA/oli-l2/30m.zarr"], "sensor": "landsat", "group": "optical",
            "label": "SYNTHETIC DEMO - not real satellite imagery", "default_period": "year",
            "default_year": "2025", "bands": [b.name for b in product.optical_bands],
        }],
    })
    return {"synthetic": True, "scenes": 2, "shape": [64, 64], "root": str(root),
            "gui_command": f'landsat-grits gui "{root / "archive"}"', "validation": report}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="outputs/offline-demo")
    print(json.dumps(create_demo(parser.parse_args().output), indent=2))
