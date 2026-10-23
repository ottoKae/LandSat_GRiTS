"""A real cross-UTM boundary case reproduced with a deterministic synthetic raster."""

import hashlib
import json

from affine import Affine
import numpy as np
from pyproj import Transformer
import rasterio
from shapely.geometry import box, mapping
import zarr

from landsat_grits.bands import PRODUCTS
from landsat_grits.grid import GridSpec
from landsat_grits.storage import SceneStore, atomic_json


def grid():
    return GridSpec("49QHE", "EPSG:32649", Affine(60, 0, 799980, 0, -60, 2500020),
                    1831, 1831, 60, mapping(box(113.9, 21.5, 115, 22.6)),
                    "synthetic.parquet", "a" * 64)


def test_cross_zone_nearest_pixel_uses_precise_transform(tmp_path):
    source_transform = Affine(60, 0, 176490, 0, -60, 2663430)
    source = tmp_path / "row-codes.tif"
    height, width = 3620, 3793
    values = np.broadcast_to((np.arange(height) % 253 + 1).astype("uint8")[:, None],
                             (height, width)).copy()
    with rasterio.open(source, "w", driver="GTiff", crs="EPSG:32650",
                       transform=source_transform, width=width, height=height,
                       count=1, dtype="uint8", nodata=0, compress="deflate") as output:
        output.write(values, 1)
    store = SceneStore(tmp_path / "precise.zarr", PRODUCTS["mss-l1"], grid())
    store._initialize()
    root = zarr.open_group(str(store.path), mode="a")
    store._resize(root, 1)
    metadata = store._write_band(root["optical"], 0, 0, PRODUCTS["mss-l1"].optical_bands[0],
                                 {"href": str(source)})
    x, y = grid().transform * (778.5, 435.5)
    sx, sy = Transformer.from_crs("EPSG:32649", "EPSG:32650", always_xy=True).transform(x, y)
    col, row = (~source_transform) * (sx, sy)
    assert int(row) == 3184
    assert root["optical"]["data"][0, 0, 778, 435] == values[int(row), int(col)]
    assert root["optical"]["valid"][0, 0, 778, 435]
    assert metadata["warp_tolerance"] == 1e-9


def test_legacy_contract_keeps_its_original_warp_policy(tmp_path):
    path = tmp_path / "legacy.zarr"
    fresh = SceneStore(path, PRODUCTS["mss-l1"], grid())
    fresh._initialize()
    root = zarr.open_group(str(path), mode="a")
    old = dict(root.attrs["contract"])
    old.pop("warp_tolerance")
    old_hash = hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest()
    root.attrs.update({"contract": old, "contract_hash": old_hash})
    atomic_json(path.with_name(path.name + ".commits.json"), [])
    resumed = SceneStore(path, PRODUCTS["mss-l1"], grid())
    assert resumed.warp_tolerance == 0.125
    assert resumed.contract_hash == old_hash
    assert resumed.audit()["scenes"] == 0
