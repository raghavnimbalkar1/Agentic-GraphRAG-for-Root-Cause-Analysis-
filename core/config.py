"""
Core configuration loader - reads .env and environment variables.

Phase 1: Basic configuration for simulation and Neo4j connectivity.
Phase 2+: Extended configuration for LLM endpoints, sandbox constraints, etc.

Usage:
    from core.config import Config
    config = Config()
    print(config.neo4j_uri)
"""

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


@dataclass
class Config:
    """Application-wide configuration from environment variables."""

    # Neo4j Configuration
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str

    # LLM Configuration
    llm_model: str
    llm_base_url: str
    llm_api_key: Optional[str]

    # Docker Configuration
    docker_socket: str
    sandbox_memory_limit: str
    sandbox_cpu_limit: float
    sandbox_timeout: int

    # Application Configuration
    log_level: str
    environment: str
    enable_telemetry: bool

    # Cluster Configuration
    cluster_name: str
    cluster_type: str  # 'docker-compose' or 'minikube'

    # Phase Configuration
    current_phase: int

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from .env file and environment variables."""
        load_dotenv()

        return cls(
            neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
            neo4j_password=os.getenv("NEO4J_PASSWORD", "password"),
            llm_model=os.getenv("LLM_MODEL", "qwen2.5-coder:14b"),
            llm_base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434"),
            llm_api_key=os.getenv("LLM_API_KEY"),
            docker_socket=os.getenv("DOCKER_SOCKET", "/var/run/docker.sock"),
            sandbox_memory_limit=os.getenv("SANDBOX_MEMORY_LIMIT", "512m"),
            sandbox_cpu_limit=float(os.getenv("SANDBOX_CPU_LIMIT", "0.5")),
            sandbox_timeout=int(os.getenv("SANDBOX_TIMEOUT", "30")),
            log_level=os.getenv("LOG_LEVEL", "DEBUG"),
            environment=os.getenv("ENVIRONMENT", "development"),
            enable_telemetry=os.getenv("ENABLE_TELEMETRY", "true").lower() == "true",
            cluster_name=os.getenv("CLUSTER_NAME", "online-boutique"),
            cluster_type=os.getenv("CLUSTER_TYPE", "docker-compose"),
            current_phase=int(os.getenv("CURRENT_PHASE", "1")),
        )


# Global config instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Retrieve or initialize the global config singleton."""
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config
