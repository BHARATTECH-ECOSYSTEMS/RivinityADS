"""Dedicated Optimization Worker (Phase 5).

Runs the campaign optimization scheduler as an independent dedicated background worker process,
ensuring clean separation from API serving workers.
"""

import asyncio
import logging
import signal

from liteads.ad_server.services.optimization_scheduler import optimization_loop
from liteads.common.cache import redis_client
from liteads.common.config import get_settings
from liteads.common.database import close_db, init_db

logger = logging.getLogger(__name__)


async def main() -> None:
    """CLI entry point for running a standalone dedicated optimization worker."""
    settings = get_settings()
    logger.info("Initializing database and Redis for standalone optimization worker")
    await init_db()
    await redis_client.connect()

    stop_event = asyncio.Event()

    def _signal_handler(*_):
        logger.info("Termination signal received in optimization worker")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Signal handlers not implemented on Windows for some signals
            pass

    optimization_loop.start()
    logger.info(
        "Dedicated optimization worker loop started. Note: Ensure LITEADS_ENABLE_OPTIMIZATION_WORKER=false "
        "is set on API servers to prevent dual active schedulers."
    )

    try:
        while not stop_event.is_set():
            await asyncio.sleep(1.0)
    finally:
        logger.info("Shutting down dedicated optimization worker")
        await optimization_loop.stop()
        await redis_client.close()
        await close_db()
        logger.info("Dedicated optimization worker stopped cleanly")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
