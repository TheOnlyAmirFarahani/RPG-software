"""
conftest.py – shared fixtures for integration & system test suites.
"""

import time
import requests
import pytest

BASE = __import__("os").getenv("GATEWAY_URL", "http://localhost:8080")
TIMEOUT = int(__import__("os").getenv("STACK_TIMEOUT", "120"))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (require live stack)"
    )
    config.addinivalue_line(
        "markers",
        "system: marks tests as system / end-to-end tests (require live stack)"
    )


@pytest.fixture(scope="session", autouse=True)
def wait_for_stack():
    """Block until the gateway's public health endpoint responds."""
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        try:
            r = requests.get(f"{BASE}/scores/hall-of-fame", timeout=3)
            if r.status_code == 200:
                return
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(2)
    pytest.exit(
        f"Gateway at {BASE} did not become healthy within {TIMEOUT}s",
        returncode=1
    )
