"""Server-operator recovery: revoke sessions and require fresh MFA enrollment."""
import argparse
import asyncio
import getpass
from sqlalchemy import select
from app.db import models as m
from app.db.session import session_scope,engine
from app.auth.jwt import hash_password


async def recover(username,reason):
    password=getpass.getpass("New administrator password: ")
    confirmation=getpass.getpass("Repeat password: ")
    if password!=confirmation or not 12<=len(password.encode())<=72:
        raise ValueError("Passwords must match and contain 12–72 UTF-8 bytes")
    async with session_scope() as s:
        user=(await s.execute(select(m.User).where(m.User.username==username,m.User.role=="admin").with_for_update())).scalar_one()
        user.password_hash=hash_password(password);user.totp_secret=None;user.pending_totp_secret=None;user.recovery_codes=None;user.session_version+=1
        s.add(m.Audit(tenant_id=user.tenant_id,actor="server-operator",action="administrator.recovery",details={"username":username,"reason":reason}))
    await engine.dispose()
    print("Administrator recovered. Previous sessions are revoked; production login requires MFA enrollment.")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username",required=True);parser.add_argument("--reason",required=True)
    args=parser.parse_args();asyncio.run(recover(args.username,args.reason))
