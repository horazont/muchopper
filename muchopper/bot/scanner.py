"""
Scanner
#######

The Scanner is responsible for periodically scanning domains and MUC services
for MUC rooms. It compares the lists against the locally available lists and
synchronizes them (for publicly listed MUCs only).
"""
import asyncio
import random
import time

import aioxmpp
import aioxmpp.service

from datetime import timedelta, datetime

from . import utils


class Scanner(aioxmpp.service.Service,
              utils.PeriodicBackgroundTask):
    WORKER_POOL_SIZE = 8
    MIN_INTERVAL = timedelta(hours=1)
    MIN_PROCESS_INTERVAL = timedelta(seconds=0.4)

    ORDER_AFTER = [
        aioxmpp.DiscoClient,
    ]

    def __init__(self, client, **kwargs):
        super().__init__(client, **kwargs)
        self._disco_svc = self.dependencies[aioxmpp.DiscoClient]
        self.expire_after = timedelta(days=7)

    async def _get_items(self, state):
        domains = state.get_all_domains()
        random.shuffle(domains)
        return domains

    async def _process_muc_domain(self, state, domain):
        suggester = await self._suggester_future
        result = await self._disco_svc.query_items(domain)

        self.logger.debug("got %d items for MUC domain %r",
                          len(result.items),
                          domain)
        for item in result.items:
            address = item.jid
            if not address.localpart and not address.resource:
                # drive-by domain find! but don’t try to use that as MUC here
                state.require_domain(address)
                continue

            info = state.get_address_metadata(address)
            if info is None:
                self.logger.debug("jid %s is not yet known, suggesting",
                                  address)
                await suggester(address, privileged=True)

    async def _process_other_domain(self, state, domain):
        result = await self._disco_svc.query_items(domain)

        for item in result.items:
            address = item.jid
            if item.node:
                # otherwise, we run into a huge overhead for pubsub things
                continue
            self.logger.debug("domain scanner found %r", address)
            if address.localpart or address.resource:
                # we don’t want items with local/resourcepart for domain
                # discovery
                continue

            # add domain to list for future scans
            state.require_domain(address)

    async def _process_item(self, state, domain, fut):
        address = aioxmpp.JID(localpart=None, domain=domain, resource=None)

        try:
            info = await self._disco_svc.query_info(address)
        except aioxmpp.errors.XMPPError as exc:
            self.logger.error("failed to disco#info %s: %s",
                              address,
                              exc)
            return

        try:
            if "http://jabber.org/protocol/muc" in info.features:
                # is a MUC domain
                await self._process_muc_domain(state, address)
            else:
                # is unknown domain, use disco#items to find more
                await self._process_other_domain(state, address)
        except aioxmpp.errors.XMPPError as exc:
            self.logger.error("received error response while scanning %s: %s",
                              address, exc)
        except aioxmpp.errors.ErroneousStanza as exc:
            self.logger.error("received invalid response while scanning %s: %s",
                              address, exc)

    async def _execute(self, state):
        await super()._execute(state)
        # now clean up all stale MUCs
        threshold = datetime.utcnow() - self.expire_after
        self.logger.debug("expiring domains which haven’t been seen since %s",
                          threshold)
        state.expire_domains(threshold)
