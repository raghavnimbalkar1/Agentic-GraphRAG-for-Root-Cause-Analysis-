"""Test suite for core utilities."""

import pytest

from core.config import Config, get_config
from core.schemas import ServiceEntity, RemediationSOP


def test_config_loading():
    """Test configuration loading from environment."""
    config = get_config()
    assert config.neo4j_uri is not None


def test_schema_validation():
    """Test Pydantic schema validation."""
    service = ServiceEntity(
        service_id="svc-1",
        name="frontend",
        container_image="gcr.io/boutique/frontend:v0.1",
    )
    assert service.name == "frontend"
