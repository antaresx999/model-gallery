"""E-Mail-Benachrichtigungen über SMTP (optional, per Umgebungsvariablen konfiguriert)."""
import logging
import os
import smtplib
import ssl
import threading
from email.message import EmailMessage

log = logging.getLogger("gallery")

SMTP_HOST = os.environ.get("GALLERY_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("GALLERY_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("GALLERY_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("GALLERY_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("GALLERY_SMTP_FROM", SMTP_USER)
SMTP_TLS = os.environ.get("GALLERY_SMTP_TLS", "starttls")  # starttls | ssl | none


def configured():
    return bool(SMTP_HOST and SMTP_FROM)


def send(to, subject, body):
    if not configured():
        raise RuntimeError("SMTP ist nicht konfiguriert (GALLERY_SMTP_HOST / GALLERY_SMTP_FROM).")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = SMTP_FROM, to, subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if SMTP_TLS == "ssl":
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=20)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
        if SMTP_TLS == "starttls":
            server.starttls(context=ctx)
    with server:
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


def send_async(to, subject, body):
    if not configured():
        return

    def run():
        try:
            send(to, subject, body)
        except Exception as exc:
            log.warning("E-Mail an %s fehlgeschlagen: %s", to, exc)

    threading.Thread(target=run, daemon=True).start()
