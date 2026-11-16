"""Generate ignored local API configuration from the local Compose password."""
from pathlib import Path
from urllib.parse import quote
root = Path(__file__).resolve().parents[1]
env = dict(line.split('=', 1) for line in (root/'deploy/.env').read_text().splitlines() if '=' in line)
(root/'apps/server/.env').write_text('DATABASE_URL=postgresql+psycopg://ronghub:'+quote(env['POSTGRES_PASSWORD'], safe='')+'@127.0.0.1:'+env.get('POSTGRES_PORT','55432')+'/ronghub\nDEMO_MODE=true\n')
print('Local API config ready (demo mode; secrets not printed).')
