#!/usr/bin/env bash
set -Eeuo pipefail

# This short entry point pins the reviewed v0.1.0 source and installer.
release_commit=cdef93948997877066c47c0b818dc8d6e0b22b13
installer_sha=45a0760a77e498b1c8c85faedd977c673fc70542aa87e2d919cccd392442178e
archive_sha=b6e3baa3b59fcda429b8d384cd50e776c0f790fc99f0b8cd574197f044858561

for program in curl sha256sum python3; do
    if ! command -v "$program" >/dev/null 2>&1; then
        printf '[安装失败] 缺少 %s；请先安装后重试。\n' "$program" >&2
        exit 1
    fi
done

work=$(mktemp -d) || {
    printf '[安装失败] 无法创建临时目录。\n' >&2
    exit 1
}
trap 'rm -rf -- "$work"' EXIT

installer_url="https://raw.githubusercontent.com/dabiao996/dabiao996-toolbox/$release_commit/install.py"
archive_url="https://github.com/dabiao996/dabiao996-toolbox/archive/$release_commit.tar.gz"

if ! curl --fail --silent --show-error --location --max-time 30 \
    "$installer_url" --output "$work/install.py"; then
    printf '[安装失败] 无法下载安装器；请检查网络后重试。\n' >&2
    exit 1
fi
if ! printf '%s  %s\n' "$installer_sha" "$work/install.py" | sha256sum -c - >/dev/null; then
    printf '[安装失败] 安装器 SHA256 校验失败；未执行安装。\n' >&2
    exit 1
fi

python3 "$work/install.py" \
    --archive "$archive_url" --sha256 "$archive_sha" --version 0.1.0 "$@"
