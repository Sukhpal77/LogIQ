from fastapi import APIRouter,Depends,Request,HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import models as m
from app.db.session import get_session
from app.auth.rbac import current_user
from app.api.routes_workspace import serialize
from app.config import settings
from app.billing import verify_event,process_event
router=APIRouter(tags=["billing"])


@router.post("/billing/webhook")
async def webhook(request:Request,session:AsyncSession=Depends(get_session, scope="function")):
    if settings.billing_provider!="stripe" or not settings.stripe_webhook_secret:
        raise HTTPException(503,"payment webhooks are not configured")
    event=verify_event(await request.body(),request.headers.get("stripe-signature",""),settings.stripe_webhook_secret)
    return await process_event(session,event)


@router.get("/workspace/invoices")
async def invoices(session:AsyncSession=Depends(get_session, scope="function"),user=Depends(current_user)):
    return [serialize(row) for row in (await session.execute(select(m.Invoice).order_by(m.Invoice.created_at.desc()).limit(100))).scalars()]
