"""Explicit access to PC, unpacked local products, and USGS Requester Pays.

USGS discovery is public, but LandsatLook data URLs can redirect to a login page.
Those URLs are never opened here. AWS data reads require an explicit
``mode: aws-requester-pays`` configuration and can incur charges. Credentials
come exclusively from the SDK's standard credential chain, optionally selecting
an existing profile; they are never added to an item or a manifest.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
import os
from pathlib import Path, PurePosixPath
from typing import Iterator
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

import rasterio
from rasterio.session import AWSSession

from .catalog import (
    CatalogError,
    _request_json,
    _unsigned_metadata,
    get_session,
    sign_href,
)

_RASTER_ENV = {
    "GDAL_HTTP_MAX_RETRY": 3,
    "GDAL_HTTP_RETRY_DELAY": 2,
    "GDAL_HTTP_TIMEOUT": 90,
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
}
_ACCESS_REQUIRED = (
    "USGS Landsat assets require unpacked local files "
    "(access.mode='local', directory=<absolute path>) or explicitly configured "
    "access.mode='aws-requester-pays' (AWS requests/data transfer can incur charges). "
    "LandsatLook login URLs are not downloadable anonymously."
)
_LOCAL_JOB: ContextVar[dict | None] = ContextVar("landsat_grits_local_assets", default=None)


class AssetAccessError(CatalogError):
    """An asset cannot be accessed under the explicitly selected access mode."""


def _options(access: dict | None) -> dict:
    if access is None:
        return {"mode": "auto"}
    if not isinstance(access, dict):
        raise ValueError("access must be a mapping or None.")
    unknown = set(access) - {"mode", "directory", "aws_profile"}
    if unknown:
        raise ValueError(
            "access accepts only mode, directory, and aws_profile; "
            "provide credentials through the standard AWS credential chain."
        )
    result = dict(access)
    result.setdefault("mode", "auto")
    if result["mode"] not in {"auto", "public", "local", "aws-requester-pays"}:
        raise ValueError("Unknown asset access mode.")
    if "aws_profile" in result and (
        result["mode"] != "aws-requester-pays"
        or not isinstance(result["aws_profile"], str)
        or not result["aws_profile"].strip()
    ):
        raise ValueError("aws_profile requires explicit aws-requester-pays mode and a name.")
    if "directory" in result and result["mode"] != "local":
        raise ValueError("directory requires local access mode.")
    return result


def _local_root(options: dict) -> Path:
    raw = options.get("directory")
    if not isinstance(raw, (str, os.PathLike)) or not Path(raw).is_absolute():
        raise ValueError("Local access.directory must be an absolute directory.")
    try:
        root = Path(raw).resolve(strict=True)
    except OSError:
        raise AssetAccessError("Local access.directory does not exist.") from None
    if not root.is_dir():
        raise AssetAccessError("Local access.directory is not a directory.")
    return root


def _local_path(href: str) -> Path | None:
    parsed = urlsplit(href)
    if parsed.scheme.lower() == "file":
        path = url2pathname(parsed.path)
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        return Path(path)
    if parsed.scheme.lower() in {"http", "https", "s3"} or href.startswith("/vsi"):
        return None
    # A drive-letter Windows path is a local path, not a URI scheme.
    if parsed.scheme and not (len(parsed.scheme) == 1 and href[1:2] == ":"):
        raise AssetAccessError("Unsupported asset URI scheme.")
    return Path(href)


def _href(asset: dict) -> str:
    href = asset.get("href")
    if not isinstance(href, str) or not href:
        raise AssetAccessError("Asset has no nonempty href.")
    if href.lower().startswith("/vsi"):
        raise AssetAccessError(
            "GDAL virtual filesystem asset paths are unsupported; use local files, public HTTPS, "
            "or an S3 URI with explicit aws-requester-pays access."
        )
    return href


def _metadata_key(assets: dict) -> str | None:
    candidates = [
        key for key, asset in assets.items()
        if key.lower() == "mtl.json"
        or urlsplit(str(asset.get("href", ""))).path.lower().endswith("_mtl.json")
    ]
    if len(candidates) > 1:
        raise AssetAccessError("Item has multiple MTL JSON assets; refusing an ambiguous selection.")
    return candidates[0] if candidates else None


def _is_raster(asset: dict) -> bool:
    return urlsplit(str(asset.get("href", ""))).path.lower().endswith((".tif", ".tiff"))


def _requires_explicit_access(asset: dict) -> bool:
    href = _href(asset)
    if _local_path(href) is not None:
        return False
    parsed = urlsplit(href)
    alternate = asset.get("alternate", {}).get("s3", {})
    return (
        parsed.scheme.lower() == "s3"
        or (parsed.hostname or "").lower() == "landsatlook.usgs.gov"
        or bool(asset.get("storage:requester_pays"))
        or bool(alternate.get("storage:requester_pays"))
    )


def _s3_href(asset: dict) -> str:
    candidate = asset.get("alternate", {}).get("s3", {}).get("href")
    if not candidate and _href(asset).startswith("s3://"):
        candidate = asset["href"]
    if not isinstance(candidate, str):
        raise AssetAccessError("Requester Pays access requires an alternate.s3.href.")
    parsed = urlsplit(candidate)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
        raise AssetAccessError("Invalid alternate S3 asset address.")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise AssetAccessError("S3 asset addresses must be unsigned bucket/key URIs.")
    return candidate


def _stamp(path: Path) -> tuple[int, int, int, int]:
    try:
        stat = path.stat()
    except OSError:
        raise AssetAccessError("Local source tree changed or became inaccessible during ingestion.") from None
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def snapshot_local_assets(
    assets: dict[str, dict], asset_keys: tuple[str, ...] | list[str] | None = None,
) -> dict[Path, tuple[int, int, int, int]]:
    """Capture selected resolved local files and MTL before reading a scene."""
    selected = set(assets if asset_keys is None else asset_keys)
    metadata_key = _metadata_key(assets)
    if metadata_key is not None:
        selected.add(metadata_key)
    snapshot = {}
    for key in sorted(selected & assets.keys()):
        local = _local_path(_href(assets[key]))
        if local is not None:
            snapshot[local] = _stamp(local)
    return snapshot


def validate_local_snapshot(snapshot: dict[Path, tuple[int, int, int, int]]) -> None:
    """Reject a changed scene source before publishing its commit."""
    for path, stamp in snapshot.items():
        if _stamp(path) != stamp:
            raise AssetAccessError("Local source file changed before scene commit; scene not committed.")


@contextmanager
def local_asset_index(access: dict | None = None):
    """Reuse one lazy local index within a job, checking source stability on exit.

    Wrap the append loop and leave this context before issuing a success receipt.
    Directory changes and changes to files used by this job raise an error;
    previously committed scenes remain recoverable. No index survives the job.
    """
    options = _options(access)
    if options["mode"] != "local":
        yield
        return
    state = {"root": _local_root(options), "index": None, "directories": {}, "files": {}}
    token = _LOCAL_JOB.set(state)
    try:
        yield
        for path, stamp in state["directories"].items():
            if _stamp(path) != stamp:
                raise AssetAccessError("Local source directory changed during ingestion; retry the job.")
        for path, stamp in state["files"].items():
            if _stamp(path) != stamp:
                raise AssetAccessError("Local source file changed during ingestion; retry the job.")
    finally:
        _LOCAL_JOB.reset(token)


def _local_index(root: Path) -> dict[str, list[Path]]:
    state = _LOCAL_JOB.get()
    if state is not None and state["root"] == root and state["index"] is not None:
        return state["index"]
    index: dict[str, list[Path]] = {}
    directories = {}

    def fail(exc):
        raise AssetAccessError("Could not completely enumerate the local product directory.") from exc

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        directories[Path(directory)] = _stamp(Path(directory))
        # Do not traverse junctions/symlinks to unrelated source trees.
        dirs[:] = [
            name for name in dirs
            if not (Path(directory) / name).is_symlink()
            and not (Path(directory) / name).is_junction()
        ]
        for name in files:
            index.setdefault(name.casefold(), []).append(Path(directory) / name)
    if state is not None and state["root"] == root:
        state["index"], state["directories"] = index, directories
    return index


def _resolve_local(asset: dict, root: Path, index: dict | None) -> tuple[dict, dict | None]:
    href = _href(asset)
    direct = _local_path(href)
    if direct is not None and direct.is_absolute():
        try:
            path = direct.resolve(strict=True)
        except OSError:
            raise AssetAccessError("Resolved local asset is missing.") from None
    else:
        basename = (
            direct.name if direct is not None
            else PurePosixPath(unquote(urlsplit(href).path)).name
        )
        if not basename:
            raise AssetAccessError("Cannot derive an exact source filename for local resolution.")
        if index is None:
            index = _local_index(root)
        matches = index.get(basename.casefold(), [])
        if not matches:
            raise AssetAccessError(f"Missing unpacked source file: {basename}")
        if len(matches) != 1:
            raise AssetAccessError(f"Duplicate unpacked source filename: {basename}")
        path = matches[0].resolve(strict=True)
    if not path.is_file() or not path.is_relative_to(root):
        raise AssetAccessError("Resolved local asset is not a file inside access.directory.")
    state = _LOCAL_JOB.get()
    if state is not None and state["root"] == root:
        stamp = _stamp(path)
        if state["files"].setdefault(path, stamp) != stamp:
            raise AssetAccessError("Local source file changed during ingestion; retry the job.")
    resolved = deepcopy(asset)
    resolved["href"] = str(path)
    return resolved, index


def resolve_item_assets(
    item: dict, access: dict | None = None, asset_keys: tuple[str, ...] | list[str] | None = None
) -> dict[str, dict]:
    """Return independent access assets; never modify the provenance item.

    Local resolution uses exact case-insensitive source basenames, which include
    the full product id and original B/QA suffix. Duplicate matches and missing
    files are errors. ``asset_keys`` selects existing writer assets; MTL JSON is
    always included. Without it all TIFF and MTL JSON assets are resolved.
    Unselected assets (for example browse images) retain their original hrefs.
    """
    options = _options(access)
    assets = deepcopy(item.get("assets", {}))
    metadata_key = _metadata_key(assets)
    selected = (
        {key for key in asset_keys if key in assets}
        if asset_keys is not None else {key for key, asset in assets.items() if _is_raster(asset)}
    )
    if metadata_key is not None:
        selected.add(metadata_key)
    if options["mode"] == "local":
        root, index = _local_root(options), None
        for key in sorted(selected):
            assets[key], index = _resolve_local(assets[key], root, index)
    elif options["mode"] == "aws-requester-pays":
        for key in selected:
            if _local_path(_href(assets[key])) is None:
                # Retain the public URL/alternate metadata. raster_access chooses
                # the S3 transport only while an explicitly configured Env is open.
                _s3_href(assets[key])
    else:
        usgs_remote = item.get("collection") == "landsat-c2l1" and any(
            _local_path(_href(assets[key])) is None for key in selected
        )
        if usgs_remote or any(_requires_explicit_access(assets[key]) for key in selected):
            raise AssetAccessError(_ACCESS_REQUIRED)
    return assets


def _boto_session(options: dict):
    try:
        import boto3
    except ImportError:
        raise AssetAccessError(
            "AWS Requester Pays requires the optional boto3 dependency; install landsat-grits[aws]."
        ) from None
    try:
        return boto3.Session(
            **({"profile_name": options["aws_profile"]} if options.get("aws_profile") else {})
        )
    except Exception:
        raise AssetAccessError("Could not initialize the configured AWS credential profile.") from None


@contextmanager
def raster_access(asset: dict, access: dict | None = None) -> Iterator[str]:
    """Yield a temporary readable href inside its Rasterio environment.

    Only explicit ``aws-requester-pays`` mode enables charged S3 reads.
    The yielded href and AWS environment must not be stored as provenance.
    """
    options = _options(access)
    href = _href(asset)
    if options["mode"] == "local":
        asset, _ = _resolve_local(asset, _local_root(options), None)
        href = asset["href"]
    local = _local_path(href)
    try:
        if local is not None:
            if not local.is_file():
                raise AssetAccessError("Local raster asset does not exist.")
            stamp = _stamp(local)
            with rasterio.Env(**_RASTER_ENV):
                yield str(local)
            if _stamp(local) != stamp:
                raise AssetAccessError("Local source file changed during raster read; scene not committed.")
        elif options["mode"] == "aws-requester-pays":
            href = _s3_href(asset)
            session = AWSSession(
                session=_boto_session(options), aws_unsigned=False, requester_pays=True
            )
            with rasterio.Env(session=session, **_RASTER_ENV):
                yield href
        else:
            if _requires_explicit_access(asset):
                raise AssetAccessError(_ACCESS_REQUIRED)
            with rasterio.Env(**_RASTER_ENV):
                yield sign_href(href)
    except rasterio.errors.RasterioError:
        # GDAL errors can include the entire signed asset URL.
        raise AssetAccessError(
            "Raster access failed; check source availability and the configured access mode."
        ) from None


def read_metadata(item: dict, access: dict | None = None) -> dict:
    """Read the complete MTL JSON without storing access tokens.

    After ``resolve_item_assets``, pass a shallow item copy with those assets to
    avoid scanning a local directory twice. The untouched item belongs in the
    archive provenance. Calling this function on an original item also works.
    """
    options = _options(access)
    key = _metadata_key(item.get("assets", {}))
    if key is None:
        if item.get("collection") == "landsat-c2l1":
            raise AssetAccessError("USGS Level-1 items require an MTL JSON asset.")
        return {}
    assets = resolve_item_assets(item, options, asset_keys=(key,))
    asset = assets[key]
    href = _href(asset)
    local = _local_path(href)
    if local is not None:
        stamp = _stamp(local)
        try:
            document = json.loads(local.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise AssetAccessError("Could not read local MTL JSON.") from None
        if _stamp(local) != stamp:
            raise AssetAccessError("Local source file changed during metadata read; scene not committed.")
    elif options["mode"] == "aws-requester-pays":
        parsed = urlsplit(_s3_href(asset))
        try:
            client = _boto_session(options).client("s3")
            response = client.get_object(
                Bucket=parsed.netloc, Key=unquote(parsed.path.lstrip("/")),
                RequestPayer="requester",
            )
            body = response["Body"]
            try:
                document = json.loads(body.read())
            finally:
                body.close()
        except AssetAccessError:
            raise
        except Exception:
            raise AssetAccessError(
                "Could not read USGS MTL JSON through the configured Requester Pays access."
            ) from None
        finally:
            if "client" in locals():
                client.close()
    else:
        if _requires_explicit_access(asset):
            raise AssetAccessError(_ACCESS_REQUIRED)
        with get_session() as session:
            document = _request_json(session, "GET", sign_href(href, session=session))
    if not isinstance(document, dict):
        raise AssetAccessError("MTL JSON must be an object.")
    if item.get("collection") == "landsat-c2l1":
        metadata = document.get("LANDSAT_METADATA_FILE")
        contents = metadata.get("PRODUCT_CONTENTS") if isinstance(metadata, dict) else None
        product_id = contents.get("LANDSAT_PRODUCT_ID") if isinstance(contents, dict) else None
        if not isinstance(product_id, str) or not product_id:
            raise AssetAccessError(
                "USGS MTL JSON lacks LANDSAT_METADATA_FILE.PRODUCT_CONTENTS.LANDSAT_PRODUCT_ID."
            )
        if product_id != item.get("id"):
            raise AssetAccessError("USGS MTL product id does not match the selected STAC item.")
    return _unsigned_metadata(document)
