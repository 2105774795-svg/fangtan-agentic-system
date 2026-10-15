"""临时附件在正常下载、客户端断开和请求取消后均清理。"""
import anyio
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool


class TemporaryDownloadResponse(FileResponse):
    def __init__(self, path, *, cleanup, **kwargs):
        super().__init__(path, **kwargs)
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # 下载连接断开导致外层取消时，仍完成本次独占 ZIP 的删除。
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(self.cleanup)
