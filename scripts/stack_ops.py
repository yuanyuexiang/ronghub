"""Local stack lifecycle and coordinated DB/private-file backup. Never targets the dev 'ronghub' project."""

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / "deploy/.env.stack"


def initialize(path, project="ronghub-stack", port=3080):
    if path.exists():
        raise ValueError("Configuration already exists; it was not replaced")
    if (
        not re.fullmatch(r"ronghub-[a-z0-9][a-z0-9-]*", project)
        or not 1024 <= port <= 65535
    ):
        raise ValueError("Invalid isolated stack name or port")
    values = {
        "COMPOSE_PROJECT_NAME": project,
        "HTTP_PORT": str(port),
        "PUBLIC_ORIGIN": f"http://127.0.0.1:{port}",
        "DEMO_MODE": "true",
        "SMS_MODE": "demo",
        "OWNER_DB_PASSWORD": secrets.token_urlsafe(32),
        "APP_DB_PASSWORD": secrets.token_urlsafe(32),
        "DEMO_PASSWORD": secrets.token_urlsafe(24),
        "RONGHUB_IMAGE_TAG": "local",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(
            "# Local demo only. Private credentials; do not commit or send to clients.\n"
        )
        stream.writelines(f"{key}={value}\n" for key, value in values.items())
    return values


def digest(path):
    with path.open("rb") as stream:
        checksum = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
        return checksum.hexdigest()


def validate_archive(path):
    with tarfile.open(path, "r:") as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe attachment archive path")
            if member.isdir() and not relative.parts:
                continue
            if (
                not member.isfile()
                or len(relative.parts) != 1
                or not re.fullmatch(r"[a-f0-9]{48}", relative.name)
            ):
                raise ValueError("Archive contains an unexpected attachment entry")


class Stack:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.values = {}
        for line in self.path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                self.values[key.strip()] = value.strip()
        project = self.values.get("COMPOSE_PROJECT_NAME", "")
        if not re.fullmatch(r"ronghub-[a-z0-9][a-z0-9-]*", project):
            raise ValueError(
                "Refusing a project outside the isolated ronghub-* namespace"
            )
        for key in ["OWNER_DB_PASSWORD", "APP_DB_PASSWORD"]:
            if not re.fullmatch(r"[A-Za-z0-9_-]{24,}", self.values.get(key, "")):
                raise ValueError(
                    f"{key} must be a generated URL-safe secret of at least 24 characters"
                )
        origin = urlsplit(self.values.get("PUBLIC_ORIGIN", ""))
        if (
            origin.scheme not in {"http", "https"}
            or not origin.netloc
            or origin.username
            or origin.password
            or origin.path
            or origin.query
            or origin.fragment
        ):
            raise ValueError("PUBLIC_ORIGIN must be an exact HTTP(S) origin")
        self.command = [
            "docker",
            "compose",
            "--env-file",
            str(self.path),
            "-p",
            project,
            "-f",
            str(ROOT / "deploy/stack.compose.yml"),
        ]
        self.environment = {**os.environ, **self.values}

    def run(self, *args, capture=False, **kwargs):
        return subprocess.run(
            [*self.command, *args],
            cwd=ROOT,
            env=self.environment,
            check=True,
            capture_output=capture,
            **kwargs,
        )

    def tool(self, *args, **kwargs):
        return self.run("run", "--rm", "--no-deps", "-T", "api", *args, **kwargs)

    def up(self, build=False):
        if build:
            self.run("build", "api", "web")
        self.run("up", "-d", "--wait", "--wait-timeout", "180")

    def seed(self):
        if self.values.get("DEMO_MODE", "").lower() != "true":
            raise ValueError("Demo seeding is disabled in production mode")
        self.run(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "-e",
            "DEMO_PASSWORD",
            "api",
            "python",
            "-m",
            "app.seed",
        )

    def stop_writers(self):
        running = (
            self.run("ps", "--status", "running", "--services", capture=True)
            .stdout.decode()
            .splitlines()
        )
        writers = [name for name in ["api", "worker"] if name in running]
        if writers:
            self.run("stop", *writers)
        return writers

    def resume(self, writers):
        if writers:
            self.run("start", "--wait", "--wait-timeout", "120", *writers)
            running = (
                self.run("ps", "--status", "running", "--services", capture=True)
                .stdout.decode()
                .splitlines()
            )
            if "api" in writers and "edge" in running:
                deadline = time.monotonic() + 60
                while True:
                    try:
                        with urllib.request.urlopen(
                            self.values["PUBLIC_ORIGIN"] + "/api/v1/health", timeout=3
                        ) as response:
                            if response.status == 200:
                                break
                    except (urllib.error.URLError, TimeoutError, socket.timeout):
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "API restarted but the configured gateway origin is not ready"
                        )
                    time.sleep(0.5)

    def backup(self, destination):
        destination = Path(destination).resolve()
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        previous = self.stop_writers()
        try:
            self.tool("python", "-m", "app.backup_integrity")
            for name, args in [
                (
                    "database.dump",
                    [
                        "exec",
                        "-T",
                        "postgres",
                        "pg_dump",
                        "-U",
                        "ronghub_owner",
                        "-d",
                        "ronghub",
                        "--format=custom",
                        "--no-owner",
                        "--no-acl",
                    ],
                ),
                (
                    "attachments.tar",
                    [
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "api",
                        "tar",
                        "-C",
                        "/var/lib/ronghub/attachments",
                        "-cf",
                        "-",
                        ".",
                    ],
                ),
            ]:
                path = destination / name
                with path.open("xb") as stream:
                    path.chmod(0o600)
                    self.run(*args, stdout=stream)
            validate_archive(destination / "attachments.tar")
            revision = (
                self.run(
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-U",
                    "ronghub_owner",
                    "-d",
                    "ronghub",
                    "-At",
                    "-c",
                    "SELECT version_num FROM alembic_version",
                    capture=True,
                )
                .stdout.decode()
                .strip()
            )
            manifest = {
                "format": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_project": self.values["COMPOSE_PROJECT_NAME"],
                "postgres_major": 17,
                "revision": revision,
                "quiesced_writers": previous,
                "files": {
                    name: {
                        "sha256": digest(destination / name),
                        "size": (destination / name).stat().st_size,
                    }
                    for name in ["database.dump", "attachments.tar"]
                },
            }
            path = destination / "manifest.json"
            path.write_text(json.dumps(manifest, indent=2) + "\n")
            path.chmod(0o600)
        finally:
            self.resume(previous)
        print("Coordinated database and attachment backup completed.")

    def restore(self, source):
        source = Path(source).resolve()
        manifest = json.loads((source / "manifest.json").read_text())
        if (
            manifest.get("format") != 1
            or manifest.get("postgres_major") != 17
            or set(manifest.get("files", {})) != {"database.dump", "attachments.tar"}
        ):
            raise ValueError("Unsupported backup manifest")
        for name, expected in manifest["files"].items():
            path = source / name
            if (
                path.is_symlink()
                or path.stat().st_size != expected["size"]
                or digest(path) != expected["sha256"]
            ):
                raise ValueError("Backup checksum verification failed")
        with (source / "database.dump").open("rb") as stream:
            if stream.read(5) != b"PGDMP":
                raise ValueError("Unsupported database dump")
        validate_archive(source / "attachments.tar")
        previous = self.stop_writers()
        try:
            result = self.tool(
                "python", "-m", "app.backup_integrity", "--require-empty", capture=True
            )
            print(result.stdout.decode().strip())
            # An older archive knows nothing about later tables/FKs. Remove only
            # the checked-empty current application tables before pg_restore;
            # retain extensions, and never CASCADE into unmanaged objects.
            result = self.run(
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "migrate",
                "python",
                "-m",
                "app.restore_prepare",
                capture=True,
            )
            print(result.stdout.decode().strip())
        except subprocess.CalledProcessError as error:
            self.resume(previous)
            if b"Restore refused:" in (error.stderr or b""):
                raise ValueError("Restore target not empty") from error
            raise
        except Exception:
            self.resume(previous)
            raise
        # Target was empty. On a failed restore, leave writers stopped for diagnosis.
        with (source / "database.dump").open("rb") as stream:
            self.run(
                "exec",
                "-T",
                "postgres",
                "pg_restore",
                "-U",
                "ronghub_owner",
                "-d",
                "ronghub",
                "--single-transaction",
                "--exit-on-error",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-acl",
                stdin=stream,
            )
        self.run(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "ronghub_owner",
            "-d",
            "ronghub",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "/docker-entrypoint-initdb.d/20-runtime-grants.sql",
        )
        with (source / "attachments.tar").open("rb") as stream:
            self.tool(
                "tar",
                "-C",
                "/var/lib/ronghub/attachments",
                "-xf",
                "-",
                "--no-same-owner",
                "--no-same-permissions",
                stdin=stream,
            )
        self.run("run", "--rm", "--no-deps", "-T", "migrate")
        self.tool("python", "-m", "app.backup_integrity", "--revoke-sessions")
        self.resume(previous)
        print(
            "Restore completed; old authentication sessions were revoked. Log in again."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--project", default="ronghub-stack")
    init.add_argument("--port", type=int, default=3080)
    up = sub.add_parser("up")
    up.add_argument("--build", action="store_true")
    sub.add_parser("seed")
    sub.add_parser("status")
    sub.add_parser("down")
    backup = sub.add_parser("backup")
    backup.add_argument("destination", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("source", type=Path)
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.env, args.project, args.port)
        print("Private local-demo configuration created; credentials were not printed.")
        return
    stack = Stack(args.env)
    if args.command == "up":
        stack.up(args.build)
    elif args.command == "seed":
        stack.seed()
    elif args.command == "status":
        stack.run("ps")
    elif args.command == "down":
        stack.run("down")  # Named data volumes are deliberately retained.
    elif args.command == "backup":
        stack.backup(args.destination)
    elif args.command == "restore":
        stack.restore(args.source)


if __name__ == "__main__":
    main()
