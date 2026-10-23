"""Fixed MGRS extent grids, using the S1-GRiTS polygon and alignment rules."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
from affine import Affine
from pyproj import CRS, Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform as transform_geometry


_TILE_PATTERN = re.compile(r"^(\d{2})([C-HJ-NP-X])([A-HJ-NP-Z])([A-HJ-NP-V])$")


def _normalize_tile(tile_id: str) -> str:
    if not isinstance(tile_id, str):
        raise ValueError("MGRS tile_id must be a string.")
    tile_id = tile_id.strip().upper()
    match = _TILE_PATTERN.fullmatch(tile_id)
    if match is None or not 1 <= int(match.group(1)) <= 60:
        raise ValueError(f"Invalid MGRS tile_id: {tile_id!r}; expected e.g. '50RKV'.")
    return tile_id


def _resolution(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("resolution must be a finite positive number in metres.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("resolution must be a finite positive number in metres.") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError("resolution must be a finite positive number in metres.")
    return result


def _validate_geometry(geometry: Any) -> None:
    if geometry is None or geometry.is_empty:
        raise ValueError("MGRS geometry is missing or empty.")
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("MGRS geometry must be a Polygon or MultiPolygon.")
    if not geometry.is_valid:
        raise ValueError("MGRS geometry is invalid.")
    minx, miny, maxx, maxy = geometry.bounds
    if not all(math.isfinite(value) for value in geometry.bounds):
        raise ValueError("MGRS geometry has non-finite coordinates.")
    if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
        raise ValueError("MGRS geometry must have valid WGS84 longitude/latitude bounds.")
    if maxx - minx > 180:
        raise ValueError(
            "MGRS geometry crosses the antimeridian; split or repair the source "
            "geometry before creating this grid."
        )


@dataclass(frozen=True)
class GridSpec:
    """An immutable spatial grid; ``x`` increases and ``y`` decreases.

    ``transform`` uses rasterio/Affine order ``[a, b, c, d, e, f]`` at pixel
    edges; ``x`` and ``y`` are pixel centres. ``geometry`` is the original tile polygon
    in WGS84, for source queries; the raster covers its snapped UTM bounds.
    """

    tile_id: str
    crs: str
    transform: Affine
    width: int
    height: int
    resolution: float
    geometry: dict[str, Any]
    source_path: str
    source_sha256: str

    def __post_init__(self) -> None:
        if self.tile_id != _normalize_tile(self.tile_id):
            raise ValueError("GridSpec.tile_id must be canonical uppercase MGRS.")
        resolution = _resolution(self.resolution)
        object.__setattr__(self, "resolution", resolution)
        for name in ("width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value <= 0:
                raise ValueError(f"GridSpec.{name} must be a positive integer.")
        if not isinstance(self.transform, Affine):
            raise ValueError("GridSpec.transform must be an affine.Affine.")
        values = list(self.transform)[:6]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("GridSpec.transform must be finite.")
        if (
            self.transform.b != 0
            or self.transform.d != 0
            or not math.isclose(self.transform.a, resolution, rel_tol=0, abs_tol=1e-9)
            or not math.isclose(self.transform.e, -resolution, rel_tol=0, abs_tol=1e-9)
        ):
            raise ValueError("GridSpec.transform must describe a north-up grid at resolution.")
        zone = int(self.tile_id[:2])
        expected_epsg = (32700 if self.tile_id[2] < "N" else 32600) + zone
        if CRS.from_user_input(self.crs) != CRS.from_epsg(expected_epsg):
            raise ValueError("GridSpec.crs must be the MGRS tile's UTM CRS.")
        try:
            geometry = shape(self.geometry)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("GridSpec.geometry must be a WGS84 GeoJSON geometry.") from exc
        _validate_geometry(geometry)
        if not isinstance(self.source_path, str) or not self.source_path:
            raise ValueError("GridSpec.source_path must identify the source GeoParquet.")
        if not isinstance(self.source_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.source_sha256
        ):
            raise ValueError("GridSpec.source_sha256 must be a lowercase SHA-256 digest.")

    @property
    def x(self) -> np.ndarray:
        return (
            self.transform.c
            + (np.arange(self.width, dtype=np.float64) + 0.5) * self.resolution
        )

    @property
    def y(self) -> np.ndarray:
        return (
            self.transform.f
            - (np.arange(self.height, dtype=np.float64) + 0.5) * self.resolution
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tile_id": self.tile_id,
            "crs": self.crs,
            "transform": [float(value) for value in list(self.transform)[:6]],
            "width": int(self.width),
            "height": int(self.height),
            "resolution": float(self.resolution),
            "geometry": mapping(shape(self.geometry)),
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GridSpec:
        values = dict(data)
        transform = values.get("transform")
        if not isinstance(transform, (tuple, list)) or len(transform) != 6:
            raise ValueError("GridSpec transform must contain six affine coefficients.")
        values["transform"] = Affine(*transform)
        if "geometry" in values:
            values["geometry"] = mapping(shape(values["geometry"]))
        return cls(**values)


def load_grid(
    mgrs_path: str | Path, tile_id: str, resolution: float
) -> GridSpec:
    """Read one tile and snap its UTM bounds outward, exactly as S1-GRiTS.

    The extent is determined before querying scenes, so years, sensors and
    scene availability cannot change the grid. No reprojection of imagery is
    performed here. A missing tile is an error: the supplied S1-GRiTS table
    is a coverage subset, not a guarantee of global MGRS coverage.
    """
    tile_id = _normalize_tile(tile_id)
    resolution = _resolution(resolution)
    path = Path(mgrs_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"MGRS source is not a file: {path}")
    try:
        table = gpd.read_parquet(path)
    except Exception as exc:
        raise ValueError(f"Cannot read MGRS GeoParquet: {path}") from exc
    if "mgrs_tile_id" not in table.columns:
        raise ValueError("MGRS GeoParquet is missing the 'mgrs_tile_id' column.")
    if table.crs is None:
        raise ValueError("MGRS GeoParquet must declare its geometry CRS.")
    selected = table.loc[table["mgrs_tile_id"] == tile_id]
    if len(selected) == 0:
        raise ValueError(f"MGRS tile {tile_id} is absent from {path}.")
    if len(selected) != 1:
        raise ValueError(f"MGRS tile {tile_id} has duplicate rows in {path}.")
    geometry = selected.to_crs("EPSG:4326").geometry.iloc[0]
    _validate_geometry(geometry)
    zone = int(tile_id[:2])
    epsg = (32700 if tile_id[2] < "N" else 32600) + zone
    if "utm_epsg" in selected.columns:
        try:
            declared_epsg = float(selected.iloc[0]["utm_epsg"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"MGRS tile {tile_id} has an invalid utm_epsg value.") from exc
        if declared_epsg != epsg:
            raise ValueError(
                f"MGRS tile {tile_id} declares utm_epsg={declared_epsg}; expected {epsg}."
            )
    crs = f"EPSG:{epsg}"
    project = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    projected = transform_geometry(project, geometry)
    if not projected.is_valid or not all(
        math.isfinite(value) for value in projected.bounds
    ):
        raise ValueError(f"MGRS tile {tile_id} cannot be projected to {crs}.")
    minx, miny, maxx, maxy = projected.bounds
    minx = math.floor(minx / resolution) * resolution
    miny = math.floor(miny / resolution) * resolution
    maxx = math.ceil(maxx / resolution) * resolution
    maxy = math.ceil(maxy / resolution) * resolution
    width = int(round((maxx - minx) / resolution))
    height = int(round((maxy - miny) / resolution))
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return GridSpec(
        tile_id=tile_id,
        crs=crs,
        transform=Affine(resolution, 0.0, minx, 0.0, -resolution, maxy),
        width=width,
        height=height,
        resolution=resolution,
        geometry=mapping(geometry),
        source_path=str(path),
        source_sha256=digest.hexdigest(),
    )
