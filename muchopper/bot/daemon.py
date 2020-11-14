import asyncio
import functools
import logging
import re
import signal
import time
import urllib.parse

from datetime import datetime
from enum import Enum

import aioxmpp

import muchopper.bot.state
from . import (
    worker_pool, state, utils, watcher, scanner, insideman, spokesman, mirror
)


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


class Component(Enum):
    WATCHER = "watcher"
    INSIDEMAN = "insideman"
    SCANNER = "scanner"
    SPOKESMAN = "spokesman"
    INTERACTION = "interaction"
    MIRROR_SERVER = "mirror-server"
    MIRROR_CLIENT = "mirror-client"


class InteractionHandler(aioxmpp.service.Service,
                         utils.MuchopperService):
    HELLO_EXPIRE = 3600
    HELLO_EXPIRE_INTERVAL = HELLO_EXPIRE / 4

    ORDER_AFTER = [
        aioxmpp.MUCClient,
    ]

    PRIVILEGED_ENTITIES = []

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

    def _handle_mediated_invite(self, message, invite):
        self.logger.debug("received invite: %s / %s",
                          message, invite)

        if invite.from_:
            # mediated invite
            self._suggester(
                message.from_.bare()
            )
            return

    def _handle_direct_invite(self, message):
        self.logger.debug("received direct: %s / %s", message,
                          message.xep0249_invite)
        invite = message.xep0249_invite

        privileged = message.from_.bare() in self.PRIVILEGED_ENTITIES

        # direct invite
        self._suggester(
            invite.jid.bare(),
            privileged=privileged,
        )

        self._spoken_to[message.from_] = time.monotonic()

        reply = message.make_reply()
        reply.type_ = aioxmpp.MessageType.CHAT
        reply.body.clear()
        reply.body.update(ACK_BODY)
        self.logger.debug("sending reply to direct invite: %s", reply)
        self.client.enqueue(reply)

    @aioxmpp.service.depsignal(
        aioxmpp.MUCClient,
        "on_muc_invitation")
    def _handle_invite(self, stanza, muc_address, inviter_address, mode, *,
                       password=None, reason=None, **kwargs):
        self._suggester(muc_address)

        self.logger.debug("received invite: mode=%r, muc=%r, from=%r",
                          mode, muc_address, inviter_address)

        if mode == aioxmpp.im.InviteMode.DIRECT:
            self._spoken_to[inviter_address] = time.monotonic()

            reply = stanza.make_reply()
            reply.type_ = aioxmpp.MessageType.CHAT
            reply.body.clear()
            reply.body.update(ACK_BODY)
            self.logger.debug("sending reply to direct invite: %s", reply)


class MUCHopper:
    def __init__(self,
                 loop,
                 jid, security_layer,
                 default_nickname,
                 state,
                 privileged_entities,
                 components,
                 mirror_config,
                 spokesman_config,
                 avatar_whitelist,
                 prometheus_config):
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
        self._muc_svc = self._client.summon(aioxmpp.MUCClient)
        self._disco_svc = self._client.summon(aioxmpp.DiscoClient)

        if (Component.MIRROR_CLIENT in components and
                (Component.WATCHER in components or
                 Component.SCANNER in components or
                 Component.INTERACTION in components or
                 Component.INSIDEMAN in components)):
            raise Exception(
                "Invalid configuration: mirror-client can not be used together "
                "with watcher, scanner, interaction or insideman components, "
                "since mirror-client needs full control over the database."
            )

        if Component.INTERACTION in components:
            self._interaction = self._client.summon(InteractionHandler)
            self._interaction.state = state
            self._interaction.suggester = self.suggest_new_address_nonblocking
            self._interaction.PRIVILEGED_ENTITIES.extend(privileged_entities)

        if Component.WATCHER in components:
            self._watcher = self._client.summon(watcher.Watcher)
            self._watcher.state = state
            self._watcher.suggester = self.suggest_new_address
            self._watcher.avatar_whitelist = avatar_whitelist
        else:
            self._watcher = None

        if Component.SCANNER in components:
            self._scanner = self._client.summon(scanner.Scanner)
            self._scanner.state = state
            self._scanner.suggester = self.suggest_new_address

        if Component.INSIDEMAN in components:
            self._insideman = self._client.summon(insideman.InsideMan)
            self._insideman.state = state
            self._insideman.default_nickname = default_nickname
            self._insideman.suggester = self.suggest_new_address_nonblocking

        if Component.SPOKESMAN in components:
            self._spokesman = self._client.summon(spokesman.Spokesman)
            self._spokesman.state = state
            self._spokesman.suggester = self.suggest_new_address
            self._spokesman.min_keyword_length = spokesman_config.get(
                "min_keyword_length",
                self._spokesman.min_keyword_length
            )
            self._spokesman.max_query_length = spokesman_config.get(
                "max_query_length",
                self._spokesman.max_query_length
            )
            self._spokesman.max_page_size = spokesman_config.get(
                "max_page_size",
                self._spokesman.max_page_size
            )
            self._spokesman.max_keywords = spokesman_config.get(
                "max_keywords",
                self._spokesman.max_keywords
            )

        if Component.MIRROR_SERVER in components:
            self._mirror_server = self._client.summon(mirror.MirrorServer)
            self._mirror_server.publish_target = aioxmpp.JID.fromstr(
                mirror_config["server"]["pubsub_service"],
            )
            self._mirror_server.state = state

        if Component.MIRROR_CLIENT in components:
            self._mirror_client = self._client.summon(mirror.MirrorClient)
            self._mirror_client.source = aioxmpp.JID.fromstr(
                mirror_config["client"]["pubsub_service"],
            )
            self._mirror_client.state = state

        if prometheus_config.get("enable", False):
            bind_host = prometheus_config["bind_address"]
            bind_port = prometheus_config["port"]
            self._prometheus_app = self._setup_prometheus(bind_host, bind_port)
            if prometheus_config.get("state_metrics", False):
                import prometheus_client
                prometheus_client.REGISTRY.register(
                    muchopper.bot.state.StateMetricsCollector(self._state)
                )
        else:
            self._prometheus_app = None

        version_svc = self._client.summon(aioxmpp.VersionServer)
        version_svc.name = "search.jabber.network Crawler"
        version_svc.version = "0.1.0"
        version_svc.os = ""

        self._analysis_pool = worker_pool.WorkerPool(
            16,
            self._analyse_address,
            delay=0.5,
            max_queue_size=128,
            logger=self.logger.getChild("analysis"),
        )

    async def suggest_new_address(self, address, privileged=False):
        self.logger.debug("queue-ing JID for investigation: %s", address)
        await self._analysis_pool.enqueue((address, None, privileged))
        self.logger.debug("queued JID for investigation: %s", address)

    def suggest_new_address_nonblocking(self, address, privileged=False):
        try:
            self._analysis_pool.enqueue_nowait((address, None, privileged))
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
        address, notifier, privileged = info
        self.logger.debug("jid %s: investigating (privileged=%s)",
                          address,
                          privileged)

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

        metadata = await utils.collect_address_metadata(
            self._disco_svc,
            address,
            require_fresh=True
        )

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
            if self._watcher is not None:
                await self._watcher.queue_request(address)

    def _setup_prometheus(self, bind_host, bind_port):
        from . import prometheus
        metrics_endpoint = prometheus.PrometheusMetrics()
        self._prometheus_bind_host = bind_host
        self._prometheus_bind_port = bind_port
        return prometheus.make_app(metrics_endpoint)

    async def run(self):
        self._loop.add_signal_handler(signal.SIGTERM, self._handle_intr)
        self._loop.add_signal_handler(signal.SIGINT, self._handle_intr)

        intr_fut = asyncio.ensure_future(self._intr_event.wait())

        tasks = []
        tasks.append(intr_fut)

        if self._prometheus_app is not None:
            from . import prometheus
            self.logger.info(
                "starting up prometheus endpoint at http://%s:%d/metrics",
                self._prometheus_bind_host,
                self._prometheus_bind_port,
            )
            prometheus_runner = await prometheus.start_app(
                self._prometheus_app,
                self._prometheus_bind_host,
                self._prometheus_bind_port,
            )

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

        if self._prometheus_app is not None:
            await prometheus_runner.cleanup()
