"""Send Growth Studio email from the user's own mailbox over SMTP.

Separate from email_svc.py, which sends TenderFlow's own system mail from the server's SMTP settings.
Here the credentials belong to a user, are stored Fernet-encrypted (key: MAIL_CRED_KEY), and are never
returned by the API nor put in a log line or an error text from this module.

Pure logic plus one injectable SMTP factory, so it can be tested without a network.
"""
from __future__ import annotations

import ipaddress
import re
import smtplib
import socket
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any, Callable, Iterable, Mapping

from cryptography.fernet import Fernet, InvalidToken

ALLOWED_PORTS = (25, 465, 587, 2525)
SECURITY_CHOICES = ("ssl", "starttls")
DEFAULT_DAILY_CAP = 100
MAX_DAILY_CAP = 500
SEND_PAUSE_SECONDS = 1
SMTP_TIMEOUT = 15

PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {
        "label": "Gmail / Google Workspace",
        "host": "smtp.gmail.com",
        "port": 465,
        "security": "ssl",
        "help": "Turn on 2-step verification, then create an app password and use it here.",
    },
    "outlook": {
        "label": "Outlook / Microsoft 365",
        "host": "smtp.office365.com",
        "port": 587,
        "security": "starttls",
        "help": "Your administrator must allow SMTP AUTH for this mailbox; use an app password if MFA is on.",
    },
    "zoho": {
        "label": "Zoho Mail",
        "host": "smtp.zoho.com",
        "port": 465,
        "security": "ssl",
        "help": "Use an application-specific password from your Zoho security settings.",
    },
    "onecom": {
        "label": "one.com",
        "host": "send.one.com",
        "port": 465,
        "security": "ssl",
        "help": "Use your full email address as the login and your mailbox password.",
    },
    "custom": {
        "label": "Other (custom SMTP)",
        "host": "",
        "port": 587,
        "security": "starttls",
        "help": "Use the SMTP host, port and login your email provider gave you, "
                "for example smtp.yourcompany.com.",
    },
}

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)

SmtpFactory = Callable[[str, int, str], "smtplib.SMTP"]


class MailError(Exception):
    """A failure with a message that is safe to show the user. `fatal` means stop sending the batch."""

    def __init__(self, message: str, code: str = "mail_error", fatal: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.fatal = fatal


# ── secrets ───────────────────────────────────────────────────────────────────

def _fernet(key: str) -> Fernet:
    if not key:
        raise MailError(
            "Mailbox connections are not enabled on this server. Ask your administrator to set MAIL_CRED_KEY.",
            "no_key", True,
        )
    try:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except Exception:
        raise MailError("MAIL_CRED_KEY is not a valid Fernet key.", "bad_key", True) from None


def encrypt_secret(plain: str, key: str) -> str:
    return _fernet(key).encrypt((plain or "").encode("utf-8")).decode("ascii")


def decrypt_secret(token: str, key: str) -> str:
    try:
        return _fernet(key).decrypt((token or "").encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        raise MailError(
            "The saved mailbox password can no longer be read. Please enter it again.", "bad_secret", True
        ) from None


def mask_login(login: str) -> str:
    """j***@company.com style hint, so the UI can show which login is saved without exposing it."""
    login = login or ""
    if "@" not in login:
        return "***" if login else ""
    local, _, domain = login.partition("@")
    return (local[:1] + "***@" + domain) if local else "***@" + domain


# ── validation ────────────────────────────────────────────────────────────────

def host_problem(host: str) -> str | None:
    """Why this SMTP host must not be connected to, in the user's words; None when it is acceptable.

    The server connects to a host the user typed, so private, loopback, link-local or metadata ranges are refused.
    """
    host = (host or "").strip().lower()
    if not host or not _HOST_RE.match(host):
        return "Enter the mail server's host name, for example smtp.yourcompany.com."
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return "That mail server host name could not be found."
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return "That mail server address is not allowed."
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            return "That mail server address is not allowed."
    return None


def clean(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalise the settings a user submitted. Raises MailError with a user-facing message.

    The password is passed through untouched (it may be blank when updating an account that already has one).
    """
    provider = str(data.get("provider") or "custom").strip().lower()
    if provider not in PRESETS:
        provider = "custom"
    preset = PRESETS[provider]

    host = str(data.get("host") or preset["host"]).strip().lower()
    security = str(data.get("security") or preset["security"]).strip().lower()
    if security not in SECURITY_CHOICES:
        raise MailError("Security must be SSL/TLS or STARTTLS.", "bad_security")

    raw_port = data.get("port")
    try:
        port = int(raw_port if raw_port not in (None, "") else preset["port"])
    except (TypeError, ValueError):
        raise MailError("The port must be a whole number.", "bad_port") from None
    if port not in ALLOWED_PORTS:
        raise MailError(
            "The port must be one of " + ", ".join(str(p) for p in ALLOWED_PORTS) + ".", "bad_port"
        )

    problem = host_problem(host)
    if problem:
        raise MailError(problem, "bad_host")

    from_email = str(data.get("from_email") or "").strip()
    if not _EMAIL_RE.match(from_email):
        raise MailError("Enter a valid From email address.", "bad_from")

    login = str(data.get("login") or "").strip()
    if not login or len(login) > 254 or any(c.isspace() for c in login):
        raise MailError("Enter the mailbox login (usually your email address, with no spaces).", "bad_login")

    reply_to = str(data.get("reply_to") or "").strip()
    if reply_to and not _EMAIL_RE.match(reply_to):
        raise MailError("The reply-to address is not valid.", "bad_reply_to")

    raw_cap = data.get("daily_cap")
    if raw_cap in (None, ""):
        cap = DEFAULT_DAILY_CAP
    else:
        try:
            cap = int(raw_cap)
        except (TypeError, ValueError):
            raise MailError("The daily limit must be a whole number.", "bad_cap") from None
    if not 1 <= cap <= MAX_DAILY_CAP:
        raise MailError(f"The daily limit must be between 1 and {MAX_DAILY_CAP}.", "bad_cap")

    return {
        "provider": provider,
        "host": host,
        "port": port,
        "security": security,
        "login": login,
        "from_email": from_email,
        "from_name": str(data.get("from_name") or "").strip()[:120],
        "reply_to": reply_to,
        "daily_cap": cap,
        "password": data.get("password") or "",
    }


# ── SMTP ──────────────────────────────────────────────────────────────────────

def _default_factory(host: str, port: int, security: str) -> "smtplib.SMTP":
    if security == "ssl":
        return smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=ssl.create_default_context())
    return smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)


def _explain(exc: Exception) -> MailError:
    """Turn an smtplib/socket failure into a message that is useful and never echoes the password."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return MailError(
            "The mailbox rejected the login. Check the username and password "
            "(many providers need an app password).", "auth_failed", True)
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return MailError(
            "The mailbox refused this From address. It must be the account's own address or an alias of it.",
            "sender_refused", True)
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return MailError("The recipient address was refused.", "recipient_refused")
    if isinstance(exc, ssl.SSLError):
        return MailError(
            "A secure connection could not be made. Check the port and the security setting.",
            "ssl_error", True)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return MailError("The mail server did not answer in time.", "timeout", True)
    if isinstance(exc, (OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)):
        return MailError("The mail server could not be reached. Check the host and port.",
                         "connect_failed", True)
    if isinstance(exc, smtplib.SMTPResponseException):
        detail = exc.smtp_error
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "ignore")
        return MailError(
            f"The mail server answered with error {exc.smtp_code}: {str(detail)[:160]}",
            "smtp_error", exc.smtp_code >= 500 and exc.smtp_code != 550)
    return MailError("Sending failed.", "send_failed")


def _connect(account: Mapping[str, Any], password: str, factory: SmtpFactory | None):
    factory = factory or _default_factory
    host, port, security = account["host"], int(account["port"]), account["security"]
    try:
        smtp = factory(host, port, security)
        if security == "starttls":
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        smtp.login(account["login"], password)
        return smtp
    except MailError:
        raise
    except Exception as exc:  # noqa: BLE001 - mapped to a safe message
        raise _explain(exc) from None


def test_connection(account: Mapping[str, Any], password: str, factory: SmtpFactory | None = None) -> None:
    """Connect and log in without sending anything. Raises MailError."""
    smtp = _connect(account, password, factory)
    try:
        smtp.quit()
    except Exception:  # noqa: BLE001
        pass


_HTML_FRAME = ('<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#222">'
               "{}</body></html>")


def build_message(account: Mapping[str, Any], to: str, subject: str, body: str,
                  unsubscribe_to: str | None = None, html: str | None = None,
                  attachments: Iterable[Mapping[str, Any]] | None = None) -> EmailMessage:
    """A message from the user: plain text, or text plus an HTML version (so a mail client that cannot show formatting still
    reads it), with files attached when there are any ({"filename", "content_type", "data"}). Carries a mailto List-Unsubscribe,
    which gives mail clients their unsubscribe affordance without pointing at a TenderFlow URL."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((account.get("from_name") or "", account["from_email"]))
    msg["To"] = to
    if account.get("reply_to"):
        msg["Reply-To"] = account["reply_to"]
    msg["Date"] = formatdate(localtime=True)
    domain = account["from_email"].split("@", 1)[-1] if "@" in account["from_email"] else "localhost"
    msg["Message-ID"] = make_msgid(domain=domain)
    unsub = unsubscribe_to or account["from_email"]
    msg["List-Unsubscribe"] = f"<mailto:{unsub}?subject=unsubscribe>"
    msg.set_content(body)
    if html:
        msg.add_alternative(_HTML_FRAME.format(html), subtype="html")
    for f in attachments or ():
        maintype, _, subtype = (f.get("content_type") or "application/octet-stream").partition("/")
        msg.add_attachment(bytes(f["data"]), maintype=maintype or "application", subtype=subtype or "octet-stream", filename=f["filename"])
    return msg


def send_batch(account: Mapping[str, Any], password: str, items: Iterable[Mapping[str, Any]],
               on_result: Callable[[Mapping[str, Any], dict[str, Any]], None] | None = None,
               factory: SmtpFactory | None = None,
               pause: float = SEND_PAUSE_SECONDS) -> list[dict[str, Any]]:
    """Send each {"key", "to", "subject", "body", optional "html" and "attachments"} item over one connection, pausing between messages.

    `on_result` is called as soon as each message has an outcome, so the caller can record it before the
    next one is attempted. Stops after a fatal failure (bad login, unreachable server); the remaining items
    are returned with ok=False and error "not attempted".
    """
    items = list(items)
    results: list[dict[str, Any]] = []

    def record(item: Mapping[str, Any], res: dict[str, Any]) -> None:
        results.append(res)
        if on_result:
            on_result(item, res)

    try:
        smtp = _connect(account, password, factory)
    except MailError as exc:
        for item in items:
            record(item, {"key": item.get("key"), "ok": False,
                          "error": exc.message if exc.fatal or True else "not attempted",
                          "message_id": None, "fatal": exc.fatal})
        return results

    stopped = False
    try:
        for i, item in enumerate(items):
            if stopped:
                record(item, {"key": item.get("key"), "ok": False, "error": "not attempted",
                              "message_id": None, "fatal": True})
                continue
            if i and pause:
                time.sleep(pause)
            try:
                msg = build_message(account, item["to"], item["subject"], item["body"],
                                    html=item.get("html"), attachments=item.get("attachments"))
                smtp.send_message(msg)
                record(item, {"key": item.get("key"), "ok": True, "error": None,
                              "message_id": msg["Message-ID"], "fatal": False})
            except Exception as exc:  # noqa: BLE001
                err = exc if isinstance(exc, MailError) else _explain(exc)
                record(item, {"key": item.get("key"), "ok": False, "error": err.message,
                              "message_id": None, "fatal": err.fatal})
                if err.fatal:
                    stopped = True
    finally:
        try:
            smtp.quit()
        except Exception:  # noqa: BLE001
            pass
    return results
