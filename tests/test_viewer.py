"""Scientific display and local-server boundary checks on tiny synthetic archives."""

from concurrent.futures import ThreadPoolExecutor
import base64
import json
import threading
import urllib.error
import urllib.request

from affine import Affine
from filelock import FileLock
import numpy as np
import pytest
from rasterio.io import MemoryFile
from shapely.geometry import box, mapping
import zarr

from landsat_grits.bands import PRODUCTS
from landsat_grits.catalog import scene_fingerprint
from landsat_grits.grid import GridSpec
from landsat_grits.storage import SceneStore, atomic_json
from landsat_grits.viewer import Viewer, create_server


@pytest.fixture
def archive(tmp_path):
    grid = GridSpec("50RKV", "EPSG:32650", Affine(30, 0, 300000, 0, -30, 3200000),
                    8, 5, 30, mapping(box(114, 28, 115, 29)), "synthetic.parquet", "a" * 64)
    path = tmp_path / "nested" / "test.zarr"
    store = SceneStore(path, PRODUCTS["oli-l2"], grid, chunk=16)
    store._initialize()
    root = zarr.open_group(str(path), mode="a")
    SceneStore._resize(root, 2)
    for name, specifications in store.groups.items():
        group = root[name]
        group["time"][:] = [0, 1_000_000]
        group["scene_id"][:] = ["landsat-c2-l2/A", "landsat-c2-l2/B"]
        group["platform"][:] = ["landsat-8", "landsat-9"]
        for scene in range(2):
            group["scale"][scene, :] = [band.scale for band in specifications]
            group["offset"][scene, :] = [band.offset for band in specifications]
            group["source_band"][scene, :] = [band.source_band for band in specifications]
            group["source_dtype"][scene, :] = [band.dtype for band in specifications]
            group["present"][scene, :] = True
            # Stored x,y; left side differs strongly from right to expose transposition.
            values = np.repeat(np.arange(8)[:, None] * 1000 + 1000, 5, axis=1)
            for band_index in range(len(specifications)):
                group["data"][scene, band_index, :, :] = 0 if name == "qa" else values
                group["valid"][scene, band_index, :, :] = True
        group["valid"][0, :, 0, 0] = False
    root["optical"]["source_band"][1, 0] = "SECOND_SCENE_NATIVE"
    root["thermal"]["present"][0, :] = False
    atomic_json(store.commits_path, [
        {"key": "landsat-c2-l2/A", "datetime": "1970-01-01T00:00:00+00:00"},
        {"key": "landsat-c2-l2/B", "datetime": "1970-01-01T00:00:01+00:00"},
    ])
    return path


def opened(archive):
    viewer = Viewer(archive.parent.parent)
    catalog = viewer.catalog()
    assert catalog["errors"] == []
    return viewer, catalog["archives"][0]


def pixels(preview):
    with MemoryFile(base64.b64decode(preview["image"].split(",")[1])) as memory:
        with memory.open() as raster:
            return raster.read().transpose(1, 2, 0)


def test_catalog_preserves_all_groups_scenes_and_actual_source_band(archive):
    viewer, entry = opened(archive)
    assert viewer.catalog()["counts"] == {"archives": 1, "scenes": 2, "variables": 19, "tiles": 1}
    assert entry["extent_kind"] == "window"
    assert [group["name"] for group in entry["groups"]] == ["optical", "thermal", "ancillary", "qa"]
    optical = entry["groups"][0]
    assert optical["bands"][0]["source_bands"] == ["SR_B1", "SECOND_SCENE_NATIVE"]
    assert entry["scenes"][1]["platform"] == "landsat-9"
    assert [scene["track"] for scene in entry["scenes"]] == [None, None]
    assert all(scene["orbit_metadata_source"] == "missing" for scene in entry["scenes"])
    assert optical["resolution"] == 30


def add_orbit_provenance(archive, *, conflicting=False):
    ledger = archive.with_name(archive.name + ".commits.json")
    commits = json.loads(ledger.read_text())
    for index, commit in enumerate(commits):
        item = {"collection": "landsat-c2-l2", "id": "AB"[index], "properties": {
            "platform": ["landsat-8", "landsat-9"][index], "datetime": commit["datetime"],
            "landsat:wrs_type": 1 if conflicting else 2,
            "landsat:wrs_path": [122, 123][index], "landsat:wrs_row": 44,
        }}
        commit["fingerprint"] = scene_fingerprint(item)
        commit["provenance"] = commit["fingerprint"] + ".json"
        atomic_json(archive.with_name(archive.name + ".provenance") / commit["provenance"],
                    {"item": item})
    atomic_json(ledger, commits)


def test_catalog_keeps_two_tracks_in_one_archive_as_separate_scenes(archive):
    add_orbit_provenance(archive)
    viewer, entry = opened(archive)
    assert [scene["track"] for scene in entry["scenes"]] == ["WRS2/P122", "WRS2/P123"]
    assert [scene["id"] for scene in entry["scenes"]] == [
        "landsat-c2-l2/A", "landsat-c2-l2/B",
    ]
    assert [scene["wrs_row"] for scene in entry["scenes"]] == [44, 44]
    assert all(scene["orbit_metadata_source"] == "stac" for scene in entry["scenes"])
    first = viewer.preview(entry["id"], time=0, bands="red", mode="single", size=256)
    second = viewer.preview(entry["id"], time=1, bands="red", mode="single", size=256)
    assert first["valid_fraction"] == 39 / 40
    assert second["valid_fraction"] == 1


def test_conflicting_track_metadata_is_not_shown_as_a_valid_archive(archive):
    add_orbit_provenance(archive, conflicting=True)
    catalog = Viewer(archive.parent.parent).catalog()
    assert catalog["archives"] == []
    assert len(catalog["errors"]) == 1
    assert "WRS system conflicts" in catalog["errors"][0]


def test_single_scaled_orientation_mask_and_georeferencing(archive):
    viewer, entry = opened(archive)
    preview = viewer.preview(entry["id"], bands="red", mode="single", size=256)
    stat = preview["stats"][0]
    assert stat["min"] == pytest.approx(1000 * 0.0000275 - 0.2)
    assert stat["max"] == pytest.approx(8000 * 0.0000275 - 0.2)
    assert stat["valid_fraction"] == 39 / 40
    assert preview["bounds"] == entry["bounds"]
    assert preview["geographic_bounds"] == entry["geographic_bounds"]
    rgba = pixels(preview)
    h, w = rgba.shape[:2]
    assert rgba[h // 2, w // 4, 0] < rgba[h // 2, 3 * w // 4, 0]
    assert rgba[h // 2, w // 2, 3] == 255
    assert np.count_nonzero(rgba[:, :, 3] == 0) > 0
    assert stat["unit"] == "1"


def test_zero_qa_is_valid_raw_category_and_missing_band_is_transparent(archive):
    viewer, entry = opened(archive)
    qa = viewer.preview(entry["id"], group="qa", bands="qa_radsat", mode="single", size=256)
    assert qa["stats"][0]["min"] == qa["stats"][0]["max"] == 0
    assert qa["legend"]["categories"] == [{"value": 0, "color": qa["legend"]["colors"][0], "count": 39}]
    assert pixels(qa)[:, :, 3].max() == 255
    missing = viewer.preview(entry["id"], group="thermal", bands="surface_temperature", mode="single")
    assert missing["stats"][0]["min"] is None
    assert missing["stats"][0]["present"] is False
    assert missing["valid_fraction"] == 0
    assert not pixels(missing)[:, :, 3].any()
    valid = viewer.preview(entry["id"], group="thermal", bands="surface_temperature", mode="valid")
    assert pixels(valid)[:, :, 3].max() == 255


@pytest.mark.parametrize("kwargs", [
    {"size": 255}, {"size": 1025}, {"size": True}, {"time": -1}, {"time": 2},
    {"group": "../optical"}, {"bands": "missing", "mode": "single"},
    {"bands": "red", "mode": "rgb"}, {"mode": "other"},
    {"group": "qa", "bands": "qa_pixel,qa_radsat,qa_aerosol", "mode": "rgb"},
])
def test_preview_rejects_invalid_requests(archive, kwargs):
    viewer, entry = opened(archive)
    with pytest.raises(ValueError):
        viewer.preview(entry["id"], **kwargs)


@pytest.mark.parametrize("corrupt", ["ledger_key", "ledger_time", "time_tail", "metadata_shape"])
def test_uncommitted_or_mismatched_metadata_never_enters_catalog(archive, corrupt):
    root = zarr.open_group(str(archive), mode="a")
    ledger_path = archive.with_name(archive.name + ".commits.json")
    if corrupt.startswith("ledger"):
        commits = json.loads(ledger_path.read_text())
        commits[0]["key" if corrupt == "ledger_key" else "datetime"] = (
            "landsat-c2-l2/WRONG" if corrupt == "ledger_key" else "1970-01-02T00:00:00Z"
        )
        atomic_json(ledger_path, commits)
    elif corrupt == "time_tail":
        root["optical"]["data"].resize((3, 7, 8, 5))
    else:
        root["thermal"]["present"].resize((1, 1))
    catalog = Viewer(archive.parent.parent).catalog()
    assert catalog["counts"]["archives"] == 0
    assert len(catalog["errors"]) == 1


def test_own_preview_and_catalog_are_serialized_without_losing_archive(archive, monkeypatch):
    viewer, entry = opened(archive)
    started = threading.Event()
    release = threading.Event()
    render = viewer._render

    def paused(*args):
        started.set()
        assert release.wait(timeout=5)
        return render(*args)

    monkeypatch.setattr(viewer, "_render", paused)
    with ThreadPoolExecutor(max_workers=2) as executor:
        rendering = executor.submit(viewer.preview, entry["id"], bands="red", mode="single")
        assert started.wait(timeout=5)
        refreshing = executor.submit(viewer.catalog)
        release.set()
        assert rendering.result(timeout=10)["stats"]
        assert refreshing.result(timeout=10)["counts"]["archives"] == 1
    assert viewer.preview(entry["id"], bands="red", mode="single")["stats"]


def test_writer_lock_is_respected_even_when_preview_is_cached(archive):
    viewer, entry = opened(archive)
    viewer.preview(entry["id"], bands="red", mode="single")
    with FileLock(str(archive) + ".lock", timeout=0):
        from filelock import Timeout
        with pytest.raises(Timeout):
            viewer.preview(entry["id"], bands="red", mode="single")


def test_http_routes_host_origin_and_invalid_parameters(archive):
    with create_server(archive.parent.parent, port=0) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/api/catalog") as response:
                assert json.load(response)["counts"]["variables"] == 19
            for route, headers, status in [
                ("/../../pyproject.toml", {}, 404),
                ("/api/catalog", {"Host": "evil.example"}, 403),
                ("/api/catalog", {"Origin": "https://evil.example"}, 403),
                ("/api/preview?archive=missing", {}, 400),
                ("/api/preview?archive=x&size=256&size=512", {}, 400),
            ]:
                with pytest.raises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(urllib.request.Request(base + route, headers=headers))
                assert raised.value.code == status
        finally:
            server.shutdown()
            worker.join(timeout=5)
