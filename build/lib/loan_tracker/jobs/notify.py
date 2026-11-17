"""Senders for the daily digest: Telegram (primary) and email (backup).

Standard library only. Each sender returns None on success or an error text.
"""
import json
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

from loan_tracker.config import Settings


def send_telegram(settings: Settings, text: str) -> str | None:
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return "telegram not configured"
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    body = json.dumps({"chat_id": settings.telegram_chat_id, "text": text[:4000],
                       "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            ok = json.loads(resp.read()).get("ok")
            return None if ok else "telegram returned ok=false"
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return f"telegram failed: {exc}"


def send_email(settings: Settings, subject: str, text: str) -> str | None:
    if not (settings.smtp_host and settings.email_to):
        return "email not configured"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.email_from or settings.smtp_user
    msg["To"] = settings.email_to
    msg.set_content(text)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return None
    except (smtplib.SMTPException, OSError) as exc:
        return f"email failed: {exc}"


def default_senders(settings: Settings) -> dict:
    """channel -> callable(subject, text) -> error | None, for configured channels only."""
    out = {}
    if settings.telegram_bot_token and settings.telegram_chat_id:
        out["TELEGRAM"] = lambda subject, text: send_telegram(settings, text)
    if settings.smtp_host and settings.email_to:
        out["EMAIL"] = lambda subject, text: send_email(settings, subject, text)
    return out
