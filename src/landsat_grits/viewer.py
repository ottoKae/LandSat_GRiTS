"""Read-only local archive explorer, with bounded, georeferenced PNG previews.

The display is a nearest-neighbour overview, not an analysis raster. Values and
percentiles are sampled before reprojection; alpha always comes from ``valid``.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import struct
import threading
from urllib.parse import parse_qs, urlsplit
import zlib

from affine import Affine
from filelock import FileLock, Timeout
import numpy as np
from rasterio.enums import Resampling
from rasterio.transform import array_bounds, from_bounds
from rasterio.warp import reproject, transform_bounds
from rasterio.warp import transform_geom
from shapely.geometry import shape
import zarr

from .bands import PRODUCTS
from .observations import read_observations


def _png(rgba: np.ndarray) -> str:
    """Encode an RGBA8 image without an additional image-library dependency."""
    height, width, _ = rgba.shape

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    rows = b"".join(b"\x00" + row.tobytes() for row in rgba)
    data = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _color(value: int) -> tuple[int, int, int]:
    # Stable colors for packed QA values, including valid zero. Not a bit decoder.
    digest = hashlib.sha256(str(value).encode("ascii")).digest()
    return tuple(48 + int(channel) * 192 // 256 for channel in digest[:3])


def _hex(color) -> str:
    return "#" + "".join(f"{int(channel):02x}" for channel in color)


def _bounds(crs, transform, width, height):
    native = array_bounds(height, width, transform)
    geographic = transform_bounds(crs, "EPSG:4326", *native, densify_pts=21)
    if geographic[1] < -85.05112878 or geographic[3] > 85.05112878:
        raise ValueError("Archive lies outside the Web Mercator display latitude range")
    projected = transform_bounds(crs, "EPSG:3857", *native, densify_pts=21)
    return list(projected), list(geographic)


def _extent_kind(grid):
    """Distinguish a complete snapped MGRS extent from a retained pilot window."""
    geometry = shape(transform_geom("EPSG:4326", grid["crs"], grid["geometry"]))
    west, south, east, north = geometry.bounds
    resolution = grid["resolution"]
    expected = [math.floor(west / resolution) * resolution,
                math.floor(south / resolution) * resolution,
                math.ceil(east / resolution) * resolution,
                math.ceil(north / resolution) * resolution]
    actual = array_bounds(grid["height"], grid["width"], Affine(*grid["transform"]))
    return "full_grid" if np.allclose(actual, expected, rtol=0, atol=1e-6) else "window"


class Viewer:
    """Discover committed local archives and render previews under writer locks."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Archive root must be a directory")
        self._paths: dict[str, Path] = {}
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._mutex = threading.RLock()
        self._read_locks = {}
        self._render_slots = threading.BoundedSemaphore(2)

    def _reader_lock(self, path):
        # FileLock represents the archive writer protocol. Serialize our own
        # reads first so a catalog refresh never mistakes a preview for a writer.
        with self._mutex:
            return self._read_locks.setdefault(path, threading.RLock())

    def _inside(self, path: Path) -> Path:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.root):
            raise ValueError("Archive link escapes the selected root")
        return resolved

    def _archives(self):
        if self.root.name.endswith(".zarr"):
            yield self.root
            return
        for parent, directories, _ in os.walk(self.root, followlinks=False):
            retained = []
            for name in sorted(directories):
                path = Path(parent) / name
                if path.is_symlink() or path.is_junction():
                    # Do not follow junctions either; os.walk's followlinks only
                    # describes symbolic links on some Windows Python versions.
                    continue
                if name.endswith(".zarr"):
                    yield path
                else:
                    retained.append(name)
            directories[:] = retained

    def _check_links(self, path):
        self._inside(path)
        for parent, directories, files in os.walk(path, followlinks=False):
            for name in directories + files:
                candidate = Path(parent) / name
                if candidate.is_symlink() or candidate.is_junction():
                    raise ValueError("Linked archive members are not supported")
        ledger = path.with_name(path.name + ".commits.json")
        # A root that is itself one archive still needs its sibling ledger.
        if ledger.is_symlink() or ledger.is_junction():
            raise ValueError("Linked commit ledgers are not supported")
        provenance = path.with_name(path.name + ".provenance")
        if provenance.is_symlink() or provenance.is_junction():
            raise ValueError("Linked provenance directories are not supported")
        if provenance.is_dir():
            for member in provenance.iterdir():
                if member.is_symlink() or member.is_junction():
                    raise ValueError("Linked provenance members are not supported")

    @staticmethod
    def _open(path):
        ledger = path.with_name(path.name + ".commits.json")
        with ledger.open(encoding="utf-8") as stream:
            commits = json.load(stream)
        if not isinstance(commits, list) or not commits:
            raise ValueError("Archive has no committed scenes")
        root = zarr.open_group(str(path), mode="r")
        contract = root.attrs["contract"]
        if contract["dimension_order"] != ["time", "bands", "x", "y"]:
            raise ValueError("Unsupported archive dimension order")
        expected = len(commits)
        scene_identity = None
        for name in contract["groups"]:
            group = root[name]
            if group["data"].shape[0] != expected or group["time"].shape != (expected,):
                raise ValueError("Uncommitted data; finish or resume ingestion before viewing")
            count = len(contract["groups"][name])
            if len(group["data"].shape) != 4 or group["data"].shape[1] != count:
                raise ValueError("Data shape does not match the band contract")
            for field in ("present", "scale", "offset", "source_band", "source_dtype"):
                if group[field].shape != (expected, count):
                    raise ValueError(f"Invalid {name}/{field} metadata shape")
            if group["valid"].shape != group["data"].shape:
                raise ValueError("Data and validity shapes differ")
            identity = (group["scene_id"][:].tolist(), group["time"][:].tolist())
            if scene_identity is not None and identity != scene_identity:
                raise ValueError("Group scene coordinates do not match")
            if len(identity[0]) != expected or group["platform"].shape != (expected,):
                raise ValueError("Invalid scene metadata shape")
            scene_identity = identity
        if scene_identity is None:
            raise ValueError("Archive contains no variable groups")
        for index, commit in enumerate(commits):
            expected_key = scene_identity[0][index]
            instant = datetime.fromisoformat(commit["datetime"].replace("Z", "+00:00"))
            if instant.tzinfo is None:
                raise ValueError("Commit datetime must include its timezone")
            epoch_delta = instant - datetime(1970, 1, 1, tzinfo=timezone.utc)
            microseconds = ((epoch_delta.days * 86400 + epoch_delta.seconds) * 1_000_000
                            + epoch_delta.microseconds)
            if commit["key"] != expected_key or microseconds != scene_identity[1][index]:
                raise ValueError("Commit ledger does not match scene coordinates")
        return root, commits

    def catalog(self) -> dict:
        result = {"root_label": self.root.name, "archives": [], "errors": []}
        paths = {}
        for path in self._archives():
            relative = path.relative_to(self.root).as_posix()
            relative = path.name if relative == "." else relative
            try:
                self._check_links(path)
                with self._reader_lock(path), FileLock(str(path) + ".lock", timeout=0):
                    root, commits = self._open(path)
                    observations = read_observations(path, root, commits)
                    attrs = dict(root.attrs)
                    grid = attrs["grid"]
                    contract = attrs["contract"]
                    product_key = contract["product"]
                    product = PRODUCTS.get(product_key)
                    projected, geographic = _bounds(
                        grid["crs"], Affine(*grid["transform"]), grid["width"], grid["height"],
                    )
                    identifier = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
                    entry = {
                        "id": identifier, "relative_path": relative,
                        "tile_id": grid["tile_id"], "product": product_key,
                        "collection": attrs.get("collection", ""),
                        "sensor": product.sensor if product else product_key,
                        "crs": grid["crs"], "bounds": projected,
                        "geographic_bounds": geographic, "geometry": grid["geometry"],
                        "shape": [grid["width"], grid["height"]],
                        "extent_kind": _extent_kind(grid),
                        "scenes": [{**scene, "id": scene["scene_id"]}
                                   for scene in observations], "groups": [],
                    }
                    for name, metadata in contract["groups"].items():
                        group = root[name]
                        band_names = group["bands"][:].tolist()
                        if band_names != [band["name"] for band in metadata]:
                            raise ValueError("Band coordinates do not match the archive contract")
                        width, height = group["data"].shape[2:]
                        transform = Affine(*group.attrs["transform"])
                        group_bounds, group_geo = _bounds(
                            group.attrs["crs"], transform, width, height,
                        )
                        source_bands = group["source_band"][:]
                        present = group["present"][:]
                        group_entry = {
                            "name": name, "resolution": abs(transform.a),
                            "shape": [width, height], "bounds": group_bounds,
                            "geographic_bounds": group_geo, "bands": [], "presets": [],
                        }
                        for index, band in enumerate(metadata):
                            group_entry["bands"].append({
                                "name": band["name"], "kind": band["kind"],
                                "unit": band["unit"], "dtype": str(group["data"].dtype),
                                "source_band": str(source_bands[0, index]) or band["source_band"],
                                "source_bands": source_bands[:, index].tolist(),
                                "present": present[:, index].tolist(),
                            })
                        if name == "optical":
                            for key, label, channels in (
                                ("natural", "自然色 · 红 / 绿 / 蓝", ["red", "green", "blue"]),
                                ("vegetation", "假彩色 · 近红外 / 红 / 绿", ["nir08", "red", "green"]),
                                ("swir", "短波红外 · SWIR2 / NIR / 红", ["swir22", "nir08", "red"]),
                            ):
                                if set(channels).issubset(band_names):
                                    group_entry["presets"].append({
                                        "name": key, "label": label, "bands": channels,
                                    })
                        entry["groups"].append(group_entry)
                    result["archives"].append(entry)
                    paths[identifier] = path
            except (OSError, ValueError, KeyError, TypeError, Timeout) as exc:
                reason = "Archive is being written" if isinstance(exc, Timeout) else str(exc)
                result["errors"].append(f"{relative}: {reason}")
        with self._mutex:
            self._paths = paths
        archives = result["archives"]
        result["counts"] = {
            "archives": len(archives),
            "scenes": sum(len(entry["scenes"]) for entry in archives),
            "variables": sum(len(group["bands"]) for entry in archives for group in entry["groups"]),
            "tiles": len({entry["tile_id"] for entry in archives}),
        }
        return result

    def preview(self, archive: str, time: int = 0, group: str = "optical",
                bands: str = "red,green,blue", mode: str = "rgb", size: int = 640) -> dict:
        if type(size) is not int or not 256 <= size <= 1024:
            raise ValueError("Preview size must be an integer between 256 and 1024")
        if type(time) is not int or time < 0:
            raise ValueError("Scene index must be a nonnegative integer")
        if mode not in {"rgb", "single", "valid"}:
            raise ValueError("Unknown display mode")
        selected = bands.split(",") if isinstance(bands, str) else []
        if len(selected) != (3 if mode == "rgb" else 1) or any(not b for b in selected):
            raise ValueError("RGB needs three bands; single and valid need one band")
        with self._mutex:
            path = self._paths.get(archive)
        if path is None:
            raise ValueError("Unknown archive; refresh the catalog")
        self._inside(path)
        if not self._render_slots.acquire(blocking=False):
            raise TimeoutError("Two previews are rendering; retry shortly")
        try:
            with self._reader_lock(path), FileLock(str(path) + ".lock", timeout=0):
                root, commits = self._open(path)
                if time >= len(commits) or group not in root.attrs["contract"]["groups"]:
                    raise ValueError("Unknown scene or variable group")
                raster = root[group]
                names = raster["bands"][:].tolist()
                if not set(selected).issubset(names):
                    raise ValueError("Requested band is not in this variable group")
                metadata = {band["name"]: band for band in raster.attrs["band_metadata"]}
                if mode == "rgb" and any(metadata[b]["kind"] == "qa" for b in selected):
                    raise ValueError("QA bit fields are displayed as individual raw categories")
                ledger = path.with_name(path.name + ".commits.json").stat()
                version = (ledger.st_mtime_ns, ledger.st_size, root.attrs["contract_hash"])
                key = (archive, version, time, group, bands, mode, size)
                with self._mutex:
                    if key in self._cache:
                        self._cache.move_to_end(key)
                        return self._cache[key]
                rendered = self._render(raster, time, selected, metadata, mode, size)
                rendered.update({"archive": archive, "time": time, "group": group,
                                 "bands": selected, "mode": mode})
                with self._mutex:
                    if key in self._cache:
                        self._cache_bytes -= len(self._cache[key]["image"])
                    self._cache[key] = rendered
                    self._cache_bytes += len(rendered["image"])
                    while len(self._cache) > 24 or self._cache_bytes > 32 * 1024 * 1024:
                        _, evicted = self._cache.popitem(last=False)
                        self._cache_bytes -= len(evicted["image"])
                return rendered
        finally:
            self._render_slots.release()

    @staticmethod
    def _render(group, time, selected, metadata, mode, size):
        width, height = group["data"].shape[2:]
        transform = Affine(*group.attrs["transform"])
        crs = group.attrs["crs"]
        projected, geographic = _bounds(crs, transform, width, height)
        factor = min(1.0, size / max(width, height))
        nx, ny = max(1, round(width * factor)), max(1, round(height * factor))
        ix = np.minimum(width - 1, ((np.arange(nx) + 0.5) * width / nx).astype(int))
        iy = np.minimum(height - 1, ((np.arange(ny) + 0.5) * height / ny).astype(int))
        sample_transform = transform * Affine.scale(width / nx, height / ny)
        dx, dy = projected[2] - projected[0], projected[3] - projected[1]
        output_width = max(1, round(size * dx / max(dx, dy)))
        output_height = max(1, round(size * dy / max(dx, dy)))
        output_transform = from_bounds(*projected, output_width, output_height)
        values, masks, stats = [], [], []
        names = group["bands"][:].tolist()
        for band in selected:
            index = names.index(band)
            present = bool(group["present"][time, index])
            raw = group["data"].oindex[time, index, ix, iy].T
            valid = group["valid"].oindex[time, index, ix, iy].T.astype(bool) & present
            scale, offset = float(group["scale"][time, index]), float(group["offset"][time, index])
            if metadata[band]["kind"] in {"qa", "dn", "thermal_dn", "panchromatic_dn"}:
                scale, offset = 1.0, 0.0
            physical = raw.astype("float32") * scale + offset
            valid &= np.isfinite(physical)
            sample = physical[valid]
            p02, p98 = np.percentile(sample, [2, 98]).tolist() if sample.size else [None, None]
            stats.append({
                "band": band, "source_band": str(group["source_band"][time, index]),
                "min": float(sample.min()) if sample.size else None,
                "max": float(sample.max()) if sample.size else None,
                "p02": p02, "p98": p98, "unit": metadata[band]["unit"],
                "valid_fraction": float(valid.mean()), "scale": scale, "offset": offset,
                "present": present, "sample_count": int(sample.size),
            })
            values.append(physical)
            masks.append(valid)
        rgba = np.zeros((ny, nx, 4), dtype="uint8")
        valid = np.logical_and.reduce(masks)
        note = "预览采用最近邻抽样；统计和 2–98% 拉伸来自抽样像元。valid 不等于无云。"
        legend = {"title": "2–98% 独立拉伸", "colors": [], "labels": [], "note": note}
        if mode == "valid":
            rgba[:, :, :3] = np.where(valid[:, :, None], [83, 210, 160], [61, 72, 89])
            rgba[:, :, 3] = 255
            legend.update({"title": "源像元有效性", "colors": ["#3d4859", "#53d2a0"],
                           "labels": ["无效 / 缺失", "有效（可能有云）"]})
        elif mode == "single" and metadata[selected[0]]["kind"] == "qa":
            categories, inverse, counts = np.unique(
                values[0][valid].astype("int64"), return_inverse=True, return_counts=True,
            )
            entries = []
            for value, count in zip(categories, counts, strict=True):
                color = _color(int(value))
                entries.append({"value": int(value), "color": _hex(color), "count": int(count)})
            if categories.size:
                palette = np.asarray([_color(int(value)) for value in categories], dtype="uint8")
                rgba[valid, :3] = palette[inverse]
            entries.sort(key=lambda entry: (-entry["count"], entry["value"]))
            legend.update({"title": "QA 原始整数类别（未解码位标志）",
                           "colors": [entry["color"] for entry in entries[:32]],
                           "labels": [str(entry["value"]) for entry in entries[:32]],
                           "categories": entries[:32], "category_count": len(entries),
                           "note": note + " QA=0 可有效；颜色只区分类别，不代表质量高低。"})
            rgba[:, :, 3] = valid.astype("uint8") * 255
        else:
            for index, data in enumerate(values):
                low, high = stats[index]["p02"], stats[index]["p98"]
                if low is None:
                    normalized = np.zeros(data.shape, dtype="float32")
                elif high == low:
                    normalized = np.full(data.shape, 0.5, dtype="float32")
                else:
                    normalized = np.clip((data - low) / (high - low), 0, 1)
                normalized = np.nan_to_num(normalized)
                if mode == "rgb":
                    rgba[:, :, index] = np.round(normalized * 255).astype("uint8")
                else:
                    # A small sequential blue/teal/yellow ramp, continuous values only.
                    stops = np.array([[19, 34, 67], [30, 132, 142], [238, 229, 139]])
                    for channel in range(3):
                        rgba[:, :, channel] = np.interp(
                            normalized, [0, 0.5, 1], stops[:, channel],
                        ).astype("uint8")
                    legend.update({"colors": [_hex(stop) for stop in stops],
                                   "labels": ["无有效值" if low is None else f"{low:.5g}",
                                              "", "" if high is None else f"{high:.5g}"]})
            rgba[:, :, 3] = valid.astype("uint8") * 255
        destination = np.zeros((4, output_height, output_width), dtype="uint8")
        reproject(
            source=rgba.transpose(2, 0, 1), destination=destination,
            src_transform=sample_transform, src_crs=crs,
            dst_transform=output_transform, dst_crs="EPSG:3857",
            resampling=Resampling.nearest, num_threads=1,
        )
        return {"image": _png(destination.transpose(1, 2, 0)), "bounds": projected,
                "geographic_bounds": geographic, "width": output_width, "height": output_height,
                "stats": stats, "legend": legend, "valid_fraction": float(valid.mean()),
                "statistics_scope": "sampled_source_preview"}


def create_server(root: str | Path, port: int = 8765):
    """Build a localhost-only server (port=0 is useful for integration tests)."""
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    viewer = Viewer(root)
    viewer.catalog()
    web = Path(__file__).with_name("web")
    static = {"/": ("index.html", "text/html; charset=utf-8"),
              "/index.html": ("index.html", "text/html; charset=utf-8"),
              "/app.js": ("app.js", "application/javascript; charset=utf-8"),
              "/style.css": ("style.css", "text/css; charset=utf-8")}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send(self, status, body, content_type="application/json; charset=utf-8"):
            payload = body if isinstance(body, bytes) else json.dumps(
                body, ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; "
                             "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
                             "base-uri 'none'; object-src 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            if host not in allowed or (origin is not None and origin not in {f"http://{h}" for h in allowed}):
                self._send(403, {"error": "Only same-origin localhost requests are accepted"})
                return
            request = urlsplit(self.path)
            try:
                if request.path == "/api/catalog":
                    self._send(200, viewer.catalog())
                elif request.path == "/api/preview":
                    params = parse_qs(request.query, keep_blank_values=True)
                    allowed_params = {"archive", "time", "group", "bands", "mode", "size"}
                    if set(params) - allowed_params or any(len(v) != 1 for v in params.values()):
                        raise ValueError("Invalid or repeated preview parameters")
                    kwargs = {key: values[0] for key, values in params.items()}
                    if "archive" not in kwargs:
                        raise ValueError("Archive is required")
                    for key in ("time", "size"):
                        if key in kwargs:
                            kwargs[key] = int(kwargs[key])
                    self._send(200, viewer.preview(**kwargs))
                elif request.path in static:
                    filename, mime = static[request.path]
                    self._send(200, (web / filename).read_bytes(), mime)
                else:
                    self._send(404, {"error": "Unknown local viewer route"})
            except (Timeout, TimeoutError):
                self._send(503, {"error": "Archive or renderer is busy; retry shortly"})
            except (ValueError, KeyError, TypeError) as exc:
                self._send(400, {"error": str(exc)})
            except OSError as exc:
                self._send(500, {"error": f"Unable to read local archive: {exc}"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.viewer = viewer
    return server


def serve(root: str | Path, port: int = 8765) -> None:
    """Serve the bundled local UI until interrupted."""
    with create_server(root, port) as server:
        print(f"Landsat GRiTS viewer: http://127.0.0.1:{server.server_port}", flush=True)
        print(f"Read-only archive root: {server.viewer.root}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
