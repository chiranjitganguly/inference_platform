import os
import pytest
import requests


@pytest.fixture(scope="session")
def kong_base_url() -> str:
    return os.environ.get("KONG_BASE_URL", "http://localhost:8080")


@pytest.fixture(scope="session")
def api_key() -> str:
    key = os.environ.get("SMOKE_API_KEY", "")
    if not key:
        pytest.skip("SMOKE_API_KEY not set — start the stack and run make seed-kong first")
    return key


@pytest.fixture(scope="session")
def auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture(scope="session")
def phoenix_base_url() -> str:
    url = os.environ.get("PHOENIX_BASE_URL", "http://localhost:6006")
    try:
        requests.get(url, timeout=2)
    except Exception:
        pytest.skip(f"Phoenix not reachable at {url} — start with: make up-obs")
    return url


@pytest.fixture(scope="session")
def langfuse_base_url() -> str:
    return os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3002")


@pytest.fixture(scope="session")
def langfuse_auth_headers() -> dict[str, str]:
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not public_key or not secret_key:
        pytest.skip("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set — obs profile only")
    import base64
    creds = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


@pytest.fixture(scope="session")
def langfuse_reachable(langfuse_base_url: str, langfuse_auth_headers: dict[str, str]) -> str:
    try:
        resp = requests.get(
            f"{langfuse_base_url}/api/public/health",
            headers=langfuse_auth_headers,
            timeout=2,
        )
        if resp.status_code >= 500:
            pytest.skip(f"Langfuse not healthy at {langfuse_base_url} — start with: make up-obs")
    except Exception:
        pytest.skip(f"Langfuse not reachable at {langfuse_base_url} — start with: make up-obs")
    return langfuse_base_url
