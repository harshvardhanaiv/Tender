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

## Email through Outreach by Aivhub

Settings has an **Email (Outreach)** panel for anyone an administrator has allowed to send (administrators always
may). It sends through [Outreach by Aivhub](https://outreach.aivhub.com), from the mailbox connected there. The
key lives on the server only: it is never sent to the browser, logged, stored in the database or put in an error
text (the panel shows `sk_test_…`, the first 8 characters). Growth Studio's "Send to Outreach" uses the same key.

### Settings (server environment)

| Variable | Default | Meaning |
|---|---|---|
| `OUTREACH_API_KEY` | none | Bearer key, `sk_test_...` or `sk_live_...` |
| `OUTREACH_API_BASE` | `https://outreach.aivhub.com` | `api.outreach.aivhub.com` currently fails its TLS handshake; `/v1/*` works on the main host |
| `OUTREACH_SEND_MAILBOX_FIELD` / `OUTREACH_CAMPAIGN_MAILBOX_FIELD` | `mailbox_id` / `mailbox_ids` | Field names sent when a mailbox is chosen in Growth Studio's "Send from" dropdown (shown when Outreach reports two or more mailboxes). Outreach has not documented them yet, so the answer's `from` is checked and a mismatch is flagged |
| `OUTREACH_WEB_URL` | `https://outreach.aivhub.com` | Outreach's web app: where "Connect Outreach" and "Manage in Outreach" open (new tab) so the user signs in or up, chooses a plan and connects a mailbox. https only |
| `OUTREACH_CONNECT_URL` / `OUTREACH_MANAGE_URL` | blank | Optional deeper links (its sign-up or plans page, its Mailboxes page). Blank uses `OUTREACH_WEB_URL` |
| `OUTREACH_ALLOW_LIVE` | `0` | While `0` a `sk_live_` key is refused with a clear message. Set `1` only when real sending is intended |
| `OUTREACH_TIMEOUT_SECONDS` | `15` | Per request |
| `OUTREACH_DAILY_SEND_CAP` | `25` | Single sends a day per company (Outreach's limit) |
| `OUTREACH_MAX_BATCH` | `200` | Most emails in one user action; the rest are reported as skipped |
| `OUTREACH_CAMPAIGN_MERGE_FIELDS` | `0` | `1` once a test key has shown that `{{firstName}}` resolves per lead inside a campaign step |

### How a send is routed (`tender_app/email_send.py`)

| Recipients | Route |
|---|---|
| 1 | `POST /v1/email/send`. Sent straight away: no warm-up wait, no sending hours. Costs one credit. Recorded as sent **only** when Outreach answers `status: "sent"` |
| 2 or more, the same message | One campaign with all of them as leads (our own `steps`, never `personalise`), then `POST .../decision {"decision":"approve"}`. Shown as "approved, sending in the sending window", never as sent |
| 2 or more, different messages | One `/v1/email/send` each, up to the daily cap; the rest come back as "not sent: daily limit" and nothing is queued |

"The same message" means the same text after `{{firstName}}` and `{{company}}` are filled in for each person. A batch
can mix the cases: people who share a text become a campaign, the others are sent singly.

Before anything is sent the whole request is refused for an empty subject or message, an unknown merge field or a
message with no opt-out wording. Individual recipients are skipped, with the reason, and never fail the batch: no or
invalid address, listed twice, opted out or suppressed (the suppression list, bounced addresses, and Growth Studio
opt-outs), or the same text already sent to that address (a unique hash of mode + address + subject + body in
`email_sends`, claimed before Outreach is called, so a double click or a retry cannot send twice).

If creating a campaign works but approving it fails, the campaign is kept as "created" (it sends nothing) and the
next attempt approves **that** campaign instead of creating a second. A 401, 402, 403 or 429, an unreachable
Outreach, the daily cap or a "connect a mailbox first" answer stops the batch; the recipients not yet tried are
listed and can be sent later. A 422 only fails that recipient.

### Limits and Outreach-side setup

- 25 single sends a day per company. Campaigns obey the campaign's sending window (company timezone, UK default
  09:00 to 17:00) and mailbox warm-up, so a campaign is **not** instant.
- In Outreach (not automated): connect a mailbox, set DKIM/DMARC and start warm-up, and give the key `email:write`
  and `email:approve`. The mailbox needs `can_send` true.
- **Refresh status** polls Outreach (campaign state, then each email: sent / replied / bounced) and updates what is
  stored; it never moves an email backwards. A bounced address is added to the suppression list. No webhook is
  needed (none is implemented).

### Testing

```
python tests/test_outreach_client.py      # client: key checks, request shapes, every error, no key in any text
python tests/test_email_send.py           # routing, guards, skips, caps, hashes, status rules (no database)
python tests/test_email_send_api.py       # the routes, with a fake Outreach (needs the dev database)
node tests/email_outreach_view_test.js    # the settings panel
```

No test calls Outreach. To try the real thing: set `OUTREACH_API_KEY` to an `sk_test_...` key (it pretends
everything and sends nothing), restart, and use the panel's Preview and Send. Use the same test key to check whether
Outreach fills `{{firstName}}` per lead in a campaign step before setting `OUTREACH_CAMPAIGN_MERGE_FIELDS=1`.
Grant a user access with `POST /api/email/outreach/permissions {"username": "...", "allowed": true}` (administrators).

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

## Supplier contact details (website, email, phone, LinkedIn)

Tender notices rarely carry contact details, so opening a supplier looks them up instead of showing a search link.
`tender_app/supplier_contacts.py` uses the website already on the notice (or the domain of a known email); otherwise it
searches for the company's own site, or guesses one from its name, and only accepts a site whose text contains the
company's name (and its postcode or Companies House number when known). It reads the home page and up to two
contact/about pages and keeps business emails, UK phone numbers and the company's LinkedIn page.

* It reads public pages only: every address and redirect must resolve to a public IP, robots.txt is respected, at most
  six small fetches per supplier, 14 s overall.
* Results are stored in `supplier_contact_enrichment` (30 days; 7 days for "nothing found") and shown in the panel as
  "from website". The notice's own email/phone/website fields are never overwritten.
* `POST /api/suppliers/<id>/contacts/enrich` (rate limited; `?force=1` to look again); `GET /api/suppliers/<id>` returns
  `found_contacts`.
* These are business contact details from a company's own site. Check PECR/GDPR rules before using them for marketing.
