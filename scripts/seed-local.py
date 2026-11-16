"""Create an ignored local-only demo password and seed explicitly demo-marked records."""
import json
import os
from pathlib import Path
import secrets
import subprocess

root = Path(__file__).resolve().parents[1]
credentials = root / "apps/server/.demo-credentials.json"
if not credentials.exists():
    credentials.write_text(json.dumps({"password": secrets.token_urlsafe(24)}))
    credentials.chmod(0o600)
env = os.environ.copy()
env["DEMO_PASSWORD"] = json.loads(credentials.read_text())["password"]
subprocess.run(["uv", "run", "python", "-m", "app.seed"], cwd=root/"apps/server", env=env, check=True)
print("Password is kept only in ignored apps/server/.demo-credentials.json (not printed).")
