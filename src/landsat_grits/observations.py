"""Read track identities from committed provenance without rewriting pixel stores.

A WRS path is a repeating ground track; a row is an along-track scene frame.
WRS-1 and WRS-2 path numbers are different namespaces, including for MSS.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from filelock import FileLock
import numpy as np
import zarr

from .catalog import item_datetime, scene_fingerprint, scene_key
from .storage import read_json


def _integer(value, field, upper):
    if value is None:
        return None
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
        raise ValueError(f"{field} must be a positive integer")
    number = int(value)
    if not 1 <= number <= upper:
        raise ValueError(f"{field} must be between 1 and {upper}")
    return number


def wrs_identity(properties: dict) -> dict:
    """Use explicit STAC WRS fields; never infer a path from a date or MGRS ID."""
    platform = str(properties.get("platform", "")).lower().replace("_", "-")
    mission = re.fullmatch(r"landsat-([1-9])", platform)
    expected = (1 if int(mission[1]) <= 3 else 2) if mission else None
    system = _integer(properties.get("landsat:wrs_type"), "landsat:wrs_type", 2)
    if system is not None and expected is not None and system != expected:
        raise ValueError("WRS system conflicts with the Landsat platform")
    system = system or expected
    path = _integer(properties.get("landsat:wrs_path"), "landsat:wrs_path",
                    251 if system == 1 else 233 if system == 2 else 251)
    # Ascending scenes use the complementary half of the WRS row sequence.
    row = _integer(properties.get("landsat:wrs_row"), "landsat:wrs_row", 248)
    track = f"WRS{system}/P{path:03d}" if system is not None and path is not None else None
    return {"wrs_system": system, "wrs_path": path, "wrs_row": row, "track": track,
            "orbit_metadata_source": "stac" if track is not None else "missing"}


def read_observations(path: Path, root, commits: list) -> list[dict]:
    """Read an index while the caller holds the archive lock.

    No new orbit arrays are required in existing archives. A legacy commit with
    no provenance reference has unknown orbit; a broken referenced file is an error.
    """
    path = Path(path)
    if not isinstance(commits, list):
        raise ValueError("Invalid commit ledger")
    expected = len(commits)
    identities = None
    for name in root.attrs["contract"]["groups"]:
        group = root[name]
        if group["data"].shape[0] != expected or group["time"].shape != (expected,):
            raise ValueError("Uncommitted data; resume ingestion before reading")
        current = tuple(group[key][:].tolist() for key in ("scene_id", "time", "platform"))
        if any(len(values) != expected for values in current):
            raise ValueError("Invalid scene coordinate lengths")
        if identities is not None and identities != current:
            raise ValueError("Group scene coordinates do not match")
        identities = current
    if identities is None:
        raise ValueError("Archive has no variable groups")
    records = []
    for index, commit in enumerate(commits):
        key = commit["key"]
        instant = datetime.fromisoformat(commit["datetime"].replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError("Commit datetime must include a timezone")
        instant = instant.astimezone(timezone.utc)
        stamp = int(np.datetime64(instant.replace(tzinfo=None), "us").astype("int64"))
        if key != identities[0][index] or stamp != identities[1][index]:
            raise ValueError("Commit ledger does not match scene coordinates")
        platform = identities[2][index]
        properties = {"platform": platform}
        filename = commit.get("provenance")
        if filename is not None:
            if not isinstance(filename, str) or not re.fullmatch(r"[a-f0-9]{64}\.json", filename):
                raise ValueError("Invalid provenance filename")
            directory = path.with_name(path.name + ".provenance").resolve()
            source_path = directory / filename
            if not source_path.resolve().is_relative_to(directory):
                raise ValueError("Provenance path escapes its directory")
            item = read_json(source_path)["item"]
            if (scene_key(item) != key or item_datetime(item) != instant
                    or item["properties"]["platform"] != platform):
                raise ValueError("Provenance scene identity differs from the committed scene")
            if scene_fingerprint(item) != commit["fingerprint"]:
                raise ValueError("Provenance fingerprint differs")
            properties = item["properties"]
        collection, _, item_id = key.partition("/")
        record = {"index": index, "scene_id": key, "collection": collection, "item_id": item_id,
                  "platform": platform, "time": instant.isoformat().replace("+00:00", "Z"),
                  **wrs_identity(properties)}
        records.append(record)
    if len({record["scene_id"] for record in records}) != len(records):
        raise ValueError("Duplicate scene identities in archive")
    return records


def list_scenes(path: str | Path, *, track: str | None = None,
                wrs_row: int | None = None) -> list[dict]:
    """List all committed observations, optionally selecting one WRS track/frame."""
    validate_selection(track, wrs_row)
    path = Path(path)
    with FileLock(str(path) + ".lock", timeout=0):
        root = zarr.open_group(str(path), mode="r")
        commits = read_json(path.with_name(path.name + ".commits.json"))
        records = read_observations(path, root, commits)
    return [record for record in records
            if (track is None or record["track"] == track)
            and (wrs_row is None or record["wrs_row"] == wrs_row)]


def validate_selection(track, wrs_row):
    if track is not None:
        match = re.fullmatch(r"WRS([12])/P(\d{3})", track) if isinstance(track, str) else None
        if not match or not 1 <= int(match[2]) <= (251 if match[1] == "1" else 233):
            raise ValueError("track must include its WRS namespace, e.g. WRS2/P122")
    if wrs_row is not None and (type(wrs_row) is not int or not 1 <= wrs_row <= 248):
        raise ValueError("wrs_row must be an integer between 1 and 248")


def list_tracks(path: str | Path) -> list[dict]:
    """Summarize every known track; each unknown scene remains a separate entry."""
    groups = {}
    for record in list_scenes(path):
        key = record["track"] or record["scene_id"]
        entry = groups.setdefault(key, {"track": record["track"], "wrs_system": record["wrs_system"],
                                        "wrs_path": record["wrs_path"], "rows": [], "scene_ids": []})
        if record["wrs_row"] is not None and record["wrs_row"] not in entry["rows"]:
            entry["rows"].append(record["wrs_row"])
        entry["scene_ids"].append(record["scene_id"])
    for entry in groups.values():
        entry["rows"].sort()
        entry["scene_count"] = len(entry["scene_ids"])
    return list(groups.values())
