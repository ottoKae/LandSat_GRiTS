from dataclasses import replace
import json

from affine import Affine
import numpy as np
import pytest
import rasterio
from shapely.geometry import box, mapping
import zarr

from landsat_grits.bands import PRODUCTS
from landsat_grits.access import AssetAccessError
from landsat_grits.grid import GridSpec
from landsat_grits.reader import open_common_reflectance
from landsat_grits.storage import SceneStore, atomic_json, open_group, read_json


@pytest.fixture
def grid():
    return GridSpec("50RKV", "EPSG:32650", Affine(30, 0, 300000, 0, -30, 3200000),
                    7, 5, 30, mapping(box(114, 28, 115, 29)), "synthetic.parquet", "a" * 64)


def write_tif(path, grid, values, nodata):
    with rasterio.open(path, "w", driver="GTiff", width=values.shape[1],
                       height=values.shape[0], count=1, dtype=values.dtype,
                       crs=grid.crs, transform=grid.transform, nodata=nodata) as dst:
        dst.write(values, 1)


def scene(tmp_path, grid, key="A", date="2020-01-01T12:00:00Z", product_key="oli-l2",
          include_optional=True):
    product = PRODUCTS[product_key]
    assets = {}
    for bi, band in enumerate(product.optical_bands):
        values = (np.arange(35).reshape(5, 7) + 100 + bi * 50).astype(band.dtype)
        values[1, 2] = 0
        path = tmp_path / f"{key}-{band.asset_key}.tif"
        # Exercise registry fallback for a missing TIFF nodata tag.
        write_tif(path, grid, values, None)
        assets[band.asset_key] = {"href": str(path)}
    if include_optional:
        qa = tmp_path / f"{key}-qa_radsat.tif"
        write_tif(qa, grid, np.zeros((5, 7), dtype="uint16"), None)
        assets["qa_radsat"] = {"href": str(qa)}
        qa_pixel = tmp_path / f"{key}-qa_pixel.tif"
        values = np.zeros((5, 7), dtype="uint16")
        values[0, 0] = 1
        write_tif(qa_pixel, grid, values, 1)
        assets["qa_pixel"] = {"href": str(qa_pixel)}
        if product.ancillary_bands:
            band = product.ancillary_bands[0]
            path = tmp_path / f"{key}-{band.asset_key}.tif"
            values = np.full((5, 7), 15, dtype="int16")
            values[2, 3] = -9999
            write_tif(path, grid, values, -9999)
            assets[band.asset_key] = {"href": str(path)}
    return {
        "type": "Feature", "id": key, "collection": product.collection,
        "geometry": grid.geometry, "properties": {
            "datetime": date, "platform": product.platforms[0],
        }, "assets": assets,
    }


def test_pixels_dimensions_zero_qa_missing_temperature_and_xarray(tmp_path, grid):
    item = scene(tmp_path, grid)
    store = SceneStore(tmp_path / "archive.zarr", PRODUCTS["oli-l2"], grid, chunk=16)
    assert store.append(item)
    root = zarr.open_group(str(store.path), mode="r")
    optical = root["optical"]
    assert optical["data"].shape == (1, 7, 7, 5)
    assert optical["data"][0, 0, 4, 3] == 125
    assert not optical["valid"][0, 0, 2, 1]
    assert optical["valid"][0, 0, 0, 0]
    assert optical["source_band"][0, 3] == "SR_B4"
    assert root["qa"]["data"][0, 1, 0, 0] == 0
    assert root["qa"]["valid"][0, 1, :, :].all()
    assert not root["qa"]["valid"][0, 0, 0, 0]
    assert root["qa"]["valid"][0, 0, 1, 1]
    assert not root["thermal"]["present"][:].any()
    assert not root["thermal"]["valid"][:].any()
    assert root["ancillary"]["data"].dtype == np.dtype("int16")
    assert root["ancillary"]["data"][0, 0, 3, 2] == -9999
    assert not root["ancillary"]["valid"][0, 0, 3, 2]
    assert store.audit()["scenes"] == 1
    ds = open_group(store.path)
    assert ds.data.dims == ("time", "bands", "x", "y")
    assert ds.time.values[0] == np.datetime64("2020-01-01T12:00:00")
    assert ds.data.dtype == "uint16"
    np.testing.assert_array_equal(ds.x, grid.x)
    np.testing.assert_array_equal(ds.y, grid.y)
    assert float((ds.data.where(ds.valid) * ds.scale + ds.offset)[0, 0, 4, 3]) == pytest.approx(
        125 * 0.0000275 - 0.2
    )
    ds.close()
    common = open_common_reflectance([store.path])
    assert common.bands.values.tolist() == ["blue", "green", "red", "nir08", "swir16", "swir22"]
    assert common.reflectance.dtype == np.dtype("float32")
    assert not common.attrs["spectrally_harmonized"]
    common.close()
    # Missing entire groups must not pass audit or an idempotent resume.
    writable = zarr.open_group(str(store.path), mode="a")
    del writable["thermal"]
    with pytest.raises(ValueError, match="groups are missing"):
        store.audit()
    with pytest.raises(ValueError, match="groups are missing"):
        store.append(item)


def test_same_time_different_scenes_resume_and_reject_history(tmp_path, grid):
    store = SceneStore(tmp_path / "archive.zarr", PRODUCTS["oli-l2"], grid, chunk=16)
    a = scene(tmp_path, grid, key="A")
    b = scene(tmp_path, grid, key="B")
    assert store.append(a)
    assert store.append(b)
    assert not store.append(a)
    assert store.audit()["scenes"] == 2
    with pytest.raises(ValueError, match="Historical"):
        store.append(scene(tmp_path, grid, key="C", date="2019-01-01T12:00:00Z"))
    a["properties"]["changed"] = True
    with pytest.raises(ValueError, match="changed"):
        store.append(a)


def test_failure_and_crash_tail_rollback(tmp_path, grid, monkeypatch):
    store = SceneStore(tmp_path / "archive.zarr", PRODUCTS["oli-l2"], grid, chunk=16)
    a = scene(tmp_path, grid, key="A")
    b = scene(tmp_path, grid, key="B")
    store.append(a)
    original = store._write_band
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("simulated network failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_write_band", fail)
    with pytest.raises(OSError, match="simulated"):
        store.append(b)
    assert store.audit()["scenes"] == 1
    root = zarr.open_group(str(store.path), mode="a")
    # Emulate process termination halfway through resize/write.
    root["optical/data"].resize((2, 7, 7, 5))
    root["optical/data"][1, :, :, :] = 555
    with pytest.raises(ValueError, match="Uncommitted"):
        store.audit()
    monkeypatch.setattr(store, "_write_band", original)
    assert store.append(b)
    assert store.audit()["scenes"] == 2
    assert root["optical/data"][0, 0, 4, 3] == 125


def test_wrong_sensor_and_missing_asset_rejected(tmp_path, grid):
    store = SceneStore(tmp_path / "archive.zarr", PRODUCTS["tm-l2"], grid, chunk=16)
    with pytest.raises(ValueError, match="does not match"):
        store.append(scene(tmp_path, grid, product_key="etm-l2"))
    item = scene(tmp_path, grid, product_key="tm-l2")
    del item["assets"]["blue"]
    with pytest.raises(ValueError, match="required optical"):
        store.append(item)
    assert store.audit()["scenes"] == 0


def test_changed_grid_and_missing_commit_ledger_fail_closed(tmp_path, grid):
    path = tmp_path / "archive.zarr"
    item = scene(tmp_path, grid)
    store = SceneStore(path, PRODUCTS["oli-l2"], grid, chunk=16)
    store.append(item)
    changed = SceneStore(path, PRODUCTS["oli-l2"], replace(grid, width=8), chunk=16)
    with pytest.raises(ValueError, match="contract differs"):
        changed.append(item)
    store.commits_path.unlink()
    with pytest.raises(ValueError, match="Missing commit ledger"):
        store.append(item)


def test_outside_footprint_invalid_and_no_full_scene_allocation(tmp_path, grid):
    item = scene(tmp_path, grid)
    larger = replace(grid, width=9, height=8)
    store = SceneStore(tmp_path / "archive.zarr", PRODUCTS["oli-l2"], larger, chunk=16)
    store.append(item)
    root = zarr.open_group(str(store.path), mode="r")
    assert not root["qa/valid"][0, 1, 7:, :].any()
    assert not root["optical/valid"][0, 0, :, 5:].any()
    assert root["qa/valid"][0, 1, :7, :5].all()


def test_mss_has_two_nir_and_preserves_uint8(tmp_path, grid):
    item = scene(tmp_path, grid, product_key="mss-l1")
    store = SceneStore(tmp_path / "mss.zarr", PRODUCTS["mss-l1"], grid, chunk=16)
    store.append(item)
    root = zarr.open_group(str(store.path), mode="r")
    assert root["optical/data"].dtype == np.dtype("uint8")
    assert root["optical/source_band"][0, :].tolist() == ["B4", "B5", "B6", "B7"]
    assert root["optical/bands"][:].tolist() == ["green", "red", "nir08", "nir09"]
    assert "thermal" not in root
    assert json.dumps(read_json(store.commits_path))


def test_recovery_rejects_a_truncated_ledger_instead_of_deleting_multiple_scenes(tmp_path, grid):
    store = SceneStore(tmp_path / "archive.zarr", PRODUCTS["oli-l2"], grid, chunk=16)
    a = scene(tmp_path, grid, key="A")
    store.append(a)
    store.append(scene(tmp_path, grid, key="B"))
    atomic_json(store.commits_path, [])
    with pytest.raises(ValueError, match="one uncommitted"):
        store.append(a)
    root = zarr.open_group(str(store.path), mode="r")
    assert root["optical/data"].shape[0] == 2
    assert root["optical/data"][0, 0, 4, 3] == 125


def test_recovery_checks_all_lengths_before_truncating_any_tail(tmp_path, grid):
    store = SceneStore(tmp_path / "archive.zarr", PRODUCTS["oli-l2"], grid, chunk=16)
    item = scene(tmp_path, grid)
    store.append(item)
    root = zarr.open_group(str(store.path), mode="a")
    root["ancillary/data"].resize((2, 8, 7, 5))
    root["ancillary/data"][1, :, :, :] = 123
    root["thermal/time"].resize((0,))
    with pytest.raises(ValueError, match="shorter than committed"):
        store.append(item)
    # No earlier group may be trimmed before damage in a later one is found.
    root = zarr.open_group(str(store.path), mode="r")
    assert root["ancillary/data"].shape[0] == 2
    assert root["ancillary/data"][1, 0, 0, 0] == 123


@pytest.mark.parametrize("damage", [
    "mapping", "entry_type", "missing", "key", "fingerprint", "provenance",
    "datetime", "naive", "non_utc", "duplicate", "reverse",
])
def test_malformed_commit_ledger_never_changes_array_lengths(tmp_path, grid, damage):
    store = SceneStore(tmp_path / "archive.zarr", PRODUCTS["oli-l2"], grid, chunk=16)
    item = scene(tmp_path, grid, key="A")
    store.append(item)
    store.append(scene(tmp_path, grid, key="B", date="2020-02-01T12:00:00Z"))
    commits = read_json(store.commits_path)
    broken = [dict(commits[0])]
    if damage == "mapping":
        broken = {"one": commits[0]}
    elif damage == "entry_type":
        broken = [None]
    elif damage == "missing":
        del broken[0]["datetime"]
    elif damage == "duplicate":
        broken = [commits[0], commits[0]]
    elif damage == "reverse":
        broken = commits[::-1]
    else:
        field, value = {
            "key": ("key", ""),
            "fingerprint": ("fingerprint", "not-a-sha256"),
            "provenance": ("provenance", "../outside.json"),
            "datetime": ("datetime", "not-a-time"),
            "naive": ("datetime", "2020-01-01T12:00:00"),
            "non_utc": ("datetime", "2020-01-01T12:00:00+08:00"),
        }[damage]
        broken[0][field] = value
    atomic_json(store.commits_path, broken)
    with pytest.raises(ValueError, match="Invalid commit ledger"):
        store.append(item)
    root = zarr.open_group(str(store.path), mode="r")
    for _, group in root.groups():
        for _, array in group.arrays():
            if array.attrs.get("_ARRAY_DIMENSIONS", [])[:1] == ["time"]:
                assert array.shape[0] == 2
    assert root["optical/data"][0, 0, 4, 3] == 125
    assert root["optical/data"][1, 0, 4, 3] == 125


@pytest.mark.parametrize("damage", [
    "valid_dtype", "valid_fill", "spatial_chunks", "crs", "transform", "time_units",
])
def test_resume_rejects_changed_scientific_metadata(tmp_path, grid, damage):
    store = SceneStore(tmp_path / "archive.zarr", PRODUCTS["oli-l2"], grid, chunk=16)
    item = scene(tmp_path, grid)
    store.append(item)
    if damage in {"valid_dtype", "valid_fill", "spatial_chunks"}:
        variable = "data" if damage == "spatial_chunks" else "valid"
        path = store.path / "optical" / variable / ".zarray"
        metadata = read_json(path)
        if damage == "valid_dtype":
            metadata["dtype"] = "|u1"
        elif damage == "valid_fill":
            metadata["fill_value"] = True
        else:
            metadata["chunks"][1] = 2
        atomic_json(path, metadata)
    else:
        root = zarr.open_group(str(store.path), mode="a")
        if damage == "crs":
            root["optical"].attrs["crs"] = "EPSG:32750"
        elif damage == "transform":
            root["optical"].attrs["transform"] = [30, 0, 300030, 0, -30, 3200000]
        else:
            root["optical/time"].attrs["units"] = "milliseconds since 1970-01-01"
    with pytest.raises(ValueError, match="Unexpected"):
        store.append(item)


def test_failure_during_resize_rolls_back_immediately(tmp_path, grid, monkeypatch):
    store = SceneStore(tmp_path / "archive.zarr", PRODUCTS["oli-l2"], grid, chunk=16)
    store.append(scene(tmp_path, grid, key="A"))
    original = zarr.Array.resize
    enlargements = []

    def fail_once(array, shape):
        if shape[0] == 2:
            enlargements.append(array.path)
            if len(enlargements) == 4:
                raise OSError("synthetic resize failure")
        return original(array, shape)

    monkeypatch.setattr(zarr.Array, "resize", fail_once)
    with pytest.raises(OSError, match="resize failure"):
        store.append(scene(tmp_path, grid, key="B"))
    assert len(enlargements) == 4
    assert store.audit()["scenes"] == 1


def test_window_writes_reuse_array_metadata_and_write_scene_metadata_once(
    tmp_path, grid, monkeypatch,
):
    larger = replace(grid, width=33, height=35)
    item = scene(tmp_path, grid)
    store = SceneStore(tmp_path / "archive.zarr", PRODUCTS["oli-l2"], larger, chunk=16)
    original_write = store._write_band
    opens = []

    class CountedGroup:
        def __init__(self, group):
            self.group = group
            self.opens = {"data": 0, "valid": 0}

        def __getitem__(self, key):
            if key in self.opens:
                self.opens[key] += 1
            return self.group[key]

    def count_metadata(group, *args, **kwargs):
        counted = CountedGroup(group)
        result = original_write(counted, *args, **kwargs)
        opens.append(counted.opens)
        return result

    original_set = zarr.Array.__setitem__
    metadata_writes = {}

    def count_writes(array, selection, value):
        if array.path in {f"optical/{name}" for name in (
            "scale", "offset", "source_band", "source_dtype", "present",
        )}:
            metadata_writes[array.path] = metadata_writes.get(array.path, 0) + 1
        return original_set(array, selection, value)

    monkeypatch.setattr(store, "_write_band", count_metadata)
    monkeypatch.setattr(zarr.Array, "__setitem__", count_writes)
    assert store.append(item)
    assert opens and all(count == {"data": 1, "valid": 1} for count in opens)
    assert metadata_writes == {
        f"optical/{name}": 1
        for name in ("scale", "offset", "source_band", "source_dtype", "present")
    }
    assert store.audit()["scenes"] == 1


def test_direct_append_never_persists_nested_access_credentials(tmp_path, grid):
    item = scene(tmp_path, grid)
    item["links"] = [{"href": "https://example.org/item?sig=secret-sas&keep=scene"}]
    item["assets"]["blue"]["alternate"] = {
        "signed": {"href": "https://example.org/blue?access_token=secret-token&keep=band"}
    }
    item["properties"]["authorization"] = "secret-authorization"
    store = SceneStore(tmp_path / "archive.zarr", PRODUCTS["oli-l2"], grid, chunk=16)
    assert store.append(item)
    commit = read_json(store.commits_path)[0]
    serialized = (store.provenance / commit["provenance"]).read_text(encoding="utf-8")
    assert "secret-" not in serialized
    assert "keep=scene" in serialized and "keep=band" in serialized
    assert item["properties"]["authorization"] == "secret-authorization"
    assert store.audit()["scenes"] == 1


def test_previously_read_source_replaced_before_commit_rolls_back(tmp_path, grid, monkeypatch):
    store = SceneStore(tmp_path / "archive.zarr", PRODUCTS["oli-l2"], grid, chunk=16)
    first = scene(tmp_path, grid, key="A")
    second = scene(tmp_path, grid, key="B")
    assert store.append(first)
    original = store._write_band

    def replace_earlier_band(group, index, bi, band, asset, **kwargs):
        result = original(group, index, bi, band, asset, **kwargs)
        if band.asset_key == "qa_radsat":
            # The original blue file has already been read and closed. Replacing
            # it now evades a check restricted to each individual raster read.
            replacement = tmp_path / "replacement.tif"
            write_tif(replacement, grid, np.full((5, 7), 222, dtype="uint16"), None)
            replacement.replace(second["assets"]["blue"]["href"])
        return result

    monkeypatch.setattr(store, "_write_band", replace_earlier_band)
    with pytest.raises(AssetAccessError, match="changed before scene commit"):
        store.append(second)
    assert store.audit()["scenes"] == 1
    root = zarr.open_group(str(store.path), mode="r")
    assert root["optical/data"][0, 0, 4, 3] == 125
    assert len(list(store.provenance.glob("*.json"))) == 1
