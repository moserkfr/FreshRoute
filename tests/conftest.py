from __future__ import annotations

import pytest

from freshroute.config import AppConfig, load_config


@pytest.fixture(scope="session")
def cfg() -> AppConfig:
    return load_config()
