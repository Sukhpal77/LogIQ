"""Bound request bodies even when Content-Length is absent."""
import asyncio


class BodyLimitMiddleware:
    def __init__(self,app,maximum=1048576):
        self.app,self.maximum=app,maximum

    async def __call__(self,scope,receive,send):
        if scope["type"]!="http" or scope["method"] not in ("POST","PUT","PATCH"):
            return await self.app(scope,receive,send)
        chunks=[];size=0
        while True:
            try:
                message=await asyncio.wait_for(receive(),timeout=10)
            except asyncio.TimeoutError:
                return await self.reject(send,408,b'{"detail":"request body timed out"}')
            if message["type"]=="http.disconnect":
                return
            chunk=message.get("body",b"");size+=len(chunk)
            if size>self.maximum:
                return await self.reject(send,413,b'{"detail":"maximum request size is 1 MiB"}')
            chunks.append(chunk)
            if not message.get("more_body",False):
                break
        consumed=False
        async def buffered():
            nonlocal consumed
            if not consumed:
                consumed=True
                return {"type":"http.request","body":b"".join(chunks),"more_body":False}
            return await receive()
        await self.app(scope,buffered,send)

    @staticmethod
    async def reject(send,status,body):
        await send({"type":"http.response.start","status":status,"headers":[(b"content-type",b"application/json")]})
        await send({"type":"http.response.body","body":body})
