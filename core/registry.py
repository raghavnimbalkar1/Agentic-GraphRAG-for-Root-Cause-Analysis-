"""Versioned deployment descriptions; registration never grants repair permission."""

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.schemas import ServiceStatus


class ServiceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    container_name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
    service_type: Literal["api", "frontend", "cache", "load_generator"]
    probe: Literal["grpc", "redis", "http", "process"]
    port: int = Field(ge=0, le=65535, strict=True)
    language: str = Field(max_length=40)
    description: str = Field(max_length=512)
    known_failure_modes: tuple[ServiceStatus, ...] = ()

    @model_validator(mode="after")
    def valid_port(self):
        if (self.probe == "process") != (self.port == 0):
            raise ValueError("Network probes need a port; process-only probes use zero")
        return self


class DeploymentRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    environment_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    revision: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}$")
    network: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
    ownership_label: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,95}$")
    ownership_value: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,95}$")
    services: tuple[ServiceSpec, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_identities(self):
        for field in ("name", "container_name"):
            values = [getattr(service, field) for service in self.services]
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate {field} in deployment registry")
        return self

    @property
    def digest(self) -> str:
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    @property
    def by_name(self) -> dict[str, ServiceSpec]:
        return {service.name: service for service in self.services}


def load_registry(path: Path | None = None) -> DeploymentRegistry:
    source = path or files("core").joinpath("deployments/online_boutique.json")
    with source.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Deployment registry exceeds the size limit")
    return DeploymentRegistry.model_validate_json(raw)


# Only the reviewed bundled profile feeds the existing runtime. Loading another
# description does not reconfigure the executor, graph, canary or live collector.
DEFAULT_DEPLOYMENT = load_registry()
