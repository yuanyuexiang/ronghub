"""Idempotent isolated demo deployment; credentials never enter build logs."""

import fcntl
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stack_ops import Stack


def main():
    os.chdir(ROOT)
    tag = sys.argv[1]
    if not re.fullmatch(r"sha-[a-f0-9]{40}", tag):
        raise SystemExit("An immutable full-commit image tag is required")
    path = ROOT / "deploy/.env.cloud"
    if not path.exists():
        domain = os.environ.get("DOMAIN", "ronghub.asksquirrel.ai")
        owner = os.environ["GHCR_OWNER"].lower()
        if not re.fullmatch(r"[a-z0-9.-]+", domain) or not re.fullmatch(r"[a-z0-9-]+", owner):
            raise SystemExit("Invalid deployment hostname or registry owner")
        values = {
            "COMPOSE_PROJECT_NAME": "ronghub-cloud",
            "DOMAIN": domain,
            "PUBLIC_ORIGIN": "https://" + domain,
            "GHCR_OWNER": owner,
            "TRAEFIK_NETWORK": os.environ.get("TRAEFIK_NETWORK", "matrix-network"),
            "TRAEFIK_CERTRESOLVER": os.environ.get("TRAEFIK_CERTRESOLVER", "cloudflare"),
            "DEMO_MODE": "true",
            "SMS_MODE": "demo",
            "IMAGE_TAG": tag,
            "OWNER_DB_PASSWORD": secrets.token_urlsafe(32),
            "APP_DB_PASSWORD": secrets.token_urlsafe(32),
            "DEMO_PASSWORD": secrets.token_urlsafe(24),
        }
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as output:
            output.writelines(f"{k}={v}\n" for k, v in values.items())
    stack = Stack(path, ROOT / "deploy/traefik.compose.yml")
    stack.environment["IMAGE_TAG"] = tag
    subprocess.run(
        ["docker", "network", "inspect", stack.values["TRAEFIK_NETWORK"]],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    stack.run("pull")
    current = ROOT / "deploy/.current_tag"
    previous = current.read_text().strip() if current.exists() else None
    if previous:
        # Backup uses the running revision and its matching image before changing it.
        old = Stack(path, ROOT / "deploy/traefik.compose.yml")
        old.backup(ROOT / ".backups" / ("predeploy-" + str(time.time_ns())))
    stack.stop_writers()
    stack.run("up", "-d", "--wait", "--wait-timeout", "120", "postgres")
    stack.run("run", "--rm", "--no-deps", "-T", "migrate")
    if not (ROOT / "deploy/.seeded").exists():
        stack.tool("python", "-m", "app.backup_integrity", "--require-empty")
        stack.seed()
        (ROOT / "deploy/.seeded").touch(mode=0o600)
    stack.run("up", "-d", "--wait", "--wait-timeout", "240")
    origin = stack.values["PUBLIC_ORIGIN"]
    for attempt in range(60):
        try:
            for route in ["/api/v1/health", "/", "/portal", "/portal/login"]:
                with urllib.request.urlopen(origin + route, timeout=10) as response:
                    if response.status != 200:
                        raise ValueError("Unexpected public response")
            break
        except (urllib.error.URLError, TimeoutError, ValueError):
            if attempt == 59:
                raise SystemExit(
                    "HTTPS verification failed. Data retained; inspect RongHub services. "
                    "Use the predeploy backup for schema-safe recovery; no destructive rollback performed."
                )
            time.sleep(3)
    values = {**stack.values, "IMAGE_TAG": tag}
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as output:
        output.writelines(f"{k}={v}\n" for k, v in values.items())
    temporary.replace(path)
    current.write_text(tag + "\n")
    print("HTTPS demo verified: " + origin + "; private credentials retained on server.")


if __name__ == "__main__":
    with open(ROOT / "deploy/.deploy.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        main()
