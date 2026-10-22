import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.error
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import toolbox  # noqa: E402


class ToolboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dabiao996-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self.base / "config"
        self.state = self.base / "state"
        self.env = dict(os.environ, DABIAO_CONFIG_DIR=str(self.config), DABIAO_STATE_DIR=str(self.state), PYTHONDONTWRITEBYTECODE="1")

    def cli(self, *args, input_text=None, root=ROOT):
        return subprocess.run([str(root / "c"), *args], input=input_text,
                              capture_output=True, text=True, env=self.env, timeout=20, check=False)

    def test_menu_help_and_platform(self):
        self.assertIn("大彪996", self.cli("help").stdout)
        self.assertIn("v0.1.0", self.cli("version").stdout)
        menu = self.cli(input_text="2\n0\n")
        self.assertEqual(menu.returncode, 0)
        self.assertIn("系统更新维护（待接入）", menu.stdout)
        self.assertIn("此分组尚未接入", menu.stdout)
        self.assertIn("未配置", self.cli("config", "show").stdout)
        self.assertIn("工具箱更新源: 未配置", self.cli("status").stdout)
        self.assertIn("暂无事件日志", self.cli("logs").stdout)
        self.assertEqual(self.cli("logs", "9999").returncode, 2)
        if sys.platform == "darwin":
            doctor = self.cli("doctor")
            self.assertEqual(doctor.returncode, 5)
            self.assertIn("不在目标范围", doctor.stdout)

    def test_command_link_idempotence_and_no_overwrite(self):
        target = self.base / "bin" / "c"
        self.assertEqual(self.cli("install-command", str(target)).returncode, 0)
        self.assertEqual(self.cli("install-command", str(target)).returncode, 0)
        self.assertIn("v0.1.0", subprocess.check_output([str(target), "version"], env=self.env, text=True))
        target.unlink()
        target.write_text("keep", encoding="utf-8")
        self.assertEqual(self.cli("install-command", str(target)).returncode, 2)
        self.assertEqual(target.read_text(), "keep")

    def test_module_registration_and_status_contract(self):
        manifest = ROOT / "examples" / "sample-app" / "module.json"
        self.assertEqual(self.cli("modules", "register", str(manifest)).returncode, 0)
        self.assertIn("未重复写入", self.cli("modules", "register", str(manifest)).stdout)
        self.assertIn("sample-app", self.cli("apps", "list").stdout)
        self.assertEqual(len(json.loads((self.config / "modules.json").read_text())["modules"]), 1)
        with mock.patch.object(toolbox, "CONFIG", self.config), mock.patch.object(toolbox, "STATE", self.state), mock.patch.object(toolbox, "platform_info", return_value=("Linux", "ubuntu", "amd64")):
            with mock.patch.dict(os.environ, {"DABIAO_SAMPLE_MARKER": str(self.base / "app" / "installed")}):
                self.assertEqual(toolbox.module_run("sample-app", "status", []), 0)
                marker = self.base / "app" / "installed"
                marker.parent.mkdir()
                marker.touch()
                self.assertEqual(toolbox.module_run("sample-app", "status", []), 0)
        self.assertEqual(self.cli("modules", "unregister", "sample-app").returncode, 0)
        self.assertEqual(self.cli("modules", "unregister", "sample-app").returncode, 0)

    def test_module_validation_rejects_traversal_bad_args_and_secrets(self):
        folder = self.base / "bad"
        folder.mkdir()
        data = json.loads((ROOT / "examples" / "sample-app" / "module.json").read_text())
        data["entry"] = "../outside.py"
        manifest = folder / "module.json"
        manifest.write_text(json.dumps(data))
        result = self.cli("modules", "register", str(manifest))
        self.assertEqual(result.returncode, 2)
        self.assertIn("不安全", result.stderr)
        self.assertEqual(self.cli("apps", "info", "../../etc/passwd").returncode, 2)
        valid = {"status": "installed", "address": "1.2.3.4", "port": 8080, "directory": "/tmp/app", "management": "c apps info sample-app", "message": "ok"}
        self.assertEqual(toolbox.validate_status(valid), valid)
        for change in ({"address": "<服务器IP>"}, {"port": "8080"}, {"message": "token=abc"}, {"management": "run --token ABC"}):
            with self.assertRaises(toolbox.ToolError):
                toolbox.validate_status(dict(valid, **change))

    def test_no_update_source_and_failed_download(self):
        result = self.cli("update")
        self.assertEqual(result.returncode, 2)
        self.assertIn("未配置", result.stderr)
        self.assertEqual(self.cli("update", "check").returncode, 2)
        self.config.mkdir()
        (self.config / "update.json").write_text(json.dumps({"source": "file:///nonexistent/dabiao.tar.gz", "version": "0.2.0", "sha256": "0" * 64}))
        copied = self.copy_project()
        result = self.cli("update", root=copied)
        self.assertEqual(result.returncode, 6)
        self.assertIn("读取失败", result.stderr)
        self.assertEqual((copied / "VERSION").read_text().strip(), "0.1.0")

    def test_offline_https_is_reported_without_network(self):
        output = self.base / "download"
        with mock.patch.object(toolbox.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = urllib.error.URLError("offline")
            with self.assertRaisesRegex(toolbox.ToolError, "网络离线"):
                toolbox.download_release("https://example.invalid/release.tar.gz", output)
        self.assertFalse(output.exists())

    def test_module_failure_does_not_reveal_stderr_secret(self):
        entry = self.base / "bad_module.py"
        entry.write_text("import sys\nprint('token=LEAK', file=sys.stderr)\nraise SystemExit(7)\n")
        module = {"id": "bad-module", "actions": ["status"], "os": ["ubuntu"], "arch": ["amd64"], "_entry": entry}
        with mock.patch.object(toolbox, "STATE", self.state), mock.patch.object(toolbox, "discover", return_value={"bad-module": module}), mock.patch.object(toolbox, "platform_info", return_value=("Linux", "ubuntu", "amd64")):
            with self.assertRaises(toolbox.ToolError) as caught:
                toolbox.module_run("bad-module", "status", [])
        self.assertNotIn("LEAK", str(caught.exception))
        self.assertNotIn("LEAK", (self.state / "events.log").read_text())

    def copy_project(self):
        target = self.base / "project"
        shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        return target

    def release(self, project, version="0.2.0"):
        release = self.base / "release"
        shutil.copytree(project, release)
        (release / "VERSION").write_text(version + "\n", encoding="utf-8")
        archive = self.base / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as package:
            for path in release.rglob("*"):
                package.add(path, arcname=path.relative_to(release).as_posix(), recursive=False)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.config.mkdir(exist_ok=True)
        (self.config / "update.json").write_text(json.dumps({"source": archive.as_uri(), "version": version, "sha256": digest}))
        return archive

    def test_checksum_failure_and_local_update_success(self):
        project = self.copy_project()
        self.release(project)
        path = self.config / "update.json"
        data = json.loads(path.read_text())
        data["sha256"] = "0" * 64
        path.write_text(json.dumps(data))
        result = self.cli("update", root=project)
        self.assertEqual(result.returncode, 6)
        self.assertIn("SHA256 校验失败", result.stderr)
        self.assertEqual((project / "VERSION").read_text().strip(), "0.1.0")
        archive = self.base / "release.tar.gz"
        data["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        path.write_text(json.dumps(data))
        result = self.cli("update", root=project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((project / "VERSION").read_text().strip(), "0.2.0")
        self.assertIn("已是当前配置的版本", self.cli("update", root=project).stdout)
        self.assertEqual(list(self.base.glob(".dabiao-backup-*")), [])

    def test_unsafe_archive_and_rollback(self):
        project = self.copy_project()
        archive = self.base / "unsafe.tar.gz"
        import io
        with tarfile.open(archive, "w:gz") as package:
            info = tarfile.TarInfo("../escape")
            info.size = 1
            package.addfile(info, io.BytesIO(b"x"))
        self.config.mkdir()
        (self.config / "update.json").write_text(json.dumps({"source": archive.as_uri(), "version": "0.2.0", "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}))
        result = self.cli("update", root=project)
        self.assertEqual(result.returncode, 6)
        self.assertIn("不安全路径", result.stderr)
        self.assertFalse((self.base / "escape").exists())
        self.release(project)
        real_replace = os.replace
        calls = 0

        def fail_second(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated interruption")
            return real_replace(src, dst)

        with mock.patch.object(toolbox, "ROOT", project), mock.patch.object(toolbox, "CONFIG", self.config), mock.patch.object(toolbox, "STATE", self.state), mock.patch.object(toolbox.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(toolbox.ToolError, "已恢复旧版本"):
                toolbox.update_run()
        self.assertEqual((project / "VERSION").read_text().strip(), "0.1.0")
        self.assertEqual(list(self.base.glob(".dabiao-backup-*")), [])

    def test_post_install_uses_live_status(self):
        entry = self.base / "module.py"
        entry.write_text("import json\nprint(json.dumps({'status':'installed','address':'127.0.0.1','port':1234,'directory':'/tmp/sample','management':'c apps info test-app','message':'ok'}))\n")
        with mock.patch.object(toolbox, "STATE", self.state):
            self.assertEqual(toolbox.post_action_status("test-app", {"_entry": entry}, "install"), 0)
        entry.write_text("import json\nprint(json.dumps({'status':'not_installed','address':None,'port':None,'directory':None,'management':None,'message':'missing'}))\n")
        with self.assertRaisesRegex(toolbox.ToolError, "实际状态并非已安装"):
            toolbox.post_action_status("test-app", {"_entry": entry}, "install")


if __name__ == "__main__":
    unittest.main()
