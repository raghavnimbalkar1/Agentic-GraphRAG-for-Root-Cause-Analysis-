"""
Module D: Docker Sandbox Engine

Phase 4 (FUTURE): Secure execution environment for AI-generated remediation code.

**CRITICAL SECURITY**: All remediation code runs in ephemeral, isolated Docker containers
— never on the host. Prevents code injection and lateral movement attacks.

Constraints enforced:
- Read-only root filesystem (/ is r/o, /tmp is writable)
- Network isolation: No outbound internet, can reach monitored services only
- Resource limits: 512MB RAM, 0.5 CPU, 30-second timeout (enforced by daemon)
- Dropped capabilities: No SYS_ADMIN, SYS_PTRACE, NET_ADMIN

Features:
1. ephemeral_sandbox: One-time use containers, destroyed after execution
2. code_executor: Parse + validate remediation scripts, execute in sandbox
3. isolation_manager: Configure security constraints, monitor resource usage
4. templates: Pre-built container images with common tools (psql, curl, etc.)

Implementation:
- Docker Engine API (not docker-py CLI, for better control)
- OCI runtime specs for security policies
- Container health monitoring + graceful timeout enforcement

Phase 1 files stubbed; Phase 4 will implement full sandbox orchestration.
"""

__version__ = "0.1.0"
