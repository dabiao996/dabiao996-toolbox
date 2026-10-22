#!/usr/bin/env bash
set -euo pipefail

source_path=${BASH_SOURCE[0]}
while [[ -L "$source_path" ]]; do
  link_dir=$(cd -P -- "$(dirname -- "$source_path")" && pwd)
  link_target=$(readlink "$source_path")
  if [[ "$link_target" = /* ]]; then source_path=$link_target
  else source_path=$link_dir/$link_target
  fi
done
root=$(cd -P -- "$(dirname -- "$source_path")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
  printf '大彪996 需要 Python 3.9 或更高版本。\n' >&2
  exit 5
fi
exec python3 "$root/toolbox.py" "$@"
