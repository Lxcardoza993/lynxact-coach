"""Shared fixtures. Keeps Drive cloud mode OFF for the whole suite by
default — .env on dev machines carries a real refresh token, and tests must
never hit the real network. Cloud tests fake the layer on explicitly.
"""
import pytest


@pytest.fixture(autouse=True)
def _cloud_off(monkeypatch):
    for key in ("DRIVE_REFRESH_TOKEN", "DRIVE_ROOT_FOLDER_ID",
                "DRIVE_CLIENT_ID", "DRIVE_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
