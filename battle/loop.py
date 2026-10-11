"""The ASGI server's event loop, captured so background threads (battle runners) can hand
channel-layer sends to it thread-safely. The in-memory channel layer's queues belong to that
loop; pushing to them from another loop would not wake the waiting consumers."""
import asyncio

main_loop: asyncio.AbstractEventLoop | None = None


class CaptureLoopMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        global main_loop
        main_loop = asyncio.get_running_loop()
        return await self.inner(scope, receive, send)
