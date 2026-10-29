"""Shared test fixtures (no AWS): `settings` is the ADR Generator's Settings with a tmp data_dir."""
from __future__ import annotations

import pytest

from adr_generator.config import Settings
from tests.adr_generator.conftest import make_settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)
