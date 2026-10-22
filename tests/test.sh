#!/usr/bin/env bash
set -euo pipefail
root=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export PYTHONDONTWRITEBYTECODE=1
bash -n "$root/c"
python3 -B -c 'from pathlib import Path; import sys; root=Path(sys.argv[1]); [compile(p.read_bytes(), str(p), "exec") for p in root.rglob("*.py")]' "$root"
python3 -B -m unittest discover -s "$root/tests" -v
