"""Orbit/frame identity and explicit scene reads, including overlapping masks."""

import hashlib

from affine import Affine
import numpy as np
import pytest
from shapely.geometry import box, mapping
import zarr

from landsat_grits.bands import PRODUCTS
from landsat_grits.catalog import scene_fingerprint, scene_key
from landsat_grits.grid import GridSpec
from landsat_grits.observations import wrs_identity
from landsat_grits.reader import (
    list_scenes, list_tracks, open_scene, open_track, open_common_reflectance,
)
from landsat_grits.storage import SceneStore, atomic_json, read_json


@pytest.fixture
def archive(tmp_path):
    grid = GridSpec("49QHE", "EPSG:32649", Affine(30, 0, 800000, 0, -30, 2500000),
                    6, 4, 30, mapping(box(113.9, 22.5, 114, 22.6)), "fixture.parquet", "a" * 64)
    store = SceneStore(tmp_path / "test.zarr", PRODUCTS["oli-l2"], grid, chunk=16)
    store._initialize()
    root = zarr.open_group(str(store.path), mode="a")
    store._resize(root, 3)
    commits = []
    for index, (path, row) in enumerate(((122, 44), (121, 45), (122, 45))):
        item = {"id": f"scene-{index}", "collection": "landsat-c2-l2", "properties": {
            "datetime": "2024-01-01T02:00:00Z", "platform": "landsat-8",
            "landsat:wrs_type": "2", "landsat:wrs_path": str(path), "landsat:wrs_row": str(row),
        }, "assets": {}}
        key = scene_key(item)
        source = hashlib.sha256(key.encode()).hexdigest() + ".json"
        atomic_json(store.provenance / source, {"item": item})
        commits.append({"key": key, "datetime": item["properties"]["datetime"],
                        "fingerprint": scene_fingerprint(item), "provenance": source})
        for name, bands in store.groups.items():
            group = root[name]
            group["time"][index] = np.datetime64("2024-01-01T02:00:00", "us").astype("int64")
            group["scene_id"][index] = key
            group["platform"][index] = "landsat-8"
            group["present"][index, :] = True
            group["scale"][index, :] = [band.scale for band in bands]
            group["offset"][index, :] = [band.offset for band in bands]
            group["source_band"][index, :] = [band.source_band for band in bands]
            group["source_dtype"][index, :] = [band.dtype for band in bands]
            group["data"][index, :, :, :] = (index + 1) * 1000
            group["valid"][index, :, :, :] = True
            group["valid"][index, :, index, :] = False
    atomic_json(store.commits_path, commits)
    return store.path


def test_same_datetime_tracks_and_rows_remain_separate(archive):
    tracks = list_tracks(archive)
    assert [(t["track"], t["rows"], t["scene_count"]) for t in tracks] == [
        ("WRS2/P122", [44, 45], 2), ("WRS2/P121", [45], 1),
    ]
    with pytest.raises(ValueError, match="Ambiguous"):
        open_scene(archive)
    with pytest.raises(ValueError, match="Ambiguous"):
        open_scene(archive, track="WRS2/P122")
    with open_track(archive, "WRS2/P122") as ds:
        assert ds.sizes["time"] == 2
        assert ds.wrs_row.values.tolist() == [44, 45]
        assert np.unique(ds.time.values).size == 1
        assert not ds.attrs["mosaicked"]
    with open_scene(archive, track="WRS2/P122", wrs_row=44, window=(1, 0, 2, 3)) as ds:
        assert ds.sizes["time"] == 1
        assert (ds.sizes["x"], ds.sizes["y"]) == (3, 2)
        assert (ds.data.values == 1000).all()
        assert not ds.valid.values[:, :, 0, :].any()
        assert np.isnan(ds.data.where(ds.valid).values[:, :, 0, :]).all()
        assert ds.attrs["transform"] == [30, 0, 800000, 0, -30, 2499970]
    # The other orbit's valid samples must never fill the selected scene's gap.
    with open_scene(archive, scene_id="landsat-c2-l2/scene-1") as other:
        assert other.valid.values[:, :, 0, :].all()
        assert (other.data.values == 2000).all()


def test_scene_id_must_agree_with_track_guard(archive):
    with pytest.raises(ValueError, match="No committed scene"):
        open_scene(archive, scene_id="landsat-c2-l2/scene-1", track="WRS2/P122")
    with pytest.raises(ValueError, match="No committed scene"):
        open_track(archive, "WRS1/P122")


def test_common_reflectance_requires_explicit_mixed_track_opt_in(archive):
    with pytest.raises(ValueError, match="Multiple WRS tracks"):
        open_common_reflectance([archive])
    with open_common_reflectance([archive], track="WRS2/P121") as ds:
        assert ds.sizes["time"] == 1
        assert ds.track.item() == "WRS2/P121"
        assert ds.reflectance.isel(time=0, bands=0, x=0, y=0).compute().item() == pytest.approx(-0.145)
    with open_common_reflectance([archive], allow_mixed_tracks=True) as ds:
        assert ds.sizes["time"] == 3
        assert ds.attrs["mixed_tracks"] is True
        assert ds.attrs["mosaicked"] is False


def test_wrs_system_is_part_of_mss_identity():
    for mission, system in ((1, 1), (3, 1), (4, 2), (5, 2), (9, 2)):
        value = wrs_identity({"platform": f"LANDSAT_{mission}", "landsat:wrs_path": "122"})
        assert value["track"] == f"WRS{system}/P122"
    with pytest.raises(ValueError, match="conflicts"):
        wrs_identity({"platform": "landsat-2", "landsat:wrs_type": 2, "landsat:wrs_path": 122})
    assert wrs_identity({"platform": "landsat-8"})["track"] is None


@pytest.mark.parametrize("kwargs", [
    {"track": "P122"}, {"track": "WRS2/P234"}, {"wrs_row": True},
    {"window": (0, 0, 5, 6)}, {"window": (-1, 0, 1, 1)}, {"window": (0, 0, 1.0, 1)},
])
def test_invalid_selectors_and_windows(archive, kwargs):
    with pytest.raises(ValueError):
        open_scene(archive, scene_id="landsat-c2-l2/scene-0", **kwargs)


def test_missing_legacy_orbits_not_grouped_and_corrupt_provenance_rejected(archive):
    ledger = archive.with_name(archive.name + ".commits.json")
    commits = read_json(ledger)
    for commit in commits:
        commit.pop("provenance")
    atomic_json(ledger, commits)
    assert len(list_tracks(archive)) == 3
    assert list_scenes(archive, track="WRS2/P122") == []
    with open_scene(archive, scene_id="landsat-c2-l2/scene-0") as ds:
        assert ds.track.item() == ""
        assert ds.wrs_path.item() == -1
    commits[0]["provenance"] = "../other.json"
    atomic_json(ledger, commits)
    with pytest.raises(ValueError, match="provenance filename"):
        list_scenes(archive)


def test_provenance_and_uncommitted_tail_fail_closed(archive):
    root = zarr.open_group(str(archive), mode="a")
    root["optical"]["data"].resize((4, 7, 6, 4))
    with pytest.raises(ValueError, match="Uncommitted"):
        list_scenes(archive)
    root["optical"]["data"].resize((3, 7, 6, 4))
    ledger = read_json(archive.with_name(archive.name + ".commits.json"))
    source = archive.with_name(archive.name + ".provenance") / ledger[0]["provenance"]
    provenance = read_json(source)
    provenance["item"]["properties"]["landsat:wrs_path"] = "121"
    atomic_json(source, provenance)
    with pytest.raises(ValueError, match="fingerprint"):
        list_scenes(archive)
