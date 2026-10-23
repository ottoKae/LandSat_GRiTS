"""Native L1 pixel preservation and atomic multi-resolution scene writes."""

import hashlib
import json
from pathlib import Path

from affine import Affine
import numpy as np
import pytest
import rasterio
from shapely.geometry import box, mapping
import zarr

from landsat_grits.bands import PRODUCTS, QA_BANDS, required_bands_for_item
from landsat_grits.grid import GridSpec, load_grid
from landsat_grits.storage import SceneStore, open_group, read_json


@pytest.fixture
def grids():
    geometry = mapping(box(114, 28, 115, 29))
    base = GridSpec(
        "50RKV", "EPSG:32650", Affine(30, 0, 300000, 0, -30, 3200000),
        7, 5, 30, geometry, "synthetic.parquet", "a" * 64,
    )
    pan = GridSpec(
        "50RKV", "EPSG:32650", Affine(15, 0, 300015, 0, -15, 3199985),
        13, 9, 15, geometry, "synthetic.parquet", "a" * 64,
    )
    return {"optical": base, "thermal": base, "pan": pan, "qa": base}


def _scene(tmp_path, grids, day="01"):
    product = PRODUCTS["oli-l1"]
    identifier = f"LC08_L1TP_123039_202001{day}_20200114_02_T1"
    assets, expected = {}, {}
    bands = (*product.bands, *(QA_BANDS[key] for key in product.qa_assets))
    for index, band in enumerate(bands):
        grid = grids["pan"] if band.asset_key == "pan" else grids["optical"]
        values = (
            np.arange(grid.height * grid.width).reshape(grid.height, grid.width)
            + 100 * (index + 1)
        ).astype(band.dtype)
        if band.asset_key == "qa_radsat":
            values[:] = 0
        elif band.asset_key == "qa_pixel":
            values[:] = 0
            values[0, 0] = 1
        else:
            values[0, 0] = 0
        path = tmp_path / f"{identifier}_{band.source_band}.TIF"
        with rasterio.open(
            path, "w", driver="GTiff", width=grid.width, height=grid.height,
            count=1, dtype=values.dtype, crs=grid.crs, transform=grid.transform,
            nodata=band.nodata,
        ) as source:
            source.write(values, 1)
        assets[band.asset_key] = {"href": str(path)}
        expected[band.asset_key] = values
    mtl = {
        "LANDSAT_METADATA_FILE": {
            "PRODUCT_CONTENTS": {"LANDSAT_PRODUCT_ID": identifier},
            "LEVEL1_RADIOMETRIC_RESCALING": {"RADIANCE_MULT_BAND_10": 0.0003342},
        }
    }
    metadata_path = tmp_path / f"{identifier}_MTL.json"
    metadata_path.write_text(json.dumps(mtl), encoding="utf-8")
    assets["mtl.json"] = {"href": str(metadata_path)}
    item = {
        "type": "Feature", "id": identifier, "collection": "landsat-c2l1",
        "geometry": grids["optical"].geometry,
        "properties": {
            "datetime": f"2020-01-{day}T12:00:00Z",
            "platform": "landsat-8", "instruments": ["OLI", "TIRS"],
        },
        "assets": assets,
    }
    return item, expected, mtl


def _store(tmp_path, grids):
    return SceneStore(
        tmp_path / "oli.zarr", PRODUCTS["oli-l1"], grids["optical"], chunk=16,
        group_grids=grids, access=None,
    )


def test_all_oli_native_bands_and_qa_keep_pixels_on_independent_pan_grid(tmp_path, grids):
    item, expected, mtl = _scene(tmp_path, grids)
    store = _store(tmp_path, grids)
    assert store.append(item)
    root = zarr.open_group(str(store.path), mode="r")
    names = {
        "optical": ["coastal", "blue", "green", "red", "nir08", "swir16", "swir22", "cirrus"],
        "thermal": ["lwir11", "lwir12"],
        "pan": ["pan"],
        "qa": ["qa_pixel", "qa_radsat"],
    }
    assert set(root.group_keys()) == set(names)
    assert root.attrs["contract"]["schema_version"] == 2
    assert root["thermal/source_band"][0, :].tolist() == ["B10", "B11"]
    assert root["pan/source_band"][0, :].tolist() == ["B8"]
    for name, assets in names.items():
        group, grid = root[name], grids[name]
        assert group["bands"][:].tolist() == assets
        assert group["data"].shape == (1, len(assets), grid.width, grid.height)
        assert group["data"].attrs["_ARRAY_DIMENSIONS"] == ["time", "bands", "x", "y"]
        assert group["present"][:].all()
        np.testing.assert_array_equal(group["x"][:], grid.x)
        np.testing.assert_array_equal(group["y"][:], grid.y)
        for index, asset in enumerate(assets):
            valid = group["valid"][0, index]
            source = expected[asset].T
            np.testing.assert_array_equal(group["data"][0, index][valid], source[valid])
            if asset == "qa_radsat":
                assert valid.all()
            else:
                assert not valid[0, 0]
                assert valid[1:, 1:].all()
        np.testing.assert_array_equal(group["time"][:], root["optical/time"][:])
        np.testing.assert_array_equal(group["scene_id"][:], root["optical/scene_id"][:])
    assert root["pan/data"].shape[2:] == (13, 9)
    assert root["optical/data"].shape[2:] == (7, 5)
    commit = read_json(store.commits_path)[0]
    provenance = read_json(store.provenance / commit["provenance"])
    assert provenance["mtl"] == mtl
    assert set(provenance["rasters"]) == set(expected)
    with open_group(store.path, group="pan", chunks=None) as ds:
        assert ds.data.dims == ("time", "bands", "x", "y")
        assert float(ds.x[1] - ds.x[0]) == 15
    assert store.audit()["scenes"] == 1


def test_pan_failure_rolls_back_every_group_and_resume_preserves_first_scene(
    tmp_path, grids, monkeypatch,
):
    first, _, _ = _scene(tmp_path, grids)
    second, _, _ = _scene(tmp_path, grids, day="02")
    store = _store(tmp_path, grids)
    assert store.append(first)
    original = store._write_band
    completed = []

    def fail_on_pan(group, index, bi, band, asset, **kwargs):
        if band.asset_key == "pan":
            raise OSError("synthetic pan read failure")
        result = original(group, index, bi, band, asset, **kwargs)
        completed.append(band.asset_key)
        return result

    monkeypatch.setattr(store, "_write_band", fail_on_pan)
    with pytest.raises(OSError, match="pan read failure"):
        store.append(second)
    assert "lwir11" in completed and "lwir12" in completed
    assert store.audit()["scenes"] == 1
    root = zarr.open_group(str(store.path), mode="r")
    preserved = root["pan/data"][0].copy()
    for _, group in root.groups():
        for _, array in group.arrays():
            if array.attrs.get("_ARRAY_DIMENSIONS", [])[:1] == ["time"]:
                assert array.shape[0] == 1
    monkeypatch.setattr(store, "_write_band", original)
    assert store.append(second)
    assert not store.append(first)
    assert store.audit()["scenes"] == 2
    resumed = zarr.open_group(str(store.path), mode="r")
    np.testing.assert_array_equal(resumed["pan/data"][0], preserved)


def test_missing_native_pan_cannot_commit_an_incomplete_scene(tmp_path, grids):
    item, _, _ = _scene(tmp_path, grids)
    del item["assets"]["pan"]
    store = _store(tmp_path, grids)
    with pytest.raises(ValueError, match="required.*pan"):
        store.append(item)
    assert store.audit()["scenes"] == 0
    assert read_json(store.commits_path) == []


@pytest.mark.parametrize("prefix,instruments,present,absent", [
    ("LO08", ["OLI"], ("optical", "pan", "qa"), ("thermal",)),
    ("LT08", ["TIRS"], ("thermal", "qa"), ("optical", "pan")),
])
def test_sensor_only_scene_keeps_acquired_bands_and_marks_others_absent(
    tmp_path, grids, prefix, instruments, present, absent,
):
    item, _, mtl = _scene(tmp_path, grids)
    item["id"] = prefix + item["id"][4:]
    item["properties"]["instruments"] = instruments
    required = {b.asset_key for b in required_bands_for_item(item)}
    item["assets"] = {key: value for key, value in item["assets"].items()
                      if key in required | {"qa_pixel", "qa_radsat", "mtl.json"}}
    mtl["LANDSAT_METADATA_FILE"]["PRODUCT_CONTENTS"]["LANDSAT_PRODUCT_ID"] = item["id"]
    Path(item["assets"]["mtl.json"]["href"]).write_text(json.dumps(mtl), encoding="utf-8")
    store = _store(tmp_path, grids)
    assert store.append(item)
    root = zarr.open_group(str(store.path), mode="r")
    for name in present:
        assert root[name]["present"][:].all()
    for name in absent:
        assert not root[name]["present"][:].any()
        assert not root[name]["valid"][:].any()
    assert store.audit()["scenes"] == 1


def test_legacy_real_grid_contract_hashes_are_unchanged(tmp_path):
    import os

    source = os.environ.get("LANDSAT_GRITS_LEGACY_MGRS")
    if not source:
        pytest.skip("Set LANDSAT_GRITS_LEGACY_MGRS for the optional historical grid check")
    path = Path(source)
    assert path.is_file(), "Configured historical grid file does not exist"
    expected = {
        "mss-l1": "2d6e571157b32c37ed03fa9e78bcb1737db49b16922422d09ae02d0b81503d69",
        "tm-l2": "cc9cb3513f4543733ed6b683c90cd4ae0dc3f0ceab235f15d8625263fd2df7b1",
        "etm-l2": "0cbbff6ca385b6258ec7897908528bb683f7b7e56fa97842beb18f798e914dec",
        "oli-l2": "6a55fb34f39d4745a02c2f7aa4b9c195f09db54ced2afc481d58f279aab2683f",
    }
    for key, fingerprint in expected.items():
        product = PRODUCTS[key]
        grid = load_grid(path, "50RKV", product.resolution)
        archive = tmp_path / f"{key}.zarr"
        fresh = SceneStore(archive, product, grid, chunk=512)
        assert fresh.contract["warp_tolerance"] == 1e-9
        fresh._initialize()
        root = zarr.open_group(str(archive), mode="a")
        legacy_contract = dict(root.attrs["contract"])
        legacy_contract.pop("warp_tolerance")
        legacy_hash = hashlib.sha256(
            json.dumps(legacy_contract, sort_keys=True).encode()
        ).hexdigest()
        assert legacy_hash == fingerprint
        root.attrs.update({"contract": legacy_contract, "contract_hash": legacy_hash})
        store = SceneStore(archive, product, grid, chunk=512)
        assert store.contract["schema_version"] == 1
        assert "group_grids" not in store.contract
        assert store.contract_hash == fingerprint
        assert store.warp_tolerance == 0.125
        assert store.audit()["scenes"] == 0
