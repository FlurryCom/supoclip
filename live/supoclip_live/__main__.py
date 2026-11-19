import asyncio
import logging
import os

from .backend import Backend
from .postiz import Postiz
from .service import LiveService


async def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    backend = Backend(os.getenv("BACKEND_URL", "http://backend:8000"), os.getenv("BACKEND_AUTH_SECRET", ""))
    url, key = os.getenv("POSTIZ_URL", "").strip(), os.getenv("POSTIZ_API_KEY", "").strip()
    postiz = Postiz(url, key) if url and key else None  # posting stays off without both
    try:
        await LiveService(os.environ["LIVE_DATABASE_URL"], backend, postiz).run()
    finally:
        await backend.close()
        if postiz:
            await postiz.close()


asyncio.run(main())
