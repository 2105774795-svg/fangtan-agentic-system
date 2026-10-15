import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import anyio
from starlette.requests import ClientDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.downloads import TemporaryDownloadResponse


class TemporaryDownloadTests(unittest.TestCase):
    def test_client_disconnect_still_cleans_private_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "private.zip"
            path.write_bytes(b"test fixture")
            cleanup = Mock(side_effect=lambda: path.unlink(missing_ok=True))

            async def run():
                async def send(message):
                    if message["type"] == "http.response.body":
                        raise OSError("client disconnected")

                async def receive():
                    return {"type": "http.disconnect"}

                response = TemporaryDownloadResponse(path, cleanup=cleanup, media_type="application/zip")
                with self.assertRaises((OSError, ClientDisconnect)):
                    await response({"type": "http", "method": "GET", "headers": [],
                                    "asgi": {"spec_version": "2.4"}}, receive, send)

            anyio.run(run)
            cleanup.assert_called_once()
            self.assertFalse(path.exists())
