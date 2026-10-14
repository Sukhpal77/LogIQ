"""Explicit, idempotent fictional data for a local showcase. Never auto-seeded."""
import asyncio
import math
import uuid
from datetime import datetime, timedelta
from sqlalchemy import select, text
from app.config import settings
from app.auth.jwt import hash_password
from app.db import models as m
from app.db.session import session_scope, engine
from app.tenancy import tenant_scope
from app.api.routes_workspace import seed_catalog


async def seed():
    if settings.app_env == "production":
        raise RuntimeError("Showcase seeding is blocked in production")
    await seed_catalog()
    now = datetime.utcnow()
    with tenant_scope(None):
        async with session_scope() as s:
            await s.execute(text("SELECT pg_advisory_xact_lock(7391046)"))
            starter=(await s.execute(select(m.Plan).where(m.Plan.name=="Starter").order_by(m.Plan.version).limit(1))).scalar_one()
            if not (await s.execute(select(m.Plan).where(m.Plan.name=="Starter",m.Plan.version==0))).first():
                s.add(m.Plan(name="Starter",version=0,price=129900,limits=dict(starter.limits),published=False))
                await s.flush()
            for name,email,plan_name,days,state in (
                ("AsterCart Showcase","aster.owner@example.test","Business",30,"active"),
                ("Northstar Studio","northstar.owner@example.test","Team",30,"active"),
                ("Harbor Tools","harbor.owner@example.test","Starter",30,"active"),
                ("Trial Workspace","trial.owner@example.test","Trial",14,"trial"),
                ("Expired Trial","expired.owner@example.test","Trial",-1,"expired"),
                ("Near Limit","near.owner@example.test","Starter",30,"active"),
                ("Over Limit","over.owner@example.test","Starter",30,"active"),
                ("Payment Past Due","pastdue.owner@example.test","Team",30,"past_due"),
                ("Canceled Workspace","canceled.owner@example.test","Starter",-1,"canceled")):
                existing=(await s.execute(select(m.User).where(m.User.username==email))).scalar_one_or_none()
                if existing:
                    continue
                org=m.Organization(name=name,demo=True,status="active")
                s.add(org); await s.flush()
                plan=(await s.execute(select(m.Plan).where(m.Plan.name==plan_name).order_by(m.Plan.version.desc()).limit(1))).scalar_one()
                if name=="Harbor Tools":
                    plan=(await s.execute(select(m.Plan).where(m.Plan.name=="Starter",m.Plan.version==0))).scalar_one()
                s.add(m.User(username=email,email=email,email_verified=True,password_hash=hash_password("LogIQ-showcase-2026!"),role="devops",tenant_id=org.id,owner=True))
                with tenant_scope(org.id):
                    s.add(m.Subscription(tenant_id=org.id,plan_id=plan.id,status=state,period_end=now+timedelta(days=days)))
                    application=m.Application(tenant_id=org.id,name="astercart" if plan_name=="Business" else "store",environment="production")
                    s.add(application);await s.flush()
                    for component,kind in (("astercart-web","app"),("astercart-api","app"),("astercart-db","db")):
                        s.add(m.Service(tenant_id=org.id,name=f"{application.name}/production/{component}",kind=kind,metadata_json={"application_id":application.id,"demo":True}))
                    await s.flush()
                    services=(await s.execute(select(m.Service))).scalars().all()
                    lookup={service.name.rsplit("/",1)[-1]:service for service in services}
                    s.add(m.Dependency(tenant_id=org.id,source_id=lookup["astercart-web"].id,target_id=lookup["astercart-api"].id,relation="calls"))
                    s.add(m.Dependency(tenant_id=org.id,source_id=lookup["astercart-api"].id,target_id=lookup["astercart-db"].id,relation="reads"))
                    service=f"{application.name}/production/astercart-api"
                    for i in range(60):
                        timestamp=now-timedelta(minutes=60-i)
                        s.add(m.Metric(tenant_id=org.id,event_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f"seed:{org.id}:metric:{i}")),service=service,cpu=34+12*math.sin(i/5),memory=48+i/8,latency=90+35*math.sin(i/7),timestamp=timestamp))
                    for i,level in enumerate(("info","debug","info","warn","error","info","critical","info")):
                        s.add(m.Log(tenant_id=org.id,event_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f"seed:{org.id}:log:{i}")),service=service,level=level,message=("Sandbox payment gateway timeout" if level in ("error","critical") else "AsterCart showcase request completed"),attrs={"demo":True,"trace_id":f"showcase-{org.id}-{i}"},timestamp=now-timedelta(minutes=12-i)))
                    s.add(m.Incident(tenant_id=org.id,service=service,summary="Showcase: checkout latency increased",reason="Fictional checkout events show a slow payment dependency.",root_cause="Demo payment gateway timeout",suggestion="Inspect the payment dependency and request timeout configuration.",severity="high",status="open",created_at=now-timedelta(minutes=5)))
                    ingestion = int(plan.limits["ingestion_gb"]*1e9*(0.85 if name=="Near Limit" else 1.01)) if name in ("Near Limit","Over Limit") else 450000000
                    for kind,amount in (("ingestion_bytes",ingestion),("ai_credits",plan.limits["ai_credits"] if name=="Over Limit" else 8),("input_tokens",18000),("output_tokens",4000)):
                        s.add(m.Usage(tenant_id=org.id,kind=kind,amount=amount,reference=f"showcase:{org.id}:{kind}"))
                    s.add(m.WorkspaceSetting(tenant_id=org.id,kind="preferences",name="Showcase display",config={"timezone":"Asia/Calcutta","demo":True}))
                    s.add(m.WorkspaceSetting(kind="agent",name="Simulated offline agent",config={"agent_id":f"showcase-{org.id}","version":"showcase","application_id":application.id,"last_seen":(now-timedelta(hours=1)).isoformat()+"Z","pending":12,"rejected":0,"demo":True}))
                    s.add(m.Invoice(provider_id=f"showcase-invoice-{org.id}",number=f"DEMO-{org.id}",amount=plan.price,status="sandbox",url=None))
                    s.add(m.NotificationOutbox(incident_id=None,text="Simulated showcase notification; no external message was sent.",sent_at=now))
                    await s.flush()
            print("Showcase customers seeded. Password: LogIQ-showcase-2026! (local demo only)")
    await engine.dispose()


if __name__=="__main__":
    asyncio.run(seed())
