"""
Contract tests for response caching layer (feature 007).

US1: Cache hit — identical request served from cache in < 10 ms; x-litellm-cache-hit: True.
US2: Cache miss — different model/messages/temperature always calls provider; x-litellm-cache-hit: False.
US3: TTL expiry — post-TTL requests are treated as cache misses (skipped unless LITELLM_CACHE_TTL <= 60).
US4: Streaming bypass — stream:true never reads/writes cache; x-litellm-cache-hit absent.
"""
import os
import time
import uuid

import pytest
import requests


# ── Helpers ───────────────────────────────────────────────────────────────────


def _unique_content() -> str:
    """Generate a unique message content to avoid cross-test cache pollution."""
    return f"Cache test {uuid.uuid4().hex[:8]}. Reply with one word only."


def _cacheable_payload(content: str, temperature: float = 0.0, model: str = "gpt-4o-mini") -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature,
    }


def _post(kong_base_url: str, auth_headers: dict, payload: dict, stream: bool = False) -> requests.Response:
    return requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers={**auth_headers, "Content-Type": "application/json"},
        stream=stream,
        timeout=30,
    )


def _cache_hit_header(resp: requests.Response) -> str | None:
    """Return the x-litellm-cache-hit header value, case-insensitively."""
    for key, val in resp.headers.items():
        if key.lower() == "x-litellm-cache-hit":
            return val
    return None


# ── US1: Cache Hit Returns Stored Response ────────────────────────────────────


def test_cache_hit_returns_identical_response(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Second identical request returns x-litellm-cache-hit: True and identical content."""
    content = _unique_content()
    payload = _cacheable_payload(content)

    first = _post(kong_base_url, auth_headers, payload)
    assert first.status_code == 200, f"First request failed: {first.text[:200]}"
    first_body = first.json()
    first_content = first_body["choices"][0]["message"]["content"]

    second = _post(kong_base_url, auth_headers, payload)
    assert second.status_code == 200, f"Second request failed: {second.text[:200]}"
    second_body = second.json()
    second_content = second_body["choices"][0]["message"]["content"]

    hit_header = _cache_hit_header(second)
    assert hit_header == "True", \
        f"Expected x-litellm-cache-hit: True on second identical request, got {hit_header!r}"
    assert second_content == first_content, \
        f"Cache hit content mismatch: {first_content!r} vs {second_content!r}"


def test_cache_hit_latency_under_10ms(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Cache hit response must arrive in under 10 ms (SC-001)."""
    content = _unique_content()
    payload = _cacheable_payload(content)

    first = _post(kong_base_url, auth_headers, payload)
    assert first.status_code == 200

    t_start = time.monotonic()
    second = _post(kong_base_url, auth_headers, payload)
    second.json()
    elapsed = time.monotonic() - t_start

    hit_header = _cache_hit_header(second)
    assert hit_header == "True", \
        f"Second request was not a cache hit (x-litellm-cache-hit={hit_header!r}); latency assertion skipped"
    assert elapsed < 0.010, \
        f"Cache hit latency {elapsed * 1000:.1f} ms exceeds 10 ms limit (SC-001)"


def test_cache_status_header_present_on_non_streaming(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """x-litellm-cache-hit header is present on all non-streaming responses."""
    payload = _cacheable_payload(_unique_content())
    resp = _post(kong_base_url, auth_headers, payload)
    assert resp.status_code == 200

    hit_header = _cache_hit_header(resp)
    assert hit_header is not None, \
        "x-litellm-cache-hit header must be present on non-streaming responses"
    assert hit_header in ("True", "False"), \
        f"x-litellm-cache-hit must be 'True' or 'False', got {hit_header!r}"


def test_cache_hit_increments_prometheus_counter(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Cache hit increments litellm_cache_hit_count Prometheus counter (FR-010)."""
    prometheus_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
    try:
        baseline_resp = requests.get(
            f"{prometheus_url}/api/v1/query",
            params={"query": "litellm_cache_hit_count"},
            timeout=3,
        )
    except Exception:
        pytest.skip(f"Prometheus not reachable at {prometheus_url}")

    def _get_count() -> float:
        r = requests.get(
            f"{prometheus_url}/api/v1/query",
            params={"query": "litellm_cache_hit_count"},
            timeout=3,
        )
        results = r.json().get("data", {}).get("result", [])
        if not results:
            return 0.0
        return float(results[0]["value"][1])

    before = _get_count()

    content = _unique_content()
    payload = _cacheable_payload(content)
    _post(kong_base_url, auth_headers, payload)   # miss — populates cache
    _post(kong_base_url, auth_headers, payload)   # hit — should increment counter

    time.sleep(2)  # allow Prometheus scrape interval
    after = _get_count()

    assert after > before, \
        f"litellm_cache_hit_count did not increment: before={before}, after={after}"


# ── US2: Cache Miss Triggers Fresh Provider Call ──────────────────────────────


def test_cache_miss_different_model(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Different model produces a cache miss even for identical messages."""
    content = _unique_content()

    first = _post(kong_base_url, auth_headers, _cacheable_payload(content, model="gpt-4o-mini"))
    assert first.status_code == 200

    second = _post(kong_base_url, auth_headers, _cacheable_payload(content, model="gpt-4o"))
    assert second.status_code == 200

    hit_header = _cache_hit_header(second)
    assert hit_header != "True", \
        f"Different model must not produce a cache hit, got x-litellm-cache-hit={hit_header!r}"


def test_cache_miss_different_messages(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Different message content produces a cache miss."""
    base = f"Cache miss messages test {uuid.uuid4().hex[:6]}"

    first = _post(kong_base_url, auth_headers, _cacheable_payload(f"{base} A"))
    assert first.status_code == 200

    second = _post(kong_base_url, auth_headers, _cacheable_payload(f"{base} B"))
    assert second.status_code == 200

    hit_header = _cache_hit_header(second)
    assert hit_header != "True", \
        f"Different message content must not produce a cache hit, got {hit_header!r}"


def test_cache_miss_different_temperature(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Different temperature produces a cache miss for the same model and messages."""
    content = _unique_content()

    first = _post(kong_base_url, auth_headers, _cacheable_payload(content, temperature=0.0))
    assert first.status_code == 200

    second = _post(kong_base_url, auth_headers, _cacheable_payload(content, temperature=0.7))
    assert second.status_code == 200

    hit_header = _cache_hit_header(second)
    assert hit_header != "True", \
        f"Different temperature must not produce a cache hit, got {hit_header!r}"


def test_cache_miss_stored_for_next_hit(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """A cache miss stores the response; the immediately following identical request is a hit."""
    content = _unique_content()
    payload = _cacheable_payload(content)

    miss = _post(kong_base_url, auth_headers, payload)
    assert miss.status_code == 200
    miss_header = _cache_hit_header(miss)
    assert miss_header != "True", \
        f"First request must be a cache miss, got x-litellm-cache-hit={miss_header!r}"

    hit = _post(kong_base_url, auth_headers, payload)
    assert hit.status_code == 200
    hit_header = _cache_hit_header(hit)
    assert hit_header == "True", \
        f"Second identical request must be a cache hit, got x-litellm-cache-hit={hit_header!r}"


# ── US3: TTL Expiry ───────────────────────────────────────────────────────────


@pytest.mark.skipif(
    int(os.environ.get("LITELLM_CACHE_TTL", "9999")) > 60,
    reason="LITELLM_CACHE_TTL > 60s — set to ≤ 60 for TTL expiry test (e.g. LITELLM_CACHE_TTL=30)",
)
def test_cache_ttl_expiry(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    """After TTL elapses, the same request is treated as a cache miss (SC-004)."""
    ttl = int(os.environ["LITELLM_CACHE_TTL"])
    content = _unique_content()
    payload = _cacheable_payload(content)

    miss = _post(kong_base_url, auth_headers, payload)
    assert miss.status_code == 200
    assert _cache_hit_header(miss) != "True", "First request should be a cache miss"

    hit = _post(kong_base_url, auth_headers, payload)
    assert hit.status_code == 200
    assert _cache_hit_header(hit) == "True", "Second request should be a cache hit before TTL"

    time.sleep(ttl + 5)

    expired = _post(kong_base_url, auth_headers, payload)
    assert expired.status_code == 200
    expired_header = _cache_hit_header(expired)
    assert expired_header != "True", \
        f"Post-TTL request must be a cache miss, got x-litellm-cache-hit={expired_header!r}"
    assert expired.json().get("choices"), "Post-TTL response must be a valid OpenAI completion"


# ── US4: Streaming Bypass ─────────────────────────────────────────────────────


def test_streaming_bypass_no_cache_hit_header(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Streaming requests do not produce x-litellm-cache-hit: True (SC-005, FR-005)."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": _unique_content()}],
        "stream": True,
    }
    resp = _post(kong_base_url, auth_headers, payload, stream=True)
    assert resp.status_code == 200

    content_type = resp.headers.get("Content-Type", "")
    assert content_type.startswith("text/event-stream"), \
        f"Streaming response must be text/event-stream, got {content_type!r}"

    hit_header = _cache_hit_header(resp)
    assert hit_header != "True", \
        f"Streaming response must not have x-litellm-cache-hit: True, got {hit_header!r}"
    resp.close()


def test_streaming_never_populates_cache(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """A streaming request does not write to cache; subsequent non-streaming call is a miss."""
    content = _unique_content()
    stream_payload = _cacheable_payload(content) | {"stream": True}
    non_stream_payload = _cacheable_payload(content)

    stream_resp = _post(kong_base_url, auth_headers, stream_payload, stream=True)
    assert stream_resp.status_code == 200
    stream_resp.close()

    non_stream_resp = _post(kong_base_url, auth_headers, non_stream_payload)
    assert non_stream_resp.status_code == 200

    hit_header = _cache_hit_header(non_stream_resp)
    assert hit_header != "True", \
        (
            "Non-streaming request after identical streaming request must be a cache miss "
            f"(streaming must not write to cache), got x-litellm-cache-hit={hit_header!r}"
        )
