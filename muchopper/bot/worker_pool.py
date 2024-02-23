import asyncio
import logging

from datetime import timedelta


class WorkerPool:
    def __init__(self, nworkers, processor, *,
                 max_queue_size=0,
                 delay=None,
                 timeout=timedelta(seconds=15.0),
                 logger=None):
        if nworkers <= 0:
            raise ValueError("need at least one worker")
        self._logger = logger or logging.getLogger(
            ".".join([__name__, type(self).__qualname__, str(id(self))])
        )
        super().__init__()
        self._processor = processor
        self._stop_event = asyncio.Event()
        self._delay = delay
        self._queue = asyncio.Queue(max_queue_size)
        self._timeout = timeout
        self._workers = [
            asyncio.ensure_future(self._worker(i))
            for i in range(nworkers)
        ]
        for worker in self._workers:
            worker.add_done_callback(self._worker_done)

    def enqueue_nowait(self, arg):
        self._queue.put_nowait(arg)

    async def enqueue(self, arg):
        await self._queue.put(arg)

    def _worker_done(self, task):
        try:
            task.result()
        except:  # NOQA
            self._logger.error("worker task terminated with error",
                               exc_info=True)

    async def _worker(self, i):
        logger = self._logger.getChild("worker{}".format(i))
        logger.debug("started up")
        stop_flag = asyncio.ensure_future(self._stop_event.wait())
        while not self._stop_event.is_set():
            item_future = asyncio.ensure_future(self._queue.get())
            done, pending = await asyncio.wait(
                [stop_flag, item_future],
                return_when=asyncio.FIRST_COMPLETED
            )

            if item_future in done:
                item = item_future.result()
                try:
                    await asyncio.wait_for(
                        self._processor(item),
                        timeout=self._timeout.total_seconds(),
                    )
                except asyncio.TimeoutError as exc:
                    logger.error(
                        "item processor %s timed out",
                        item,
                    )
                except Exception:  # NOQA
                    logger.error(
                        "item processor failed",
                        exc_info=True
                    )
                if self._delay:
                    await asyncio.sleep(self._delay)

            if stop_flag in done:
                if not item_future.done():
                    item_future.cancel()
                break

        if not stop_flag.done():
            stop_flag.cancel()

    def close(self, force=False):
        self._stop_event.set()
        if force:
            for worker in self._workers:
                if not worker.done():
                    worker.cancel()

    async def wait_closed(self):
        await asyncio.wait(self._workers,
                           return_when=asyncio.ALL_COMPLETED)
