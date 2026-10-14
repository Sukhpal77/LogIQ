"""Tenant-scoped keyset log search and bounded metric aggregation."""
import csv
import io
from datetime import datetime,timedelta,timezone
from fastapi import APIRouter,Depends,Query
from starlette.responses import Response
from sqlalchemy import select,func,desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import models as m
from app.db.session import get_session
from app.auth.rbac import current_user
router=APIRouter(tags=["exploration"])


def log_query(service,level,q,minutes,before_id):
    stmt=select(m.Log).where(m.Log.timestamp>=datetime.utcnow()-timedelta(minutes=minutes))
    if service: stmt=stmt.where(m.Log.service==service)
    if level: stmt=stmt.where(m.Log.level==level)
    if q: stmt=stmt.where(m.Log.message.ilike("%"+q.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%",escape="\\"))
    if before_id: stmt=stmt.where(m.Log.id<before_id)
    return stmt.order_by(desc(m.Log.id))


@router.get("/workspace/logs")
async def logs(service:str|None=Query(None,max_length=128),level:str|None=Query(None,max_length=16),q:str=Query("",max_length=200),minutes:int=Query(1440,ge=1,le=43200),before_id:int|None=Query(None,gt=0),limit:int=Query(100,ge=1,le=500),session:AsyncSession=Depends(get_session, scope="function"),user=Depends(current_user)):
    rows=(await session.execute(log_query(service,level,q,minutes,before_id).limit(limit+1))).scalars().all()
    return {"items":[{"id":row.id,"service":row.service,"level":row.level,"message":row.message,"attrs":row.attrs,"timestamp":row.timestamp.replace(tzinfo=timezone.utc).isoformat()} for row in rows[:limit]],"next_cursor":rows[limit-1].id if len(rows)>limit else None}


@router.get("/workspace/logs/export")
async def export(service:str|None=Query(None,max_length=128),level:str|None=Query(None,max_length=16),q:str=Query("",max_length=200),minutes:int=Query(1440,ge=1,le=43200),session:AsyncSession=Depends(get_session, scope="function"),user=Depends(current_user)):
    rows=(await session.execute(log_query(service,level,q,minutes,None).limit(10000))).scalars().all()
    output=io.StringIO();writer=csv.writer(output);writer.writerow(["timestamp_utc","service","level","message"])
    def safe(value):
        value=str(value)
        return "'"+value if value.startswith(("=","+","-","@","\t","\r")) else value
    for row in rows:
        writer.writerow([row.timestamp.isoformat()+"Z",safe(row.service),safe(row.level),safe(row.message)])
    return Response(output.getvalue(),media_type="text/csv",headers={"Content-Disposition":'attachment; filename="logiq-events.csv"',"X-Export-Limit":"10000"})


@router.get("/workspace/metrics")
async def metrics(service:str=Query(min_length=1,max_length=128),minutes:int=Query(60,ge=1,le=43200),session:AsyncSession=Depends(get_session, scope="function"),user=Depends(current_user)):
    bucket=max(60,(minutes*60+299)//300)
    slot=func.to_timestamp(func.floor(func.extract("epoch",m.Metric.timestamp)/bucket)*bucket)
    rows=(await session.execute(select(slot.label("timestamp"),func.avg(m.Metric.cpu),func.avg(m.Metric.memory),func.avg(m.Metric.latency),func.max(m.Metric.latency)).where(m.Metric.service==service,m.Metric.timestamp>=datetime.utcnow()-timedelta(minutes=minutes)).group_by(slot).order_by(slot))).all()
    return {"bucket_seconds":bucket,"items":[{"timestamp":time.isoformat(),"cpu":round(cpu,2),"memory":round(memory,2),"latency":round(latency,2),"max_latency":round(maximum,2)} for time,cpu,memory,latency,maximum in rows]}
