"""Mocked adapter tests carry their own minimal protocol data."""

import pytest

from .config_support import install_protocol_data


@pytest.fixture(autouse=True)
def protocol_data(monkeypatch, tmp_path):
    install_protocol_data(monkeypatch, tmp_path)
