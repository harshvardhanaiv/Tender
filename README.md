# TenderFlow — UK & Ireland tender search

Search one keyword across public procurement portals (eTenders IE/NI, Public Contracts Scotland,
Sell2Wales, Find a Tender, Contracts Finder and more), score fit against your company profile,
track a pipeline, and draft bid responses with AI.

The app uses **PostgreSQL only** — in local development, CI and production.

## Project layout

| Path | What it is |
|---|---|
| `server.py` | Flask entry point (`create_app()`), search/AI routes |
| `tender_app/` | Auth, billing, credits, email, DB connection (`db.py`), schema migrations (`db_ext.py`), blueprints |
| `etenders_scraper/` | Portal scrapers and award-notice ingestion |
| `web/` | Frontend (plain HTML/JS/CSS served by Flask) |
| `scripts/` | Operational scripts (see below) |
| `docs/` | How it works, methodology and design notes |
| `docker-compose.yml` | Local development stack |
| `docker-compose.prod.yml`, `deploy.sh` | Production stack and deploy script |

Retired files live in a gitignored `NOT_REQUIRED/` folder on the machines that had them.

## Pulling the cleanup commits (one time)

`.env`, `.env.production` and `users.db` used to be tracked in git and are now ignored. When you pull
the commit that untracked them, **git deletes your local copies**. Copy them somewhere safe before
pulling and put them back afterwards.

The production server is affected too: `deploy.sh` runs `git reset --hard`, so back up
`.env.production` there before the first deploy (`cp .env.production ../.env.production.bak`) and
restore it after.

## Local development

Requires Docker Desktop.

```bash
cp .env.example .env          # fill in values; DB_PASSWORD is required
docker compose up --build     # Postgres + app with live reload
```

Open **http://localhost:8092**. Use `localhost`, not `127.0.0.1`: Firebase sign-in only works on
authorized domains, and `localhost` is authorized by default.

- The repo is mounted into the container: saving a `.py` file restarts the app, and changes under
  `web/` show on the next browser refresh.
- Background schedulers (deadline emails, award scraping) are off in dev (`ENABLE_SCHEDULERS=0`).
- The database schema is created and migrated automatically when the app starts. If Postgres is
  unreachable the app exits with a clear error.
- Postgres is also published on `127.0.0.1:5440` for database tools.

Useful commands:

```bash
docker compose logs -f app                               # app output
docker compose exec db psql -U postgres                  # SQL shell
docker compose exec app python scripts/<script>.py       # run a script with the app's settings
docker compose down                                      # stop (data is kept in the postgres_data volume)
```

### Running the app outside Docker

Start only the database with `docker compose up -d db`, then in a virtualenv with
`pip install -r requirements.txt` and `DB_HOST=localhost`, `DB_PORT=5440` in `.env`:

```bash
flask --app server:create_app run --debug --port 8092
```

### Importing the old SQLite data (one-off)

Supplier intelligence (`suppliers`, `contract_awards`) used to live in `users.db`. After the app has
started once against an empty database:

```bash
docker compose exec app python scripts/migrate_sqlite_to_postgres.py --dry-run
docker compose exec app python scripts/migrate_sqlite_to_postgres.py
```

The script reads `users.db` from the repo root (use `--sqlite PATH` if it lives elsewhere, e.g.
`NOT_REQUIRED/users.db`) and refuses to run if Postgres already holds more than the startup seed rows.

## Scripts

| Script | Purpose |
|---|---|
| `contracts_finder_sync.py` | Backfill/sync award notices from Contracts Finder and Find a Tender (`--ingest` writes to Postgres) |
| `run_supplier_audit.py` | Audit and correct supplier records |
| `fix_duplicate_suppliers.py` | Merge duplicate supplier records |
| `migrate_sqlite_to_postgres.py` | One-off import of supplier data from the old `users.db` |
| `setup_stripe_products.py`, `setup_stripe_portal.py` | Create Stripe products/prices and the billing portal configuration |
| `list_stripe_prices.py`, `diagnose_stripe_billing.py`, `sync_checkout.py` | Stripe diagnostics and repair |

Run scripts inside the app container (`docker compose exec app ...`) so they use the same settings.

## Production

`deploy.sh` fetches `origin/V1`, rebuilds and restarts `docker-compose.prod.yml` behind Traefik.
The server needs `.env.production` and `firebase-service-account.json` next to the compose file;
neither is in git. See "Pulling the cleanup commits" above before the first deploy.

## Secrets

Never commit `.env`, `.env.production`, service-account JSON or database files — they are gitignored.
`.env.example` is the template for every setting.

## Legal / etiquette

- Use a reasonable delay between requests to portals (default 1 second).
- Check each portal's terms before large automated use.
- Unofficial tool — not affiliated with any government or portal operator.
