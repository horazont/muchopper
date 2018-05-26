"""
Scanner
#######

The Scanner is responsible for periodically scanning domains and MUC services
for MUC rooms. It compares the lists against the locally available lists and
synchronizes them (for publicly listed MUCs only).
"""
import asyncio
import time

import aioxmpp
import aioxmpp.service

from datetime import timedelta

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

    async def _get_items(self, state):
        return state.get_all_domains()

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
                await suggester(address)

    async def _process_other_domain(self, state, domain):
        items = await self._disco_svc.query_items(domain)

        for item in items:
            address = item.jid
            if address.localpart or address.resource:
                # we don’t want items with local/resourcepart for domain
                # discovery
                continue

            # add domain to list for future scans
            state.require_domain(address)

    async def _process_item(self, state, domain, fut):
        address = aioxmpp.JID(localpart=None, domain=domain, resource=None)

        info = await self._disco_svc.query_info(address)
        if "http://jabber.org/protocol/muc" in info.features:
            # is a MUC domain
            await self._process_muc_domain(state, address)
        else:
            # is unknown domain, use disco#items to find more
            await self._process_other_domain(state, address)
