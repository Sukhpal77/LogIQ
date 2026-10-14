"""Hosted Stripe checkout, safe plan changes and verified canonical state."""
import hashlib
import hmac
import json
import time
from datetime import datetime,timedelta,timezone
from urllib.parse import quote
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from app.config import settings
from app.db import models as m
from app.tenancy import identity,tenant_scope


async def provider(method,path,data=None,idempotency=None):
    if not settings.stripe_secret_key:
        raise HTTPException(503,"Stripe is not configured")
    headers={"Stripe-Version":"2024-06-20"}
    if idempotency:
        headers["Idempotency-Key"]=idempotency
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            result=await client.request(method,"https://api.stripe.com/v1"+path,data=data,headers=headers,auth=(settings.stripe_secret_key,""))
        if result.status_code>=400:
            raise HTTPException(402 if result.status_code==402 else 502,"Payment provider rejected the request. Check the payment status or provider configuration.")
        return result.json()
    except httpx.HTTPError as error:
        raise HTTPException(503,"Payment provider unavailable; subscription was not changed locally") from error


def verify_event(raw,signature,secret,now=None):
    fields={}
    for item in signature.split(","):
        key,_,value=item.partition("=");fields.setdefault(key,[]).append(value)
    try:
        timestamp=int(fields["t"][0])
        if not secret or abs((time.time() if now is None else now)-timestamp)>300:
            raise ValueError("signature expired")
        expected=hmac.new(secret.encode(),str(timestamp).encode()+b"."+raw,hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected,value) for value in fields.get("v1",[])):
            raise ValueError("signature invalid")
        return json.loads(raw)
    except (ValueError,KeyError,TypeError) as error:
        raise HTTPException(400,"invalid webhook signature or payload") from error


async def checkout(session,user,plan,sub,confirm_change=False):
    price_id=plan.limits.get("stripe_price_id","")
    if not price_id:
        raise HTTPException(503,"This plan needs a matching Stripe price before live purchases can be enabled")
    price=await provider("GET","/prices/"+quote(price_id,safe=""))
    recurring=price.get("recurring") or {}
    if price.get("currency")!="inr" or price.get("unit_amount")!=plan.price or recurring.get("interval")!="month" or recurring.get("interval_count")!=1:
        raise HTTPException(409,"Provider price does not match this monthly INR plan")
    if sub.provider_id and sub.status not in ("canceled","expired"):
        if sub.plan_id==plan.id:
            return {"status":"You already have this plan."}
        if not confirm_change:
            raise HTTPException(409,"Confirm the paid plan change before continuing")
        remote=await provider("GET","/subscriptions/"+quote(sub.provider_id,safe=""))
        items=remote.get("items",{}).get("data",[])
        if len(items)!=1:
            raise HTTPException(409,"This subscription requires manual billing support")
        # error_if_incomplete leaves the provider subscription unchanged when payment fails.
        await provider("POST","/subscriptions/"+quote(sub.provider_id,safe=""),{"items[0][id]":items[0]["id"],"items[0][price]":price_id,"proration_behavior":"always_invoice","payment_behavior":"error_if_incomplete","billing_cycle_anchor":"now"},f"plan-change:{identity()}:{plan.id}:{sub.period_end.isoformat()}")
        return {"status":"Payment provider accepted the change. LogIQ updates access after verified provider synchronization."}
    import uuid
    request_id=uuid.uuid4().hex
    fields={"mode":"subscription","line_items[0][price]":price_id,"line_items[0][quantity]":"1","success_url":settings.public_url.rstrip("/")+"/billing?checkout=complete","cancel_url":settings.public_url.rstrip("/")+"/billing?checkout=canceled","client_reference_id":str(identity()),"metadata[organization_id]":str(identity()),"metadata[plan_id]":str(plan.id),"subscription_data[metadata][organization_id]":str(identity())}
    if "@" in user.username:
        fields["customer_email"]=user.username
    result=await provider("POST","/checkout/sessions",fields,"checkout:"+request_id)
    session.add(m.WorkspaceSetting(kind="checkout",name=result["id"],config={"plan_id":plan.id,"request_id":request_id},enabled=False))
    return {"checkout_url":result["url"],"status":"Redirecting to the payment provider."}


async def synchronize(session,remote):
    from app.api.routes_workspace import audit
    from app.auth.rbac import Principal
    try:
        tenant=int(remote["metadata"]["organization_id"])
        item=remote["items"]["data"][0]
        price_id=item["price"]["id"]
    except (KeyError,TypeError,ValueError,IndexError):
        return  # Other products in the same provider account are not LogIQ subscriptions.
    org=(await session.execute(select(m.Organization).where(m.Organization.id==tenant).with_for_update())).scalar_one_or_none()
    if org is None:
        raise HTTPException(400,"provider workspace is unknown")
    with tenant_scope(tenant):
        plan=None
        for candidate in (await session.execute(select(m.Plan))).scalars():
            if candidate.limits.get("stripe_price_id")==price_id:
                plan=candidate;break
        if plan is None:
            raise HTTPException(409,"provider plan is not configured in LogIQ")
        sub=(await session.execute(select(m.Subscription))).scalar_one()
        if sub.provider_id and sub.provider_id!=remote["id"] and sub.status not in ("canceled","expired","trial"):
            raise HTTPException(409,"workspace already has another active subscription")
        status=remote["status"]
        start=remote.get("current_period_start") or item.get("current_period_start")
        end=remote.get("current_period_end") or item.get("current_period_end")
        if start is None or end is None:
            raise HTTPException(502,"provider billing period missing")
        sub.provider_id=remote["id"]
        sub.status=status
        sub.period_start=datetime.fromtimestamp(start,timezone.utc).replace(tzinfo=None)
        sub.period_end=datetime.fromtimestamp(end,timezone.utc).replace(tzinfo=None)
        sub.cancel_at_end=bool(remote.get("cancel_at_period_end"))
        if status=="active":
            # Confirm the latest invoice before granting a newly purchased paid plan.
            invoice_id=remote.get("latest_invoice")
            invoice=await provider("GET","/invoices/"+quote(invoice_id,safe="")) if isinstance(invoice_id,str) else invoice_id
            if invoice and invoice.get("status")=="paid":
                sub.plan_id=plan.id;sub.grace_until=None
            elif sub.plan_id != plan.id or not sub.provider_id:
                sub.status="incomplete"
        elif status=="past_due" and sub.grace_until is None:
            sub.grace_until=datetime.utcnow()+timedelta(days=3)
        audit(session,Principal("payment-provider","system",tenant),"billing.synchronized",{"provider_id":remote["id"],"status":status,"plan_id":sub.plan_id})
        await session.flush()


async def process_event(session,event):
    event_id=event.get("id","")
    if not isinstance(event_id,str) or not event_id.startswith("evt_"):
        raise HTTPException(400,"invalid event identifier")
    await session.execute(select(m.Organization).where(m.Organization.id==1).with_for_update())
    if await session.get(m.PaymentEvent,event_id):
        return {"status":"duplicate"}
    kind=event.get("type","");obj=event.get("data",{}).get("object",{})
    subscription_id=obj.get("subscription")
    if kind.startswith("customer.subscription."):
        subscription_id=obj.get("id")
    if subscription_id and (kind.startswith("invoice.") or kind.startswith("customer.subscription.") or kind.startswith("checkout.session.")):
        remote=await provider("GET","/subscriptions/"+quote(subscription_id,safe=""))
        await synchronize(session,remote)
        if kind.startswith("invoice."):
            obj = await provider("GET", "/invoices/" + quote(obj["id"], safe=""))
            try:
                tenant=int(remote["metadata"]["organization_id"])
            except (KeyError,ValueError):
                tenant=None
            if tenant:
                with tenant_scope(tenant):
                    row=(await session.execute(select(m.Invoice).where(m.Invoice.provider_id==obj["id"]))).scalar_one_or_none()
                    if row is None:
                        row=m.Invoice(provider_id=obj["id"],number=obj.get("number") or obj["id"],amount=obj.get("amount_paid",0),status=obj.get("status","unknown"),url=obj.get("hosted_invoice_url"))
                        session.add(row)
                    else:
                        row.status=obj.get("status",row.status);row.amount=obj.get("amount_paid",row.amount)
                    await session.flush()
    session.add(m.PaymentEvent(id=event_id,kind=kind))
    return {"status":"processed"}
