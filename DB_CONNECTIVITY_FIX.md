# Database connectivity — diagnosis and fix

## What the failed deploy actually shows

The deploy stalled for 420s with `web`, `celery_worker` and `celery_beat` all
looping on `Database not available after 30s`, while `db` sat there reporting
`healthy`. Three separate defects combined to produce that, and to hide the
cause.

### 1. The real error was being thrown away

`docker-entrypoint.sh` ran its probe as:

```sh
until python -c "... connection.ensure_connection()" 2>/dev/null; do
```

`2>/dev/null` discards the exception. A wrong password, an unresolvable
hostname, a closed port and a database that does not exist all printed the
same line. There was nothing in the logs to act on.

### 2. `db` reported healthy while refusing the app

The healthcheck was `mysqladmin ping`. That command prints `mysqld is alive`
and exits 0 even when the server rejects the credentials — it only proves
something answered on the socket. So the container went healthy, compose let
`web` start, and `web` was then denied.

The proof is in the deploy's own backup step, which is the one place the error
was written to disk rather than to `/dev/null`:

```
backups/2026-09-17_120942/mysqldump.err:
mysqldump: Got error: 1045: Access denied for user 'root'@'localhost'
           (using password: YES) when trying to connect
```

Error 1045 means the server was reached and the password was wrong.

### 3. `root` cannot share one password across two servers

`.env` has `DB_USER=root` with an 8-character `DB_PASSWORD`, and a separate
24-character `DB_ROOT_PASSWORD`. On a MySQL container, root's password is set
from `MYSQL_ROOT_PASSWORD` (`DB_ROOT_PASSWORD`) and **never** from
`MYSQL_PASSWORD` (`DB_PASSWORD`). So `DB_PASSWORD` — which is the password of
the MySQL installed on the host — is simply not root's password in the
container. They are two different servers with two different root accounts.

`docker-compose.yml` also passed `MYSQL_USER: root`, which the mysql image
rejects outright (root already exists). Older images instead created a second,
weakly-privileged `'root'@'%'` — which is how the 14 Sep backup got far enough
to fail on a *different* error (`you need the PROCESS privilege`) while the
17 Sep one failed on 1045.

### 4. `DB_HOST` could only ever mean one thing

`.env` says `DB_HOST=localhost`, correct for host-side `manage.py`. Inside a
container that means *the container itself*. Compose papered over this with a
hardcoded `DB_HOST: db` in `environment:`, which wins over `env_file:` — so the
in-Docker database became the only reachable option. Pointing the containers at
a MySQL outside Docker was impossible: the value in `.env` was overridden and
silently ignored.

---

## What changed

| File | Change |
|---|---|
| `university_timetable_system/settings.py` | `DATABASES` now resolves the host per context, supports container-only credential overrides, and adds `connect_timeout` / `CONN_MAX_AGE` |
| `wait_for_db.py` *(new)* | Diagnostic wait: prints the real error, separates DNS / port / auth failures, exits immediately on 1045 and 1049 instead of retrying 30 times |
| `docker-entrypoint.sh` | Calls `wait_for_db.py`; timeout raised from a hard 30s to `DB_WAIT_TIMEOUT` (120s default) |
| `docker-compose.yml` | Removed hardcoded `DB_HOST: db`; `db` moved behind the `localdb` profile with `required: false` dependencies; `MYSQL_USER` no longer root; healthcheck runs a real authenticated query; `host.docker.internal` mapping added |
| `scripts/db_doctor.sh` *(new)* | Reports and, with `--provision`, fixes accounts/grants |
| `scripts/z_omega_deployment_script/07_backup.sh` | Uses `DB_ROOT_PASSWORD` when dumping as root, so the pre-deploy snapshot stops failing |
| `.env.db.additions` *(new)* | The keys to add, with all three layouts written out |

### How the host is resolved now

`settings.py`, at import:

1. Not in a container → `DB_HOST` is used exactly as written.
2. In a container, `DB_HOST` is a real hostname → used as written (remote DB).
3. In a container, `DB_HOST` is loopback → replaced with `DOCKER_DB_HOST`,
   default `db`, and a line is written to stderr saying so.

That is what makes one `.env` correct in both places. `manage.py` on the host
keeps talking to `localhost`; the containers get something that resolves.

| Layout | `DB_HOST` | `DOCKER_DB_HOST` | `COMPOSE_PROFILES` |
|---|---|---|---|
| MySQL inside Docker | `127.0.0.1` | `db` | `localdb` |
| MySQL on the host | `127.0.0.1` | `host.docker.internal` | *(empty)* |
| MySQL elsewhere | `db.example.ac.ke` | *(empty)* | *(empty)* |

---

## Getting the current deploy working

```bash
cd ~/Downloads/chuka_timetabling

# 1. See exactly which account the container DB accepts
./scripts/db_doctor.sh

# 2. Create a dedicated app user (needs the root password to be accepted
#    in step 1's "Accounts inside the db container" section)
COMPOSE_PROFILES=localdb docker compose up -d db
./scripts/db_doctor.sh --provision

# 3. Point .env at it — see .env.db.additions for the full block
#    DB_USER=timetabling_user
#    DB_PASSWORD=<APP_DB_PASSWORD>
#    APP_DB_USER / APP_DB_PASSWORD identical
#    DOCKER_DB_HOST=db
#    COMPOSE_PROFILES=localdb

# 4. Rebuild (wait_for_db.py is baked into the image) and start
docker compose build web
docker compose up -d
docker compose logs -f web
```

If `--provision` reports that root itself is refused, the `db_data` volume was
initialised with a different `DB_ROOT_PASSWORD` than the one now in `.env`.
Options, in order of preference:

1. Put the original root password back in `DB_ROOT_PASSWORD` and re-run.
2. Recover it from an old `.env` in `backups/`.
3. Reset it offline — this keeps the data:
   ```bash
   docker compose stop db
   docker compose run --rm --no-deps -e MYSQL_ROOT_HOST=% db \
     mysqld --skip-grant-tables --skip-networking &
   # then in another shell: docker compose exec db mysql -u root
   #   FLUSH PRIVILEGES;
   #   ALTER USER 'root'@'localhost' IDENTIFIED BY '<new>';
   ```
4. Last resort, **destroys the database**: `docker compose down -v`.

## Switching to a MySQL outside Docker

```bash
# on the host
sudo sed -i 's/^bind-address.*/bind-address = 0.0.0.0/' /etc/mysql/mysql.conf.d/mysqld.cnf
sudo systemctl restart mysql
sudo ufw allow from 172.16.0.0/12 to any port 3306 proto tcp

# .env
DB_HOST=127.0.0.1
DOCKER_DB_HOST=host.docker.internal
COMPOSE_PROFILES=

./scripts/db_doctor.sh --provision     # creates 'user'@'%' + grants
docker compose up -d --force-recreate web celery_worker celery_beat
```

The `'user'@'%'` part matters: an account defined only as `'user'@'localhost'`
accepts the host's own socket connections and rejects everything arriving from
a container, again with error 1045.

## Unrelated, but visible in that log

`nginx` failed twice on `cannot load certificate key /etc/nginx/certs/privkey.pem`
before step 6 regenerated the self-signed pair, then started cleanly at 09:10.
Nothing to fix, but it means a `docker compose up` run *before* the deploy
script has generated certs will always fail that way.

`docker-compose.yml`'s obsolete `version:` key has been removed, which also
clears the `WARN ... the attribute version is obsolete` line from every command.
