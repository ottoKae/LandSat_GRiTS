"""Prepare selected tile polygons without depending on a sibling repository."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import re
import tempfile
from xml.etree import ElementTree as ET
import zipfile

from filelock import FileLock
import geopandas as gpd
import requests
from shapely.geometry import MultiPolygon, Polygon
from shapely import wkt

from .grid import _normalize_tile, _validate_geometry

GRID_URL = (
    "https://sentiwiki.copernicus.eu/__attachments/"
    "a_69a662aa1cb30487acc66009f09b1dcfa9e4e32f92af1c3b2dd1fc8a3c011738/"
    "S2A_OPER_GIP_TILPAR_MPC__20151209T095117_V20150622T000000_21000101T000000_B00.zip"
)
GRID_SHA256 = "67e222547cfea235f69d1dd35317babb0296c8c61556c198fc9f1e2dec7bcf29"
_LIMIT = 128 * 1024 * 1024
_NS = "{http://www.opengis.net/kml/2.2}"


def _source_bytes(source):
    if source is not None:
        path = Path(source).resolve(strict=True)
        if path.stat().st_size > _LIMIT:
            raise ValueError("Grid source exceeds 128 MiB")
        return path.read_bytes(), path.name, ""
    chunks, size = [], 0
    with requests.get(GRID_URL, stream=True, timeout=(15, 120)) as response:
        response.raise_for_status()
        for chunk in response.iter_content(1024 * 1024):
            size += len(chunk)
            if size > 16 * 1024 * 1024:
                raise ValueError("Official grid ZIP exceeds 16 MiB")
            chunks.append(chunk)
    raw = b"".join(chunks)
    if hashlib.sha256(raw).hexdigest() != GRID_SHA256:
        raise ValueError("Official grid checksum changed; inspect the source before updating")
    return raw, GRID_URL.rsplit("/", 1)[1], GRID_URL


def _polygons(raw, tiles):
    stream = io.BytesIO(raw)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            members = [m for m in archive.infolist() if m.filename.lower().endswith(".kml")]
            if len(members) != 1 or members[0].file_size > _LIMIT:
                raise ValueError("Grid ZIP must contain one KML no larger than 128 MiB")
            raw = archive.read(members[0])
    try:
        document = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Grid KML must use UTF-8 encoding") from None
    if "\x00" in document:
        raise ValueError("Grid KML must use UTF-8 encoding without NUL characters")
    if "<!DOCTYPE" in document or "<!ENTITY" in document:
        raise ValueError("DTD and entity declarations are not accepted in grid KML")
    selected = {}
    for _, element in ET.iterparse(io.StringIO(document), events=("end",)):
        if element.tag != _NS + "Placemark":
            continue
        name = (element.findtext(_NS + "name") or "").strip()
        if name in tiles:
            if name in selected:
                raise ValueError(f"Duplicate tile in KML: {name}")
            polygons = []
            for polygon in element.findall(".//" + _NS + "Polygon"):
                def ring(boundary):
                    coords = boundary.findtext(f"{_NS}LinearRing/{_NS}coordinates")
                    if not coords:
                        raise ValueError(f"Missing ring coordinates for {name}")
                    points = [tuple(float(v) for v in point.split(",")[:2])
                              for point in coords.split()]
                    if any(len(p) != 2 for p in points):
                        raise ValueError(f"Invalid ring coordinates for {name}")
                    return points

                outer = polygon.find(_NS + "outerBoundaryIs")
                if outer is None:
                    raise ValueError(f"Missing polygon boundary for {name}")
                holes = [ring(b) for b in polygon.findall(_NS + "innerBoundaryIs")]
                polygons.append(Polygon(ring(outer), holes))
            if not polygons:
                raise ValueError(f"No polygon for tile {name}")
            geometry = polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
            # Official LL_WKT has more precision than the KML display coordinates.
            # Rounded display vertices can add a whole pixel during outward snapping.
            precise = re.search(r"<b>LL_WKT</b>.*?<font[^>]*>\s*([^<]+)</font>",
                                element.findtext(_NS + "description") or "", re.I | re.S)
            if precise:
                geometry = wkt.loads(precise.group(1).strip())
                if geometry.geom_type == "MultiPolygon" and len(geometry.geoms) == 1:
                    geometry = geometry.geoms[0]
            _validate_geometry(geometry)
            selected[name] = geometry
        element.clear()
    missing = tiles - selected.keys()
    if missing:
        raise ValueError(f"Tiles absent from KML: {sorted(missing)}")
    return selected


def prepare_grid(output, tiles, *, source=None):
    """Create one immutable GeoParquet, with source digest and URL in its rows."""
    selected = {_normalize_tile(tile) for tile in tiles}
    if not selected:
        raise ValueError("At least one tile is required")
    destination = Path(output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(destination) + ".lock", timeout=0):
        if destination.exists():
            raise FileExistsError("Grid already exists; choose a new output to preserve provenance")
        raw, filename, url = _source_bytes(source)
        digest = hashlib.sha256(raw).hexdigest()
        polygons = _polygons(raw, selected)
        ids = sorted(polygons)
        frame = gpd.GeoDataFrame({
            "mgrs_tile_id": ids,
            "utm_epsg": [(32700 if t[2] < "N" else 32600) + int(t[:2]) for t in ids],
            "source_filename": filename, "source_url": url, "source_sha256": digest,
        }, geometry=[polygons[t] for t in ids], crs="EPSG:4326")
        fd, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".parquet.tmp")
        os.close(fd)
        try:
            frame.to_parquet(temporary, index=False)
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return {"path": str(destination), "tiles": ids, "source_sha256": digest,
            "source_url": url, "grid_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}
