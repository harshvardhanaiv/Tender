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

# (method, url, headers, body bytes or None, timeout) -> (status, response headers, response text)
Transport = Callable[[str, str, Mapping[str, str], "bytes | None", float], "tuple[int, Mapping[str, str], str]"]


def key_mode(key: str | None) -> str | None:
    """"test" or "live" for a well-formed key, None for anything else (including an empty value)."""
    m = _KEY_RE.match((key or "").strip())
    return m.group(1) if m else None


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
                            q: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
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


def wallet_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Credits and what is connected, tolerant of the exact field names."""
    credits = _find(raw, ("credits", "balance", "credits_left"))
    connected = _find(raw, ("connected", "connections", "channels"))
    return {
        "credits": credits if isinstance(credits, (int, float)) and not isinstance(credits, bool) else None,
        "connected": connected if isinstance(connected, (dict, list)) else None,
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
    return {
        "id": _text(_find(raw, ("id", "message_id")), 64),
        "to": (_text(_find(raw, ("to", "email", "recipient")), 254) or "").lower() or None,
        "status": (_text(_find(raw, ("status", "state"))) or "").lower() or None,
        "reason": _text(reason, 300),
    }


def message_list(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The emails in a /v1/email/messages answer (a list under `messages`, `data` or `items`)."""
    for name in ("messages", "data", "items", "results"):
        items = raw.get(name) if isinstance(raw, dict) else None
        if isinstance(items, list):
            return [message_summary(i) for i in items if isinstance(i, dict)]
    return []
