#!/usr/bin/env python3
"""大彪996 core. Python standard library only; no privileged operations."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
CONFIG = Path(os.environ.get("DABIAO_CONFIG_DIR", Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "dabiao996")).expanduser()
STATE = Path(os.environ.get("DABIAO_STATE_DIR", Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "dabiao996")).expanduser()
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
VER_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")
GROUPS = [
    ("1", "系统信息监控", "system info"), ("2", "系统更新维护", None),
    ("3", "Docker", "docker check"), ("4", "网站", None),
    ("5", "SSL", None), ("6", "网络", None), ("7", "SSH 安全", None),
    ("8", "应用商店", "apps list"), ("9", "备份恢复", None),
    ("10", "文件", None), ("11", "定时任务", None),
    ("12", "迁移", None), ("88", "设置", "config show"),
    ("99", "检查更新", "update check"), ("0", "退出", "exit"),
]


class ToolError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def log_event(level, event):
    """Log only fixed event identifiers, never user arguments, module output or secrets."""
    try:
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (STATE / "events.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{__import__('datetime').datetime.now().isoformat()} {level} {event}\n")
    except OSError:
        print("[警告] 事件日志不可写", file=sys.stderr)


def load_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"配置或元信息无法读取: {path.name} ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        raise ToolError(f"配置或元信息必须是 JSON 对象: {path.name}")
    return data


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".dabiao-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def platform_info():
    system = platform.system()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)
    os_id = system.lower()
    if system == "Linux":
        try:
            text = Path("/etc/os-release").read_text(encoding="utf-8")
            match = re.search(r'^ID=["\']?([^"\'\n]+)', text, re.M)
            os_id = match.group(1).lower() if match else "unknown"
        except OSError:
            os_id = "unknown"
    return system, os_id, arch


def supported():
    system, os_id, arch = platform_info()
    return system == "Linux" and os_id in ("debian", "ubuntu") and arch in ("amd64", "arm64")


def detected_addresses():
    addresses = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None):
            address = item[4][0].split("%", 1)[0]
            ip = ipaddress.ip_address(address)
            if not ip.is_loopback and not ip.is_link_local:
                addresses.add(str(ip))
    except (OSError, ValueError):
        pass
    return sorted(addresses)


def system_info():
    system, os_id, arch = platform_info()
    print(f"系统: {system} ({os_id})\n架构: {arch}\n内核: {platform.release()}")
    print(f"主机名: {socket.gethostname()}")
    print("检测到的本机地址: " + (", ".join(detected_addresses()) or "未检测到；不猜测公网 IP"))
    print(f"工具箱版本: {VERSION}\n项目目录: {ROOT}\n配置目录: {CONFIG}\n状态目录: {STATE}")


def doctor():
    print("大彪996 环境诊断")
    system_info()
    problems = []
    if sys.version_info < (3, 9):
        problems.append("需要 Python 3.9+")
    if not supported():
        problems.append("此系统或架构不在目标范围；仅 Debian/Ubuntu amd64/arm64")
    if not os.access(ROOT, os.R_OK):
        problems.append("项目目录不可读")
    if (CONFIG / "update.json").exists():
        try:
            update_config()
            print("工具箱更新源: 已配置")
        except ToolError as exc:
            problems.append(str(exc))
    else:
        print("工具箱更新源: 未配置")
    try:
        modules = discover()
        print(f"已发现应用模块: {len(modules)}")
    except ToolError as exc:
        problems.append(str(exc))
    if problems:
        for problem in problems:
            print(f"[诊断] {problem}")
        log_event("WARN", "doctor_issues")
        return 5
    print("基础检查通过")
    return 0


def docker_check():
    if not supported():
        print("Docker 检测仅支持目标 Linux；当前环境不执行安装或维护")
        return 5
    docker = shutil.which("docker")
    if not docker:
        print("Docker: 未安装（只读检测）")
        return 0
    result = subprocess.run([docker, "--version"], capture_output=True, text=True, timeout=5, check=False)
    print("Docker: " + (result.stdout.strip() if result.returncode == 0 else "命令存在但检测异常"))
    return 0 if result.returncode == 0 else 4


def validate_manifest(path):
    if path.is_symlink() or not path.is_file():
        raise ToolError("模块元信息必须是普通文件，不能是符号链接")
    data = load_json(path)
    required = {"schema", "id", "name", "version", "category", "entry", "actions", "os", "arch"}
    if set(data) != required or data["schema"] != 1:
        raise ToolError(f"模块元信息字段或 schema 错误: {path.name}")
    if not isinstance(data["id"], str) or not ID_RE.fullmatch(data["id"]):
        raise ToolError("模块 id 只能使用小写字母、数字和连字符，长度 2 至 32")
    for key in ("name", "version", "category", "entry"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise ToolError(f"模块字段无效: {key}")
    if data["category"] not in ("apps", "system", "docker", "web", "ssl", "network", "backup", "files", "cron", "migration"):
        raise ToolError("模块分类无效")
    if not VER_RE.fullmatch(data["version"]):
        raise ToolError("模块版本格式无效")
    if (not isinstance(data["actions"], list) or not data["actions"] or
            len(data["actions"]) != len(set(str(x) for x in data["actions"])) or
            "status" not in data["actions"] or
            any(x not in ("status", "install", "update", "uninstall") for x in data["actions"])):
        raise ToolError("模块 actions 无效，必须包含 status")
    for key, choices in (("os", {"debian", "ubuntu"}), ("arch", {"amd64", "arm64"})):
        if not isinstance(data[key], list) or not data[key] or any(x not in choices for x in data[key]):
            raise ToolError(f"模块 {key} 无效")
    rel = PurePosixPath(data["entry"])
    if rel.is_absolute() or ".." in rel.parts or "." in rel.parts or not rel.parts or "\\" in data["entry"]:
        raise ToolError("模块入口路径不安全")
    entry = path.parent / str(rel)
    if not entry.resolve().is_relative_to(path.parent.resolve()) or entry.is_symlink() or not entry.is_file():
        raise ToolError("模块入口必须是模块目录内的普通文件")
    data["_entry"] = entry
    data["_manifest"] = path
    return data


def registry():
    path = CONFIG / "modules.json"
    data = load_json(path) if path.exists() else {"modules": []}
    if set(data) != {"modules"} or not isinstance(data["modules"], list) or any(not isinstance(x, str) for x in data["modules"]):
        raise ToolError("modules.json 注册表格式错误")
    return data["modules"]


def discover():
    paths = sorted((ROOT / "apps").glob("*/module.json")) if (ROOT / "apps").exists() else []
    paths += [Path(x) for x in registry()]
    modules = {}
    for path in paths:
        data = validate_manifest(path)
        if data["id"] in modules:
            raise ToolError(f"重复模块 id: {data['id']}")
        modules[data["id"]] = data
    return modules


def module_register(path_arg):
    path = Path(path_arg).expanduser().absolute()
    module = validate_manifest(path)
    modules = discover()
    if module["id"] in modules:
        if modules[module["id"]]["_manifest"].resolve() == path.resolve():
            print(f"已注册，未重复写入: {module['id']}")
            return 0
        raise ToolError(f"模块 id 已存在: {module['id']}")
    entries = registry()
    entries.append(str(path))
    save_json(CONFIG / "modules.json", {"modules": entries})
    log_event("INFO", "module_registered")
    print(f"已注册模块: {module['id']}")
    return 0


def module_unregister(module_id):
    modules = discover()
    if module_id not in modules:
        print("模块未注册，状态未改变")
        return 0
    path = str(modules[module_id]["_manifest"])
    entries = registry()
    if path not in entries:
        raise ToolError("内置模块不能取消注册")
    save_json(CONFIG / "modules.json", {"modules": [x for x in entries if x != path]})
    log_event("INFO", "module_unregistered")
    print(f"已取消注册: {module_id}（未删除模块文件或数据）")
    return 0


def modules_list():
    modules = discover()
    if not modules:
        print("暂无已接入应用。可先查看 examples/sample-app；大彪TV 尚未接入。")
    for module in modules.values():
        print(f"{module['id']}  {module['name']}  v{module['version']}  动作: {','.join(module['actions'])}")
    return 0


def validate_status(data):
    if not isinstance(data, dict) or data.get("status") not in ("not_installed", "installed", "degraded", "unsupported"):
        raise ToolError("模块状态输出无效", 4)
    allowed = {"status", "address", "port", "directory", "management", "message"}
    if set(data) != allowed:
        raise ToolError("模块状态字段必须严格匹配规范", 4)
    for key in ("address", "directory", "management", "message"):
        if data[key] is not None and (not isinstance(data[key], str) or "\n" in data[key] or "\r" in data[key] or len(data[key]) > 500):
            raise ToolError(f"模块状态字段无效: {key}", 4)
    if data["port"] is not None and (type(data["port"]) is not int or not 1 <= data["port"] <= 65535):
        raise ToolError("模块端口无效", 4)
    address = data["address"]
    if address is not None:
        try:
            ipaddress.ip_address(address)
        except ValueError:
            raise ToolError("模块地址必须是实际检测到的 IP，不能是占位符或 URL", 4) from None
    for key in ("directory", "management", "message"):
        value = data[key] or ""
        if (re.search(r"(?i)(?:^|[^a-z])(?:password|passwd|token|secret|api[_-]?key)(?:\s*[=:]\s*|\s+)\S+", value) or
                re.search(r"://[^\s/]+:[^\s/@]+@", value)):
            raise ToolError("模块状态含疑似凭据，已拒绝显示", 4)
    return data


def module_run(module_id, action, args):
    if not ID_RE.fullmatch(module_id):
        raise ToolError("模块 id 无效")
    modules = discover()
    if module_id not in modules:
        raise ToolError(f"未知模块: {module_id}")
    module = modules[module_id]
    if action not in module["actions"]:
        raise ToolError(f"模块不支持动作: {action}")
    system, os_id, arch = platform_info()
    if system != "Linux" or os_id not in module["os"] or arch not in module["arch"]:
        print(f"{module_id}: 不支持当前环境 ({system}/{os_id}/{arch})")
        return 5
    if action == "status" and args:
        raise ToolError("status 不接受附加参数")
    # No shell or implicit privilege escalation. A registered module is trusted code.
    result = subprocess.run([sys.executable, str(module["_entry"]), action, *args],
                            capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        log_event("ERROR", "module_execution_failed")
        raise ToolError(f"模块 {module_id} {action} 失败 (退出码 {result.returncode})；请检查模块自身诊断", 4)
    if action == "status":
        data = parse_module_status(result.stdout)
        print_module_status(module_id, data)
    else:
        log_event("INFO", "module_action_completed")
        print(f"模块 {module_id} {action} 动作已返回；正在回查实际状态...")
        return post_action_status(module_id, module, action)
    return 0


def parse_module_status(output):
    try:
        return validate_status(json.loads(output))
    except ValueError:
        raise ToolError("模块状态不是有效 JSON", 4) from None


def print_module_status(module_id, data):
        labels = {"not_installed": "未安装", "installed": "已安装", "degraded": "异常", "unsupported": "不支持"}
        print(f"模块: {module_id}\n状态: {labels[data['status']]}\n地址: {data['address'] or '未检测到'}")
        print(f"端口: {data['port'] if data['port'] is not None else '未检测到'}")
        print(f"目录: {data['directory'] or '未检测到'}\n管理命令: {data['management'] or '未提供'}")
        print(f"说明: {data['message'] or '无'}")


def post_action_status(module_id, module, action):
    result = subprocess.run([sys.executable, str(module["_entry"]), "status"],
                            capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        raise ToolError(f"模块 {module_id} 的状态回查失败 (退出码 {result.returncode})", 4)
    data = parse_module_status(result.stdout)
    print_module_status(module_id, data)
    if action in ("install", "update") and data["status"] != "installed":
        raise ToolError("应用动作已返回，但实际状态并非已安装；请诊断模块", 4)
    if action == "uninstall" and data["status"] != "not_installed":
        raise ToolError("卸载动作已返回，但实际状态仍显示应用存在；请诊断模块", 4)
    return 0


def install_command(target_arg):
    target = Path(target_arg).expanduser().absolute() if target_arg else Path.home() / ".local" / "bin" / "c"
    if target.name != "c":
        raise ToolError("快捷命令目标文件名必须是 c")
    if target.exists() or target.is_symlink():
        if target.is_symlink() and target.resolve() == ROOT / "c":
            print(f"快捷命令已存在: {target}")
            return 0
        raise ToolError(f"目标已存在，拒绝覆盖: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(ROOT / "c")
    print(f"已建立快捷命令: {target}\n请将 {target.parent} 加入 PATH")
    return 0


def config_show():
    print(f"配置目录: {CONFIG}\n状态目录: {STATE}")
    print(f"工具箱更新源: {'已配置' if (CONFIG / 'update.json').exists() else '未配置'}")
    print(f"模块注册数: {len(registry())}")
    print("应用更新由各模块独立提供；总脚本更新使用 c update。")
    return 0


def status_show():
    print(f"大彪996 v{VERSION}")
    print(f"目标系统支持: {'是' if supported() else '否'}")
    print(f"已注册/发现模块: {len(discover())}")
    print(f"工具箱更新源: {'已配置' if (CONFIG / 'update.json').exists() else '未配置'}")
    print(f"状态日志: {STATE / 'events.log'}")
    return 0


def logs_show(count_arg=None):
    if count_arg is not None and (not count_arg.isdecimal() or not 1 <= int(count_arg) <= 200):
        raise ToolError("日志行数必须是 1 至 200")
    count = int(count_arg) if count_arg else 30
    path = STATE / "events.log"
    if not path.exists():
        print("暂无事件日志")
        return 0
    from collections import deque
    with path.open(encoding="utf-8") as stream:
        for line in deque(stream, maxlen=count):
            print(line.rstrip("\n"))
    return 0


def update_config():
    path = CONFIG / "update.json"
    if not path.is_file():
        raise ToolError("总工具箱更新源未配置；请按 README 配置 update.json", 2)
    data = load_json(path)
    if set(data) != {"source", "version", "sha256"}:
        raise ToolError("update.json 只接受 source、version、sha256 三个字段")
    if not all(isinstance(x, str) for x in data.values()):
        raise ToolError("update.json 字段必须是字符串")
    if not VER_RE.fullmatch(data["version"]) or not re.fullmatch(r"[0-9a-fA-F]{64}", data["sha256"]):
        raise ToolError("更新版本或固定 SHA256 格式无效")
    url = urllib.parse.urlsplit(data["source"])
    if url.scheme not in ("https", "file") or url.query or url.fragment or url.username or url.password:
        raise ToolError("更新源只能是无凭据的 HTTPS 或本地 file URL")
    if url.scheme == "https" and not url.hostname:
        raise ToolError("HTTPS 更新源缺少主机名")
    if url.scheme == "file" and (url.netloc not in ("", "localhost") or not url.path.startswith("/")):
        raise ToolError("本地更新源必须是绝对 file URL")
    return data


def version_tuple(version):
    return tuple(int(part) for part in version.split("."))


def update_check():
    data = update_config()
    if version_tuple(data["version"]) <= version_tuple(VERSION):
        print(f"当前 v{VERSION}；配置的发布版本 v{data['version']}，无需更新")
    else:
        print(f"当前 v{VERSION}；配置的发布版本 v{data['version']} 可供校验更新")
    print("此检查只读取本地配置；执行 c update 才会读取发布包并验证 SHA256。")
    return 0


class HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ToolError("下载重定向离开 HTTPS，已拒绝", 6)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download_release(url, output):
    parsed = urllib.parse.urlsplit(url)
    try:
        if parsed.scheme == "file":
            source = Path(urllib.parse.unquote(parsed.path))
            stream = source.open("rb")
        else:
            stream = urllib.request.build_opener(HttpsOnlyRedirect()).open(url, timeout=15)
        size = 0
        with stream, output.open("wb") as destination:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise ToolError("发布包超过 20 MiB 限制", 6)
                destination.write(chunk)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise ToolError(f"发布包读取失败或网络离线 ({type(exc).__name__})；旧版本未改动", 6) from None


def extract_release(archive, destination, expected_version):
    files = set()
    try:
        with tarfile.open(archive, "r:gz") as package:
            members = package.getmembers()
            if len(members) > 1000 or sum(x.size for x in members) > 50 * 1024 * 1024:
                raise ToolError("发布包文件数量或解压大小超限", 6)
            for member in members:
                name = member.name
                rel = PurePosixPath(name)
                if (not name or name.startswith("/") or "\\" in name or
                        ".." in rel.parts or str(rel) != name.rstrip("/") or
                        any(part in (".git", "config", "data", "state", "logs", "__pycache__") for part in rel.parts)):
                    raise ToolError("发布包含不安全路径或运行数据目录", 6)
                if not (member.isfile() or member.isdir()):
                    raise ToolError("发布包不能包含符号链接、硬链接或设备文件", 6)
                if name in files:
                    raise ToolError("发布包包含重复路径", 6)
                files.add(name)
                target = destination / name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.extractfile(member) as source, target.open("wb") as sink:
                        shutil.copyfileobj(source, sink)
                    target.chmod(0o755 if name == "c" else 0o644)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ToolError(f"发布包损坏或无法解压 ({type(exc).__name__})；旧版本未改动", 6) from None
    for required in ("c", "toolbox.py", "VERSION"):
        if required not in files:
            raise ToolError(f"发布包缺少 {required}；旧版本未改动", 6)
    version = (destination / "VERSION").read_text(encoding="utf-8").strip()
    if version != expected_version:
        raise ToolError("发布包版本与配置不符；旧版本未改动", 6)
    for source in destination.rglob("*.py"):
        try:
            compile(source.read_bytes(), str(source), "exec")
        except (SyntaxError, ValueError) as exc:
            raise ToolError(f"发布包 Python 语法错误 ({source.name})；旧版本未改动", 6) from None
    if subprocess.run(["bash", "-n", str(destination / "c")], capture_output=True, check=False).returncode != 0:
        raise ToolError("发布包 c 入口语法错误；旧版本未改动", 6)
    return files


def update_run():
    data = update_config()
    if version_tuple(data["version"]) <= version_tuple(VERSION):
        print(f"已是当前配置的版本 v{VERSION}；未做更改")
        return 0
    if ROOT.is_symlink() or (ROOT / ".git").exists():
        raise ToolError("源码仓库或链接目录不支持自动替换；请手动管理源码", 6)
    parent = ROOT.parent
    workspace = Path(tempfile.mkdtemp(prefix=".dabiao-update-", dir=str(parent)))
    backup = parent / (".dabiao-backup-" + workspace.name.removeprefix(".dabiao-update-"))
    archive = workspace / "release.tar.gz"
    candidate = workspace / "project"
    candidate.mkdir()
    replaced = False
    try:
        print(f"读取发布包并校验 v{data['version']}...")
        download_release(data["source"], archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest.lower() != data["sha256"].lower():
            raise ToolError("SHA256 校验失败；旧版本未改动", 6)
        files = extract_release(archive, candidate, data["version"])
        if any(p.is_symlink() for p in ROOT.rglob("*")):
            raise ToolError("项目目录包含符号链接，已拒绝替换以免丢失用户数据", 6)
        existing = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file()}
        extra = existing - files
        if extra:
            raise ToolError("项目目录含发布包未覆盖的文件，已拒绝以免丢失用户数据", 6)
        log_event("INFO", "update_verified")
        try:
            os.replace(ROOT, backup)
            replaced = True
            os.replace(candidate, ROOT)
        except BaseException:
            if replaced:
                try:
                    os.replace(backup, ROOT)
                    replaced = False
                except OSError:
                    raise ToolError(f"更新中断且自动恢复失败；旧版本保留在 {backup}", 6) from None
            raise ToolError("更新中断或替换失败；已恢复旧版本", 6) from None
        shutil.rmtree(backup)
        replaced = False
        log_event("INFO", "update_completed")
        print(f"总工具箱已更新到 v{data['version']}；配置与状态目录未改动")
        return 0
    finally:
        if workspace.exists():
            shutil.rmtree(workspace)


def help_text():
    print("""大彪996 v%s — 中文服务器工具箱
用法: c [命令]
  c / c menu             中文总菜单
  c help                 帮助
  c version              版本
  c status               工具箱状态
  c doctor               环境与配置诊断
  c logs [N]             查看最近 N 条事件日志（默认 30）
  c system info          只读系统信息
  c docker check         只读 Docker 检测
  c apps list            已接入应用列表
  c apps info ID         应用实际状态
  c apps run ID ACTION   调用已注册模块动作
  c modules register PATH|unregister ID|list
  c config show          配置位置和状态
  c update check         检查总工具箱更新配置与版本
  c update               更新总工具箱（须配置可信发布包）
  c install-command [PATH] 安装 c 符号链接（不会覆盖）
状态与配置位于 XDG 目录；应用更新由模块自己负责。""" % VERSION)


def menu():
    while True:
        print(f"\n===== 大彪996 服务器工具箱 v{VERSION} =====")
        for number, title, command in GROUPS:
            print(f"{number:>2}. {title}" + ("" if command else "（待接入）"))
        try:
            choice = input("请选择: ").strip()
        except EOFError:
            return 0
        selected = next((item for item in GROUPS if item[0] == choice), None)
        if selected is None:
            print("无效选项")
        elif selected[2] == "exit":
            return 0
        elif selected[2] is None:
            print("此分组尚未接入可执行功能")
        else:
            try:
                dispatch(selected[2].split())
            except ToolError as exc:
                print(f"[错误] {exc}", file=sys.stderr)


def dispatch(argv):
    if not argv or argv == ["menu"]:
        return menu()
    if argv in (["help"], ["-h"], ["--help"]):
        help_text(); return 0
    if argv in (["version"], ["-v"], ["--version"]):
        print(f"大彪996 v{VERSION}"); return 0
    if argv == ["status"]:
        return status_show()
    if argv == ["doctor"]:
        return doctor()
    if argv and argv[0] == "logs" and len(argv) <= 2:
        return logs_show(argv[1] if len(argv) == 2 else None)
    if argv == ["system", "info"]:
        system_info(); return 0
    if argv == ["docker", "check"]:
        return docker_check()
    if argv in (["apps", "list"], ["modules", "list"]):
        return modules_list()
    if len(argv) == 3 and argv[:2] == ["apps", "info"]:
        return module_run(argv[2], "status", [])
    if len(argv) >= 4 and argv[:2] == ["apps", "run"]:
        return module_run(argv[2], argv[3], argv[4:])
    if len(argv) == 3 and argv[:2] == ["modules", "register"]:
        return module_register(argv[2])
    if len(argv) == 3 and argv[:2] == ["modules", "unregister"]:
        return module_unregister(argv[2])
    if argv == ["config", "show"]:
        return config_show()
    if argv == ["update", "check"]:
        return update_check()
    if argv == ["update"]:
        return update_run()
    if argv[0] == "install-command" and len(argv) <= 2:
        return install_command(argv[1] if len(argv) == 2 else None)
    raise ToolError("未知命令或参数。运行 c help 查看用法。")


def main(argv=None):
    try:
        return dispatch(sys.argv[1:] if argv is None else argv)
    except ToolError as exc:
        log_event("ERROR", "command_failed")
        print(f"[错误] {exc}", file=sys.stderr)
        return exc.code
    except (OSError, subprocess.TimeoutExpired) as exc:
        log_event("ERROR", "io_or_timeout")
        print(f"[错误] 操作失败 ({type(exc).__name__})；可运行 c doctor 检查环境", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        log_event("WARN", "command_interrupted")
        print("\n[错误] 操作已中断；更新若进入替换阶段会尝试恢复旧版本", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
