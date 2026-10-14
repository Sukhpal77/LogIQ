"""Encrypted platform secrets and authenticator verification."""
import base64
import hashlib
import hmac
import struct
import time
from cryptography.fernet import Fernet
from app.config import settings


def cipher():
    master=settings.secrets_encryption_key or settings.jwt_secret
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(master.encode()).digest()))


def encrypt(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt(value):
    return cipher().decrypt(value.encode()).decode()


def totp(secret,step):
    key=base64.b32decode(secret.upper())
    digest=hmac.new(key,struct.pack(">Q",step),hashlib.sha1).digest()
    offset=digest[-1]&15
    number=(struct.unpack(">I",digest[offset:offset+4])[0]&0x7fffffff)%1000000
    return str(number).zfill(6)


def verify_totp(secret,code,last_step=-1,now=None):
    current=int((time.time() if now is None else now)//30)
    for step in (current-1,current,current+1):
        if step>=0 and step>last_step and hmac.compare_digest(totp(secret,step),code):
            return step
    return None
