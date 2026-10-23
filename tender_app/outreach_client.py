"""Client for the Outreach by Aivhub API (https://outreach.aivhub.com, v1).

TenderFlow uses it from Growth Studio to send a campaign message through Outreach. Outreach sends nothing
until a campaign is approved (a separate permission, `email:approve`), so sending is two calls: draft the
campaign, then approve it. Growth Studio's Send button makes both, for the buyers the user chose.

What is used (from Outreach's own API reference):

    GET  /v1/wallets                  credits left and what is connected (mailboxes, social, phone numbers)
    POST /v1/email/send               one email, sent straight away (scope email:approve; 25 a day per company)
    GET  /v1/email/messages           per-email status (sent / replied / bounced); ?campaign_id=, ?status=, ?q=
    GET  /v1/email/messages/{id}      one email, with the reply or bounce reason
    POST /v1/email/campaigns          draft an email campaign (scope email:write)
    POST /v1/email/campaigns/{id}/decision   approve it, which is what starts it sending (scope email:approve)
    GET  /v1/email/campaigns/{id}     its emails, how many went out, replies, approval state
    POST /v1/social/posts             the AI writes a post (and picture); nothing goes live until it is approved
    GET  /v1/social/posts/{id}        its status, caption and the result once published
    POST /v1/social/posts/{id}/decision   approve / reject / cancel it (scope social:approve); approving publishes
    POST /v1/voice/calls              the AI phones one number (scope voice:calls); the outcome is read by asking for the call
    GET  /v1/voice/calls/{id}         status, outcome, answers, transcript, whether a meeting was booked
    DELETE /v1/voice/calls/{id}       hang up a call that is ringing or in progress
    GET  /v1/voice/scripts            the saved call scripts

Authentication is `Authorization: Bearer sk_test_...` (pretends everything, charges nothing) or `sk_live_...`.
Requests that create something carry an `Idempotency-Key`, so a retry returns the first answer instead of a
second draft. Errors come as `{"error": {"code", "message", "details"}}`; 429 carries `Retry-After`.

The key is secret: it is never put in a message, a log line or an exception text from this module. The HTTP
transport is injectable so tests never touch the network.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

# The API host api.outreach.aivhub.com currently fails its TLS handshake; /v1/* works on the main host.
DEFAULT_BASE_URL = "https://outreach.aivhub.com"
DEFAULT_TIMEOUT = 15.0

_KEY_RE = re.compile(r"^sk_(test|live)_[A-Za-z0-9_\-]{6,}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{3,64}$")
_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")  # "x" is a network

# (method, url, headers, body bytes or None, timeout) -> (status, response headers, response text)
Transport = Callable[[str, str, Mapping[str, str], "bytes | None", float], "tuple[int, Mapping[str, str], str]"]


def key_mode(key: str | None) -> str | None:
    """"test" or "live" for a well-formed key, None for anything else (including an empty value)."""
    m = _KEY_RE.match((key or "").strip())
    return m.group(1) if m else None


def _https_url(value: str | None, limit: int = 500) -> str | None:
    """`value` when it is a plain https:// link with a host and nothing risky in it, else None."""
    text = (value or "").strip()
    if not text.lower().startswith("https://") or len(text) > limit or any(c.isspace() or c in '"<>\\' for c in text):
        return None
    host = text[8:].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return text if host and "@" not in host else None


def mask_key(key: str | None) -> str:
    """Enough to recognise which key is configured, never enough to use it."""
    text = (key or "").strip()
    return text[:8] + "…" if len(text) > 8 else ""


_KEY_ANYWHERE_RE = re.compile(r"sk_(?:test|live)_[A-Za-z0-9_\-]+")


def scrub(text: Any) -> str:
    """`text` with anything shaped like an API key replaced by its masked form, for anything that is stored or shown."""
    return _KEY_ANYWHERE_RE.sub(lambda m: mask_key(m.group(0)) or "sk_…", str(text if text is not None else ""))


def config_problem(key: str | None, allow_live: bool) -> str | None:
    """Why the configured key cannot be used, in the user's words; None when it can. The key is never quoted."""
    text = (key or "").strip()
    if not text:
        return "Outreach is not connected. Ask your administrator to set OUTREACH_API_KEY on the server."
    mode = key_mode(text)
    if mode is None:
        return "The configured Outreach key is not an sk_test_ or sk_live_ key."
    if mode == "live" and not allow_live:
        return "A live Outreach key is configured but live sending is switched off (OUTREACH_ALLOW_LIVE=0). Use an sk_test_ key."
    return None


# How the account's daily cap on single sends is worded is not documented, so it is recognised by its code or text.
_DAILY_CODE_RE = re.compile(r"daily|per_day|day_limit|limit_reached|quota", re.IGNORECASE)
_DAILY_TEXT_RE = re.compile(r"daily|per day|a day|24 hours", re.IGNORECASE)


class OutreachError(Exception):
    """A failed call. `fatal` means that retrying the same request, or the next one, cannot succeed."""

    def __init__(self, message: str, status: int = 502, code: str = "outreach_error",
                 retry_after: int | None = None, details: Any = None):
        message = scrub(message)
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.retry_after = retry_after
        self.details = details

    @property
    def daily_limit(self) -> bool:
        """The company's daily cap on single sends was reached. Sending more today cannot succeed."""
        if self.status not in (402, 403, 409, 422, 429):
            return False
        return bool(_DAILY_CODE_RE.search(self.code or "") or _DAILY_TEXT_RE.search(self.message or ""))

    @property
    def setup_required(self) -> bool:
        """Outreach needs setting up first (no connected mailbox / warm-up not started): nothing can send until then."""
        return bool(re.search(r"connect a mailbox|no mailbox|mailbox.*(not connected|warm)", f"{self.code} {self.message}", re.IGNORECASE))

    @property
    def fatal(self) -> bool:
        return (self.status in (401, 402, 403, 429) or self.code in ("unreachable", "not_configured")
                or self.daily_limit or self.setup_required)


def _requests_transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float):
    import requests

    resp = requests.request(method, url, headers=dict(headers), data=body, timeout=timeout)
    return resp.status_code, resp.headers, resp.text


PARTNER_AUTH_ACTIONS = ("signup", "login", "resend_verification", "forgot_password")


def partner_auth(action: str, data: Mapping[str, Any], *, base_url: str = DEFAULT_BASE_URL, origin: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT, transport: Transport | None = None) -> dict[str, Any]:
    """One of Outreach's partner `auth` actions (no key needed): POST /v1/run {"plugin": "auth", "action": ..., "data": ...}.
    `signup` and `login` answer with the account's `api_key`, shown once; `signup` answers `verify_email: true` with no key
    when the user must confirm their email first. The caller's `data` (which holds a password) is sent and then forgotten:
    it is never logged, and an Outreach error text is scrubbed before it can reach the user."""
    if action not in PARTNER_AUTH_ACTIONS:
        raise OutreachError("That is not an Outreach sign-in action.", 400, "invalid_request")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if origin:
        headers["Origin"] = origin
    body = json.dumps({"plugin": "auth", "action": action, "data": dict(data)}).encode("utf-8")
    try:
        status, resp_headers, text = (transport or _requests_transport)("POST", f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/v1/run", headers, body, timeout)
    except Exception as exc:  # network down, DNS, timeout: say so without echoing the request
        raise OutreachError(f"Outreach could not be reached ({type(exc).__name__}).", 502, "unreachable") from None
    payload: Any = None
    if text:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
    if 200 <= status < 300:
        if not isinstance(payload, dict):
            raise OutreachError("Outreach answered with something that was not a JSON object.", 502, "bad_response")
        return payload
    raise _error_for(status, resp_headers, payload)


def partner_key(answer: Mapping[str, Any]) -> str | None:
    """The `api_key` of a signup / login answer (at the top, or under `data`), only when it is a well-formed Outreach key."""
    for src in (answer, answer.get("data") if isinstance(answer.get("data"), Mapping) else None):
        if isinstance(src, Mapping):
            key = str(src.get("api_key") or "").strip()
            if key_mode(key):
                return key
    return None


def partner_needs_verification(answer: Mapping[str, Any]) -> bool:
    return any(isinstance(s, Mapping) and s.get("verify_email") is True for s in (answer, answer.get("data")))


class OutreachClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, *, timeout: float = DEFAULT_TIMEOUT,
                 transport: Transport | None = None):
        key = (api_key or "").strip()
        if not key_mode(key):
            raise OutreachError("The Outreach API key is missing or is not an sk_test_ / sk_live_ key.", 400, "not_configured")
        self._key = key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._transport = transport or _requests_transport

    @property
    def mode(self) -> str:
        return key_mode(self._key) or "test"

    # ── transport ────────────────────────────────────────────────────────────────────────────────

    def _call(self, method: str, path: str, body: Mapping[str, Any] | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            status, resp_headers, text = self._transport(method, f"{self.base_url}{path}", headers, data, self.timeout)
        except Exception as exc:  # network down, DNS, timeout: say so without echoing the request
            raise OutreachError(f"Outreach could not be reached ({type(exc).__name__}).", 502, "unreachable") from None
        payload: Any = None
        if text:
            try:
                payload = json.loads(text)
            except ValueError:
                payload = None
        if 200 <= status < 300:
            if not isinstance(payload, dict):
                raise OutreachError("Outreach answered with something that was not a JSON object.", 502, "bad_response")
            return payload
        raise self._error(status, resp_headers, payload)

    @staticmethod
    def _error(status: int, headers: Mapping[str, str], payload: Any) -> OutreachError:
        err = payload.get("error") if isinstance(payload, dict) else None
        code, message, details = "outreach_error", "", None
        if isinstance(err, dict):
            code = str(err.get("code") or code)
            message = str(err.get("message") or "")
            details = err.get("details")
        elif isinstance(err, str):
            message = err
        elif isinstance(payload, dict) and isinstance(payload.get("detail"), str):
            # the web host answers {"detail": "...", "code": "unauthorized"} rather than {"error": {...}}
            message = payload["detail"]
            code = str(payload.get("code") or code)
        retry_after = None
        raw = None
        try:
            raw = headers.get("Retry-After") or headers.get("retry-after")
        except Exception:
            raw = None
        if raw:
            try:
                retry_after = max(0, int(float(raw)))
            except ValueError:
                retry_after = None
        if not message:
            message = {
                401: "Outreach did not accept the API key.",
                402: "Outreach has not enough credits for this.",
                403: "The API key is not allowed to do that. Give it the permission in Outreach's Keys tab.",
                404: "Outreach does not know that id.",
                422: "Outreach rejected something in the request.",
                429: "Outreach is rate limiting requests. Wait and try again.",
            }.get(status, f"Outreach answered with HTTP {status}.")
        return OutreachError(message, status, code, retry_after, details)

    # ── endpoints ────────────────────────────────────────────────────────────────────────────────

    def wallets(self) -> dict[str, Any]:
        return self._call("GET", "/v1/wallets")

    def disconnect_partner(self) -> dict[str, Any]:
        """Tell Outreach this app is no longer connected to the account (`auth` `disconnect`, with the account's key), so the key
        stops working there as well as being forgotten here."""
        return self._call("POST", "/v1/run", {"plugin": "auth", "action": "disconnect"})

    def create_email_campaign(self, payload: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        if not idempotency_key:
            raise OutreachError("An idempotency key is required to create a campaign.", 400, "invalid_request")
        return self._call("POST", "/v1/email/campaigns", payload, idempotency_key)

    def decide_email_campaign(self, campaign_id: str, decision: str, idempotency_key: str) -> dict[str, Any]:
        """Approve ("approve") or reject a campaign. Approval is the only thing that makes Outreach send it, and
        needs the key to hold the `email:approve` permission (without it the answer is 403 missing_scope)."""
        if decision not in ("approve", "reject"):
            raise OutreachError("decision must be approve or reject.", 400, "invalid_request")
        if not _ID_RE.match(campaign_id or ""):
            raise OutreachError("That is not an Outreach campaign id.", 400, "invalid_request")
        if not idempotency_key:
            raise OutreachError("An idempotency key is required to decide a campaign.", 400, "invalid_request")
        return self._call("POST", f"/v1/email/campaigns/{campaign_id}/decision", {"decision": decision}, idempotency_key)

    def send_email(self, payload: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        """One email, sent now (no warm-up wait, no sending hours). Skips anyone on the do-not-email list and adds
        the unsubscribe link. Costs one credit; 25 a day per company. Needs the `email:approve` permission."""
        if not idempotency_key:
            raise OutreachError("An idempotency key is required to send an email.", 400, "invalid_request")
        return self._call("POST", "/v1/email/send", payload, idempotency_key)

    def list_email_messages(self, *, campaign_id: str | None = None, status: str | None = None,
                            q: str | None = None, cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if cursor:
            if not re.fullmatch(r"[A-Za-z0-9_\-=.:+/]{1,300}", cursor):
                raise OutreachError("That is not a page cursor.", 400, "invalid_request")
            params["cursor"] = cursor
        if campaign_id:
            if not _ID_RE.match(campaign_id):
                raise OutreachError("That is not an Outreach campaign id.", 400, "invalid_request")
            params["campaign_id"] = campaign_id
        if status:
            params["status"] = status[:30]
        if q:
            params["q"] = q[:200]
        return self._call("GET", "/v1/email/messages" + (("?" + urlencode(params)) if params else ""))

    def get_email_message(self, message_id: str) -> dict[str, Any]:
        if not _ID_RE.match(message_id or ""):
            raise OutreachError("That is not an Outreach message id.", 400, "invalid_request")
        return self._call("GET", f"/v1/email/messages/{message_id}")

    def get_email_campaign(self, campaign_id: str) -> dict[str, Any]:
        if not _ID_RE.match(campaign_id or ""):
            raise OutreachError("That is not an Outreach campaign id.", 400, "invalid_request")
        return self._call("GET", f"/v1/email/campaigns/{campaign_id}")

    # ── social posts ─────────────────────────────────────────────────────────────────────────────

    def create_social_post(self, payload: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        """Ask the AI to write a post. Nothing is published: it waits for `decide_social_post(..., "approve")`."""
        if not idempotency_key:
            raise OutreachError("An idempotency key is required to create a post.", 400, "invalid_request")
        return self._call("POST", "/v1/social/posts", payload, idempotency_key)

    def get_social_post(self, post_id: str) -> dict[str, Any]:
        if not _ID_RE.match(post_id or ""):
            raise OutreachError("That is not an Outreach post id.", 400, "invalid_request")
        return self._call("GET", f"/v1/social/posts/{post_id}")

    # ── connecting social accounts from the partner's own page (`social` plugin, POST /v1/run) ──────

    def run(self, plugin: str, action: str, data: Mapping[str, Any] | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        """One call of Outreach's single entry point: POST /v1/run {"plugin", "action", "data"}, with the user's key."""
        body: dict[str, Any] = {"plugin": plugin, "action": action}
        if data is not None:
            body["data"] = dict(data)
        return self._call("POST", "/v1/run", body, idempotency_key)

    def channel_providers(self) -> dict[str, Any]:
        """Which social networks are switched on at Outreach's end right now."""
        return self.run("social", "channel.providers")

    def channel_list(self, refresh: bool = False) -> dict[str, Any]:
        return self.run("social", "channel.list", {"refresh": True} if refresh else None)

    def channel_connect(self, provider: str, return_url: str) -> dict[str, Any]:
        """Start connecting one network. The answer carries `auth_url`, to open in a small window; the user signs in on the
        network's own page, so neither side sees a password or token."""
        if not _PROVIDER_RE.match(provider or ""):
            raise OutreachError("That is not a social network.", 400, "invalid_request")
        return self.run("social", "channel.connect", {"provider": provider, "return_url": return_url})

    def channel_disconnect(self, channel_id: str) -> dict[str, Any]:
        if not _ID_RE.match(channel_id or ""):
            raise OutreachError("That is not an Outreach channel id.", 400, "invalid_request")
        return self.run("social", "channel.disconnect", {"id": channel_id})

    def update_social_post(self, post_id: str, payload: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        """Change a post: its publish time, or ask the AI to write the text, the picture or both again. Outreach sends a
        changed post back for approval, so this never publishes anything."""
        if not _ID_RE.match(post_id or ""):
            raise OutreachError("That is not an Outreach post id.", 400, "invalid_request")
        if not payload:
            raise OutreachError("Nothing to change.", 400, "invalid_request")
        if not idempotency_key:
            raise OutreachError("An idempotency key is required to change a post.", 400, "invalid_request")
        return self._call("PATCH", f"/v1/social/posts/{post_id}", payload, idempotency_key)

    def decide_social_post(self, post_id: str, decision: str, idempotency_key: str, publish_at: str | None = None) -> dict[str, Any]:
        """Approve ("approve", the only way a post goes live), reject or cancel it. Needs `social:approve`. An approval
        may carry a new `publish_at`: approval closes at the publish time, so a post whose time has passed is approved
        for a later one."""
        if decision not in ("approve", "reject", "cancel"):
            raise OutreachError("decision must be approve, reject or cancel.", 400, "invalid_request")
        if not _ID_RE.match(post_id or ""):
            raise OutreachError("That is not an Outreach post id.", 400, "invalid_request")
        if not idempotency_key:
            raise OutreachError("An idempotency key is required to decide a post.", 400, "invalid_request")
        body: dict[str, Any] = {"decision": decision}
        if publish_at and decision == "approve":
            body["publish_at"] = publish_at
        return self._call("POST", f"/v1/social/posts/{post_id}/decision", body, idempotency_key)

    # ── phone calls ──────────────────────────────────────────────────────────────────────────────

    def make_call(self, payload: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        """The AI phones one number. Answers at once (status "ringing"); the outcome is read with `get_call`."""
        if not idempotency_key:
            raise OutreachError("An idempotency key is required to make a call.", 400, "invalid_request")
        return self._call("POST", "/v1/voice/calls", payload, idempotency_key)

    def get_call(self, call_id: str) -> dict[str, Any]:
        if not _ID_RE.match(call_id or ""):
            raise OutreachError("That is not an Outreach call id.", 400, "invalid_request")
        return self._call("GET", f"/v1/voice/calls/{call_id}")

    def end_call(self, call_id: str) -> dict[str, Any]:
        if not _ID_RE.match(call_id or ""):
            raise OutreachError("That is not an Outreach call id.", 400, "invalid_request")
        return self._call("DELETE", f"/v1/voice/calls/{call_id}")

    def voice_scripts(self) -> dict[str, Any]:
        return self._call("GET", "/v1/voice/scripts")


# ── reading answers whose exact shape the reference does not spell out ──────────────────────────────

def _find(obj: Any, names: tuple[str, ...]) -> Any:
    """The first value found under any of `names`, at the top level or one level down (`campaign`, `data`)."""
    if not isinstance(obj, dict):
        return None
    for name in names:
        if name in obj and obj[name] not in (None, ""):
            return obj[name]
    for inner in ("campaign", "data"):
        sub = obj.get(inner)
        if isinstance(sub, dict):
            for name in names:
                if name in sub and sub[name] not in (None, ""):
                    return sub[name]
    return None


def _text(value: Any, limit: int = 60) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    return str(value)[:limit]


def campaign_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The few fields Growth Studio shows: id, status, approval state, how many went out, replies."""
    sent = _find(raw, ("sent", "sent_count", "emails_sent"))
    replies = _find(raw, ("replies", "reply_count", "replied"))
    return {
        "id": _text(_find(raw, ("id", "campaign_id")), 64),
        "status": _text(_find(raw, ("status", "state"))),
        "approval": _text(_find(raw, ("approval", "approval_status", "approval_state"))),
        "sent": sent if isinstance(sent, int) and not isinstance(sent, bool) else None,
        "replies": replies if isinstance(replies, int) and not isinstance(replies, bool) else None,
    }


def mailbox_list(raw: Mapping[str, Any], connected: Any = None) -> list[dict[str, Any]]:
    """The mailboxes /v1/wallets reports as connected, as [{id, email, can_send, status}]. Outreach does not
    document the shape, so a count, a list of addresses or a list of objects are all read; only those four fields
    are ever picked out (nothing else the answer might carry is passed on)."""
    items: Any = None
    for src in (connected, raw):
        if isinstance(src, dict):
            for name in ("mailboxes", "email_accounts", "emails"):
                if isinstance(src.get(name), (list, dict)):
                    items = src[name]
                    break
        if items is not None:
            break
    if isinstance(items, dict):
        items = [items]
    out: list[dict[str, Any]] = []
    for item in (items if isinstance(items, list) else [])[:20]:
        if isinstance(item, str):
            if "@" in item:
                out.append({"id": None, "email": item.strip()[:254], "can_send": None, "status": None, "fields": []})
        elif isinstance(item, dict):
            email = _text(_find(item, ("email", "address", "from_email", "from")), 254)
            if email and "@" in email:
                can_send = item.get("can_send")
                ident = _text(_find(item, ("id", "mailbox_id", "uuid", "key")), 64)
                if ident and "@" in ident:
                    ident = None  # an address is not an id
                out.append({"id": ident, "email": email,
                            "can_send": can_send if isinstance(can_send, bool) else None,
                            "status": _text(_find(item, ("status", "state")), 30),
                            # names only (never values): which fields Outreach reports, so a missing id is diagnosable
                            "fields": sorted(k for k in item if isinstance(k, str) and re.fullmatch(r"[A-Za-z0-9_]{1,30}", k))[:15]})
    return out


def _connected_counts(connected: Any) -> dict[str, Any] | None:
    """What is connected, reduced to counts and flags per channel. The answer may carry anything (tokens, account
    details), and this goes to the browser, so only numbers, booleans and the size of a list or object are kept."""
    if isinstance(connected, list):
        return {"items": len(connected)}
    if not isinstance(connected, dict):
        return None
    out: dict[str, Any] = {}
    for name, value in list(connected.items())[:20]:
        key = re.sub(r"[^A-Za-z0-9_]", "", str(name))[:30]
        if not key:
            continue
        if isinstance(value, bool) or (isinstance(value, (int, float)) and not isinstance(value, bool)):
            out[key] = value
        elif isinstance(value, (list, dict)):
            out[key] = len(value)
    return out


def parse_mailbox_ids(text: str | None) -> dict[str, str]:
    """"a@x.test=mb_1, b@x.test=mb_2" -> {"a@x.test": "mb_1", ...}; anything malformed is skipped."""
    out: dict[str, str] = {}
    for part in re.split(r"[,;\s]+", text or ""):
        email, sep, ident = part.partition("=")
        email, ident = email.strip().lower(), ident.strip()
        if sep and "@" in email and _ID_RE.match(ident):
            out[email] = ident
    return out


def apply_mailbox_ids(boxes: list[dict[str, Any]], mapping: Mapping[str, str]) -> list[dict[str, Any]]:
    """Fill a mailbox's id from the configured map when Outreach's list gave only its address."""
    for b in boxes:
        if not b.get("id") and mapping.get(str(b.get("email") or "").lower()):
            b["id"] = mapping[str(b["email"]).lower()]
    return boxes


def wallet_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Credits and what is connected, tolerant of the exact field names."""
    credits = _find(raw, ("credits", "balance", "credits_left"))
    connected = _find(raw, ("connected", "connections", "channels"))
    return {
        "credits": credits if isinstance(credits, (int, float)) and not isinstance(credits, bool) else None,
        "connected": _connected_counts(connected),
        "mailboxes": mailbox_list(raw, connected),
        "social_channels": social_channel_names(raw),
        "phone_numbers": phone_number_count(raw),
        "livemode": raw.get("livemode") if isinstance(raw.get("livemode"), bool) else None,
    }


def send_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The answer to /v1/email/send: id, status ("sent" is the only status that means it went), to, from."""
    return {
        "id": _text(_find(raw, ("id", "message_id")), 64),
        "status": (_text(_find(raw, ("status", "state"))) or "").lower() or None,
        "to": _text(_find(raw, ("to", "email")), 254),
        "from": _text(_find(raw, ("from", "from_email")), 254),
    }


def message_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    """One email as listed by /v1/email/messages: id, recipient, status, and why it bounced or what the reply was."""
    reason = _find(raw, ("bounce_reason", "reason", "error"))
    mailbox = _find(raw, ("mailbox", "from", "from_email", "mailbox_email"))
    return {
        "id": _text(_find(raw, ("id", "message_id")), 64),
        "to": (_text(_find(raw, ("to", "email", "recipient")), 254) or "").lower() or None,
        "status": (_text(_find(raw, ("status", "state"))) or "").lower() or None,
        "reason": _text(reason, 300),
        # which mailbox sent it, and the id Outreach uses for that mailbox if the record carries one
        "mailbox": _text(mailbox, 254) if "@" in str(mailbox or "") else None,
        "mailbox_id": _text(_find(raw, ("mailbox_id", "mailboxId")), 64),
        "fields": sorted(k for k in raw if isinstance(k, str) and re.fullmatch(r"[A-Za-z0-9_]{1,30}", k))[:20],
    }


def message_list(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The emails in a /v1/email/messages answer (a list under `messages`, `data` or `items`)."""
    for name in ("messages", "data", "items", "results"):
        items = raw.get(name) if isinstance(raw, dict) else None
        if isinstance(items, list):
            return [message_summary(i) for i in items if isinstance(i, dict)]
    return []


# ── social posts and calls: reading answers the reference only partly spells out ─────────────────────

def _names(value: Any, limit: int = 12) -> list[str]:
    """A list of short lowercase names from a list of strings or of objects with a platform / channel / name."""
    out: list[str] = []
    for item in (value if isinstance(value, list) else [])[:limit]:
        if isinstance(item, dict):
            item = _find(item, ("platform", "channel", "network", "name", "type"))
        if isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_ .-]{1,30}", item.strip()):
            name = item.strip().lower()
            if name not in out:
                out.append(name)
    return out


def social_channel_names(raw: Mapping[str, Any]) -> list[str]:
    """The social channels /v1/wallets reports as connected (empty when it gives none or only a count)."""
    connected = _find(raw, ("connected", "connections", "channels"))
    for src in (connected, raw):
        if isinstance(src, dict):
            for name in ("social_channels", "social", "channels"):
                if isinstance(src.get(name), list):
                    return _names(src[name])
    return []


def phone_number_count(raw: Mapping[str, Any]) -> int | None:
    """How many phone numbers /v1/wallets reports as connected, None when it does not say."""
    connected = _find(raw, ("connected", "connections", "channels"))
    for src in (connected, raw):
        if isinstance(src, dict) and "phone_numbers" in src:
            v = src["phone_numbers"]
            if isinstance(v, list):
                return len(v)
            if isinstance(v, int) and not isinstance(v, bool):
                return v
    return None


def _image_link(raw: Mapping[str, Any]) -> str | None:
    """The post's picture as a plain https link, whether Outreach gives it as a string, an object or a list."""
    v = _find(raw, ("image_url", "picture_url", "image", "picture", "thumbnail", "media", "images"))
    if isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, dict):
        v = _find(v, ("url", "src", "href", "image_url", "link"))
    return _https_url(_text(v, 500))


def _variants(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """How the post reads on each channel ("how it looks on each channel"): [{channel, text}], text as plain text."""
    src = _find(raw, ("previews", "per_channel", "by_channel", "variants", "channel_previews"))
    items: list[tuple[Any, Any]] = []
    if isinstance(src, dict):
        items = list(src.items())
    else:
        entries = src if isinstance(src, list) else _find(raw, ("channels",))
        for e in (entries if isinstance(entries, list) else []):
            if isinstance(e, dict):
                items.append((_find(e, ("channel", "platform", "network", "name")), e))
    out: list[dict[str, Any]] = []
    for name, v in items[:8]:
        text = v if isinstance(v, str) else (_find(v, ("text", "caption", "body", "preview", "content")) if isinstance(v, dict) else None)
        label = _names([name])
        t = plain_text(text, 3000)
        if label and t:
            out.append({"channel": label[0], "text": t})
    return out


# ── connecting social accounts: what the three read calls answer, reduced to what the page shows ──────────────
PROVIDER_LABELS = {"linkedin": "LinkedIn", "facebook": "Facebook", "instagram": "Instagram", "x": "X", "twitter": "X", "threads": "Threads"}
DEFAULT_PROVIDERS = ("linkedin", "facebook", "instagram", "x", "threads")
_CHANNEL_STATUS = {
    "connected": "connected", "active": "connected", "ok": "connected", "linked": "connected",
    "needs_reauth": "needs_reauth", "reauth": "needs_reauth", "expired": "needs_reauth", "needs_reconnect": "needs_reauth", "reconnect": "needs_reauth",
    "error": "error", "failed": "error", "disconnected": "error",
}


def _rows(raw: Any, names: tuple[str, ...]) -> list[Any]:
    """The list under any of `names`, at the top level or one level down (`data`); a bare list answer is the list."""
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []
    for src in (raw, raw.get("data") if isinstance(raw.get("data"), dict) else None):
        if isinstance(src, dict):
            for name in names:
                if isinstance(src.get(name), list):
                    return src[name]
    return raw["data"] if isinstance(raw.get("data"), list) else []


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider) or provider.replace("_", " ").title()


def channel_providers_summary(raw: Any) -> list[dict[str, Any]]:
    """[{provider, label, available}]: the networks Outreach has switched on. `available` is False for one whose developer app is
    not set up at Outreach's end yet (the page then explains instead of offering a button that cannot work)."""
    out: list[dict[str, Any]] = []
    for item in _rows(raw, ("providers", "networks", "channels", "items"))[:20]:
        if isinstance(item, str):
            item = {"provider": item}
        if not isinstance(item, dict):
            continue
        p = str(_find(item, ("provider", "id", "network", "name", "type")) or "").strip().lower()
        if not _PROVIDER_RE.match(p) or any(o["provider"] == p for o in out):
            continue
        avail = item.get("available", item.get("enabled", True))
        out.append({"provider": p, "label": _text(item.get("label"), 40) or provider_label(p), "available": avail is not False})
    return out


def channel_list_summary(raw: Any) -> list[dict[str, Any]]:
    """[{id, provider, label, name, status, connected_at}] for the accounts the user has connected. `status` is one of
    connected, needs_reauth, error (anything else Outreach says is shown as error, so it is never mistaken for working)."""
    out: list[dict[str, Any]] = []
    for item in _rows(raw, ("channels", "accounts", "items"))[:50]:
        if not isinstance(item, dict):
            continue
        cid = _text(_find(item, ("id", "channel_id")), 64)
        p = str(_find(item, ("provider", "network", "platform", "type")) or "").strip().lower()
        if not cid or not _ID_RE.match(cid) or not _PROVIDER_RE.match(p):
            continue
        status = _CHANNEL_STATUS.get(str(_find(item, ("status", "state")) or "connected").strip().lower(), "error")
        out.append({"id": cid, "provider": p, "label": provider_label(p), "status": status,
                    "name": _text(_find(item, ("display_name", "name", "handle", "account_name", "username", "title")), 80),
                    "connected_at": _text(_find(item, ("connected_at", "created_at")), 40)})
    return out


def connect_summary(raw: Any) -> dict[str, Any]:
    """The answer of channel.connect: the link to open (https only; a test key answers with the return address at once),
    and whether it already counts as connected."""
    link = _text(_find(raw, ("auth_url", "url", "authorize_url")), 2000)
    url = _https_url(link, 2000)
    if not url and link and re.match(r"^http://(localhost|127\.0\.0\.1)(:\d{1,5})?(/[^\s\"<>\\]*)?$", link.strip()):
        url = link.strip()  # a test key answers with the return address itself, which is plain http on a developer's machine
    return {"auth_url": url, "connected": bool(_find(raw, ("connected", "done"))) if isinstance(raw, dict) else False}


def post_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A social post: id, status, caption, picture, how it reads per channel, channels, preview link (https only) and
    the published link if there is one."""
    channels = _find(raw, ("channels",))
    result = _find(raw, ("result", "published", "publication"))
    link = _find(result, ("url", "link", "permalink")) if isinstance(result, dict) else _find(raw, ("published_url", "post_url"))
    return {
        "id": _text(_find(raw, ("id", "post_id")), 64),
        "status": (_text(_find(raw, ("status", "state"))) or "").lower() or None,
        "caption": _text(_find(raw, ("caption", "text", "body")), 3000),
        "channels": _names(channels),
        "preview_url": _https_url(_text(_find(raw, ("preview_url", "preview")), 500)),
        "image_url": _image_link(raw),
        "variants": _variants(raw),
        "published_url": _https_url(_text(link, 500)),
        "publish_at": _text(_find(raw, ("publish_at", "scheduled_for")), 40),
        "reason": _text(_find(raw, ("error", "reason", "failure_reason")), 300),
    }


def _answers(value: Any) -> list[dict[str, str]]:
    """The answers to the call's questions as [{key, answer}] with short text; anything else is dropped."""
    out: list[dict[str, str]] = []
    if isinstance(value, dict):
        for k, v in list(value.items())[:12]:
            if isinstance(v, (str, int, float, bool)):
                out.append({"key": str(k)[:40], "answer": str(v)[:300]})
    return out


def _transcript(value: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for line in (value if isinstance(value, list) else [])[:200]:
        if isinstance(line, dict) and isinstance(line.get("text"), str):
            who = str(line.get("who") or line.get("speaker") or "")[:20]
            out.append({"who": who, "text": line["text"][:600]})
    return out


def call_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A call: id, status, outcome, length, the answers, whether a meeting was booked, the transcript, what can be done now."""
    secs = _find(raw, ("duration_seconds", "duration"))
    booked = _find(raw, ("meeting_booked",))
    caps = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
    return {
        "id": _text(_find(raw, ("id", "call_id")), 64),
        "status": (_text(_find(raw, ("status", "state"))) or "").lower() or None,
        "outcome": (_text(_find(raw, ("outcome", "result")), 40) or "").lower() or None,
        "to": _text(_find(raw, ("to", "number")), 30),
        "duration_seconds": secs if isinstance(secs, (int, float)) and not isinstance(secs, bool) else None,
        "meeting_booked": booked if isinstance(booked, bool) else None,
        "answers": _answers(raw.get("answers")),
        "transcript": _transcript(raw.get("transcript")),
        "live_url": _https_url(_text(_find(raw, ("live_url",)), 500)),
        "can_end": caps.get("end") is True,
        "has_recording": bool(_find(raw, ("recording_url",))),
        "reason": _text(_find(raw, ("error", "reason", "failure_reason")), 300),
    }


def script_list(raw: Mapping[str, Any]) -> list[dict[str, str]]:
    """The saved call scripts as [{id, name}]."""
    for name in ("scripts", "data", "items", "results"):
        items = raw.get(name) if isinstance(raw, dict) else None
        if isinstance(items, list):
            out = []
            for i in items[:50]:
                if isinstance(i, dict):
                    ident = _text(_find(i, ("id", "script_id")), 64)
                    label = _text(_find(i, ("name", "title")), 80)
                    if ident and _ID_RE.match(ident):
                        out.append({"id": ident, "name": label or ident})
            return out
    return []


# ── the inbox: sent emails and the replies to them ─────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]*>")
_BLANKS_RE = re.compile(r"\n{3,}")


def plain_text(value: Any, limit: int) -> str | None:
    """Readable text from an email body that may be HTML: tags dropped, entities decoded, blank runs squeezed.
    Never returns markup, so whatever shows it can escape it and still read well."""
    if not isinstance(value, str) or not value.strip():
        return None
    import html as _html

    text = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", "", value)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?i)<br\s*/?>|</div>|</li>", "\n", text)
    text = _html.unescape(_TAG_RE.sub("", text)).replace("\r\n", "\n").replace("\r", "\n")
    text = _BLANKS_RE.sub("\n\n", "\n".join(line.rstrip() for line in text.split("\n"))).strip()
    return text[:limit] or None


def inbox_message(raw: Mapping[str, Any]) -> dict[str, Any]:
    """One row of the inbox list: who it went to, the subject, what became of it, when, and from which mailbox."""
    mailbox = _find(raw, ("mailbox", "from", "from_email", "mailbox_email"))
    return {
        "id": _text(_find(raw, ("id", "message_id")), 64),
        "to": (_text(_find(raw, ("to", "email", "recipient")), 254) or "").lower() or None,
        "subject": _text(_find(raw, ("subject",)), 300),
        "status": (_text(_find(raw, ("status", "state"))) or "").lower() or None,
        "sent_at": _text(_find(raw, ("sent_at", "created_at")), 40),
        "replied_at": _text(_find(raw, ("replied_at", "reply_at")), 40),
        "mailbox": _text(mailbox, 254) if "@" in str(mailbox or "") else None,
        "kind": _text(_find(raw, ("kind", "type")), 30),
    }


def message_page(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A /v1/email/messages answer as {items: [inbox rows], next_cursor}."""
    items: list[dict[str, Any]] = []
    for name in ("data", "messages", "items", "results"):
        found = raw.get(name) if isinstance(raw, dict) else None
        if isinstance(found, list):
            items = [inbox_message(i) for i in found if isinstance(i, dict)]
            break
    cur = raw.get("next_cursor") if isinstance(raw, dict) else None
    return {"items": items, "next_cursor": cur if isinstance(cur, str) and re.fullmatch(r"[A-Za-z0-9_\-=.:+/]{1,300}", cur) else None}


_REPLY_TEXT = ("text", "body", "snippet", "content", "message", "plain", "reply", "html")
_REPLY_FROM = ("from", "from_email", "sender", "email", "address")
_REPLY_AT = ("received_at", "replied_at", "at", "date", "created_at", "sent_at")


def reply_detail(raw: Mapping[str, Any]) -> dict[str, Any]:
    """One sent email with its replies. The reference says only "the email as it went out, plus the reply or the
    bounce reason", so a reply may be text, an object or a list of either, and is read from the likely names. Text is
    reduced to plain text; nothing else the answer carries is passed on."""
    head = inbox_message(raw)
    src = _find(raw, ("replies", "reply", "response", "responses"))
    entries = src if isinstance(src, list) else ([src] if src not in (None, "") else [])
    replies: list[dict[str, Any]] = []
    for e in entries[:5]:
        if isinstance(e, str):
            text, sender, at, subject = plain_text(e, 6000), None, None, None
        elif isinstance(e, dict):
            text = plain_text(_find(e, _REPLY_TEXT), 6000)
            sender = _text(_find(e, _REPLY_FROM), 254)
            at = _text(_find(e, _REPLY_AT), 40)
            subject = _text(_find(e, ("subject",)), 300)
        else:
            continue
        if text or sender:
            replies.append({"text": text, "from": sender if sender and "@" in sender else None, "at": at, "subject": subject})
    return {
        **head,
        "replies": replies,
        "original": plain_text(_find(raw, ("body", "text", "html", "content", "message")), 4000),
        "bounce_reason": _text(_find(raw, ("bounce_reason", "reason", "error")), 300),
    }


# the real class's error builder, kept apart so partner_auth keeps working when a test swaps the OutreachClient name
_error_for = OutreachClient._error
