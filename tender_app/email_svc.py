import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, List, Dict, Optional

from tender_app.config import EMAIL_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER, SMTP_FROM_NAME
from tender_app.db import get_db_connection


def _html_to_plain_fallback(html: str) -> str:
    """Best-effort plain-text rendering of an HTML email body, used when no dedicated
    plain_body was supplied. Every outbound message needs a text/plain part alongside
    the HTML one — an HTML-only message is itself a spam signal most filters score on,
    separate from sender authentication."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|tr|h[1-6]|li)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n[ \t\n]*", "\n\n", text)
    return text.strip()


def _get_direct_db_conn():
    return get_db_connection(connect_timeout=3)


def record_smtp_bounce(recipient_email: str, reason: str) -> None:
    """Flag a recipient email address as bounced/unverified in user_prefs to prevent repeated sends."""
    if not recipient_email:
        return
    clean_email = recipient_email.strip().lower()
    try:
        import json
        conn = _get_direct_db_conn()
        cur = conn.cursor()
        ph = "%s"
        
        cur.execute("SELECT username, pref_value FROM user_prefs WHERE pref_key = 'notification_recipients'")
        rows = cur.fetchall()
        for uname, pval in rows:
            if not pval:
                continue
            try:
                recipients = json.loads(pval)
                modified = False
                for r in recipients:
                    if str(r.get("email", "")).strip().lower() == clean_email:
                        r["verified"] = False
                        r["hard_bounced"] = True
                        r["bounce_reason"] = reason or "550 5.1.1 Recipient address rejected / mailbox unavailable"
                        modified = True
                if modified:
                    new_json = json.dumps(recipients)
                    cur.execute(
                        "UPDATE user_prefs SET pref_value = %s, updated_at = NOW() WHERE username = %s AND pref_key = 'notification_recipients'",
                        (new_json, uname),
                    )
            except Exception:
                continue
        conn.commit()
        cur.close()
        conn.close()
        print(f"[SMTP Bounce Protection] Marked '{clean_email}' as hard-bounced ({reason})", flush=True)
    except Exception as ex:
        print(f"[SMTP Bounce Protection Error] Failed to record bounce for {recipient_email}: {ex}", flush=True)


def send_email(
    to: str,
    subject: str,
    body: str,
    is_html: bool = False,
    plain_body: Optional[str] = None,
    list_unsubscribe_url: Optional[str] = None,
) -> bool:
    email_from = EMAIL_FROM or SMTP_USER
    smtp_pass = SMTP_PASSWORD or os.environ.get("SMTP_PASS", "")
    smtp_host = SMTP_HOST or os.environ.get("SMTP_HOST", "")
    smtp_user = SMTP_USER or os.environ.get("SMTP_USER", "")
    from_name = SMTP_FROM_NAME or "TenderFlow"

    if not smtp_host or not email_from:
        print(f"[Email skipped] SMTP Host or From address missing. To: {to} | Subject: {subject}")
        return False

    from email.header import Header
    if is_html:
        # Every HTML send gets a text/plain alternative — an HTML-only message is itself
        # a spam-filter signal, on top of whatever sender-authentication issues exist.
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(plain_body or _html_to_plain_fallback(body), "plain", "utf-8"))
        msg.attach(MIMEText(body, "html", "utf-8"))
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    if "<" not in email_from and from_name:
        msg["From"] = f"{from_name} <{email_from}>"
    else:
        msg["From"] = email_from
    msg["To"] = to
    if list_unsubscribe_url:
        # Required by Gmail/Yahoo's bulk-sender rules (Feb 2024) for anything recurring/
        # automated like the digest below — its absence alone can tip borderline mail into spam.
        msg["List-Unsubscribe"] = f"<{list_unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    try:
        port = int(SMTP_PORT or 587)
        if port == 465:
            with smtplib.SMTP_SSL(smtp_host, port, timeout=15) as server:
                if smtp_user and smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user or email_from, [to], msg.as_bytes())
        else:
            with smtplib.SMTP(smtp_host, port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                if smtp_user and smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user or email_from, [to], msg.as_bytes())
        try:
            clean_subj = subject.encode("ascii", "replace").decode("ascii")
            print(f"[Email] Sent transactional email to {to}: {clean_subj}")
        except Exception:
            pass
        return True
    except smtplib.SMTPRecipientsRefused as ex:
        err_msg = str(ex)
        try:
            print(f"[Email] Recipient refused (hard bounce) for {to}: {err_msg.encode('ascii', 'replace').decode('ascii')}")
        except Exception:
            pass
        record_smtp_bounce(to, f"SMTPRecipientsRefused: {err_msg}")
        return False
    except smtplib.SMTPResponseException as ex:
        err_msg = f"{ex.smtp_code} {ex.smtp_error.decode('utf-8', errors='ignore') if isinstance(ex.smtp_error, bytes) else str(ex.smtp_error)}"
        try:
            print(f"[Email] SMTP error for {to}: {err_msg.encode('ascii', 'replace').decode('ascii')}")
        except Exception:
            pass
        if ex.smtp_code >= 500:
            record_smtp_bounce(to, err_msg)
        return False
    except Exception as ex:
        err_msg = str(ex)
        try:
            print(f"[Email] Send failed to {to}: {err_msg.encode('ascii', 'replace').decode('ascii')}")
        except Exception:
            pass
        lower_err = err_msg.lower()
        if any(keyword in lower_err for keyword in ["550", "5.1.1", "user unknown", "mailbox unavailable", "invalid recipient", "does not exist", "recipient rejected"]):
            record_smtp_bounce(to, err_msg)
        return False


def send_welcome(email: str, display_name: str) -> None:
    send_email(
        email,
        "Welcome to TenderFlow",
        f"Hi {display_name or 'there'},\n\nWelcome to TenderFlow! "
        f"You have free trial credits to start searching and bidding on tenders.\n\n"
        f"— The TenderFlow Team",
    )


def send_verification_code_email(email: str, code: str) -> bool:
    subject = f"Your TenderFlow 4-digit verification code: {code}"
    plain_body = (
        f"Hi,\n\n"
        f"Your verification code for TenderFlow automated tender alert notifications is:\n\n"
        f"  {code}\n\n"
        f"Enter this 4-digit code in your TenderFlow Profile under 'Email & Notifications' to verify your address.\n"
        f"This code is valid for 10 minutes.\n\n"
        f"If you did not request this verification code, please ignore this email.\n\n"
        f"— The TenderFlow Team"
    )
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background:#f8fafc; padding:30px 20px; color:#1e293b;">
      <div style="max-width:480px; margin:0 auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:12px; padding:28px; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
        <div style="font-size:20px; font-weight:800; color:#2563eb; margin-bottom:16px;">TenderFlow</div>
        <h2 style="margin:0 0 12px; font-size:18px; font-weight:700; color:#0f172a;">Verify your notification email</h2>
        <p style="margin:0 0 20px; font-size:14px; color:#475569; line-height:1.5;">
          Use the 4-digit verification code below to enable automated tender alert digests for this email address:
        </p>
        <div style="text-align:center; margin:24px 0; background:#eff6ff; border:1.5px dashed #3b82f6; border-radius:10px; padding:16px;">
          <span style="font-size:32px; font-weight:800; letter-spacing:8px; color:#1d4ed8; font-family:monospace;">{code}</span>
        </div>
        <p style="margin:0 0 16px; font-size:12.5px; color:#64748b; line-height:1.4;">
          ⏱ This code will expire in <strong>10 minutes</strong>. If you did not request this code, no action is needed.
        </p>
        <hr style="border:none; border-top:1px solid #f1f5f9; margin:20px 0;">
        <p style="margin:0; font-size:11px; color:#94a3b8; text-align:center;">&copy; TenderFlow Procurement Intelligence</p>
      </div>
    </body>
    </html>
    """
    return send_email(email, subject, html_body, is_html=True, plain_body=plain_body)


def send_password_reset_email(email: str, reset_link: str) -> bool:
    subject = "Reset your TenderFlow password"
    plain_body = (
        f"Hi,\n\n"
        f"We received a request to reset your password for your TenderFlow account.\n\n"
        f"Click the link below to set a new password:\n"
        f"{reset_link}\n\n"
        f"If you did not request a password reset, you can safely ignore this email.\n\n"
        f"— The TenderFlow Team"
    )
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background:#f8fafc; padding:30px 20px; color:#1e293b;">
      <div style="max-width:480px; margin:0 auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:12px; padding:28px; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
        <div style="font-size:20px; font-weight:800; color:#2563eb; margin-bottom:16px;">TenderFlow</div>
        <h2 style="margin:0 0 12px; font-size:18px; font-weight:700; color:#0f172a;">Password Reset Request</h2>
        <p style="margin:0 0 20px; font-size:14px; color:#475569; line-height:1.5;">
          We received a request to reset the password for your account associated with <strong>{email}</strong>.
        </p>
        <div style="text-align:center; margin:28px 0;">
          <a href="{reset_link}" style="background:#2563eb; color:#ffffff; padding:12px 28px; border-radius:8px; font-weight:600; text-decoration:none; display:inline-block; font-size:14px; box-shadow:0 2px 6px rgba(37,99,235,0.25);">
            Reset Password
          </a>
        </div>
        <p style="margin:0 0 12px; font-size:12px; color:#64748b; line-height:1.5;">
          If the button above does not work, copy and paste this link into your browser:
        </p>
        <p style="margin:0 0 20px; font-size:11.5px; color:#2563eb; word-break:break-all; line-height:1.4;">
          <a href="{reset_link}" style="color:#2563eb;">{reset_link}</a>
        </p>
        <p style="margin:0 0 16px; font-size:12px; color:#94a3b8; line-height:1.4;">
          If you did not request a password reset, you can safely ignore this email. Your password will remain unchanged.
        </p>
        <hr style="border:none; border-top:1px solid #f1f5f9; margin:20px 0;">
        <p style="margin:0; font-size:11px; color:#94a3b8; text-align:center;">&copy; TenderFlow Procurement Intelligence</p>
      </div>
    </body>
    </html>
    """
    return send_email(email, subject, html_body, is_html=True, plain_body=plain_body)


def send_low_credit(email: str, balance: int) -> None:
    send_email(
        email,
        "Low credit balance — TenderFlow",
        f"Your TenderFlow credit balance is {balance}. "
        f"Top up or upgrade your plan to keep searching and generating bids.\n",
    )


from datetime import datetime, timezone


def parse_relative_deadline(deadline_str: Any) -> tuple[int, str]:
    """Parse submission deadline string and return (days_remaining, badge_label)."""
    if not deadline_str or str(deadline_str).strip() in ("N/A", "Unknown", "None", ""):
        return 999, "DEADLINE N/A"
    
    clean_str = str(deadline_str).strip()
    today = datetime.now(timezone.utc).date()
    deadline_date = None
    
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(clean_str[:10] if "T" not in fmt else clean_str.split(".")[0], fmt)
            deadline_date = dt.date()
            break
        except Exception:
            pass
            
    if not deadline_date:
        return 999, f"CLOSES: {clean_str[:12]}"
        
    days = (deadline_date - today).days
    if days < 0:
        return days, "CLOSED"
    elif days == 0:
        return 0, "CLOSES TODAY"
    elif days == 1:
        return 1, "CLOSES TOMORROW"
    else:
        return days, f"CLOSES IN {days} DAYS"


def format_value_badge(value_val: Any) -> tuple[str, bool]:
    """Format published contract value. Returns (display_text, is_published)."""
    if not value_val or str(value_val).strip().lower() in ("n/a", "none", "unknown", "0", "0.0", ""):
        return "Value not published", False
    
    s = str(value_val).strip()
    if not any(char.isdigit() for char in s):
        return "Value not published", False
        
    return s, True


def generate_capability_reasons(tender: Dict[str, Any], profile_text: str = "", keywords: List[str] = None, company_name: str = "") -> List[str]:
    """Generate 2-3 specific capability-based alignment reasons in plain business language."""
    if tender.get("fit_reasons_list") and isinstance(tender["fit_reasons_list"], list) and len(tender["fit_reasons_list"]) >= 2:
        return tender["fit_reasons_list"][:3]
        
    title = str(tender.get("title") or "").lower()
    desc = str(tender.get("description") or "").lower()
    combined = f"{title} {desc}"
    
    reasons = []

    # 0. High-precision search keyword and company profile match reason
    fit_reason = tender.get("fit_reason")
    if fit_reason and str(fit_reason).strip():
        reasons.append(str(fit_reason).strip())

    # 1. Core scope alignment
    if any(k in combined for k in ["civil", "construction", "infrastructure", "highway", "road", "bridge", "building"]):
        reasons.append("Civil engineering and infrastructure scope aligns strongly with your core service capabilities.")
    elif any(k in combined for k in ["software", "cloud", "platform", "digital", "data", "ai", "system", "it "]):
        reasons.append("Digital, software, and technology scope matches your core solution architecture capabilities.")
    elif any(k in combined for k in ["consulting", "advisory", "management", "strategy", "audit", "training"]):
        reasons.append("Strategic consulting and advisory scope aligns directly with your service portfolio.")
    elif any(k in combined for k in ["facility", "maintenance", "cleaning", "security", "waste", "catering"]):
        reasons.append("Facilities and operational service requirements match your core delivery model.")
    elif keywords:
        matched = [kw.capitalize() for kw in keywords if kw.lower() in combined]
        if matched:
            reasons.append(f"{', '.join(matched[:3])} scope aligns directly with your stated core capabilities.")
        else:
            reasons.append("Project specifications and requirements match your established operational profile.")
    else:
        reasons.append("Procurement requirements and scope align with your company capabilities.")

    # 2. Sector & Authority Match
    authority = str(tender.get("contracting_authority") or tender.get("authority") or "").strip()
    if authority:
        reasons.append(f"Public sector opportunity with {authority} matches your target client profile.")
    else:
        reasons.append("Contract scope and delivery timeline fit your target project execution profile.")

    # 3. Regional / Portal Match
    portal = str(tender.get("portal") or tender.get("source_label") or tender.get("source") or "").strip()
    if portal:
        reasons.append(f"Geographic region and procurement guidelines on {portal} match your operational footprint.")
    else:
        reasons.append("Regional delivery footprint and procurement terms match your active profile.")

    return reasons[:3]


def render_best_fit_digest_html(display_name: str, tenders: List[Dict[str, Any]], company_name: str = "", profile_text: str = "", email: str = "") -> tuple[str, str, str, str]:
    """Render subject, HTML body, plain-text body and the pause/unsubscribe URL for the
    best-fit tender digest (Image 1 template)."""
    if not tenders:
        return ("Tender Alert", "", "", "")

    import urllib.parse
    from tender_app.config import get_app_base_url
    base_url = get_app_base_url()
    encoded_user = urllib.parse.quote(email or display_name)

    # Compute Subject Line & Summary Stats
    max_fit = max((t.get("fit_score") or 0) for t in tenders)
    min_days = 999
    closest_days_label = "N/A"
    
    for t in tenders:
        days, label = parse_relative_deadline(t.get("submission_deadline") or t.get("deadline"))
        if days < min_days:
            min_days = days
            closest_days_label = label.replace("CLOSES IN ", "").replace("CLOSES ", "")

    if len(tenders) == 1:
        t0 = tenders[0]
        _, dl_label = parse_relative_deadline(t0.get("submission_deadline") or t0.get("deadline"))
        if t0.get("material_change_reason"):
            subject = f"⚠️ Updated Tender Opportunity: {str(t0.get('title', ''))[:45]}..."
        else:
            subject = f"🎯 1 new high-fit tender opportunity ({t0.get('fit_score', 85)}% Fit — {dl_label.capitalize()})"
    else:
        subject = f"🎯 {len(tenders)} new high-fit tender opportunities (Top match: {max_fit}% Fit)"

    # Render Tender Cards
    cards_html = ""
    plain_lines = []
    for t in tenders:
        title = t.get("title") or t.get("description") or "Untitled Tender"
        portal = t.get("portal") or t.get("source_label") or t.get("source") or "Public Portal"
        authority = t.get("contracting_authority") or t.get("authority") or "Public Authority"
        fit_score = t.get("fit_score", 85)
        
        encoded_title = urllib.parse.quote(title)
        source_id = t.get("source") or ""
        rid = t.get("resource_id") or t.get("id") or ""
        tender_key = t.get("tender_key") or f"{source_id}:{rid}"
        encoded_key = urllib.parse.quote(tender_key)

        if source_id and rid:
            encoded_source = urllib.parse.quote(str(source_id))
            encoded_rid = urllib.parse.quote(str(rid))
            app_url = f"{base_url}/?source={encoded_source}&id={encoded_rid}&tender_title={encoded_title}"
        else:
            app_url = f"{base_url}/?q={encoded_title}"

        save_url = f"{app_url}&action=save"
        not_relevant_url = f"{base_url}/api/alerts/feedback?action=not_relevant&tender_key={encoded_key}&username={encoded_user}"
        tell_us_why_url = f"{base_url}/api/alerts/feedback?action=tell_us_why&tender_key={encoded_key}&username={encoded_user}"

        # 1. THREE-VALUE TOP LINE PILLS
        fit_pill_html = f'<span style="background:#10b981; color:#ffffff; font-size:11px; font-weight:700; padding:4px 10px; border-radius:12px; display:inline-block; letter-spacing:0.3px;">{fit_score}% FIT</span>'
        
        val_text, is_pub_val = format_value_badge(t.get("estimated_value_eur") or t.get("estimated_value") or t.get("value"))
        if is_pub_val:
            val_pill_html = f'<span style="background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe; font-size:11px; font-weight:600; padding:4px 10px; border-radius:12px; display:inline-block;">{val_text}</span>'
        else:
            val_pill_html = f'<span style="background:#f1f5f9; color:#64748b; font-size:11px; font-weight:600; padding:4px 10px; border-radius:12px; display:inline-block;">Value not published</span>'

        days_left, urgency_label = parse_relative_deadline(t.get("submission_deadline") or t.get("deadline"))
        if days_left <= 0:
            urgency_style = "background:#fef2f2; color:#dc2626; border:1px solid #fca5a5; font-weight:700;"
        elif days_left <= 1:
            urgency_style = "background:#fff7ed; color:#c2410c; border:1px solid #ffedd5; font-weight:700;"
        elif days_left <= 3:
            urgency_style = "background:#fef3c7; color:#b45309; border:1px solid #fde68a; font-weight:700;"
        else:
            urgency_style = "background:#f1f5f9; color:#475569; border:1px solid #e2e8f0; font-weight:600;"
            
        urgency_pill_html = f'<span style="{urgency_style} font-size:11px; padding:4px 10px; border-radius:12px; display:inline-block;">{urgency_label}</span>'

        plain_lines.append(
            f"- {title} ({fit_score}% fit)\n"
            f"  {authority} | {portal} | {val_text if is_pub_val else 'Value not published'} | {urgency_label}\n"
            f"  {app_url}"
        )

        material_change_html = ""
        if t.get("material_change_reason"):
            material_change_html = f'<div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px; padding:6px 12px; margin-bottom:10px; font-size:12px; font-weight:700; color:#1d4ed8;">⚡ Re-alert: {t["material_change_reason"]}</div>'

        # 4. WHY THIS FITS SECTION (Capability-based bullets)
        reasons_list = generate_capability_reasons(t, profile_text=profile_text, keywords=t.get("keywords"), company_name=company_name)
        reasons_bullets = "".join([f'<li style="margin-bottom:4px;">{r}</li>' for r in reasons_list])
        why_fits_html = f"""
        <div style="background:#f8fafc; border-left:3px solid #10b981; padding:10px 14px; border-radius:6px; margin:12px 0;">
            <div style="font-size:11px; font-weight:700; color:#0f172a; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px;">💡 Why This Fits</div>
            <ul style="margin:0; padding-left:18px; font-size:13px; color:#334155; line-height:1.5;">
                {reasons_bullets}
            </ul>
        </div>
        """

        # 5. OPTIONAL CHECK BEFORE BIDDING CALLOUT
        check_bidding_html = ""
        if not is_pub_val or days_left <= 3:
            check_bidding_html = """
            <div style="background:#fffbeb; border-left:3px solid #f59e0b; padding:8px 12px; margin-bottom:12px; border-radius:4px; font-size:12px; color:#92400e;">
                ⚠️ <strong>Check before bidding:</strong> Confirm scope, estimated value and supply-chain requirements.
            </div>
            """

        cards_html += f"""
        <div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:12px; padding:18px; margin-bottom:18px; box-shadow:0 2px 8px rgba(0,0,0,0.04);">
            {material_change_html}

            <!-- 1. THREE-VALUE TOP LINE -->
            <div style="display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin-bottom:10px;">
                {fit_pill_html}
                {val_pill_html}
                {urgency_pill_html}
            </div>

            <!-- 2. TITLE -->
            <h3 style="margin:0 0 6px 0; font-size:16px; font-weight:700; line-height:1.35; color:#0f172a;">
                <a href="{app_url}" target="_blank" style="color:#0f172a; text-decoration:none;">{title}</a>
            </h3>

            <!-- 3. SUBTITLE LINE -->
            <div style="font-size:13px; color:#64748b; margin-bottom:10px; font-weight:500;">
                {authority} &bull; {portal}
            </div>

            <!-- 4. WHY THIS FITS -->
            {why_fits_html}

            <!-- 5. CHECK BEFORE BIDDING -->
            {check_bidding_html}

            <!-- 6. ACTION ROW -->
            <div style="margin-top:14px;">
                <table role="presentation" border="0" cellpadding="0" cellspacing="0" style="width:100%; border-collapse:collapse;">
                    <tr>
                        <td style="width:50%; padding-right:6px;">
                            <a href="{app_url}" target="_blank" style="display:block; text-align:center; background:#4f46e5; color:#ffffff; font-size:13px; font-weight:600; padding:10px 14px; border-radius:8px; text-decoration:none;">
                                View tender &rarr;
                            </a>
                        </td>
                        <td style="width:50%; padding-left:6px;">
                            <a href="{save_url}" target="_blank" style="display:block; text-align:center; background:#ffffff; color:#334155; border:1px solid #cbd5e1; font-size:13px; font-weight:600; padding:10px 14px; border-radius:8px; text-decoration:none;">
                                Save ⭐
                            </a>
                        </td>
                    </tr>
                </table>

                <div style="display:flex; justify-space-between; align-items:center; margin-top:10px; font-size:12px;">
                    <a href="{not_relevant_url}" target="_blank" style="color:#94a3b8; text-decoration:underline;">Not relevant</a>
                    <a href="{tell_us_why_url}" target="_blank" style="color:#4f46e5; text-decoration:none; font-weight:500;">Tell us why &rarr;</a>
                </div>
            </div>
        </div>
        """

    # PART 3: Header Summary Row
    header_summary_html = f"""
    <table role="presentation" border="0" cellpadding="0" cellspacing="0" style="width:100%; margin-bottom:20px; border-collapse:separate; border-spacing:8px 0;">
        <tr>
            <td style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:8px; padding:12px; text-align:center; width:33%;">
                <div style="font-size:10px; font-weight:700; color:#1d4ed8; text-transform:uppercase; letter-spacing:0.5px;">NEW</div>
                <div style="font-size:22px; font-weight:800; color:#1e3a8a; margin-top:2px;">{len(tenders)}</div>
            </td>
            <td style="background:#ecfdf5; border:1px solid #a7f3d0; border-radius:8px; padding:12px; text-align:center; width:33%;">
                <div style="font-size:10px; font-weight:700; color:#047857; text-transform:uppercase; letter-spacing:0.5px;">BEST FIT</div>
                <div style="font-size:22px; font-weight:800; color:#065f46; margin-top:2px;">{max_fit}%</div>
            </td>
            <td style="background:#fff7ed; border:1px solid #fed7aa; border-radius:8px; padding:12px; text-align:center; width:33%;">
                <div style="font-size:10px; font-weight:700; color:#c2410c; text-transform:uppercase; letter-spacing:0.5px;">CLOSEST</div>
                <div style="font-size:15px; font-weight:800; color:#9a3412; margin-top:6px;">{closest_days_label}</div>
            </td>
        </tr>
    </table>
    """

    # PART 4: Footer Controls & Footnote
    instant_url = f"{base_url}/api/alerts/preference?cadence=immediately&user={encoded_user}"
    daily_url = f"{base_url}/api/alerts/preference?cadence=daily&user={encoded_user}"
    weekly_url = f"{base_url}/api/alerts/preference?cadence=weekly&user={encoded_user}"
    pause_url = f"{base_url}/api/alerts/preference?cadence=off&user={encoded_user}"
    settings_url = f"{base_url}/#settings"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background:#f8fafc; margin:0; padding:16px;">
        <div style="max-width:600px; margin:0 auto; background:#ffffff; border-radius:16px; overflow:hidden; border:1px solid #e2e8f0; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
            <div style="background:linear-gradient(135deg, #0f172a 0%, #1e293b 100%); color:#ffffff; padding:24px; text-align:center;">
                <h1 style="margin:0; font-size:20px; font-weight:800; letter-spacing:-0.3px;">🎯 TenderFlow High-Fit Alerts</h1>
                <p style="margin:6px 0 0 0; opacity:0.85; font-size:13px;">Curated for {company_name or display_name or 'your profile'}</p>
            </div>
            
            <div style="padding:20px;">
                {header_summary_html}
                
                {cards_html}

                <!-- PART 4: FOOTER PREFERENCES -->
                <div style="border-top:1px solid #e2e8f0; margin-top:24px; padding-top:16px; text-align:center; font-size:12px; color:#64748b; line-height:1.6;">
                    <div style="margin-bottom:8px;">
                        <a href="{settings_url}" style="color:#4f46e5; text-decoration:none; font-weight:600;">Alert settings</a> &bull; 
                        <a href="{instant_url}" style="color:#4f46e5; text-decoration:none; font-weight:600;">Instant</a> &bull;
                        <a href="{daily_url}" style="color:#4f46e5; text-decoration:none; font-weight:600;">Daily digest</a> &bull;
                        <a href="{weekly_url}" style="color:#4f46e5; text-decoration:none; font-weight:600;">Weekly digest</a> &bull;
                        <a href="{pause_url}" style="color:#64748b; text-decoration:none;">Pause alerts</a>
                    </div>
                    <div style="font-size:11px; color:#94a3b8;">
                        *Illustrative value where published contract value is estimated or state 'not published'.
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    plain_body = (
        f"TenderFlow High-Fit Alerts — curated for {company_name or display_name or 'your profile'}\n\n"
        f"{len(tenders)} new match(es), best fit {max_fit}%, closest deadline {closest_days_label}.\n\n"
        + "\n\n".join(plain_lines)
        + f"\n\n---\n"
        f"Alert settings: {settings_url}\n"
        f"Change frequency — instant: {instant_url} | daily: {daily_url} | weekly: {weekly_url}\n"
        f"Pause alerts: {pause_url}\n"
    )
    return subject, html_body, plain_body, pause_url


def send_best_fit_digest(email: str, display_name: str, tenders: List[Dict[str, Any]], company_name: str = "", profile_text: str = "") -> bool:
    """Send an HTML digest email of best-fit tenders matching the user's company profile."""
    if not tenders:
        print(f"[Email] No best-fit tenders to send to {email}")
        return False
    subject, html_body, plain_body, pause_url = render_best_fit_digest_html(display_name, tenders, company_name=company_name, profile_text=profile_text, email=email)
    return send_email(email, subject, html_body, is_html=True, plain_body=plain_body, list_unsubscribe_url=pause_url)

