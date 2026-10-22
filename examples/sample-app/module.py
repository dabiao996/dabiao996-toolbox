#!/usr/bin/env python3
"""Stand-alone read-only example. No service or Dabaio TV is installed."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["status"])
    args = parser.parse_args()
    if args.action != "status":
        return 2
    marker = Path(os.environ.get("DABIAO_SAMPLE_MARKER", "/var/lib/sample-app/installed"))
    # The example never creates or modifies this marker.
    state = "degraded" if marker.exists() else "not_installed"
    print(json.dumps({
        "status": state,
        "address": None,
        "port": None,
        "directory": str(marker.parent) if marker.exists() else None,
        "management": "c apps info sample-app",
        "message": "检测到示例标记，但未验证真实服务" if marker.exists() else "未发现示例应用",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
