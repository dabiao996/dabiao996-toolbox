import hashlib
import io
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import install as installer  # noqa: E402


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dabiao-install-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.prefix = self.base / "installed" / "toolbox"
        self.bin_dir = self.base / "bin"
        self.archive = self.base / "source.tar.gz"
        with tarfile.open(self.archive, "w:gz") as package:
            for path in ROOT.rglob("*"):
                relative = path.relative_to(ROOT)
                if (path.is_file() and not any(part in (".git", "__pycache__") for part in relative.parts)
                        and path.suffix != ".pyc" and path.name != ".DS_Store"):
                    package.add(path, arcname="dabiao996-pinned/" + relative.as_posix())
        self.sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def call_install(self, checksum=None):
        with mock.patch.object(installer, "check_platform"):
            return installer.install(self.archive.as_uri(), checksum or self.sha, "0.1.0", self.prefix, self.bin_dir)

    def test_install_and_repeat_are_idempotent(self):
        self.assertEqual(self.call_install(), 0)
        self.assertEqual((self.prefix / "VERSION").read_text().strip(), "0.1.0")
        self.assertEqual((self.bin_dir / "c").resolve(), (self.prefix / "c").resolve())
        self.assertEqual(self.call_install(), 0)
        self.assertEqual(list((self.base / "installed").glob(".dabiao-install-*")), [])
        (self.prefix / "toolbox.py").write_text("this is broken python !\n")
        with self.assertRaisesRegex(installer.InstallError, "脚本异常"):
            self.call_install()

    def test_bad_checksum_and_existing_command_preserve_files(self):
        with self.assertRaisesRegex(installer.InstallError, "SHA256"):
            self.call_install("0" * 64)
        self.assertFalse(self.prefix.exists())
        self.bin_dir.mkdir()
        command = self.bin_dir / "c"
        command.write_text("keep")
        with self.assertRaisesRegex(installer.InstallError, "拒绝覆盖"):
            self.call_install()
        self.assertEqual(command.read_text(), "keep")
        self.assertFalse(self.prefix.exists())

    def test_unsafe_archive_is_rejected(self):
        with tarfile.open(self.archive, "w:gz") as package:
            for name in ("c", "VERSION", "toolbox.py"):
                data = b"0.1.0\n" if name == "VERSION" else b"test\n"
                item = tarfile.TarInfo(name)
                item.size = len(data)
                package.addfile(item, io.BytesIO(data))
            item = tarfile.TarInfo("../escape")
            item.size = 1
            package.addfile(item, io.BytesIO(b"x"))
        self.sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        with self.assertRaisesRegex(installer.InstallError, "危险路径"):
            self.call_install()
        self.assertFalse((self.base / "escape").exists())
        self.assertFalse(self.prefix.exists())

    def test_runtime_data_in_archive_is_rejected(self):
        with tarfile.open(self.archive, "w:gz") as package:
            for name in ("c", "VERSION", "toolbox.py", ".git/config"):
                data = b"0.1.0\n" if name == "VERSION" else b"test\n"
                item = tarfile.TarInfo(name)
                item.size = len(data)
                package.addfile(item, io.BytesIO(data))
        self.sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        with self.assertRaisesRegex(installer.InstallError, "运行数据或不安全目录"):
            self.call_install()
        self.assertFalse(self.prefix.exists())

    def test_platform_boundary_and_url_validation(self):
        with mock.patch.object(installer.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(installer.InstallError, "仅限"):
                installer.check_platform()
        with self.assertRaisesRegex(installer.InstallError, "HTTPS"):
            installer.validate_url("http://example.invalid/archive.tar.gz")
