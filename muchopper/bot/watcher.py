"""
Watcher
#######

Watcher is responsible for periodically updating the metrics of inactive rooms.
Inactive rooms are rooms where Inside Man is not joined. The update period can
be configured in a range, and will be scaled according to the number of known
inactive rooms.
"""
import asyncio
import random
import sys
import time

from datetime import timedelta

import aioxmpp
import aioxmpp.service

from aioxmpp.utils import namespaces

from . import utils, state, worker_pool


class Watcher(aioxmpp.service.Service,
              utils.PeriodicBackgroundTask):
    WORKER_POOL_SIZE = 8
    MIN_INTERVAL = timedelta(hours=1)
    MIN_PROCESS_INTERVAL = timedelta(seconds=0.1)

    ORDER_AFTER = [
        aioxmpp.DiscoClient,
    ]

    def __init__(self, client, **kwargs):
        super().__init__(client, **kwargs)
        self._disco_svc = self.dependencies[aioxmpp.DiscoClient]

    async def _get_items(self, state):
        items = state.get_all_known_inactive_mucs()
        random.shuffle(items)
        return items

    async def _process_item(self, state, item, fut):
        try:
            info = await utils.collect_muc_metadata(self._disco_svc,
                                                    item,
                                                    require_fresh=True)
        except aioxmpp.errors.XMPPCancelError as e:
            if e.condition == (namespaces.stanzas, "item-not-found"):
                # delete muc
                self.logger.info(
                    "MUC does not exist anymore, "
                    "erasing from database"
                )
                if fut is not None and not fut.done():
                    fut.set_exception(e)
                state.delete_all_muc_data(item)
                return
            raise

        if fut is not None and not fut.done():
            fut.set_result(info)
        self.logger.debug("jid %s: updating metadata: %r",
                          item, info)
        state.update_muc_metadata(item, **info)
        return info

    async def queue_request(self, address: aioxmpp.JID):
        state = await self._state_future
        await self._worker_pool.enqueue((state, address, None, None))

    async def request(self, address: aioxmpp.JID):
        state = await self._state_future
        fut = asyncio.Future()
        await self._worker_pool.enqueue((state, address, None, fut))
        return (await fut)
