#!/bin/bash
# Request the fixed operation authorized for this skill and target.
set -euo pipefail
exec python3 /script/control_client.py restore_cpu
