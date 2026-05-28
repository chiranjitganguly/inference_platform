import os
import pytest


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
