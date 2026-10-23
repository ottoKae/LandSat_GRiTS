"""Windowed, single-writer, recoverable local Zarr scene archives.

Only scenes in the atomic commit sidecar are committed. All time chunks are
one scene, so truncating an uncommitted tail cannot remove committed pixels.
Readers must use ``open_group`` (or wait until ingestion finishes).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from filelock import FileLock
import numpy as np
from numcodecs import Blosc
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
import zarr

from .access import (
    raster_access, read_metadata, resolve_item_assets,
    snapshot_local_assets, validate_local_snapshot,
)
from .bands import (
    BandSpec, ProductSpec, QA_BANDS, group_resolutions, product_for_item,
    required_bands_for_item, source_band_for_item,
)
from .catalog import (
    _unsigned_metadata,
    item_datetime,
    scene_fingerprint,
    scene_key,
)
from .grid import GridSpec


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def band_groups(product: ProductSpec) -> dict[str, tuple[BandSpec, ...]]:
    result = {}
    for name, kinds in (
        ("optical", {"dn", "surface_reflectance"}),
        ("thermal", {"surface_temperature", "thermal_dn"}),
        ("pan", {"panchromatic_dn"}),
        ("ancillary", {"ancillary"}),
    ):
        selected = tuple(b for b in product.bands if b.kind in kinds)
        if selected:
            result[name] = selected
    result["qa"] = tuple(QA_BANDS[key] for key in product.qa_assets)
    return {name: bands for name, bands in result.items() if bands}


def _create(group, name, shape, chunks, dtype, dimensions, *, fill=None, data=None):
    array = group.create_array(
        name, shape=shape, chunks=chunks, dtype=dtype, fill_value=fill,
        compressor=Blosc(cname="zstd", clevel=5, shuffle=Blosc.SHUFFLE),
    )
    array.attrs["_ARRAY_DIMENSIONS"] = list(dimensions)
    if data is not None:
        array[:] = data
    return array


class SceneStore:
    def __init__(self, path: str | Path, product: ProductSpec, grid: GridSpec, chunk=512,
                 *, group_grids: dict[str, GridSpec] | None = None, access: dict | None = None):
        self.path = Path(path)
        if not 16 <= chunk <= 2048:
            raise ValueError("chunk must be between 16 and 2048 pixels")
        self.product, self.grid, self.chunk = product, grid, chunk
        self.groups = band_groups(product)
        self.access = access
        supplied = group_grids or {}
        if supplied and product.source != "usgs":
            raise ValueError("Per-group grids are only supported by native Level-1 contracts")
        if set(supplied) - set(self.groups):
            raise ValueError("Unexpected group grid")
        self.group_grids = {name: supplied.get(name, grid) for name in self.groups}
        for name, spatial in self.group_grids.items():
            if (spatial.tile_id != grid.tile_id or spatial.crs != grid.crs
                    or spatial.source_sha256 != grid.source_sha256
                    or json.dumps(spatial.geometry, sort_keys=True)
                    != json.dumps(grid.geometry, sort_keys=True)):
                raise ValueError("Group grids must refer to the same MGRS geometry and source")
            if product.source == "usgs" and spatial.resolution != group_resolutions(product)[name]:
                raise ValueError(f"{product.key}/{name} requires its published product resolution")
        self.commits_path = self.path.with_name(self.path.name + ".commits.json")
        self.provenance = self.path.with_name(self.path.name + ".provenance")
        self.lock = FileLock(str(self.path) + ".lock", timeout=0)
        grid_contract = grid.to_dict()
        grid_contract.pop("source_path", None)
        self.contract = {
            "schema_version": 1, "product": product.key, "grid": grid_contract,
            "groups": {k: [asdict(b) for b in v] for k, v in self.groups.items()},
            "dimension_order": ["time", "bands", "x", "y"],
            "resampling": "nearest", "values": "source DN", "chunk": chunk,
        }
        # Bind new archives to a tight source-pixel transform tolerance. Preserve
        # legacy 0.125-pixel contracts on resume rather than silently mixing policies.
        self.warp_tolerance = 1e-9
        if self.path.exists():
            previous = read_json(self.path / ".zattrs")["contract"]
            self.warp_tolerance = previous.get("warp_tolerance", 0.125)
            if "warp_tolerance" in previous:
                if self.warp_tolerance != 1e-9:
                    raise ValueError("Unsupported archive warp tolerance")
                self.contract["warp_tolerance"] = self.warp_tolerance
        else:
            self.contract["warp_tolerance"] = self.warp_tolerance
        if product.source == "usgs":
            self.contract["schema_version"] = 2
            self.contract["group_grids"] = {
                name: {k: v for k, v in spatial.to_dict().items() if k != "source_path"}
                for name, spatial in self.group_grids.items()
            }
        self.contract_hash = hashlib.sha256(
            json.dumps(self.contract, sort_keys=True).encode()
        ).hexdigest()

    def _initialize(self):
        if self.path.exists():
            return
        if self.commits_path.exists() and read_json(self.commits_path):
            raise ValueError("Commit ledger exists but the corresponding Zarr store is missing")
        staging = self.path.with_name(self.path.name + ".initializing-" + uuid.uuid4().hex)
        root = zarr.open_group(str(staging), mode="w", zarr_format=2)
        root.attrs.update({
            "contract": self.contract, "contract_hash": self.contract_hash,
            "grid": self.grid.to_dict(), "collection": self.product.collection,
            "commit_ledger": self.commits_path.name,
            "note": "Raw DN; use valid and present. No cloud masking or spectral harmonization.",
        })
        for name, bands in self.groups.items():
            grid = self.group_grids[name]
            group = root.create_group(name)
            count = len(bands)
            dtype = np.result_type(*[b.dtype for b in bands])
            fill = 0 if name == "qa" else bands[0].nodata
            if name != "qa" and any(b.nodata != fill for b in bands):
                raise ValueError(f"Mixed fill values within {name}")
            group.attrs.update({
                "crs": grid.crs,
                "transform": list(grid.transform)[:6],
                "raw_dn": True,
                "band_metadata": [asdict(b) for b in bands],
            })
            shape = (0, count, grid.width, grid.height)
            chunks = (1, 1, min(self.chunk, grid.width), min(self.chunk, grid.height))
            data = _create(group, "data", shape, chunks, dtype, ("time", "bands", "x", "y"),
                           fill=fill)
            data.attrs["grid_mapping"] = "spatial_ref"
            _create(group, "valid", shape, chunks, "bool", ("time", "bands", "x", "y"),
                    fill=False)
            _create(group, "bands", (count,), (count,), "U64", ("bands",),
                    data=np.asarray([b.name for b in bands], dtype="U64"))
            _create(group, "x", (grid.width,), (min(4096, grid.width),), "f8",
                    ("x",), data=grid.x)
            _create(group, "y", (grid.height,), (min(4096, grid.height),), "f8",
                    ("y",), data=grid.y)
            group["x"].attrs.update({"standard_name": "projection_x_coordinate", "units": "m"})
            group["y"].attrs.update({"standard_name": "projection_y_coordinate", "units": "m"})
            time = _create(group, "time", (0,), (1,), "i8", ("time",))
            time.attrs.update({"units": "microseconds since 1970-01-01", "calendar": "proleptic_gregorian"})
            for field, dt in (("scene_id", "U256"), ("platform", "U32")):
                _create(group, field, (0,), (1,), dt, ("time",))
            for field, dt, fill_value in (
                ("present", "bool", False), ("scale", "f8", None), ("offset", "f8", None),
                ("source_band", "U64", ""), ("source_dtype", "U16", ""),
            ):
                _create(group, field, (0, count), (1, count), dt, ("time", "bands"),
                        fill=fill_value)
            spatial = group.create_array("spatial_ref", shape=(), dtype="i4", fill_value=None)
            spatial[...] = 0
            spatial.attrs.update(rasterio.crs.CRS.from_string(grid.crs).to_dict())
            spatial.attrs.update({
                "_ARRAY_DIMENSIONS": [], "spatial_ref": rasterio.crs.CRS.from_string(grid.crs).to_wkt(),
                "crs_wkt": rasterio.crs.CRS.from_string(grid.crs).to_wkt(),
                "GeoTransform": " ".join(map(str, grid.transform.to_gdal())),
            })
        # Rename publishes only a fully initialized schema.
        staging.rename(self.path)
        atomic_json(self.commits_path, [])

    def _read_commits(self):
        commits = read_json(self.commits_path)
        if not isinstance(commits, list):
            raise ValueError("Invalid commit ledger: expected a list")
        keys, previous = set(), None
        fields = ("key", "datetime", "fingerprint", "provenance")
        for commit in commits:
            if (not isinstance(commit, dict)
                    or any(not isinstance(commit.get(field), str) for field in fields)):
                raise ValueError("Invalid commit ledger: malformed entry")
            key = commit["key"]
            prefix = self.product.collection + "/"
            if not key.startswith(prefix) or len(key) <= len(prefix) or len(key) > 256:
                raise ValueError("Invalid commit ledger: invalid scene identity")
            if (re.fullmatch(r"[0-9a-f]{64}", commit["fingerprint"]) is None
                    or commit["provenance"] != hashlib.sha256(key.encode()).hexdigest() + ".json"):
                raise ValueError("Invalid commit ledger: invalid fingerprint or provenance")
            try:
                dt = datetime.fromisoformat(commit["datetime"])
            except ValueError:
                raise ValueError("Invalid commit ledger: invalid observation time") from None
            if dt.utcoffset() != timedelta(0):
                raise ValueError("Invalid commit ledger: observation time must be UTC")
            order = (dt, key)
            if key in keys or (previous is not None and order < previous):
                raise ValueError("Invalid commit ledger: duplicate or unordered scenes")
            keys.add(key)
            previous = order
        return commits

    def _open(self, *, recover=False):
        root = zarr.open_group(str(self.path), mode="a" if recover else "r", zarr_format=2)
        if root.attrs.get("contract_hash") != self.contract_hash:
            raise ValueError("Existing archive grid/bands/chunks contract differs; use another output")
        if json.dumps(root.attrs.get("contract"), sort_keys=True) != json.dumps(
            self.contract, sort_keys=True
        ):
            raise ValueError("Unexpected archive contract metadata")
        if set(root.group_keys()) != set(self.groups):
            raise ValueError("Archive groups are missing or unexpected")
        required = {"data", "valid", "bands", "x", "y", "time", "scene_id", "platform",
                    "present", "scale", "offset", "source_band", "source_dtype", "spatial_ref"}
        for name, bands in self.groups.items():
            grid = self.group_grids[name]
            group = root[name]
            if set(group.array_keys()) != required:
                raise ValueError(f"Archive arrays are missing or unexpected in {name}")
            if (group.attrs.get("crs") != grid.crs
                    or group.attrs.get("transform") != list(grid.transform)[:6]
                    or group.attrs.get("raw_dn") is not True
                    or group.attrs.get("band_metadata") != [asdict(b) for b in bands]):
                raise ValueError(f"Unexpected scientific metadata in {name}")
            chunks = (1, 1, min(self.chunk, grid.width), min(self.chunk, grid.height))
            for field in ("data", "valid"):
                array = group[field]
                if array.attrs.get("_ARRAY_DIMENSIONS") != ["time", "bands", "x", "y"]:
                    raise ValueError(f"Unexpected dimensions for {name}/{field}")
                if array.shape[1:] != (len(bands), grid.width, grid.height):
                    raise ValueError(f"Unexpected spatial/band shape for {name}/{field}")
                if array.chunks != chunks:
                    raise ValueError(f"Unexpected chunks for {name}/{field}")
                dtype = np.result_type(*[b.dtype for b in bands]) if field == "data" else np.dtype("bool")
                fill = (0 if name == "qa" else bands[0].nodata) if field == "data" else False
                if array.dtype != dtype or array.fill_value != fill:
                    raise ValueError(f"Unexpected dtype/fill for {name}/{field}")
            for field in ("present", "scale", "offset", "source_band", "source_dtype"):
                if (group[field].attrs.get("_ARRAY_DIMENSIONS") != ["time", "bands"]
                        or group[field].shape[1:] != (len(bands),)
                        or group[field].chunks[0] != 1):
                    raise ValueError(f"Unexpected scene metadata layout for {name}/{field}")
            for field in ("time", "scene_id", "platform"):
                if (group[field].attrs.get("_ARRAY_DIMENSIONS") != ["time"]
                        or group[field].ndim != 1 or group[field].chunks[0] != 1):
                    raise ValueError(f"Unexpected scene coordinate layout for {name}/{field}")
            if (group["time"].dtype != np.dtype("i8")
                    or group["time"].attrs.get("units") != "microseconds since 1970-01-01"
                    or group["time"].attrs.get("calendar") != "proleptic_gregorian"):
                raise ValueError(f"Unexpected time metadata in {name}")
            if group["bands"][:].tolist() != [b.name for b in bands]:
                raise ValueError(f"Unexpected band order in {name}")
            if (not np.array_equal(group["x"][:], grid.x)
                    or not np.array_equal(group["y"][:], grid.y)):
                raise ValueError(f"Unexpected spatial coordinates in {name}")
        if not self.commits_path.exists():
            lengths = [a.shape[0] for _, g in root.groups() for _, a in g.arrays()
                       if a.attrs.get("_ARRAY_DIMENSIONS", [None])[0:1] == ["time"]]
            if any(lengths) or not recover:
                raise ValueError("Missing commit ledger; refusing to guess committed scenes")
            atomic_json(self.commits_path, [])
        commits = self._read_commits()
        time_arrays = [array for _, group in root.groups() for _, array in group.arrays()
                       if array.attrs.get("_ARRAY_DIMENSIONS", [])[:1] == ["time"]]
        # Validate every length before mutating any array. With the single-writer
        # protocol only one uncommitted scene can exist, even during partial resize.
        if any(array.shape[0] < len(commits) for array in time_arrays):
            raise ValueError("Archive is shorter than committed scene ledger")
        if any(array.shape[0] > len(commits) + 1 for array in time_arrays):
            raise ValueError("Recovery permits at most one uncommitted scene; inspect the ledger")
        tails = [array for array in time_arrays if array.shape[0] > len(commits)]
        if tails and not recover:
            raise ValueError("Uncommitted tail exists; resume ingest before reading")
        for array in tails:
            array.resize((len(commits), *array.shape[1:]))
        return root, commits

    @staticmethod
    def _resize(root, length):
        for _, group in root.groups():
            for _, array in group.arrays():
                if array.attrs.get("_ARRAY_DIMENSIONS", [])[:1] == ["time"]:
                    array.resize((length, *array.shape[1:]))

    def append(self, item: dict) -> bool:
        """Append one scene; return False for an identical already committed scene."""
        if product_for_item(item).key != self.product.key:
            raise ValueError("Scene product does not match the destination archive")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self._initialize()
            root, commits = self._open(recover=True)
            key, fingerprint = scene_key(item), scene_fingerprint(item)
            if len(key) > 256:
                raise ValueError("Scene identity exceeds storage string length")
            for commit in commits:
                if commit["key"] == key:
                    if commit["fingerprint"] != fingerprint:
                        raise ValueError(f"Source metadata changed for committed scene {key}")
                    return False
            dt = item_datetime(item).astimezone(timezone.utc)
            stamp = dt.isoformat()
            if commits and (dt, key) < (
                datetime.fromisoformat(commits[-1]["datetime"]), commits[-1]["key"]
            ):
                raise ValueError("Historical insertion requires a separate rebuild; cannot append out of order")
            required_bands = required_bands_for_item(item)
            absent = [b.asset_key for b in required_bands
                      if b.asset_key not in item["assets"]]
            if absent:
                raise ValueError(f"Missing required optical/native assets in {key}: {absent}")
            # Download metadata before changing the archive.
            asset_keys = [b.asset_key for bands in self.groups.values() for b in bands]
            resolved = resolve_item_assets(item, self.access, asset_keys=asset_keys)
            local_snapshot = snapshot_local_assets(resolved, asset_keys=asset_keys)
            provenance = {
                "item": item, "mtl": read_metadata({**item, "assets": resolved}, self.access),
                "rasters": {},
            }
            if self.product.source == "usgs" and not provenance["mtl"]:
                raise ValueError("Level-1 native DN archive requires original MTL metadata")
            index = len(commits)
            try:
                self._resize(root, index + 1)
                epoch = np.datetime64(dt.replace(tzinfo=None), "us").astype("int64")
                for name, bands in self.groups.items():
                    group = root[name]
                    group["time"][index] = epoch
                    group["scene_id"][index] = key
                    group["platform"][index] = item["properties"]["platform"]
                    present = np.zeros(len(bands), dtype="bool")
                    source_dtypes = np.full(len(bands), "", dtype="U16")
                    for bi, band in enumerate(bands):
                        asset = resolved.get(band.asset_key)
                        if asset is None:
                            continue
                        provenance["rasters"][band.asset_key] = self._write_band(
                            group, index, bi, band, asset, grid=self.group_grids[name]
                        )
                        source_dtypes[bi] = provenance["rasters"][band.asset_key]["dtype"]
                        present[bi] = True
                    group["scale"][index, :] = [b.scale for b in bands]
                    group["offset"][index, :] = [b.offset for b in bands]
                    group["source_band"][index, :] = [source_band_for_item(b, item) for b in bands]
                    group["source_dtype"][index, :] = source_dtypes
                    group["present"][index, :] = present
                source_file = hashlib.sha256(key.encode()).hexdigest() + ".json"
                validate_local_snapshot(local_snapshot)
                atomic_json(self.provenance / source_file, _unsigned_metadata(provenance))
                commit = {
                    "key": key, "datetime": stamp, "fingerprint": fingerprint,
                    "provenance": source_file,
                }
                atomic_json(self.commits_path, [*commits, commit])
            except BaseException:
                # An interrupt after ledger publication must not truncate a committed scene.
                self._resize(root, len(self._read_commits()))
                raise
            return True

    def _write_band(self, group, index, bi, band, asset, *, grid=None):
        grid = self.grid if grid is None else grid
        data, validity = group["data"], group["valid"]
        with raster_access(asset, self.access) as href, rasterio.Env(
            GDAL_HTTP_MAX_RETRY=3, GDAL_HTTP_RETRY_DELAY=2,
            GDAL_HTTP_TIMEOUT=90, GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        ):
            with rasterio.open(href) as source:
                if source.count != 1 or source.crs is None:
                    raise ValueError(f"Expected one georeferenced raster band: {band.asset_key}")
                target_dtype = data.dtype
                if not np.can_cast(source.dtypes[0], target_dtype, casting="safe"):
                    raise ValueError(f"Unsafe conversion: {source.dtypes[0]} -> {target_dtype}")
                if source.nodata is not None and source.nodata != band.nodata:
                    raise ValueError(f"Unexpected source nodata for {band.asset_key}: {source.nodata}")
                # Physical scales in source metadata are retained, but MSS radiance
                # coefficients must NOT be substituted for raw-DN scale 1.
                metadata = {
                    "dtype": source.dtypes[0], "nodata": source.nodata,
                    "crs": source.crs.to_string(), "transform": list(source.transform)[:6],
                    "shape": [source.height, source.width], "scales": list(source.scales),
                    "offsets": list(source.offsets), "tags": source.tags(),
                    "asset_metadata": asset,
                    "warp_tolerance": self.warp_tolerance,
                }
                fill = 0 if band.nodata is None else band.nodata
                vrt_options = dict(
                    crs=grid.crs, transform=grid.transform,
                    width=grid.width, height=grid.height,
                    resampling=Resampling.nearest, nodata=fill,
                    # GDAL/Rasterio rejects tolerance=0 in this VRT path.
                    tolerance=self.warp_tolerance,
                )
                effective_nodata = source.nodata if source.nodata is not None else band.nodata
                if effective_nodata is not None:
                    vrt_options["src_nodata"] = effective_nodata
                # An alpha band separates valid QA value 0 from pixels outside the footprint.
                vrt_options["add_alpha"] = True
                # add_alpha removes destination nodata; initialize both data and
                # alpha to zero explicitly, then restore each band's fill below.
                vrt_options["init_dest"] = "0"
                with WarpedVRT(source, **vrt_options) as vrt:
                    for row in range(0, grid.height, self.chunk):
                        height = min(self.chunk, grid.height - row)
                        for col in range(0, grid.width, self.chunk):
                            width = min(self.chunk, grid.width - col)
                            window = Window(col, row, width, height)
                            values = vrt.read(1, window=window)
                            valid = vrt.read(vrt.count, window=window) > 0
                            if effective_nodata is not None:
                                valid &= values != effective_nodata
                            values = np.where(valid, values, fill)
                            target = (index, bi, slice(col, col + width), slice(row, row + height))
                            data[target] = values.T
                            validity[target] = valid.T
                return metadata

    def audit(self) -> dict:
        with self.lock:
            root, commits = self._open()
            keys = [c["key"] for c in commits]
            missing = {}
            for name, group in root.groups():
                grid = self.group_grids[name]
                if list(group["data"].shape) != [
                    len(commits), len(self.groups[name]), grid.width, grid.height
                ]:
                    raise ValueError(f"Invalid data shape in {name}")
                if list(group["scene_id"][:]) != keys:
                    raise ValueError(f"Scene identities differ in {name}")
                expected_times = [
                    np.datetime64(c["datetime"].replace("+00:00", ""), "us").astype("int64")
                    for c in commits
                ]
                if not np.array_equal(group["time"][:], expected_times):
                    raise ValueError(f"Observation times differ in {name}")
                missing[name] = int(np.size(group["present"][:]) - np.count_nonzero(group["present"][:]))
                for _, array in group.arrays():
                    compressor = array.metadata.compressor
                    if array.ndim and (compressor is None or compressor.cname != "zstd"):
                        raise ValueError(f"Unexpected codec in {name}")
            for commit in commits:
                provenance = read_json(self.provenance / commit["provenance"])
                if scene_fingerprint(provenance["item"]) != commit["fingerprint"]:
                    raise ValueError("Provenance fingerprint differs")
            return {"path": str(self.path), "scenes": len(commits), "keys": keys,
                    "fingerprints": {c["key"]: c["fingerprint"] for c in commits},
                    "missing_optional_band_observations": missing,
                    "verification": "structure, codec, identities and provenance; not all pixels"}


def open_group(path: str | Path, group="optical", *, chunks="auto"):
    """Open committed data with xarray; zero DN/QA is not automatically masked.

    Use ``data.where(valid)`` for missing pixels. A snapshot opened while another
    writer is active is unsupported; ingestion and reading must be separate phases.
    """
    import xarray as xr

    path = Path(path)
    with FileLock(str(path) + ".lock", timeout=0):
        commits = read_json(path.with_name(path.name + ".commits.json"))
        ds = xr.open_zarr(str(path), group=group, consolidated=False,
                          mask_and_scale=False, chunks=chunks)
        if ds.sizes["time"] != len(commits):
            ds.close()
            raise ValueError("Uncommitted data; resume ingestion before reading")
        return ds
