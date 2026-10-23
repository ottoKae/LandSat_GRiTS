"""Catalogue contracts tested without a live service or asset credentials."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import requests

from landsat_grits import catalog
from landsat_grits.catalog import (
    CatalogError,
    SIGN_URL,
    STAC_URL,
    fetch_source_metadata,
    get_session,
    item_datetime,
    scene_fingerprint,
    scene_key,
    search_items,
    sign_href,
)

GEOMETRY = {"type": "Point", "coordinates": [116.4, 39.9]}
COLLECTION = "landsat-c2-l1"
ASSET_URL = "https://landsateuwest.blob.core.windows.net/landsat-c2/scene_B1.TIF"


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body
        self.status_code = status
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("failure including secret", response=self)

    def json(self):
        return self.body

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, deepcopy(kwargs)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def clear_signatures():
    catalog._SIGN_CACHE.clear()
    yield
    catalog._SIGN_CACHE.clear()


def item(identifier="scene-a", date="2020-01-01T10:00:00Z", platform="landsat-5"):
    return {
        "type": "Feature",
        "id": identifier,
        "collection": COLLECTION,
        "geometry": GEOMETRY,
        "properties": {
            "datetime": date,
            "platform": platform,
            "eo:cloud_cover": 10,
        },
        "assets": {"green": {"href": ASSET_URL}},
    }


def page(*items, links=None):
    return FakeResponse({"type": "FeatureCollection", "features": list(items), "links": links or []})


def search(session, **kwargs):
    return search_items(
        COLLECTION, GEOMETRY, "2020-01-01", "2020-12-31",
        ("landsat-5",), session=session, **kwargs,
    )


def test_complete_pagination_get_post_merge_sort_and_scene_identity():
    session = FakeSession(
        page(
            item("b"), item("excluded", platform="landsat-8"),
            links=[{"rel": "next", "href": "/api/stac/v1/search", "method": "POST",
                    "body": {"token": "p2"}, "merge": True}],
        ),
        page(
            item("b"), item("a"),  # Same datetime is not the identity.
            links=[{"rel": "next", "href": f"{STAC_URL}/search?token=p3", "method": "GET"}],
        ),
        page(item("early", date="2020-01-01T10:30:00+01:00")),
    )
    result = search(session)
    assert [entry["id"] for entry in result] == ["early", "a", "b"]
    first_body = session.calls[0][2]["json"]
    assert first_body["datetime"].endswith("/2020-12-31T23:59:59.999999Z")
    assert session.calls[1][2]["json"] == {**first_body, "token": "p2"}
    assert session.calls[2][0] == "GET"
    assert "json" not in session.calls[2][2]
    assert scene_key(result[0]) == f"{COLLECTION}/early"


def test_post_without_merge_does_not_reuse_previous_body():
    session = FakeSession(
        page(links=[{"rel": "next", "href": f"{STAC_URL}/search",
                     "method": "POST", "body": {"token": "p2"}}]),
        page(item(), links=[{"rel": "next", "href": f"{STAC_URL}/search",
                           "method": "POST", "body": {"token": "p3"}, "merge": True,
                           "headers": {"Search-After": "a"}}]),
        page(item("b")),
    )
    assert len(search(session)) == 2
    assert session.calls[1][2]["json"] == {"token": "p2"}
    assert session.calls[2][2]["json"] == {**session.calls[0][2]["json"], "token": "p3"}
    assert session.calls[2][2]["headers"] == {"Search-After": "a"}


def test_pilot_limit_counts_unique_matching_scenes():
    session = FakeSession(
        page(item("excluded", platform="landsat-8"), item("b"), item("b"),
             links=[{"rel": "next", "href": "?token=p2"}]),
        page(item("a"), item("c"), links=[{"rel": "next", "href": "?token=p3"}]),
    )
    assert [entry["id"] for entry in search(session, limit=2)] == ["a", "b"]
    assert len(session.calls) == 2


def test_page_failure_does_not_return_partial_results():
    session = FakeSession(
        page(item(), links=[{"rel": "next", "href": "?token=p2"}]),
        FakeResponse({}, status=503),
    )
    with pytest.raises(CatalogError, match="HTTP 503"):
        search(session)


def test_repeated_page_is_an_error():
    session = FakeSession(
        page(links=[{"rel": "next", "href": f"{STAC_URL}/search",
                     "method": "POST", "merge": True}]),
    )
    with pytest.raises(CatalogError, match="repeated"):
        search(session)


def test_missing_features_is_an_error():
    with pytest.raises(CatalogError, match="features"):
        search(FakeSession(FakeResponse({"links": []})))


def test_cloud_filter_is_applied_to_results_and_request():
    cloudy = item("cloudy")
    cloudy["properties"]["eo:cloud_cover"] = 90
    missing = item("missing")
    missing["properties"].pop("eo:cloud_cover")
    unknown = item("unknown")
    unknown["properties"]["eo:cloud_cover"] = -1
    session = FakeSession(page(item(), cloudy, missing, unknown))
    assert len(search(session, max_cloud_cover=20)) == 1
    assert session.calls[0][2]["json"]["query"] == {"eo:cloud_cover": {"lte": 20}}


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": True}, {"limit": -1}, {"max_cloud_cover": 101},
    {"max_cloud_cover": float("nan")},
])
def test_search_rejects_invalid_bounds(kwargs):
    with pytest.raises(ValueError):
        search(FakeSession(), **kwargs)


def test_item_datetime_converts_to_aware_utc_and_rejects_naive():
    assert item_datetime(item(date="2020-01-01T12:00:00+02:00")) == datetime(
        2020, 1, 1, 10, tzinfo=timezone.utc
    )
    with pytest.raises(ValueError, match="timezone-naive"):
        item_datetime(item(date="2020-01-01T12:00:00"))


def test_fingerprint_is_order_independent_and_ignores_signature_expiry():
    original = item()
    signed = deepcopy(original)
    signed["assets"]["green"]["href"] += "?sv=2021&sig=secret&se=tomorrow"
    signed["properties"]["msft:expiry"] = "tomorrow"
    signed["assets"]["green"]["msft:expiry"] = "tomorrow"
    reordered = dict(reversed(list(signed.items())))
    assert scene_fingerprint(original) == scene_fingerprint(reordered)
    signed["properties"]["eo:cloud_cover"] = 50
    assert scene_fingerprint(original) != scene_fingerprint(signed)


def test_search_returns_unsigned_copy_and_does_not_mutate_response():
    original = item()
    original["assets"]["green"]["href"] += "?sig=secret&se=tomorrow"
    result = search(FakeSession(page(original)))
    assert result[0]["assets"]["green"]["href"] == ASSET_URL
    assert original["assets"]["green"]["href"].endswith("se=tomorrow")


@pytest.mark.parametrize("reverse", [False, True])
def test_search_rejects_conflicting_duplicate_sources_across_pages(reverse):
    first, second = item(), item()
    second["assets"]["green"]["href"] = ASSET_URL.replace("scene_B1", "revised_B1")
    if reverse:
        first, second = second, first
    session = FakeSession(
        page(first, links=[{"rel": "next", "href": "?token=p2"}]), page(second),
    )
    with pytest.raises(CatalogError, match="Conflicting source metadata"):
        search(session)


def test_search_deduplicates_signature_only_changes():
    first, second = item(), item()
    second["assets"]["green"]["href"] += "?sig=renewed&se=tomorrow"
    second["properties"]["msft:expiry"] = "tomorrow"
    result = search(FakeSession(page(first, second)))
    assert len(result) == 1
    assert result[0]["assets"]["green"]["href"] == ASSET_URL


def test_sign_refreshes_old_credentials_through_official_endpoint():
    signed_href = ASSET_URL + "?sig=new-secret&se=tomorrow"
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    session = FakeSession(FakeResponse({"href": signed_href, "msft:expiry": expiry}))
    assert sign_href(ASSET_URL + "?sig=old-secret&se=yesterday", session) == signed_href
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("GET", SIGN_URL)
    assert kwargs["params"] == {"href": ASSET_URL}


@pytest.mark.parametrize("href", [
    r"D:\data\band.tif", "/tmp/band.tif", "file:///D:/data/band.tif",
    "https://example.org/public/band.tif",
])
def test_sign_preserves_local_paths_and_non_azure_urls(href):
    session = FakeSession()
    assert sign_href(href, session) == href
    assert not session.calls


def test_sign_error_is_explicit_and_does_not_expose_token():
    session = FakeSession(requests.ConnectionError("https://example/?sig=secret"))
    with pytest.raises(CatalogError) as caught:
        sign_href(ASSET_URL, session)
    assert "secret" not in str(caught.value)


def test_source_metadata_retains_calibration_and_removes_signed_urls():
    scene = item()
    source_href = ASSET_URL.replace("_B1.TIF", "_MTL.json")
    scene["assets"]["mtl.json"] = {"href": source_href}
    metadata = {"LANDSAT_METADATA_FILE": {
        "RADIOMETRIC_RESCALING": {"RADIANCE_MULT_BAND_4": 1.23},
        "source_url": source_href + "?sig=secret&se=tomorrow",
    }}
    session = FakeSession(
        FakeResponse({
            "href": source_href + "?sig=secret&se=tomorrow",
            "msft:expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }),
        FakeResponse(metadata),
    )
    result = fetch_source_metadata(scene, session)
    assert result["LANDSAT_METADATA_FILE"]["RADIOMETRIC_RESCALING"] == {
        "RADIANCE_MULT_BAND_4": 1.23,
    }
    assert result["LANDSAT_METADATA_FILE"]["source_url"] == source_href
    assert scene["assets"]["mtl.json"]["href"] == source_href
    assert "sig=secret" in session.calls[1][1]


def test_metadata_absence_and_local_json(tmp_path):
    assert fetch_source_metadata(item()) == {}
    source = tmp_path / "source_MTL.json"
    source.write_text('{"LANDSAT_METADATA_FILE": {"VALUE": 42}}', encoding="utf-8")
    scene = item()
    scene["assets"]["mtl.json"] = {"href": str(source)}
    assert fetch_source_metadata(scene) == {"LANDSAT_METADATA_FILE": {"VALUE": 42}}
    scene["assets"]["mtl.json"]["href"] = source.as_uri()
    assert fetch_source_metadata(scene) == {"LANDSAT_METADATA_FILE": {"VALUE": 42}}


def test_retry_policy_includes_search_post():
    session = get_session()
    try:
        retry = session.get_adapter("https://").max_retries
        assert retry.total == 5
        assert {"GET", "POST"} <= retry.allowed_methods
        assert 429 in retry.status_forcelist
        assert retry.respect_retry_after_header
    finally:
        session.close()


def test_truncated_http_response_body_is_retried():
    class Handler(BaseHTTPRequestHandler):
        calls = 0
        def do_GET(self):
            Handler.calls += 1
            payload = b'{"complete": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload[:5] if self.calls == 1 else payload)
            self.close_connection = True
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    session = get_session()
    session.trust_env = False
    try:
        assert catalog._request_json(
            session, "GET", f"http://127.0.0.1:{server.server_port}/metadata"
        ) == {"complete": True}
        assert Handler.calls == 2
    finally:
        session.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_body_retry_is_bounded_and_does_not_retry_invalid_json(monkeypatch):
    monkeypatch.setattr(catalog.time_module, "sleep", lambda seconds: None)
    session = FakeSession(*[
        requests.exceptions.ChunkedEncodingError("truncated") for _ in range(3)
    ])
    with pytest.raises(CatalogError, match="request failed"):
        catalog._request_json(session, "GET", "https://example.org/metadata")
    assert len(session.calls) == 3
    invalid = FakeResponse([])
    session = FakeSession(invalid)
    with pytest.raises(CatalogError, match="JSON object"):
        catalog._request_json(session, "GET", "https://example.org/metadata")
    assert len(session.calls) == 1
    assert invalid.closed


def test_body_read_timeout_is_retried_without_repeating_status_retries(monkeypatch):
    monkeypatch.setattr(catalog.time_module, "sleep", lambda seconds: None)
    timeout = requests.ConnectionError(catalog.ReadTimeoutError(None, None, "read timed out"))
    session = FakeSession(timeout, FakeResponse({"done": True}))
    assert catalog._request_json(session, "GET", "https://example.org/data") == {"done": True}
    assert len(session.calls) == 2
    denied = FakeSession(FakeResponse({}, status=403))
    with pytest.raises(CatalogError, match="403"):
        catalog._request_json(denied, "GET", "https://example.org/data")
    assert len(denied.calls) == 1


def signed_response(href=ASSET_URL, *, seconds=3600):
    return FakeResponse({
        "href": href + "?sig=temporary",
        "msft:expiry": (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(),
    })


def test_expired_signing_response_refreshes_once_and_never_returns_expired_token():
    session = FakeSession(signed_response(seconds=-1), signed_response())
    assert sign_href(ASSET_URL, session).endswith("?sig=temporary")
    assert len(session.calls) == 2
    assert session.calls[1][2]["headers"]["Cache-Control"] == "no-cache"
    session = FakeSession(signed_response(seconds=-1), signed_response(seconds=-1))
    with pytest.raises(CatalogError, match="expired"):
        sign_href(ASSET_URL, session)
    assert len(session.calls) == 2


def test_signed_asset_mismatch_and_missing_expiry_are_errors():
    for response in (
        signed_response("https://different.blob.core.windows.net/c/other.tif"),
        FakeResponse({"href": ASSET_URL + "?sig=temporary"}),
    ):
        with pytest.raises(CatalogError):
            sign_href(ASSET_URL, FakeSession(response))


def test_signatures_are_reused_only_until_refresh_margin(monkeypatch):
    sessions = []
    def new_session():
        value = FakeSession(signed_response())
        sessions.append(value)
        return value
    monkeypatch.setattr(catalog, "get_session", new_session)
    assert sign_href(ASSET_URL) == sign_href(ASSET_URL)
    assert len(sessions) == 1 and sessions[0].closed
    catalog._SIGN_CACHE[ASSET_URL] = (
        datetime.now(timezone.utc) + timedelta(seconds=30), "near-expiry"
    )
    assert sign_href(ASSET_URL).endswith("?sig=temporary")
    assert len(sessions) == 2


def test_signature_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(catalog, "_SIGN_CACHE_SIZE", 2)
    urls = [ASSET_URL.replace("B1", f"B{index}") for index in range(1, 4)]
    responses = [FakeSession(signed_response(url)) for url in urls]
    monkeypatch.setattr(catalog, "get_session", lambda: responses.pop(0))
    for url in urls:
        sign_href(url)
    assert list(catalog._SIGN_CACHE) == urls[1:]
