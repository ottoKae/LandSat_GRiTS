from __future__ import annotations

import hashlib
import json

import geopandas as gpd
import numpy as np
import pytest
from affine import Affine
from shapely.geometry import Point, Polygon, box

from landsat_grits.grid import GridSpec, load_grid


def _write_tile(tmp_path, *, tile="31NEA", crs="EPSG:32631", duplicate=False):
    source = gpd.GeoDataFrame(
        {"mgrs_tile_id": [tile]},
        geometry=[box(500101, 2000101, 500159, 2000159)],
        crs=crs,
    ).to_crs("EPSG:4326")
    if duplicate:
        source = source.loc[[0, 0]]
    path = tmp_path / "mgrs.parquet"
    source.to_parquet(path, index=False)
    return path


def test_fixed_grid_uses_outward_snapped_utm_bounds(tmp_path):
    path = _write_tile(tmp_path)
    grid = load_grid(path, "31nea", 30)
    assert grid.tile_id == "31NEA"
    assert grid.crs == "EPSG:32631"
    assert grid.transform == Affine(30, 0, 500100, 0, -30, 2000160)
    assert (grid.width, grid.height) == (2, 2)
    np.testing.assert_array_equal(grid.x, [500115.0, 500145.0])
    np.testing.assert_array_equal(grid.y, [2000145.0, 2000115.0])
    assert grid.x.dtype == grid.y.dtype == np.dtype("float64")
    assert grid.source_path == str(path.resolve())
    assert grid.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert grid.geometry["type"] == "Polygon"


def test_grid_json_roundtrip_and_southern_hemisphere(tmp_path):
    path = _write_tile(tmp_path, tile="55HFA", crs="EPSG:32755")
    grid = load_grid(path, "55HFA", 60)
    restored = GridSpec.from_dict(json.loads(json.dumps(grid.to_dict())))
    assert restored == grid
    assert restored.crs == "EPSG:32755"
    assert restored.resolution == 60.0
    np.testing.assert_array_equal(restored.x, grid.x)
    np.testing.assert_array_equal(restored.y, grid.y)


@pytest.mark.parametrize("resolution", [0, -30, float("nan"), float("inf"), True, "bad"])
def test_invalid_resolution_rejected_before_file_access(tmp_path, resolution):
    with pytest.raises(ValueError, match="resolution"):
        load_grid(tmp_path / "absent.parquet", "31NEA", resolution)


@pytest.mark.parametrize("tile", ["", "00NEA", "61NEA", "31IEA", "31NEZ", "31NEAA", None])
def test_invalid_tile_rejected_before_file_access(tmp_path, tile):
    with pytest.raises(ValueError, match="tile_id"):
        load_grid(tmp_path / "absent.parquet", tile, 30)


def test_missing_and_duplicate_tile_rejected(tmp_path):
    path = _write_tile(tmp_path)
    with pytest.raises(ValueError, match="absent"):
        load_grid(path, "32NEA", 30)
    _write_tile(tmp_path, duplicate=True)
    with pytest.raises(ValueError, match="duplicate"):
        load_grid(path, "31NEA", 30)


@pytest.mark.parametrize(
    ("geometry", "message"),
    [
        (None, "missing or empty"),
        (Polygon(), "missing or empty"),
        (Point(3, 1), "Polygon or MultiPolygon"),
        (Polygon([(0, 0), (1, 1), (0, 1), (1, 0)]), "invalid"),
        (box(-179, 1, 179, 2), "antimeridian"),
    ],
)
def test_unusable_geometry_rejected(tmp_path, geometry, message):
    path = tmp_path / "bad.parquet"
    gpd.GeoDataFrame(
        {"mgrs_tile_id": ["31NEA"]}, geometry=[geometry], crs="EPSG:4326"
    ).to_parquet(path, index=False)
    with pytest.raises(ValueError, match=message):
        load_grid(path, "31NEA", 30)


def test_source_requires_crs_and_tile_column(tmp_path):
    path = tmp_path / "bad.parquet"
    gpd.GeoDataFrame(
        {"wrong_column": ["31NEA"]}, geometry=[box(3, 1, 3.1, 1.1)], crs="EPSG:4326"
    ).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="mgrs_tile_id"):
        load_grid(path, "31NEA", 30)
    gpd.GeoDataFrame(
        {"mgrs_tile_id": ["31NEA"]}, geometry=[box(3, 1, 3.1, 1.1)]
    ).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="CRS"):
        load_grid(path, "31NEA", 30)


def test_grid_rejects_invalid_serialized_spatial_metadata(tmp_path):
    grid = load_grid(_write_tile(tmp_path), "31NEA", 30)
    data = grid.to_dict()
    data["transform"] = [30, 0, 500100]
    with pytest.raises(ValueError, match="six"):
        GridSpec.from_dict(data)
    data = grid.to_dict()
    data["crs"] = "EPSG:32731"
    with pytest.raises(ValueError, match="UTM"):
        GridSpec.from_dict(data)


def test_declared_utm_must_agree_with_tile_id(tmp_path):
    path = _write_tile(tmp_path)
    source = gpd.read_parquet(path)
    source["utm_epsg"] = 32731
    source.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="utm_epsg"):
        load_grid(path, "31NEA", 30)
