# Module D: Docker Sandbox Engine

## Overview

A **hardened execution environment** for AI-generated remediation code.

All remediation SOPs (from Module B) execute here — isolated from the host and
production services. Implements **defense-in-depth**:

1. **Ephemeral Containers**: One-time use, destroyed immediately after execution
2. **Read-Only Filesystem**: `/` is read-only; only `/tmp` and bind-mounted volumes writable
3. **Network Isolation**: No outbound internet; can reach only authorized services
4. **Resource Limits**: 512MB RAM, 0.5 CPU cores, 30-second execution timeout
5. **Dropped Capabilities**: No SYS_ADMIN, SYS_PTRACE, NET_ADMIN (prevents container escape)

## Security Model

```
┌─────────────────────────────────────────────────────┐
│  Agent generates remediation code (Module C)         │
│  "SELECT * FROM stale_connections; KILL ..."        │
└──────────────────────┬──────────────────────────────┘
                       │
                       ↓ (SandboxExecutionRequest)
┌─────────────────────────────────────────────────────┐
│  Code Executor validates + parses script             │
│  ✓ No shell injection (Python AST analysis)          │
│  ✓ No dangerous imports (blacklist check)            │
└──────────────────────┬──────────────────────────────┘
                       │
                       ↓ (Docker Create + Start)
┌─────────────────────────────────────────────────────┐
│  Isolation Manager configures security constraints  │
│  • read_only_rootfs=true                             │
│  • memory_limit=512m, cpus=0.5                       │
│  • cap_drop=[SYS_ADMIN, NET_ADMIN, ...]              │
│  • network_mode=bridge (isolated)                    │
└──────────────────────┬──────────────────────────────┘
                       │
                       ↓ (Container executes)
┌─────────────────────────────────────────────────────┐
│  Ephemeral Container (destroyed after use)           │
│  • executes remediation code                         │
│  • timeout enforced by Docker daemon (30s)           │
│  • resources monitored by cgroups                    │
└──────────────────────┬──────────────────────────────┘
                       │
                       ↓ (stdout/stderr captured)
┌─────────────────────────────────────────────────────┐
│  SandboxExecutionResult returned to agent            │
│  • exit_code, stdout, stderr, timing                 │
│  • container destroyed automatically                 │
└─────────────────────────────────────────────────────┘
```

## Phase 4 Implementation Goals

✅ Implement `sandbox_runtime.py`: Container lifecycle management  
✅ Implement `code_executor.py`: Script validation + execution  
✅ Implement `isolation_manager.py`: Security constraints enforcement  
✅ Create base container templates (Python, psql, curl, jq)  
✅ Test escape attempts (verify containment)  
✅ Benchmark overhead (container startup latency)  

## Configuration

### Environment Variables
```
SANDBOX_MEMORY_LIMIT=512m      # Max RAM per container
SANDBOX_CPU_LIMIT=0.5          # Max CPU cores
SANDBOX_TIMEOUT=30             # Execution timeout (seconds)
DOCKER_SOCKET=/var/run/docker.sock  # Docker daemon socket
```

### Pre-built Templates

```
python-slim:3.10        → Python 3.10, pip, requests, pandas
postgres-client:15      → psql, pg_dump, connection tools
redis-tools:7           → redis-cli, key inspection
curl-jq:latest          → curl, jq for HTTP/JSON APIs
```

Each template has:
- Read-only `/opt/app` (remediation script mount)
- Writable `/tmp` (temporary files, scratch space)
- Exec entrypoint: `python /opt/app/remediation.py`

## Usage (Phase 4+)

```python
from module_d_sandbox_engine.engine.sandbox_runtime import SandboxRuntime

runtime = SandboxRuntime()

# Execute remediation in sandbox
result = runtime.execute(
    script_code="SELECT * FROM stale_connections",
    timeout_seconds=30,
    memory_limit_mb=512,
    environment_vars={"DB_HOST": "db.default"},
    read_only_mounts={"/data": "/mnt/source_data"},
)

print(result.exit_code)
print(result.stdout)
print(result.stderr)
```

## Security Hardening Checklist

- [ ] Verify `read_only_rootfs=true` prevents `/etc/passwd` modification
- [ ] Test container escape via:
  - `docker run --privileged` (must be blocked)
  - cgroup escape techniques
  - kernel exploit (Dirty COW, etc.)
- [ ] Monitor resource limits:
  - Memory: Kill process at 512MB
  - CPU: Throttle at 0.5 cores
  - Disk: No write access to host `/`
- [ ] Audit container logs (all execution attempts logged)
- [ ] Rate limiting: Max N containers/minute to prevent resource exhaustion

## Testing

```bash
# Test 1: Verify isolation
python -m module_d_sandbox_engine.engine.sandbox_runtime --test isolation

# Test 2: Stress-test resource limits
python -m module_d_sandbox_engine.engine.sandbox_runtime --test stress

# Test 3: Security hardening
python -m module_d_sandbox_engine.engine.sandbox_runtime --test security
```

## Performance Considerations

- **Container startup**: ~200-500ms (includes Python interpreter load)
- **Execution overhead**: ~50ms (Docker API calls)
- **Total latency**: 250-550ms per remediation (acceptable for ops)

## Troubleshooting

**Q: "docker: permission denied"**  
A: User needs to be in `docker` group: `sudo usermod -aG docker $USER`

**Q: Container hangs at 30 seconds**  
A: Timeout enforced correctly; remediation script may be too slow

**Q: "read-only file system"**  
A: Write operations must target `/tmp` or bind-mounted volumes

---

Dependencies: Docker Engine, Python, Pydantic  
Author: AIOps Research Team  
