import asyncio
import logging
import os

from .backend import Backend
from .service import LiveService


async def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    backend = Backend(os.getenv("BACKEND_URL", "http://backend:8000"), os.getenv("BACKEND_AUTH_SECRET", ""))
    try:
        await LiveService(os.environ["LIVE_DATABASE_URL"], backend).run()
    finally:
        await backend.close()


asyncio.run(main())
