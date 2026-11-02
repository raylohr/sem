#!/usr/bin/env python3
"""Create restricted DB/media backups without putting credentials in argv or logs."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile


def run(*args, **kwargs):
    return subprocess.run(*args, check=True, **kwargs)


def main():
    container = json.loads(subprocess.check_output(['docker', 'inspect', 'sem-prod-web-1']))[0]
    config = dict(value.split('=', 1) for value in container['Config']['Env'])
    if config.get('DB_NAME') != 'sem_prod' or config.get('DB_USER') != 'sem_prod_app':
        raise SystemExit('Onverwachte productiedatabase; backup gestopt.')
    directory = Path('/srv/sem-backups')
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    backup = Path(tempfile.mkdtemp(prefix=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=directory))
    cert = Path(__file__).resolve().parent / 'database/certs/db-pg.crt'
    env = {**os.environ, 'PGPASSWORD': config['DB_PASSWORD']}
    os.umask(0o077)
    with (backup / 'sem_prod.dump').open('wb') as stream:
        run(['docker', 'run', '--rm', '--network', 'sem-prod_default',
             '-v', f'{cert}:/run/db-pg.crt:ro', '-e', 'PGPASSWORD',
             '-e', 'PGHOST=db-pg', '-e', 'PGHOSTADDR=192.168.178.152',
             '-e', 'PGSSLMODE=verify-full', '-e', 'PGSSLROOTCERT=/run/db-pg.crt',
             'postgres:16-alpine', 'pg_dump', '--format=custom', '--no-owner', '--no-acl',
             '--username=sem_prod_app', '--dbname=sem_prod'], stdout=stream, env=env)
    with (backup / 'sem_prod.dump').open('rb') as stream:
        listing = subprocess.check_output(['docker', 'run', '--rm', '-i', 'postgres:16-alpine', 'pg_restore', '--list'], stdin=stream)
    if b'TABLE DATA' not in listing:
        raise SystemExit('Backup bevat geen verwachte tabeldata; controleer backup.')
    with (backup / 'media.tar.gz').open('wb') as stream:
        run(['docker', 'exec', 'sem-prod-web-1', 'tar', '-czf', '-', '-C', '/app/media', '.'], stdout=stream)
    (backup / 'release.json').write_text(json.dumps({'image': container['Config']['Image'], 'database': 'sem_prod'}, indent=2)+'\n')
    print('Database- en mediabackup opgeslagen:', backup)
    print('Dump-inhoud gecontroleerd met pg_restore --list; geen volledige herstelproef.')


if __name__ == '__main__':
    main()
