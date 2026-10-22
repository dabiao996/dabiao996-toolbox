#!/usr/bin/env python3
"""Verified, rootless-capable installer for a pinned 大彪996 source archive."""
import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request


class InstallError(Exception):
    pass


class HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise InstallError("下载重定向离开 HTTPS，已拒绝")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def check_platform():
    if sys.version_info < (3, 9):
        raise InstallError("需要 Python 3.9 或更高版本")
    if platform.system() != "Linux":
        raise InstallError("安装目标仅限 Debian/Ubuntu Linux；当前系统未修改")
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        raise InstallError("无法检测 Linux 发行版") from None
    match = re.search(r'^ID=["\']?([^"\'\n]+)', text, re.M)
    os_id = match.group(1).lower() if match else "unknown"
    arch = platform.machine().lower()
    if os_id not in ("debian", "ubuntu") or arch not in ("x86_64", "aarch64", "arm64"):
        raise InstallError(f"不支持的系统或架构: {os_id}/{arch}")
    if shutil.which("bash") is None:
        raise InstallError("缺少 Bash")


def default_paths():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return Path("/opt/dabiao996"), Path("/usr/local/bin")
    return Path.home() / ".local" / "share" / "dabiao996" / "toolbox", Path.home() / ".local" / "bin"


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("https", "file") or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise InstallError("发布包地址只能是无凭据的 HTTPS 或绝对 file URL")
    if parsed.scheme == "https" and not parsed.hostname:
        raise InstallError("HTTPS 地址缺少主机名")
    if parsed.scheme == "file" and (parsed.netloc not in ("", "localhost") or not parsed.path.startswith("/")):
        raise InstallError("本地发布包必须使用绝对 file URL")
    return parsed


def download(url, output):
    parsed = validate_url(url)
    try:
        if parsed.scheme == "file":
            source = Path(urllib.parse.unquote(parsed.path)).open("rb")
        else:
            source = urllib.request.build_opener(HttpsOnlyRedirect()).open(url, timeout=15)
        size = 0
        with source, output.open("wb") as target:
            while True:
                chunk = source.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise InstallError("发布包超过 20 MiB 限制")
                target.write(chunk)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise InstallError(f"下载失败或网络离线 ({type(exc).__name__})；未安装") from None


def extract(archive, target, version):
    try:
        with tarfile.open(archive, "r:gz") as package:
            members = package.getmembers()
            if len(members) > 1000 or sum(x.size for x in members) > 50 * 1024 * 1024:
                raise InstallError("发布包文件数或解压大小超限")
            names = {item.name.rstrip("/") for item in members}
            if {"c", "VERSION", "toolbox.py"}.issubset(names):
                prefix = ""
            else:
                roots = {name.split("/", 1)[0] for name in names}
                if len(roots) != 1:
                    raise InstallError("发布包缺少工具箱入口或目录结构异常")
                prefix = next(iter(roots)) + "/"
                if not {prefix + item for item in ("c", "VERSION", "toolbox.py")}.issubset(names):
                    raise InstallError("发布包缺少 c、VERSION 或 toolbox.py")
            seen = set()
            for member in members:
                name = member.name.rstrip("/")
                raw = PurePosixPath(name)
                if (not name or name.startswith("/") or "\\" in name or
                        ".." in raw.parts or str(raw) != name or
                        not (member.isfile() or member.isdir())):
                    raise InstallError("发布包含危险路径、链接或特殊文件")
                if name in seen:
                    raise InstallError("发布包含重复路径")
                seen.add(name)
                if prefix and name == prefix.rstrip("/"):
                    continue
                rel = name[len(prefix):] if prefix else name
                if (not rel or any(part in (".git", "config", "data", "state", "logs", "__pycache__")
                                  for part in PurePosixPath(rel).parts)):
                    raise InstallError("发布包含运行数据或不安全目录")
                path = target / rel
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with package.extractfile(member) as source, path.open("wb") as sink:
                        shutil.copyfileobj(source, sink)
                    path.chmod(0o755 if rel == "c" else 0o644)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise InstallError(f"发布包损坏 ({type(exc).__name__})") from None
    if (target / "VERSION").read_text(encoding="utf-8").strip() != version:
        raise InstallError("发布包版本与安装命令不符")
    for source in target.rglob("*.py"):
        try:
            compile(source.read_bytes(), str(source), "exec")
        except (SyntaxError, ValueError):
            raise InstallError(f"Python 语法检查失败: {source.name}") from None
    if subprocess.run(["bash", "-n", str(target / "c")], capture_output=True, check=False).returncode:
        raise InstallError("c 入口脚本语法检查失败")


def install(archive_url, checksum, version, prefix, bin_dir):
    check_platform()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        raise InstallError("必须提供固定的 64 位 SHA256")
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
        raise InstallError("版本格式无效")
    validate_url(archive_url)
    prefix = prefix.expanduser().absolute()
    bin_dir = bin_dir.expanduser().absolute()
    command = bin_dir / "c"
    if prefix.is_symlink() or bin_dir.is_symlink():
        raise InstallError("安装目录或命令目录不能是符号链接")
    if command.exists() or command.is_symlink():
        if not command.is_symlink() or command.resolve() != (prefix / "c").resolve():
            raise InstallError(f"已有其他 c 命令，拒绝覆盖: {command}")
    if prefix.exists():
        current = prefix / "VERSION"
        if not current.is_file() or not (prefix / "c").is_file() or not (prefix / "toolbox.py").is_file():
            raise InstallError(f"安装目录异常，拒绝覆盖: {prefix}")
        try:
            compile((prefix / "toolbox.py").read_bytes(), str(prefix / "toolbox.py"), "exec")
        except (OSError, SyntaxError, ValueError):
            raise InstallError(f"已安装脚本异常，拒绝当作成功: {prefix}") from None
        if subprocess.run(["bash", "-n", str(prefix / "c")], capture_output=True, check=False).returncode:
            raise InstallError(f"已安装入口异常，拒绝当作成功: {prefix}")
        installed_version = current.read_text(encoding="utf-8").strip()
        if installed_version != version:
            raise InstallError(f"已安装 v{installed_version}；请用 c update 升级，不覆盖现有目录")
        if not command.is_symlink():
            bin_dir.mkdir(parents=True, exist_ok=True)
            command.symlink_to(prefix / "c")
        print(f"大彪996 v{version} 已安装，无需重复安装\n命令: {command}\n目录: {prefix}")
        return 0
    prefix.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".dabiao-install-", dir=str(prefix.parent)))
    archive = work / "release.tar.gz"
    stage = work / "toolbox"
    stage.mkdir()
    try:
        download(archive_url, archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest().lower() != checksum.lower():
            raise InstallError("SHA256 校验失败；未安装")
        extract(archive, stage, version)
        bin_dir.mkdir(parents=True, exist_ok=True)
        os.replace(stage, prefix)
        try:
            command.symlink_to(prefix / "c")
        except OSError:
            shutil.rmtree(prefix)
            raise InstallError("命令链接创建失败；已撤销新安装") from None
        print(f"大彪996 v{version} 安装完成\n命令: {command}\n目录: {prefix}")
        if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
            print(f"当前 PATH 未包含 {bin_dir}；可先运行 {command}，下次登录前将该目录加入 PATH。")
        return 0
    finally:
        if work.exists():
            shutil.rmtree(work)


def main():
    default_prefix, default_bin = default_paths()
    parser = argparse.ArgumentParser(description="大彪996 已校验一键安装器")
    parser.add_argument("--archive", required=True, help="固定发布包 HTTPS URL 或本地 file URL")
    parser.add_argument("--sha256", required=True, help="发布包固定 SHA256")
    parser.add_argument("--version", required=True, help="发布包版本")
    parser.add_argument("--prefix", type=Path, default=default_prefix)
    parser.add_argument("--bin-dir", type=Path, default=default_bin)
    args = parser.parse_args()
    try:
        return install(args.archive, args.sha256, args.version, args.prefix, args.bin_dir)
    except InstallError as exc:
        print(f"[安装失败] {exc}", file=sys.stderr)
        return 1
    except (OSError, KeyboardInterrupt) as exc:
        print(f"[安装失败] {type(exc).__name__}；请检查目录权限及安装状态", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
