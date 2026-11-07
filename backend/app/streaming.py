import asyncio
import contextlib


class MessageStream:
    """首段立即落盘，后续按时间或大小合并；退出时保存剩余内容。"""

    def __init__(self, store, conversation_id, message_id):
        self.store = store
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.parts = []
        self.size = 0
        self.first = True
        self.lock = asyncio.Lock()

    async def __aenter__(self):
        self.timer = asyncio.create_task(self._tick())
        return self

    async def __aexit__(self, *args):
        self.timer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.timer
        await self.flush()

    async def write(self, chunk):
        self.parts.append(chunk)
        self.size += len(chunk)
        if self.first or self.size >= 1024:
            self.first = False
            await self.flush()

    async def flush(self):
        async with self.lock:
            if not self.parts:
                return
            delta = "".join(self.parts)
            self.parts.clear()
            self.size = 0
            await self.store.append_delta(self.conversation_id, self.message_id, delta)

    async def _tick(self):
        while True:
            await asyncio.sleep(0.15)
            await self.flush()
