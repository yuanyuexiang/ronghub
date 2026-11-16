"""Exercise backup rejection boundaries without Docker mutations."""

import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "stack_ops", Path(__file__).resolve().parents[3] / "scripts/stack_ops.py"
)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


def make_archive(path, name, kind=tarfile.REGTYPE):
    with tarfile.open(path, "w") as stream:
        entry = tarfile.TarInfo(name)
        entry.type = kind
        if kind == tarfile.REGTYPE:
            entry.size = 3
            stream.addfile(entry, io.BytesIO(b"pdf"))
        else:
            entry.linkname = "outside"
            stream.addfile(entry)


def test_configuration_is_private_and_excludes_dev_project(tmp_path):
    env = tmp_path / "stack.env"
    ops.initialize(env)
    assert env.stat().st_mode & 0o777 == 0o600
    original = env.read_bytes()
    with pytest.raises(ValueError):
        ops.initialize(env)
    assert env.read_bytes() == original
    with pytest.raises(ValueError):
        ops.initialize(tmp_path / "dev.env", project="ronghub")
    env.write_text(env.read_text().replace("ronghub-stack", "ronghub"))
    with pytest.raises(ValueError, match="namespace"):
        ops.Stack(env)


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../outside", tarfile.REGTYPE),
        ("/outside", tarfile.REGTYPE),
        ("nested/" + "a" * 48, tarfile.REGTYPE),
        ("a" * 48, tarfile.SYMTYPE),
        ("a" * 48, tarfile.LNKTYPE),
    ],
)
def test_unsafe_archive_rejected(tmp_path, name, kind):
    path = tmp_path / "attachments.tar"
    make_archive(path, name, kind)
    with pytest.raises(ValueError):
        ops.validate_archive(path)


def backup_files(path):
    (path / "database.dump").write_bytes(b"PGDMPfixture")
    make_archive(path / "attachments.tar", "a" * 48)
    files = {
        name: {"size": (path / name).stat().st_size, "sha256": ops.digest(path / name)}
        for name in ["database.dump", "attachments.tar"]
    }
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "format": 1,
                "postgres_major": 17,
                "files": files,
            }
        )
    )


def test_corrupt_backup_does_not_stop_services(tmp_path, monkeypatch):
    backup_files(tmp_path)
    (tmp_path / "database.dump").write_bytes(b"PGDMPcorrupt")
    stack = object.__new__(ops.Stack)
    monkeypatch.setattr(stack, "stop_writers", lambda: pytest.fail("Must not stop services"))
    with pytest.raises(ValueError, match="checksum"):
        stack.restore(tmp_path)


def test_nonempty_restore_resumes_without_writing(tmp_path, monkeypatch):
    backup_files(tmp_path)
    stack = object.__new__(ops.Stack)
    resumed = []
    monkeypatch.setattr(stack, "stop_writers", lambda: ["api"])
    monkeypatch.setattr(stack, "resume", lambda previous: resumed.append(previous))

    def refuse(*args, **kwargs):
        assert args == ("python", "-m", "app.backup_integrity", "--require-empty")
        raise subprocess.CalledProcessError(1, "guard", stderr=b"Restore refused: populated")

    monkeypatch.setattr(stack, "tool", refuse)
    monkeypatch.setattr(stack, "run", lambda *a, **k: pytest.fail("Must not restore"))
    with pytest.raises(ValueError, match="Restore target not empty"):
        stack.restore(tmp_path)
    assert resumed == [["api"]]
