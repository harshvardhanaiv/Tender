"""Growth Studio -> Outreach: social posts and phone calls. The rules only, no network and no database
(the HTTP layer is in blueprints/growth_bp.py, the API client in outreach_client.py), so each rule is tested alone.

Social posts. The user writes a prompt; Outreach's AI writes the caption and the picture and answers with a preview.
Nothing goes live until the user approves it (`social:approve`), and an approval is its own request, made by the user
after reading the preview, never by this module as part of creating the post.

Phone calls. Outreach's AI phones ONE number per request, made only when the user presses the button for it. In the UK
the Privacy and Electronic Communications Regulations allow a marketing call to a company or public body but not to an
individual (which includes most mobiles and sole traders), and a number registered with the TPS / CTPS must not be
called. So a call is refused unless the user has confirmed both points for that call, the number is a UK landline
(01, 02 or 03: mobile, premium and freephone numbers are refused outright), and the buyer is not on the do-not-contact
list. These are the rules that can be checked before the request; the confirmation itself is the user's.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

MAX_PROMPT = 1500
MIN_PROMPT = 10
MAX_GOAL = 400
MAX_INSTRUCTIONS = 1000
MAX_QUESTIONS = 8
MAX_QUESTION = 200
MAX_NAME = 120
MAX_CHANNELS = 6
MAX_IMAGE_NOTE = 300
MIN_LEAD_MINUTES = 5
MAX_LEAD_DAYS = 90

# Used when Outreach does not say which social channels are connected; Outreach still refuses one that is not.
DEFAULT_CHANNELS = ("linkedin", "facebook", "instagram", "x")
_CHANNEL_RE = re.compile(r"^[a-z0-9_]{1,20}$")  # "x" is a channel name
_ATTEMPT_RE = re.compile(r"^[A-Za-z0-9]{8,40}$")


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _clean_block(text: Any, limit: int) -> str:
    """Keeps line breaks (a script has paragraphs), drops control characters and trailing blanks."""
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in s.split("\n")).strip()[:limit]


def attempt_token(raw: Any) -> str | None:
    """The browser's one-per-attempt token, or None when it is missing or odd. It makes a double click one request."""
    text = str(raw or "").strip()
    return text if _ATTEMPT_RE.match(text) else None


def attempt_key(username: str, kind: str, attempt: str, tag: str = "") -> str:
    """Idempotency key for one attempt: a repeat of the same attempt is answered with the first result, a new
    attempt (a new token) is a new request. `kind` is "post", "post-decide" or "call"."""
    digest = hashlib.sha256((username or "").encode("utf-8")).hexdigest()[:10]
    base = f"tf-{kind}-{digest}-{attempt}"
    return f"{base}-{tag}" if tag else base


# ── social posts ─────────────────────────────────────────────────────────────────────────────────

def parse_publish_at(raw: Any, now: datetime | None = None) -> tuple[str | None, str | None]:
    """(publish time as UTC "YYYY-MM-DDTHH:MM:SSZ" or None when none was given, problem or None).
    Needs an ISO time with an offset or Z; at least MIN_LEAD_MINUTES ahead and at most MAX_LEAD_DAYS."""
    text = str(raw or "").strip()
    if not text:
        return None, None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, "The publish time is not a valid date and time."
    if when.tzinfo is None:
        return None, "The publish time needs a time zone."
    when = when.astimezone(timezone.utc)
    now = now or datetime.now(timezone.utc)
    if when < now + timedelta(minutes=MIN_LEAD_MINUTES):
        return None, f"Choose a publish time at least {MIN_LEAD_MINUTES} minutes from now, so there is time to read the preview and approve it."
    if when > now + timedelta(days=MAX_LEAD_DAYS):
        return None, f"Choose a publish time within the next {MAX_LEAD_DAYS} days."
    return when.strftime("%Y-%m-%dT%H:%M:%SZ"), None


def post_problems(prompt: Any, channels: Any, allowed: Iterable[str] | None) -> tuple[list[str], list[str], str]:
    """(problems, channels, prompt) for a new post. `allowed` are the channels Outreach reports as connected;
    when it reports none, DEFAULT_CHANNELS are accepted and Outreach has the last word."""
    problems: list[str] = []
    text = _clean_block(prompt, MAX_PROMPT + 1)
    if len(text) < MIN_PROMPT:
        problems.append("Say what the post should be about (at least a sentence).")
    elif len(text) > MAX_PROMPT:
        problems.append(f"The prompt is too long (the limit is {MAX_PROMPT} characters).")
        text = text[:MAX_PROMPT]
    picked: list[str] = []
    if not isinstance(channels, list):
        channels = []
    for c in channels:
        name = str(c or "").strip().lower()
        if _CHANNEL_RE.match(name) and name not in picked:
            picked.append(name)
    if not picked:
        problems.append("Choose at least one channel to post to.")
    elif len(picked) > MAX_CHANNELS:
        problems.append(f"Choose at most {MAX_CHANNELS} channels.")
    else:
        ok = [a for a in (allowed or [])] or list(DEFAULT_CHANNELS)
        bad = [c for c in picked if c not in ok]
        if bad:
            problems.append("Not connected in Outreach: " + ", ".join(bad) + ". Connect it there, or choose another channel.")
    return problems, picked, text


def build_post_payload(prompt: str, channels: list[str], publish_at: str | None, picture: Mapping[str, Any] | None) -> dict[str, Any]:
    """The POST /v1/social/posts body. The picture is the AI's by default; `picture` {"mode": "none"} leaves it out."""
    body: dict[str, Any] = {"prompt": prompt, "channels": channels}
    if publish_at:
        body["publish_at"] = publish_at
    pic = picture or {}
    if pic.get("mode") != "none":
        image: dict[str, Any] = {"mode": "ai"}
        note = _clean(pic.get("instruction"), MAX_IMAGE_NOTE)
        if note:
            image["instruction"] = note
        body["image"] = image
    return body


# A post that can still be approved; once it is anything else, approving cannot work and is refused before the request.
APPROVABLE = {"needs_approval", "pending_approval", "awaiting_approval", "draft", "ready", "scheduled_pending"}
FINAL_POST_STATES = {"published", "rejected", "cancelled", "canceled", "failed", "expired"}


def can_decide(status: str | None, decision: str) -> str | None:
    """Why `decision` cannot be made on a post in `status`; None when it can."""
    s = (status or "").lower()
    if s in FINAL_POST_STATES:
        return f"This post is already {s}. Nothing more can be done with it."
    if decision == "approve" and s == "writing":
        return "The AI is still writing this post. Press Refresh, read the preview, then approve it."
    return None


# ── phone calls ──────────────────────────────────────────────────────────────────────────────────

def normalize_uk_landline(raw: Any) -> tuple[str | None, str | None]:
    """(E.164 number, problem). Only a UK geographic (01, 02) or 03 number is accepted: a mobile (07) usually belongs
    to a person, 08 / 09 are freephone or premium, 05 is internet telephony, and a foreign number is outside the UK rules."""
    text = str(raw or "").strip()
    if not text:
        return None, "Enter the buyer's phone number."
    if not re.fullmatch(r"[+0-9 ()\-.]{6,25}", text):
        return None, "That does not look like a phone number."
    digits = re.sub(r"\D", "", text)
    if text.startswith("+") or digits.startswith("00"):
        if digits.startswith("00"):
            digits = digits[2:]
        if not digits.startswith("44"):
            return None, "Only UK numbers (+44) can be called from here."
        national = digits[2:]
        if national.startswith("0"):  # "+44 (0)20 ..."
            national = national[1:]
    elif digits.startswith("0"):
        national = digits[1:]
    else:
        return None, "Enter the number with its area code, for example 020 7946 0000."
    if not national or national[0] not in "123":
        first = national[:1]
        if first == "7":
            return None, "That is a mobile number. Mobiles usually belong to a person, not an organisation, so they are not called from here."
        return None, "Only landline numbers starting 01, 02 or 03 can be called from here."
    if not 9 <= len(national) <= 10:
        return None, "That number is the wrong length for a UK number."
    return "+44" + national, None


def mask_number(e164: str | None) -> str:
    """"+4420****0000": enough to recognise the number, used where the full number is not needed."""
    n = e164 or ""
    return n[:5] + "*" * (len(n) - 9) + n[-4:] if len(n) > 9 else ""


def call_problems(*, number: Any, buyer: Any, goal: Any, script_id: Any, instructions: Any, questions: Any,
                  business_line: Any, tps_checked: Any, suppressed: bool) -> tuple[list[str], dict[str, Any]]:
    """(problems, clean fields). Everything that can be checked before Outreach is asked, in the user's words."""
    problems: list[str] = []
    e164, bad_number = normalize_uk_landline(number)
    if bad_number:
        problems.append(bad_number)
    name = _clean(buyer, MAX_NAME)
    if not name:
        problems.append("Say who you are calling (the buyer's name).")
    g = _clean(goal, MAX_GOAL + 1)
    script = str(script_id or "").strip()
    if script and not re.fullmatch(r"[A-Za-z0-9_\-]{3,64}", script):
        problems.append("That is not a saved script.")
        script = ""
    if not g and not script:
        problems.append("Say what the call is for, or choose a saved script.")
    if len(g) > MAX_GOAL:
        problems.append(f"The goal is too long (the limit is {MAX_GOAL} characters).")
        g = g[:MAX_GOAL]
    qs: list[str] = []
    for q in (questions if isinstance(questions, list) else [])[: MAX_QUESTIONS + 1]:
        t = _clean(q, MAX_QUESTION)
        if t:
            qs.append(t)
    if len(qs) > MAX_QUESTIONS:
        problems.append(f"Ask at most {MAX_QUESTIONS} questions.")
        qs = qs[:MAX_QUESTIONS]
    if suppressed:
        problems.append("This buyer is on your do-not-contact list.")
    if business_line is not True:
        problems.append("Confirm that this is an organisation's line, not an individual's.")
    if tps_checked is not True:
        problems.append("Confirm that you have checked the number against the TPS / CTPS.")
    return problems, {
        "number": e164, "buyer": name, "goal": g, "script_id": script or None,
        "instructions": _clean_block(instructions, MAX_INSTRUCTIONS), "questions": qs,
    }


def build_call_payload(fields: Mapping[str, Any]) -> dict[str, Any]:
    """The POST /v1/voice/calls body for one number."""
    body: dict[str, Any] = {"to": fields["number"], "lead_name": fields["buyer"], "variables": {"company": fields["buyer"]}}
    if fields.get("goal"):
        body["goal"] = fields["goal"]
    if fields.get("script_id"):
        body["script_id"] = fields["script_id"]
    if fields.get("instructions"):
        body["instructions"] = fields["instructions"]
    if fields.get("questions"):
        body["questions"] = list(fields["questions"])
    return body


CALL_LIVE = {"queued", "ringing", "in_progress", "in-progress", "answered", "dialing", "connecting"}
CALL_DONE = {"completed", "failed", "no_answer", "busy", "voicemail", "cancelled", "canceled", "ended"}


def call_is_live(status: str | None) -> bool:
    return (status or "").lower() in CALL_LIVE
