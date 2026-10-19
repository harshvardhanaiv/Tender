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

### Connections (top bar and Account menu)

**Connections** (top bar) and **Connected apps** (left menu, under Account) open a small window with two cards: Outreach and
the user's own Email account. Nothing opens in another window: the user connects and disconnects in place. Every Growth Studio
panel (Send mail, Inbox, Social, Calls) and the Email (Outreach) settings panel use the same window: their "Connect Outreach",
"Add an email account" and "Connections" buttons carry `data-open-connections` (`outreach` or `mail`, which card to scroll to),
and nothing in the app links out to Outreach's website any more. When the window is closed after something was connected or
disconnected it fires a `connections:changed` event and each panel that is showing looks again by itself.

- **Connected** (from `GET /api/growth/outreach/status`, which now also says `source`: `own`, `shared`, `disconnected` or
  `none`) shows the key mode, credits, email accounts, social channels and phone numbers, with **Refresh** and **Disconnect**.
- **Connect** takes the user's own Outreach key (`POST /api/growth/outreach/connect {"api_key": "sk_test_..."}`): the format is
  checked, Outreach is asked (`/v1/wallets`) whether it works, and only then is it kept, Fernet-encrypted with `MAIL_CRED_KEY`
  in `user_outreach_connections`. It is never returned, logged or put back on the page. `{"use_shared": true}` goes back to the
  organisation's key (`OUTREACH_API_KEY`).
- **Disconnect** (`DELETE /api/growth/outreach/connect`) forgets the user's key and, when the server has a key, switches
  Outreach off for that user (a `disabled` row), so the shared key is not quietly used for them. Every Outreach route
  (Growth Studio, Social, Calls, Inbox, the one-to-one email routes) follows the user's choice; other users are untouched.
  Anything already sent or posted is not affected.
- **Sign in / Create account (Outreach partner programme).** With `OUTREACH_CLIENT_ID` set (from Outreach's Developer API ->
  Partner programme tab) the Connect form offers **Sign in** (`POST /api/growth/outreach/signin`) and, with
  `OUTREACH_CONSENT_VERSION` too, **Create account** (`/signup`: company, name, email, password and a consent tick). The server
  calls Outreach's `POST /v1/run` `{"plugin": "auth", "action": "login" | "signup" ...}` with the client id, an `Origin` header
  (`OUTREACH_ORIGIN`, else this app's address) and, for signup, a `return_url` (`OUTREACH_RETURN_URL`, else
  `<address>/outreach/connected`); both must be registered in that tab. The answer's `api_key` is stored encrypted (`via =
  'partner'`) and never returned. The password goes only to Outreach: it is not stored, logged, echoed in an error or drawn
  back onto the page. If Outreach wants the email confirmed (`verify_email`), nothing is stored; the user confirms, Outreach
  sends them to `/outreach/connected` (which opens this window on Sign in), and they sign in. **Forgot password** and **Send
  the confirmation email again** call `forgot_password` / `resend_verification`. Disconnecting a partner sign-in also calls
  `auth` `disconnect` with the user's own key so it stops working in Outreach too (best effort; the user is disconnected here
  either way). Outreach's error codes (`bad_credentials`, `email_not_verified`, `account_exists`, `consent_required`,
  `origin_not_allowed`, `return_url_not_allowed`, `partner_suspended`, `invalid_client`) become plain messages.
- Outreach's public API has no call to add a mailbox, social channel or phone number, so those still belong to the user's
  Outreach account and show in the card once they exist there. Without `MAIL_CRED_KEY` a user cannot save their own key
  (the card says so and offers the organisation's connection if there is one).

**Email account card.** Because Outreach cannot add a mailbox, a user can set up their own sending account here: a provider
preset (Gmail, Outlook, Zoho or custom), the address, login, password (an app password where the provider asks for one), server,
port, security and a daily limit (default 100, at most 500). Routes: `GET /api/growth/email-account` (the account without its
password, whether the server can keep one, the presets), `PUT` (the login is tried against the mail server first, then kept;
a blank password keeps the saved one only while the login and server are unchanged), `POST .../test` (a short email from the
account to its own address) and `DELETE`. The password is Fernet-encrypted with `MAIL_CRED_KEY` in `user_mail_accounts`, never
returned, logged or drawn back onto the page; private and loopback mail servers are refused. Storage is
`tender_app/mail_account.py`, the SMTP work is `tender_app/user_mailer.py`.

**Sending from it.** The Send mail panel offers *Send from*: the own account and/or Outreach's mailboxes. With an account set up
and nothing else picked, the own account is the default, and it works with Outreach not connected at all. `POST
/api/growth/campaigns/<id>/outreach {"mailbox": "smtp"}` sends one email per buyer over SMTP (a `List-Unsubscribe` mailto header
and the opt-out line the message must already carry); each result is recorded as soon as the mail server answers
(`growth_outreach_drafts.mode = 'smtp'`, `outreach_id` = the Message-ID, approval `sent_now`), so a stopped batch never sends a
buyer twice and a retry sends only what is left. A refused address does not stop the others; a login that stops working stops
the batch and records nothing as sent. The limit counts emails sent from the account in the last 24 hours. Replies arrive in the
user's own mailbox (the Inbox tab reads Outreach only), which the panel says. Emails sent this way are never asked about in
Outreach (Refresh status skips them).

Files: `web/connections.js`, `web/connections.css`, `tender_app/outreach_connection.py`, `tender_app/mail_account.py`,
`tests/connections_test.js`, `tests/test_outreach_connection_api.py`, `tests/test_growth_email_account_api.py`.

### Inbox (Growth Studio, "Inbox" tab)

Shows the emails sent through Outreach and the replies to them (`GET /v1/email/messages?status=replied`, then the message
for the reply text; needs `email:write`). The Outreach key is shared by every TenderFlow user, so its list holds everyone's
emails: an email is shown to a user only when TenderFlow recorded its id (every one-to-one email), or, for a campaign
email, when it went to an address that user emailed and no other TenderFlow user has. A reply marks that buyer *replied*
in the campaign (counted on the Performance tab); a buyer already in a meeting or opted out is left alone. Opening a reply
marks it read. A reply that reads like "stop" is flagged, with a button to add the buyer to the do-not-contact list.
**Reply by email** opens the user's own mail program: nothing is sent from the inbox.

**Email accounts.** The top of the Inbox lists the accounts emails can go out from: the user's own email account (see
*Connections*) and the mailboxes Outreach reports as connected (from `/v1/wallets`; address, whether it can send, its status).
**Add an email account** opens the Connections window on the page, where the user sets up their own account; closing the window
re-checks, so the account appears here and in the *Send from* list of Growth Studio. Outreach's API cannot add a mailbox to an
Outreach account, so Outreach mailboxes are only listed. With no account the card says nothing can be sent.

**Delete** (on every row and on an opened email, with a confirmation and an **Undo** for nine seconds) removes a sent email
or a reply from the user's list: `DELETE /api/growth/inbox/<id>` writes a row in `growth_inbox_hidden`, `POST .../restore`
removes it. It hides, it does not erase: Outreach's public API has no call that deletes a sent email (its mail plugin's
`messages.move` / `messages.delete` work on a connected mailbox's own messages, need `mail:write` / `mail:delete`, and use
different ids), so Outreach keeps its record and the email stays sent. It affects only the user who deleted it, and only an
email that is theirs can be hidden.

### Social posts and phone calls (Growth Studio, "Social" and "Calls" tabs)

Uses the same key. Give it `social:write` and `social:approve` for posts, and `voice:calls` for calls, in Outreach's Keys tab.

- **Social post.** The user describes the post; Outreach's AI writes the caption and picture (`POST /v1/social/posts`).
  Creating a post never publishes it: it waits as *Ready to approve*, and only the **Approve and publish** button
  (after a confirmation) sends the approval (`.../decision`). TenderFlow reads Outreach's own state before approving,
  so a post still being written or already finished is refused. Channels come from `/v1/wallets`.
  The **Social** tab has the composer and the list of posts on the left and, on the right, the selected post as Outreach
  made it: the AI picture, the caption and how it reads on each channel, its **schedule** (change the time with
  `PATCH /v1/social/posts/{id}`; approve for a new time with `publish_at` on the decision) and the approval choices
  (approve, reject, cancel), plus **Write again** (the AI rewrites the text, the picture or both). A change sends the post
  back for approval and never publishes it.
  **Post as your company:** the composer's *Post as* field lists the user's company profiles from TenderFlow (the one Growth
  Studio uses, else the default, is preselected). The AI is told the company's name, what it does, where it is and its website,
  taken from the profile, plus an instruction to use only those facts; contact people, emails, phones, VAT and turnover are never
  sent. The panel shows exactly what is sent, and hints say what to add to the profile. Only the user's own profiles can be used.
  With no company set up, a general post can still be written.
  **Write it with the AI or yourself:** the composer asks *Let the AI write it* (an idea) or *I'll write the caption*
  (optionally *Let the AI polish it*: same meaning and facts, nothing added). The picture is *AI picture*, *My own picture*
  (PNG/JPG/WebP up to 8 MB, shrunk to 1600 px in the browser; the server checks the bytes really are that kind of picture and
  caps it at 5 MB) or *No picture*. The API only creates a post from a prompt, so a caption used as written is sent as
  "publish exactly this", and it, and any own picture, are then set **by hand** with `PATCH` once Outreach has finished writing
  (the browser does that by itself on the next refresh; an edit while the AI is still writing would be overwritten, so it is
  refused). Under the caption the preview has tabs: *Approve*, *Edit* (caption box, own picture), *Improve with AI* (write the text, the picture or both again, with a note) and *Schedule*. A finished post (published, rejected, cancelled, failed, expired) cannot change, so it offers *Start a new post from this*, which puts its wording in the caption box. A hand edit goes
  back for approval and never publishes. **Unverified against live Outreach:** its docs say a post can be edited "by hand" but
  not which fields; `caption` and `image_url` (a data URL) are the names its own editor uses and are set in one place
  (`HAND_CAPTION_FIELD`, `HAND_PICTURE_FIELD` in `tender_app/outreach_channels.py`).
- **Phone call.** One number per request, placed only by the **Call now** button after a confirmation
  (`POST /v1/voice/calls`). UK PECR: a marketing call may go to an organisation, not an individual, and never to a
  number on the TPS / CTPS, so the call is refused unless the user ticked both confirmations for that call, the number is
  a UK landline (01, 02 or 03; mobiles, 08/09 and foreign numbers are refused), and the buyer is not on the
  do-not-contact list. The same number is not called twice in 24 hours unless nobody answered. The buyer's number can be
  looked up from the notices already held (never invented). Results (outcome, answers, transcript) are read back on
  demand; transcripts are not stored here.
- Both are idempotent per attempt (a double click is one request) and capped per user per 24 hours.

| Variable | Default | Meaning |
|---|---|---|
| `OUTREACH_DAILY_CALL_CAP` | `20` | Calls one user may place in 24 hours |
| `OUTREACH_DAILY_POST_CAP` | `20` | Posts one user may create in 24 hours |

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

### Buyer contact inbox in Growth Studio

In the campaign builder, a buyer with no contact email is looked up automatically instead of sending the user to the
notice. `tender_app/buyer_contacts.py` reads, in order, the notice itself (Find a Tender, Contracts Finder, Sell2Wales,
Public Contracts Scotland; only those exact hosts are ever fetched) and the buyer's own website (procurement / contracts /
contact pages). Only addresses that look like the public body's own are kept (an award notice also names the winning
supplier, and every notice carries the portal's helpdesk), and role inboxes (procurement@, tenders@, contracts@, info@)
are preferred. The inbox is saved in the empty contact box with where it was found, never over an address the user typed.
A named person's address is not filled in automatically, but it is offered with a **Use this address** button (the
response carries `find_contact.suggestion`), so the user does not have to open the notice.
Where the buyer's website comes from, in order: the website the notice states, the domain of the notice's address,
the usual domain of a public body of that name (`public_body_domains`: swale.gov.uk for Swale Borough Council,
x.ac.uk, x.police.uk, x.nhs.uk; only believed once the page names the buyer), and only then a web search (which is
often blocked, so it is no longer the first resort). The GOV.UK notice portals answer 403 to a bot, so their notice
pages are read with a browser user agent, as the app's scrapers already do; a council's own site is always fetched with
the honest TenderFlowBot agent. A site that blocks us (Swale's answers 403 to everything) is not worked around: the
notice's named contact is offered instead and nothing is guessed. `buyer_contact_cache.lookup_version` is 2; answers
from the older, weaker lookup are looked up again.
`POST /api/growth/campaigns/<id>/targets/<tid>/find-contact`; answers are cached per buyer in `buyer_contact_cache`
(30 days; 7 days for "nothing published"). Nothing is emailed by looking an address up.
