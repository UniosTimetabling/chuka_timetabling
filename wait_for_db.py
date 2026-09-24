#!/usr/bin/env python
"""
wait_for_db.py — wait for the configured database, and say *why* when it
does not come up.

Used by docker-entrypoint.sh. Replaces the old inline loop, which ran the
connection attempt with stderr redirected to /dev/null, so every failure
looked identical ("Database not available after 30s") no matter whether the
cause was a bad hostname, a closed port, or a rejected password.

Exit codes:
    0  connected
    1  gave up after the timeout (network-level problem)
    2  the server answered but refused us (credentials / missing database) —
       retrying cannot help, so it fails immediately with an explanation

Environment:
    DB_WAIT_TIMEOUT   seconds to keep retrying (default 120)
    DB_WAIT_INTERVAL  seconds between attempts (default 2)
"""

import os
import socket
import sys
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'university_timetable_system.settings')

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.db import connections  # noqa: E402

TIMEOUT = int(os.environ.get('DB_WAIT_TIMEOUT', '120'))
INTERVAL = float(os.environ.get('DB_WAIT_INTERVAL', '2'))

CFG = settings.DATABASES['default']
HOST = CFG.get('HOST') or 'localhost'
PORT = int(CFG.get('PORT') or 3306)
USER = CFG.get('USER') or ''
NAME = CFG.get('NAME') or ''

IN_CONTAINER = getattr(settings, 'IN_CONTAINER', False)
CONFIGURED_HOST = getattr(settings, 'DB_HOST_CONFIGURED', HOST)

# MySQL client error codes we can explain precisely.
ER_ACCESS_DENIED = 1045
ER_BAD_DB = 1049
CR_CONN_HOST_ERROR = 2003
CR_SERVER_GONE = 2006
CR_CONNECTION_ERROR = 2002


def log(msg=''):
    sys.stdout.write(msg + '\n')
    sys.stdout.flush()


def banner():
    log('==> Waiting for database...')
    log('    target : %s@%s:%s/%s' % (USER or '<no user>', HOST, PORT, NAME or '<no db>'))
    if IN_CONTAINER and CONFIGURED_HOST != HOST:
        log('    note   : DB_HOST=%s was rewritten to %s (loopback is unreachable '
            'from inside a container)' % (CONFIGURED_HOST, HOST))
    log('    limit  : %ss' % TIMEOUT)


def resolve():
    """Return (ok, detail) for DNS resolution of HOST."""
    try:
        socket.getaddrinfo(HOST, PORT, proto=socket.IPPROTO_TCP)
        return True, None
    except socket.gaierror as exc:
        return False, str(exc)


def tcp_open():
    """Return (ok, detail) for a raw TCP connect to HOST:PORT."""
    try:
        with socket.create_connection((HOST, PORT), timeout=5):
            return True, None
    except OSError as exc:
        return False, str(exc)


def db_connect():
    """
    Return (status, detail).

    status is one of: 'ok', 'retry', 'fatal'
    """
    conn = connections['default']
    try:
        conn.close()
    except Exception:
        pass
    try:
        conn.ensure_connection()
        return 'ok', None
    except Exception as exc:  # noqa: BLE001 - we classify below
        code = None
        args = getattr(exc, 'args', ())
        if args and isinstance(args[0], int):
            code = args[0]
        else:
            cause = getattr(exc, '__cause__', None)
            cargs = getattr(cause, 'args', ())
            if cargs and isinstance(cargs[0], int):
                code = cargs[0]
        if code in (ER_ACCESS_DENIED, ER_BAD_DB):
            return 'fatal', (code, str(exc))
        return 'retry', (code, str(exc))


def explain_network(detail):
    log('')
    log('!!! Could not reach the database server. !!!')
    log('    %s' % detail)
    log('')
    if IN_CONTAINER:
        log('    Checklist for a container:')
        log('      - DB inside Docker: DB_HOST must be the compose service name (db).')
        log('        Check that the db service is actually running:')
        log('          docker compose ps db')
        log('      - DB on the host machine: set DOCKER_DB_HOST=host.docker.internal')
        log('        in .env, make sure MySQL is bound to 0.0.0.0 (not 127.0.0.1) in')
        log('        /etc/mysql/mysql.conf.d/mysqld.cnf, and allow the docker bridge:')
        log('          sudo ufw allow from 172.16.0.0/12 to any port 3306 proto tcp')
        log('      - Remote DB: set DB_HOST to its address and confirm the firewall.')
    else:
        log('    Checklist for a host process:')
        log('      - Is MySQL running?   sudo systemctl status mysql')
        log('      - Is it on this port? ss -lntp | grep %s' % PORT)
        log('      - If the DB lives in Docker, publish it first (uncomment the')
        log('        db "ports:" block in docker-compose.yml) and use 127.0.0.1.')
    log('')
    log('    Run ./scripts/db_doctor.sh for a full host+container report.')


def explain_fatal(code, detail):
    log('')
    log('!!! The database server answered, but refused this connection. !!!')
    log('    %s' % detail)
    log('')
    if code == ER_ACCESS_DENIED:
        log('    The host and port are fine — only the credentials are wrong.')
        log('    User being used: %r' % USER)
        if USER == 'root':
            log('')
            log('    Note on root: MySQL takes root\'s password from')
            log('    MYSQL_ROOT_PASSWORD (DB_ROOT_PASSWORD in .env), never from')
            log('    MYSQL_PASSWORD (DB_PASSWORD). If DB_PASSWORD is the password of')
            log('    the MySQL on your host machine, it will not match the root')
            log('    account inside the db container — they are two different servers.')
            log('')
            log('    Recommended: stop using root for the app. Create a dedicated')
            log('    user in whichever server you are pointing at:')
            log('      ./scripts/db_doctor.sh --provision')
        log('')
        log('    If the account exists but only as user@localhost, it will not accept')
        log('    connections from another container. It needs a host pattern that')
        log("    matches, e.g. 'user'@'%%'.")
    elif code == ER_BAD_DB:
        log('    The database %r does not exist on that server.' % NAME)
        log('    Create it:  CREATE DATABASE `%s` CHARACTER SET utf8mb4 '
            'COLLATE utf8mb4_unicode_ci;' % NAME)
        log('    Or point DB_NAME at an existing one.')
    log('')
    log('    Run ./scripts/db_doctor.sh to test candidate credentials.')


def main():
    banner()

    deadline = time.time() + TIMEOUT
    attempt = 0
    last_net_detail = 'no attempt made'

    while time.time() < deadline:
        attempt += 1

        ok, detail = resolve()
        if not ok:
            last_net_detail = 'hostname %r does not resolve: %s' % (HOST, detail)
        else:
            ok, detail = tcp_open()
            if not ok:
                last_net_detail = 'nothing accepting connections on %s:%s: %s' % (
                    HOST, PORT, detail)
            else:
                status, info = db_connect()
                if status == 'ok':
                    log('==> Database is ready (%s:%s, after %ss).'
                        % (HOST, PORT, attempt * INTERVAL))
                    return 0
                if status == 'fatal':
                    code, text = info
                    explain_fatal(code, text)
                    return 2
                last_net_detail = info[1]

        remaining = int(deadline - time.time())
        log('    ... still waiting (attempt %d, %ss left) — %s'
            % (attempt, max(remaining, 0), last_net_detail))
        time.sleep(INTERVAL)

    explain_network(last_net_detail)
    return 1


if __name__ == '__main__':
    sys.exit(main())
