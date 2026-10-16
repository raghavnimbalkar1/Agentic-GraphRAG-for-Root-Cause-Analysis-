"""Registry loading is configuration, not execution authorization."""

import json

import pytest
from pydantic import ValidationError

from agent.tools.control_gateway import OPERATION_TARGETS
from core.health import NETWORK, PORTS, SERVICES
from core.registry import DEFAULT_DEPLOYMENT, DeploymentRegistry, load_registry
from graph.schema_definitions import SERVICE_REGISTRY


def test_bundled_registry_matches_existing_runtime():
    registry = load_registry()
    assert len(registry.services) == 12
    assert list(registry.by_name) == SERVICES
    assert set(SERVICE_REGISTRY) == set(SERVICES)
    assert NETWORK == registry.network == "boutique-sim"
    assert PORTS == {s.name: s.port for s in registry.services if s.probe == "grpc"}
    for spec in registry.services:
        assert SERVICE_REGISTRY[spec.name].port == spec.port
    assert registry.digest == DEFAULT_DEPLOYMENT.digest
    assert len(registry.digest) == 64


@pytest.mark.parametrize("field", ["name", "container_name"])
def test_duplicate_service_identity_rejected(field):
    data = DEFAULT_DEPLOYMENT.model_dump(mode="json")
    data["services"][1][field] = data["services"][0][field]
    with pytest.raises(ValidationError, match="Duplicate"):
        DeploymentRegistry.model_validate(data)


@pytest.mark.parametrize("change", [
    {"port": 0}, {"port": "6379"}, {"port": 65536}, {"probe": "process"},
    {"command": "unapproved"}, {"name": "../target"},
])
def test_invalid_probe_or_executable_configuration_rejected(change):
    data = DEFAULT_DEPLOYMENT.model_dump(mode="json")
    data["services"][0].update(change)
    with pytest.raises(ValidationError):
        DeploymentRegistry.model_validate(data)


def test_external_description_does_not_reconfigure_runtime_or_permissions(tmp_path):
    data = DEFAULT_DEPLOYMENT.model_dump(mode="json")
    data["environment_id"] = "other-environment"
    data["services"][4].update(name="other-payment", container_name="other-payment-instance")
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(data))
    registry = load_registry(path)
    assert registry.by_name["other-payment"].container_name == "other-payment-instance"
    assert registry.digest != DEFAULT_DEPLOYMENT.digest
    assert "other-payment" not in SERVICES
    assert all("other-payment" not in targets for targets in OPERATION_TARGETS.values())


def test_registry_size_and_schema_are_bounded(tmp_path):
    path = tmp_path / "registry.json"
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="size limit"):
        load_registry(path)
    data = DEFAULT_DEPLOYMENT.model_dump(mode="json")
    data["schema_version"] = 2
    with pytest.raises(ValidationError):
        DeploymentRegistry.model_validate(data)
