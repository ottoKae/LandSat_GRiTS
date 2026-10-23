"""Unsigned Planetary Computer/USGS discovery and short-lived PC asset access.

Discovery exhausts the pages advertised by the API unless ``limit`` is explicitly
set for a pilot. This is not a claim that the upstream catalogue contains every
historical Landsat acquisition. Signed URLs are only returned for immediate I/O;
neither this module nor its source metadata contains persisted access tokens.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import OrderedDict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import time as time_module
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import url2pathname

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import ReadTimeoutError
from urllib3.util.retry import Retry

from .bands import _usgs_instruments

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
USGS_STAC_URL = "https://landsatlook.usgs.gov/stac-server"
SIGN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"
REQUEST_TIMEOUT = (15, 120)
PAGE_SIZE = 1000
_SIGN_CACHE: OrderedDict[str, tuple[datetime, str]] = OrderedDict()
_SIGN_CACHE_SIZE = 256

# Azure SAS fields, including user-delegation SAS and optional response overrides.
_SAS_FIELDS = frozenset(
    "sig se st sp sv sr spr sip si srt ss skoid sktid skt ske sks skv "
    "saoid suoid scid ses rscc rscd rsce rscl rsct".split()
)
_SECRET_FIELDS = frozenset(
    {
        "token", "access_token", "sas_token", "credential", "authorization",
        "aws_access_key_id", "aws_secret_access_key", "aws_session_token",
        "x-amz-algorithm", "x-amz-credential", "x-amz-date", "x-amz-expires",
        "x-amz-signedheaders", "x-amz-signature", "x-amz-security-token",
    }
)


class CatalogError(RuntimeError):
    """Discovery, source metadata, or signing failed; partial results are unsafe."""


def get_session() -> requests.Session:
    """Create a session with bounded retries for read-only GET and search POST."""
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _unsigned_href(href: str) -> str:
    """Remove SAS/authentication query fields while preserving resource queries."""
    parsed = urlsplit(href)
    if parsed.scheme.lower() not in {"http", "https"}:
        return href
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _SAS_FIELDS | _SECRET_FIELDS
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _unsigned_metadata(value: Any) -> Any:
    """Copy metadata without ephemeral signing fields, including nested URLs."""
    if isinstance(value, dict):
        return {
            key: _unsigned_metadata(child)
            for key, child in value.items()
            if key.lower() not in _SECRET_FIELDS | {"msft:expiry"}
        }
    if isinstance(value, list):
        return [_unsigned_metadata(child) for child in value]
    if isinstance(value, str) and value.lower().startswith(("https://", "http://")):
        return _unsigned_href(value)
    return value


def scene_key(item: dict) -> str:
    """Return the collection-scoped scene identity, never a date-only identity."""
    collection, identifier = item.get("collection"), item.get("id")
    if not isinstance(collection, str) or not collection:
        raise ValueError("A STAC item requires a nonempty collection.")
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("A STAC item requires a nonempty id.")
    return f"{collection}/{identifier}"


def item_datetime(item: dict) -> datetime:
    """Read an acquisition datetime as aware UTC; reject ambiguous naive dates."""
    properties = item.get("properties", {})
    value = properties.get("datetime") or properties.get("start_datetime")
    if not isinstance(value, str):
        raise ValueError(f"Scene {scene_key(item)} has no acquisition datetime.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Scene {scene_key(item)} has an invalid datetime.") from None
    if parsed.tzinfo is None:
        raise ValueError(f"Scene {scene_key(item)} has a timezone-naive datetime.")
    return parsed.astimezone(timezone.utc)


def scene_fingerprint(item: dict) -> str:
    """Hash stable scene content, excluding links and ephemeral access tokens."""
    scene_key(item)
    content = {
        field: item[field]
        for field in ("collection", "id", "geometry", "bbox", "properties", "assets")
        if field in item
    }
    encoded = json.dumps(
        _unsigned_metadata(content),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _boundary(value: str, *, end: bool) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Search dates must be ISO date or timezone-aware datetime strings.")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            parsed = datetime.fromisoformat(value)
            parsed = datetime.combine(
                parsed.date(), time.max if end else time.min, timezone.utc
            )
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timezone required.")
        return parsed.astimezone(timezone.utc)
    except ValueError:
        raise ValueError(f"Invalid search date: {value!r}.") from None


def _request_json(session: Any, method: str, href: str, **kwargs: Any) -> dict:
    # Do not include a request URL or the original requests exception in failures:
    # asset GETs may contain a short-lived SAS credential.
    for attempt in range(3):
        response = None
        try:
            response = session.request(method, href, timeout=REQUEST_TIMEOUT, **kwargs)
            response.raise_for_status()
            document = response.json()
            if not isinstance(document, dict):
                raise CatalogError(f"{method} response must be a JSON object.")
            return document
        except ValueError:
            raise CatalogError(f"{method} response was not valid JSON.") from None
        except requests.RequestException as exc:
            # HTTPAdapter retries stop at response headers; body reads happen later.
            body_interrupted = isinstance(exc, requests.exceptions.ChunkedEncodingError) or (
                isinstance(exc, requests.ConnectionError)
                and exc.args and isinstance(exc.args[0], ReadTimeoutError)
            )
            if body_interrupted and attempt < 2:
                time_module.sleep(0.2 * 2**attempt)
                continue
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" (HTTP {status})" if status is not None else ""
            raise CatalogError(f"{method} request failed{suffix}.") from None
        finally:
            if response is not None:
                response.close()


def search_items(
    collection: str,
    geometry: dict,
    start: str,
    end: str,
    platforms: tuple[str, ...],
    max_cloud_cover: float | None = None,
    limit: int | None = None,
    session: Any = None,
    source: str = "planetary-computer",
    instruments: tuple[str, ...] | None = None,
) -> list[dict]:
    """Search one collection, following GET/POST next links to exhaustion.

    Dates without a time include the whole UTC day. ``platforms=()`` accepts all
    platforms; platform matching treats ``LANDSAT_9`` and ``landsat-9`` alike.
    Optional instrument matching is applied before counting a pilot limit.
    Original item properties are preserved for provenance. The optional
    ``limit`` counts matching unique scenes, and deliberately makes a pilot
    incomplete. Results are sorted by UTC acquisition time, collection, and id.
    A pilot selects the first matching scenes in API traversal order, then sorts
    that subset; it does not guarantee the globally earliest scenes.
    """
    if not isinstance(collection, str) or not collection:
        raise ValueError("collection must be a nonempty STAC collection id.")
    endpoints = {"planetary-computer": STAC_URL, "usgs": USGS_STAC_URL}
    if source not in endpoints:
        raise ValueError("source must be 'planetary-computer' or 'usgs'.")
    if not isinstance(geometry, dict) or not geometry.get("type"):
        raise ValueError("geometry must be a GeoJSON geometry.")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        raise ValueError("limit must be a positive integer or None.")
    if max_cloud_cover is not None and (
        isinstance(max_cloud_cover, bool)
        or not math.isfinite(max_cloud_cover)
        or not 0 <= max_cloud_cover <= 100
    ):
        raise ValueError("max_cloud_cover must be between 0 and 100.")
    start_dt, end_dt = _boundary(start, end=False), _boundary(end, end=True)
    if start_dt > end_dt:
        raise ValueError("start must be no later than end.")
    initial_body: dict[str, Any] = {
        "collections": [collection],
        "intersects": geometry,
        "datetime": (
            f"{start_dt.isoformat().replace('+00:00', 'Z')}/"
            f"{end_dt.isoformat().replace('+00:00', 'Z')}"
        ),
        "limit": PAGE_SIZE,
    }
    if max_cloud_cover is not None:
        initial_body["query"] = {"eo:cloud_cover": {"lte": max_cloud_cover}}
    accepted_platforms = {value.lower().replace("_", "-") for value in platforms}
    accepted_instruments = (
        {value.upper().replace("+", "").replace("-", "") for value in instruments}
        if instruments else set()
    )
    found: dict[str, dict] = {}
    seen_pages: set[str] = set()
    method, href, body = "POST", f"{endpoints[source]}/search", initial_body
    headers: dict[str, str] = {}
    owns_session = session is None
    session = get_session() if owns_session else session
    try:
        while True:
            page_key = json.dumps([method, href, body, headers], sort_keys=True)
            if page_key in seen_pages:
                raise CatalogError("STAC pagination repeated a request; search is incomplete.")
            seen_pages.add(page_key)
            options: dict[str, Any] = {"headers": headers}
            if body is not None:
                options["json"] = body
            page = _request_json(session, method, href, **options)
            features = page.get("features")
            if not isinstance(features, list):
                raise CatalogError("STAC search response has no features list.")
            for item in features:
                if not isinstance(item, dict):
                    raise CatalogError("STAC search returned a malformed feature.")
                if item.get("collection") != collection:
                    raise CatalogError("STAC search returned a different collection.")
                properties = item.get("properties", {})
                platform = str(properties.get("platform", "")).lower().replace("_", "-")
                if accepted_platforms and platform not in accepted_platforms:
                    continue
                if accepted_instruments:
                    if source == "usgs":
                        raw_instruments = _usgs_instruments(item, platform)
                        if raw_instruments is None:
                            raise CatalogError(
                                f"Cannot establish consistent instruments for {scene_key(item)}."
                            )
                    else:
                        raw_instruments = properties.get("instruments") or []
                        if isinstance(raw_instruments, str):
                            raw_instruments = [raw_instruments]
                    actual_instruments = {
                        str(value).upper().replace("+", "").replace("-", "")
                        for value in raw_instruments
                    }
                    if not actual_instruments.intersection(accepted_instruments):
                        continue
                if max_cloud_cover is not None:
                    cloud = properties.get("eo:cloud_cover")
                    if (
                        not isinstance(cloud, (int, float))
                        or isinstance(cloud, bool)
                        or not math.isfinite(cloud)
                        or not 0 <= cloud <= max_cloud_cover
                    ):
                        continue
                # Validate identity and time before admitting a scene to a result.
                key = scene_key(item)
                item_datetime(item)
                if key in found and scene_fingerprint(found[key]) != scene_fingerprint(item):
                    raise CatalogError(f"Conflicting source metadata for duplicate scene {key}.")
                if key not in found:
                    found[key] = _unsigned_metadata(item)
                if limit is not None and len(found) >= limit:
                    return sorted(
                        found.values(),
                        key=lambda value: (item_datetime(value), value["collection"], value["id"]),
                    )
            links = page.get("links", [])
            if not isinstance(links, list):
                raise CatalogError("STAC search returned malformed pagination links.")
            next_links = [
                link for link in links if isinstance(link, dict) and link.get("rel") == "next"
            ]
            if not next_links:
                break
            if len(next_links) != 1:
                raise CatalogError("STAC search returned ambiguous next-page links.")
            link = next_links[0]
            next_href = link.get("href")
            if not isinstance(next_href, str) or not next_href:
                raise CatalogError("STAC next-page link is missing its href.")
            href = urljoin(href, next_href)
            method = str(link.get("method", "GET")).upper()
            if method not in {"GET", "POST"}:
                raise CatalogError(f"Unsupported STAC next-page method: {method}.")
            next_body = link.get("body")
            if next_body is not None and not isinstance(next_body, dict):
                raise CatalogError("STAC next-page body must be an object.")
            merge = link.get("merge", False)
            if not isinstance(merge, bool):
                raise CatalogError("STAC next-page merge flag must be boolean.")
            # The STAC specification defines merge relative to the original
            # search body, not to the preceding continuation token.
            body = {**initial_body, **(next_body or {})} if merge else next_body
            headers = link.get("headers", {})
            if not isinstance(headers, dict):
                raise CatalogError("STAC next-page headers must be an object.")
    finally:
        if owns_session:
            session.close()
    return sorted(
        found.values(),
        key=lambda value: (item_datetime(value), value["collection"], value["id"]),
    )


def sign_href(href: str, session: Any = None) -> str:
    """Sign assets on demand, reusing at most 256 unexpired in-memory signatures."""
    if not isinstance(href, str) or not href:
        raise ValueError("An asset href must be a nonempty string.")
    parsed = urlsplit(href)
    if parsed.scheme.lower() not in {"http", "https"}:
        return href
    if not (parsed.hostname or "").lower().endswith(".blob.core.windows.net"):
        return href
    unsigned = _unsigned_href(href)
    owns_session = session is None
    fresh_after = datetime.now(timezone.utc) + timedelta(seconds=60)
    if owns_session and unsigned in _SIGN_CACHE:
        expiry, signed = _SIGN_CACHE[unsigned]
        if expiry > fresh_after:
            _SIGN_CACHE.move_to_end(unsigned)
            return signed
        del _SIGN_CACHE[unsigned]
    session = get_session() if owns_session else session
    try:
        for attempt in range(2):
            options = {"headers": {"Cache-Control": "no-cache"}} if attempt else {}
            document = _request_json(
                session, "GET", SIGN_URL, params={"href": unsigned}, **options
            )
            signed = document.get("href")
            if not isinstance(signed, str) or _unsigned_href(signed) != unsigned:
                raise CatalogError("Planetary Computer signing response does not match the asset.")
            try:
                expiry = datetime.fromisoformat(document["msft:expiry"].replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    raise ValueError("Timezone missing.")
            except (KeyError, AttributeError, ValueError):
                raise CatalogError("Planetary Computer signing response has no valid expiry.") from None
            if expiry > datetime.now(timezone.utc) + timedelta(seconds=60):
                if owns_session:
                    _SIGN_CACHE[unsigned] = (expiry, signed)
                    _SIGN_CACHE.move_to_end(unsigned)
                    while len(_SIGN_CACHE) > _SIGN_CACHE_SIZE:
                        _SIGN_CACHE.popitem(last=False)
                return signed
        raise CatalogError("Planetary Computer repeatedly returned an expired or expiring signature.")
    finally:
        if owns_session:
            session.close()


def fetch_source_metadata(item: dict, session: Any = None) -> dict:
    """Read the complete MTL JSON object, or return {} when no MTL JSON exists."""
    assets = item.get("assets", {})
    asset = assets.get("mtl.json")
    if asset is None:
        for key, candidate in assets.items():
            href = candidate.get("href", "")
            if key.lower() == "mtl.json" or urlsplit(href).path.lower().endswith("_mtl.json"):
                asset = candidate
                break
    if asset is None:
        return {}
    href = asset.get("href")
    if not isinstance(href, str) or not href:
        raise CatalogError("MTL JSON asset is missing its href.")
    parsed = urlsplit(href)
    if parsed.scheme.lower() not in {"http", "https"}:
        local_path = href
        if parsed.scheme.lower() == "file":
            local_path = url2pathname(parsed.path)
            if parsed.netloc:
                local_path = f"//{parsed.netloc}{local_path}"
        try:
            document = json.loads(Path(local_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise CatalogError("Could not read local MTL JSON metadata.") from None
    else:
        owns_session = session is None
        session = get_session() if owns_session else session
        try:
            document = _request_json(session, "GET", sign_href(href, session=session))
        finally:
            if owns_session:
                session.close()
    if not isinstance(document, dict):
        raise CatalogError("MTL JSON metadata must be an object.")
    return _unsigned_metadata(document)
