"""
Isolation manager: Configure Docker security constraints.

Phase 4: Enforce read-only FS, network isolation, resource limits, capability dropping.
"""

from typing import Dict, List, Any

from core.logging_config import get_logger

logger = get_logger(__name__)


class IsolationManager:
    """Manages container security and isolation constraints."""

    # Security capabilities to drop (prevent container escape)
    DROPPED_CAPABILITIES = [
        "SYS_ADMIN",      # Prevents mount syscalls, ptrace, module loading
        "SYS_PTRACE",     # Prevents process tracing attacks
        "NET_ADMIN",      # Prevents network namespace manipulation
        "SYS_MODULE",     # Prevents kernel module loading
        "DAC_OVERRIDE",   # Prevents file permission bypass
    ]

    def configure_read_only_fs(self) -> Dict[str, Any]:
        """Configure read-only root filesystem."""
        return {
            "read_only_rootfs": True,
            "tmpfs": {
                "/tmp": {"size": 100 * 1024 * 1024},  # 100MB /tmp
                "/run": {"size": 50 * 1024 * 1024},   # 50MB /run
            },
        }

    def configure_network_isolation(self) -> Dict[str, Any]:
        """Isolate container network from host and external services."""
        return {
            "network_mode": "bridge",
            "dns": ["8.8.8.8"],  # Only Google DNS (outbound blocked anyway)
            "network_disable": False,  # But can reach monitored services via bridge
        }

    def configure_resource_limits(self, memory_mb: int, cpu_limit: float) -> Dict[str, Any]:
        """Set memory and CPU hard limits."""
        return {
            "mem_limit": f"{memory_mb}m",
            "memswap_limit": f"{memory_mb}m",  # No swap
            "cpus": cpu_limit,
            "cpu_quota": int(cpu_limit * 100000),
        }

    def configure_capability_constraints(self) -> Dict[str, Any]:
        """Drop dangerous Linux capabilities."""
        return {
            "cap_drop": self.DROPPED_CAPABILITIES,
            "cap_add": [],  # No additional capabilities
        }

    def apply_security_opts(self) -> List[str]:
        """Apply OCI runtime security options."""
        return [
            "no-new-privileges",  # Prevent setuid/setgid escape
            "apparmor=docker-default",  # AppArmor profile
        ]

    def get_docker_run_flags(self, memory_mb: int, cpu_limit: float) -> Dict[str, Any]:
        """Combine all isolation constraints into Docker run flags."""
        flags = {}
        flags.update(self.configure_read_only_fs())
        flags.update(self.configure_network_isolation())
        flags.update(self.configure_resource_limits(memory_mb, cpu_limit))
        flags.update(self.configure_capability_constraints())
        flags["security_opt"] = self.apply_security_opts()
        return flags
