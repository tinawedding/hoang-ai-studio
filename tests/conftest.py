import os
import tempfile
import pytest

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="hn-plus-tests-")
os.environ["APP_ACCESS_KEY"] = "test-password-only"
os.environ["SESSION_SECRET"] = "test-secret-only-never-deploy"
os.environ["REQUIRE_ACCESS_KEY"] = "true"

from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as instance:
        assert instance.post("/api/session", json={"password":"test-password-only"}).status_code == 200
        yield instance
