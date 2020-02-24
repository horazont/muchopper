"""
Watcher
#######

Watcher is responsible for periodically updating the metrics of inactive rooms.
Inactive rooms are rooms where Inside Man is not joined. The update period can
be configured in a range, and will be scaled according to the number of known
inactive rooms.
"""
import asyncio
import contextlib
import random
import sys
import time

from datetime import timedelta, datetime

import aioxmpp
import aioxmpp.vcard
import aioxmpp.service

from aioxmpp.utils import namespaces

from . import utils, state, worker_pool
from .promhelpers import time_optional, set_optional, time_optional_late


class Watcher(aioxmpp.service.Service,
              utils.PeriodicBackgroundTask):
    WORKER_POOL_SIZE = 8
    MIN_INTERVAL = timedelta(hours=1)
    MIN_PROCESS_INTERVAL = timedelta(seconds=0.05)

    ORDER_AFTER = [
        aioxmpp.DiscoClient,
        aioxmpp.vcard.VCardService,
    ]

    def __init__(self, client, **kwargs):
        super().__init__(client, **kwargs)
        self._disco_svc = self.dependencies[aioxmpp.DiscoClient]
        self._vcard_client = self.dependencies[aioxmpp.vcard.VCardService]
        self.expire_after = timedelta(days=2)
        self.avatar_whitelist = []

        try:
            import prometheus_client
        except ImportError:
            self._room_scanned_metric = None
            self._disco_info_duration_metric = None
            self._avatar_fetch_duration_metric = None
            self._avatar_proc_duration_metric = None
            self._pass_duration_metric = None
            self._last_pass_end_metric = None
            self._update_duration_metric = None
        else:
            self._room_scanned_metric = prometheus_client.Summary(
                "muclumbus_watcher_room_scan_duration",
                "Total number of scan operations executed",
            )
            self._disco_info_duration_metric = prometheus_client.Summary(
                "muclumbus_watcher_disco_info_duration_seconds",
                "Duration of info requests",
                ["result"]
            )
            self._update_duration_metric = prometheus_client.Summary(
                "muclumbus_watcher_update_duration_seconds",
                "Duration of database updates",
            )
            self._avatar_proc_duration_metric = prometheus_client.Summary(
                "muclumbus_watcher_avatar_proc_duration_seconds",
                "Duration of avatar processing",
            )
            self._avatar_fetch_duration_metric = prometheus_client.Summary(
                "muclumbus_watcher_avatar_fetch_duration_seconds",
                "Duration of avatar requests",
                ["result"]
            )
            self._pass_duration_metric = prometheus_client.Gauge(
                "muclumbus_watcher_pass_duration_seconds",
                "Duration of the last pass in seconds"
            )
            self._last_pass_end_metric = prometheus_client.Gauge(
                "muclumbus_watcher_last_pass_end_seconds",
                "Timestamp of the last pass"
            )

    async def _get_items(self, state):
        items = state.get_all_known_inactive_mucs()
        random.shuffle(items)
        return items

    async def _process_item(self, state, item, fut):
        self.logger.debug("looking at %s", item)

        with time_optional(self._room_scanned_metric):
            with time_optional_late(self._disco_info_duration_metric) as info_m:
                info_m["labels"] = ["timeout"]
                try:
                    info = await utils.collect_muc_metadata(
                        self._disco_svc,
                        item,
                        require_fresh=True)
                    info_m["labels"] = ["success"]
                except aioxmpp.errors.XMPPCancelError as e:
                    info_m["labels"] = [e.condition.value[1]]
                    # TODO: follow new address in gone if available
                    if e.condition in (
                            (namespaces.stanzas, "item-not-found"),
                            (namespaces.stanzas, "gone")):
                        # delete muc
                        self.logger.info(
                            "MUC %s does not exist anymore, "
                            "erasing from database",
                            item,
                        )
                        if fut is not None and not fut.done():
                            fut.set_exception(e)
                        state.delete_all_muc_data(item)
                        return
                    raise

            if info.get("is_public") and (
                    item in self.avatar_whitelist or
                    item.replace(localpart=None) in self.avatar_whitelist):
                with time_optional_late(
                        self._avatar_fetch_duration_metric) as avatar_m:
                    avatar_m["labels"] = ["success"]
                    try:
                        avatar = await utils.fetch_avatar(
                            self._vcard_client,
                            item
                        )
                    except aioxmpp.errors.XMPPError as e:
                        avatar_m["labels"] = [e.condition.value[1]]
                        self.logger.info(
                            "failed to fetch avatar of MUC %s",
                            item
                        )
                        avatar = None, None
            else:
                avatar = None, None

            if fut is not None and not fut.done():
                fut.set_result(info)

            self.logger.debug("jid %s: updating metadata: %r",
                              item, info)
            with time_optional(self._update_duration_metric):
                state.update_muc_metadata(item, **info)

            with contextlib.ExitStack() as stack:
                if avatar != (None, None):
                    stack.enter_context(
                        time_optional(self._avatar_proc_duration_metric)
                    )
                await state.update_muc_avatar(item, *avatar)
        return info

    async def _execute(self, state):
        with time_optional(self._pass_duration_metric):
            await super()._execute(state)
        set_optional(self._last_pass_end_metric, time.time())
        # now clean up all stale MUCs
        threshold = datetime.utcnow() - self.expire_after
        self.logger.debug("expiring MUCs which haven’t been seen since %s",
                          threshold)
        state.expire_mucs(threshold)

    async def queue_request(self, address: aioxmpp.JID):
        state = await self._state_future
        await self._worker_pool.enqueue((state, address, None, None))

    async def request(self, address: aioxmpp.JID):
        state = await self._state_future
        fut = asyncio.Future()
        await self._worker_pool.enqueue((state, address, None, fut))
        return (await fut)
