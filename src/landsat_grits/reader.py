"""Explicit common-band view for TM/ETM+/OLI surface reflectance."""

from pathlib import Path

from affine import Affine
import numpy as np
import xarray as xr
import zarr

from .storage import open_group
from .observations import list_scenes, list_tracks

COMMON_REFLECTANCE_BANDS = ("blue", "green", "red", "nir08", "swir16", "swir22")
__all__ = ["list_scenes", "list_tracks", "open_scene", "open_track", "open_common_reflectance"]


def _selected_group(path, group, records, *, window=None, chunks="auto"):
    if not records:
        raise ValueError("No committed scene matches the requested track/frame/scene")
    ds = open_group(path, group, chunks=chunks)
    try:
        indices = [record["index"] for record in records]
        if ds.scene_id.isel(time=indices).values.tolist() != [r["scene_id"] for r in records]:
            raise ValueError("Archive changed while selecting scenes; stop ingestion before reading")
        selected = ds.isel(time=indices)
        for key in ("wrs_system", "wrs_path", "wrs_row"):
            selected = selected.assign_coords({key: ("time", [r[key] if r[key] is not None else -1
                                                            for r in records])})
            selected[key].attrs["unknown_value"] = -1
        selected = selected.assign_coords(track=("time", [r["track"] or "" for r in records]))
        if window is not None:
            if (not isinstance(window, (tuple, list)) or len(window) != 4
                    or any(type(value) is not int for value in window)):
                raise ValueError("window must be (row_off, col_off, height, width) integers")
            row, col, height, width = window
            if (row < 0 or col < 0 or height <= 0 or width <= 0
                    or row + height > ds.sizes["y"] or col + width > ds.sizes["x"]):
                raise ValueError("Window is outside the selected group's native grid")
            selected = selected.isel(x=slice(col, col + width), y=slice(row, row + height))
            transform = Affine(*ds.attrs["transform"]) * Affine.translation(col, row)
            selected.attrs["transform"] = list(transform)[:6]
            if "spatial_ref" in selected:
                selected["spatial_ref"].attrs = {
                    **selected["spatial_ref"].attrs,
                    "GeoTransform": " ".join(map(str, transform.to_gdal())),
                }
        selected.attrs.update({"scene_selection": "explicit", "mosaicked": False,
                               "orbit_unknown_value": "numeric=-1, track=empty string"})
        selected.set_close(ds.close)
        return selected
    except BaseException:
        ds.close()
        raise


def open_scene(path: str | Path, group="optical", *, scene_id: str | None = None,
               track: str | None = None, wrs_row: int | None = None,
               window=None, chunks="auto") -> xr.Dataset:
    """Open exactly one observation, preserving time length 1 and per-band masks.

    A scene ID is collection-scoped, as returned by list_scenes. Multiple matches
    are an error, including multiple dates on one track. No orbit/date gap filling
    or cloud masking occurs. Stop ingestion while this lazy dataset is in use.
    """
    records = list_scenes(path, track=track, wrs_row=wrs_row)
    if scene_id is not None:
        if not isinstance(scene_id, str) or not scene_id:
            raise ValueError("scene_id must be a nonempty collection-scoped identity")
        records = [record for record in records if record["scene_id"] == scene_id]
    if len(records) > 1:
        raise ValueError("Ambiguous scene selection; choose an explicit scene_id from list_scenes")
    return _selected_group(path, group, records, window=window, chunks=chunks)


def open_track(path: str | Path, track: str, group="optical", *, wrs_row: int | None = None,
               window=None, chunks="auto") -> xr.Dataset:
    """Open one WRS path time series; rows and scene identities remain separate.

    Row selects an along-track scene frame, not a second orbit identifier.
    This view never merges same-date frames into a composite image.
    """
    if track is None:
        raise ValueError("An explicit WRS track is required")
    return _selected_group(path, group, list_scenes(path, track=track, wrs_row=wrs_row),
                           window=window, chunks=chunks)


def open_common_reflectance(paths: list[str | Path], *, track: str | None = None,
                            wrs_row: int | None = None,
                            allow_mixed_tracks: bool = False) -> xr.Dataset:
    """Concatenate compatible grids, preserving scene identity and platform.

    Applies C2 scale/offset after masking fill values. This is band selection
    and calibration, NOT spectral-response harmonization or cloud masking.
    MSS L1 is deliberately rejected: it has neither SR nor these six bands.
    Known different WRS tracks require explicit allow_mixed_tracks=True; such a
    view still keeps every scene separate. Prefer track=... for one ground track.
    The caller must keep ingestion stopped while reading these stores.
    """
    if not paths:
        raise ValueError("At least one archive path is required")
    datasets = []
    baseline = None
    try:
        for path in paths:
            root = zarr.open_group(str(path), mode="r")
            product = root.attrs["contract"]["product"]
            if product not in {"tm-l2", "etm-l2", "oli-l2"}:
                raise ValueError("Common surface reflectance requires TM/ETM+/OLI Level-2")
            records = list_scenes(path, track=track, wrs_row=wrs_row)
            if not records:
                continue
            ds = _selected_group(path, "optical", records)
            datasets.append(ds)
            if baseline is None:
                baseline = ds
            elif (ds.attrs["crs"] != baseline.attrs["crs"]
                  or not np.array_equal(ds.x, baseline.x)
                  or not np.array_equal(ds.y, baseline.y)):
                raise ValueError("Common-band view requires identical CRS and pixel grids")
        if not datasets:
            raise ValueError("No committed scene matches the requested WRS track/frame")
        known_tracks = {value for ds in datasets for value in ds.track.values.tolist() if value}
        if len(known_tracks) > 1 and not allow_mixed_tracks:
            raise ValueError("Multiple WRS tracks; select track=... or explicitly allow_mixed_tracks=True")
        views = []
        for ds in datasets:
            selected = ds.sel(bands=list(COMMON_REFLECTANCE_BANDS))
            reflectance = (
                selected.data.where(selected.valid).astype("float32")
                * selected.scale.astype("float32") + selected.offset.astype("float32")
            )
            reflectance.attrs.update({"units": "1", "spectrally_harmonized": False})
            views.append(xr.Dataset({
                "reflectance": reflectance, "scene_id": selected.scene_id,
                "platform": selected.platform,
            }))
        result = xr.concat(views, dim="time", join="exact", data_vars="all",
                           coords="minimal", compat="equals").sortby(["time", "scene_id"])
        identities = result.scene_id.values.tolist()
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate scene identities across input archives")
        result.attrs.update({
            "crs": baseline.attrs["crs"], "cloud_masked": False,
            "spectrally_harmonized": False,
            "mosaicked": False, "mixed_tracks": len(known_tracks) > 1,
            "unknown_track_scenes": sum(not value for ds in datasets for value in ds.track.values.tolist()),
        })
        result.set_close(lambda: [ds.close() for ds in datasets])
        return result
    except BaseException:
        for ds in datasets:
            ds.close()
        raise
