import asyncio
import functools
import logging
import re
import signal
import time
import urllib.parse

from datetime import datetime

import aioxmpp

from . import worker_pool, state, utils, watcher, scanner, insideman


INFO_BODY = {
    aioxmpp.structs.LanguageTag.fromstr("en"):
        "Hi! I am the bot feeding https://muclumbus.jabbercat.org. Please "
        "see there for my Privacy Policy and what I do.",
}

ACK_BODY = {
    aioxmpp.structs.LanguageTag.fromstr("en"):
        "Hi, and thank you for your invite. I will consider it. It may take a "
        "while (approximately two hours) until your suggestion is added to "
        "the public list. I will not actually join the room, though.",
}


class InteractionHandler(aioxmpp.service.Service,
                         utils.MuchopperService):
    HELLO_EXPIRE = 3600
    HELLO_EXPIRE_INTERVAL = HELLO_EXPIRE / 4

    ORDER_AFTER = [
        aioxmpp.im.dispatcher.IMDispatcher,
    ]

    ORDER_BEFORE = [
        aioxmpp.MUCClient,
    ]

    def __init__(self, client, **kwargs):
        super().__init__(client, **kwargs)
        self._spoken_to = aioxmpp.cache.LRUDict()
        self._spoken_to.maxsize = 1000
        self._loop = asyncio.get_event_loop()
        self._loop.call_later(self.HELLO_EXPIRE_INTERVAL,
                              self._expire_spoken_to)

    def _expire_spoken_to(self):
        threshold = time.monotonic() - self.HELLO_EXPIRE
        to_delete = [
            key for key, ts in self._spoken_to.items()
            if ts < threshold
        ]
        for key in to_delete:
            del self._spoken_to[key]

        self._loop.call_later(self.HELLO_EXPIRE_INTERVAL,
                              self._expire_spoken_to)

    def _handle_relevant_message(self, message):
        from_ = message.from_
        self.logger.debug("relevant message: %s", message)
        if from_ in self._spoken_to:
            self.logger.debug("... but I know them already")
            return

        self._spoken_to[from_] = time.monotonic()
        reply = message.make_reply()
        reply.body.clear()
        reply.body.update(INFO_BODY)

        self.logger.debug("reply: %s", reply)
        self.client.enqueue(reply)

    def _handle_invite(self, message, invite):
        self.logger.debug("received invite: %s / %s",
                          message, invite)

        if invite.from_:
            # mediated invite
            self._suggester(
                message.from_.bare()
            )
            return

        if invite.to:
            # direct invite
            self._suggester(
                invite.to.bare()
            )

            self._spoken_to[message.from_] = time.monotonic()

            reply = message.make_reply()
            reply.type_ = aioxmpp.MessageType.CHAT
            reply.body.clear()
            reply.body.update(ACK_BODY)
            self.logger.debug("sending reply to direct invite: %s", reply)
            self.client.enqueue(reply)

    @aioxmpp.service.depfilter(
        aioxmpp.im.dispatcher.IMDispatcher,
        "message_filter")
    def _handle_message(self, message, peer, sent, source):
        if message.type_ == aioxmpp.MessageType.ERROR:
            return message

        if (message.xep0045_muc_user and
                message.xep0045_muc_user.invites):
            invite = message.xep0045_muc_user.invites[0]
            self._handle_invite(message, invite)
            return None

        if (message.type_ != aioxmpp.MessageType.GROUPCHAT and
                message.type_ != aioxmpp.MessageType.ERROR):
            if message.body:
                self._handle_relevant_message(message)
                return None

        return message


class MUCHopper:
    def __init__(self,
                 loop,
                 jid, security_layer,
                 default_nickname,
                 state):
        self.logger = logging.getLogger("muclogger")
        self._loop = loop
        self._state = state
        self._intr_event = asyncio.Event(loop=self._loop)
        self._client = aioxmpp.PresenceManagedClient(
            jid,
            security_layer,
            logger=logging.getLogger("muchopper.client")
        )
        self._client.summon(aioxmpp.DiscoServer)
        self._interaction = self._client.summon(InteractionHandler)
        self._interaction.state = state
        self._interaction.suggester = self.suggest_new_address_nonblocking
        self._muc_svc = self._client.summon(aioxmpp.MUCClient)
        self._disco_svc = self._client.summon(aioxmpp.DiscoClient)
        self._watcher = self._client.summon(watcher.Watcher)
        self._watcher.state = state
        self._watcher.suggester = self.suggest_new_address
        self._scanner = self._client.summon(scanner.Scanner)
        self._scanner.state = state
        self._scanner.suggester = self.suggest_new_address
        self._insideman = self._client.summon(insideman.InsideMan)
        # self._insideman.state = state
        self._insideman.default_nickname = default_nickname
        self._insideman.suggester = self.suggest_new_address_nonblocking

        version_svc = self._client.summon(aioxmpp.VersionServer)
        version_svc.name = "MUCHopper"
        version_svc.version = "0.1.0"
        version_svc.os = ""

        self._analysis_pool = worker_pool.WorkerPool(
            4,
            self._analyse_address,
            delay=0.5,
            logger=self.logger.getChild("analysis"),
        )

    async def suggest_new_address(self, address):
        self.logger.debug("queue-ing JID for investigation: %s", address)
        await self._analysis_pool.enqueue((address, None))
        self.logger.debug("queued JID for investigation: %s", address)

    def suggest_new_address_nonblocking(self, address):
        try:
            self._analysis_pool.enqueue_nowait((address, None))
            self.logger.debug("queued JID for investigation: %s", address)
        except asyncio.QueueFull:
            self.logger.warning(
                "dropping suggested JID due to queue overrun: %s",
                address,
            )

    def _handle_intr(self):
        self._intr_event.set()

    def _enqueue_referral(self, timestamp, source_jid, dest_jid, score):
        def store_referral(address, metadata):
            if not metadata.is_indexable_muc:
                return
            self._state.store_referral(
                source_jid,
                dest_jid,
                timestamp=timestamp,
            )

        self._analysis_pool.enqueue_nowait((dest_jid, store_referral))

    def _enqueue_invitation(self, timestamp, dest_jid):
        self._analysis_pool.enqueue_nowait((dest_jid, None))

    async def _analyse_address(self, info):
        address, notifier = info
        self.logger.debug("jid %s: investigating", address)

        metadata = self._state.get_address_metadata(address)
        if metadata is not None:
            if metadata.is_banned:
                self.logger.info("jid %s: I am banned there, not checking now",
                                 address)
                return

            if not metadata.is_joinable_muc:
                self.logger.info(
                    "jid %s: (from cache) is not joinable a MUC (or is an IRC)",
                    address
                )
                return

        metadata = await utils.collect_address_metadata(self._disco_svc,
                                                        address)

        self.logger.info("jid %s: discovered metadata: %s",
                         address, metadata)

        if metadata.is_banned:
            self._state.cache_address_metadata(
                address,
                metadata,
                utils.CACHE_TTL_BANNED,
            )

        if not metadata.is_reachable:
            self._state.cache_address_metadata(
                address,
                metadata,
                utils.CACHE_TTL_UNREACHABLE,
            )

        if not metadata.is_muc:
            self._state.cache_address_metadata(
                address,
                metadata,
                utils.CACHE_TTL_NON_MUC,
            )

        if notifier is not None:
            notifier(address, metadata)

        if metadata.is_joinable_muc or metadata.is_indexable_muc:
            await self._watcher.queue_request(address)

    def handle_new_address(self, address):
        self._analysis_pool.enqueue_nowait((address, None))

    async def run(self):
        self._loop.add_signal_handler(signal.SIGTERM, self._handle_intr)
        self._loop.add_signal_handler(signal.SIGINT, self._handle_intr)

        intr_fut = asyncio.ensure_future(self._intr_event.wait())

        tasks = []
        tasks.append(intr_fut)

        async with self._client.connected():
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )

        for fut in tasks:
            if not fut.done():
                fut.cancel()

        await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)

        for fut in tasks:
            try:
                fut.result()
            except asyncio.CancelledError:
                pass
