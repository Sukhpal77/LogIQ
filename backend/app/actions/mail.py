import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime,timedelta
from sqlalchemy import select
from app.db import models as m
from app.db.session import session_scope
from app.config import settings
from app.auth.security import decrypt


def connect():
    if settings.smtp_port==465:
        client=smtplib.SMTP_SSL(settings.smtp_host,settings.smtp_port,timeout=15,context=ssl.create_default_context())
    else:
        client=smtplib.SMTP(settings.smtp_host,settings.smtp_port,timeout=15)
        try:
            client.starttls(context=ssl.create_default_context())
        except Exception:
            client.close()
            raise
    try:
        if settings.smtp_user:
            client.login(settings.smtp_user,settings.smtp_password)
    except Exception:
        client.close()
        raise
    return client


def check_connection():
    with connect() as client:
        code, _ = client.noop()
        if code != 250:
            raise smtplib.SMTPException("SMTP connection check failed")


def send(row):
    message=EmailMessage()
    message["From"]=settings.smtp_sender;message["To"]=row.recipient;message["Subject"]=row.subject
    message["Message-ID"]=f"<logiq-mail-{row.id}@{settings.smtp_sender.split('@')[-1]}>"
    message.set_content(decrypt(row.encrypted_body))
    with connect() as client:
        client.send_message(message)


async def drain():
    if not settings.smtp_host or not settings.smtp_sender:
        return
    for _ in range(20):
        async with session_scope() as s:
            row=(await s.execute(select(m.MailOutbox).where(m.MailOutbox.sent_at.is_(None),m.MailOutbox.attempts<10,m.MailOutbox.next_attempt<=datetime.utcnow()).order_by(m.MailOutbox.id).limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
            if row is None:
                return
            row.attempts+=1
            try:
                await asyncio.to_thread(send,row)
                row.sent_at=datetime.utcnow()
            except Exception:
                row.next_attempt=datetime.utcnow()+timedelta(seconds=min(3600,2**row.attempts*10))
