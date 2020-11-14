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
import aioxmpp.disco.xso as disco_xso
import aioxmpp.service
import aioxmpp.rsm.xso as rsm_xso

from datetime import timedelta, datetime

from . import utils
from .promhelpers import time_optional_late, set_optional, time_optional


disco_xso.ItemsQuery.xep0059_set = aioxmpp.xso.Child(
    [rsm_xso.ResultSetMetadata]
)


async def list_items_with_rsm(domain: aioxmpp.JID,
                              client: aioxmpp.Client,
                              page_size: int):
    rsm = rsm_xso.ResultSetMetadata.limit(max_=page_size)
    while True:
        q = disco_xso.ItemsQuery()
        q.xep0059_set = rsm
        req = aioxmpp.IQ(
            type_=aioxmpp.IQType.GET,
            to=domain,
            payload=q,
        )
        resp = await client.send(req)
        if not resp.items:
            break
        rsm = resp.xep0059_set.next_page(max_=page_size)
        yield resp.items


async def list_items(domain: aioxmpp.JID,
                     client: aioxmpp.Client):
    disco_client = client.summon(aioxmpp.DiscoClient)
    info = await disco_client.query_info(domain)
    if "http://jabber.org/protocol/rsm" in info.features:
        # XXX: page size: jabber.ietf.org is buggy.
        # When you specify a page size > 100(?) (e.g. 1000), it will return
        # only 100 items -- but from the *end* of the result set. When you
        # try to force the beginning by adding <after/>, it rejects the query
        # altogether.
        # So we have to essentially guess the maximum and hope that no other
        # implementation sucks that much.
        async for item_batch in list_items_with_rsm(domain, client, 100):
            yield item_batch
        return

    yield (await disco_client.query_items(domain)).items


class Scanner(aioxmpp.service.Service,
              utils.PeriodicBackgroundTask):
    WORKER_POOL_SIZE = 8
    MIN_INTERVAL = timedelta(hours=1)
    MIN_PROCESS_INTERVAL = timedelta(seconds=0.1)

    DOMAIN_TYPE_OTHER = "other"
    DOMAIN_TYPE_FAILED = "failed"
    DOMAIN_TYPE_MUC = "muc"

    ORDER_AFTER = [
        aioxmpp.DiscoClient,
    ]

    def __init__(self, client, **kwargs):
        super().__init__(client, **kwargs)
        self._disco_svc = self.dependencies[aioxmpp.DiscoClient]
        self.expire_after = timedelta(days=7)
        self.non_muc_rescan_delay = timedelta(hours=6)

        try:
            import prometheus_client
        except ImportError:
            self._domain_scanned_metric = None
            self._disco_info_duration_metric = None
            self._version_duration_metric = None
            self._disco_items_duration_metric = None
            self._pass_duration_metric = None
            self._last_pass_end_metric = None
            self._update_duration_metric = None
        else:
            self._domain_scanned_metric = prometheus_client.Summary(
                "muclumbus_scanner_domain_scan_duration",
                "Total number of scan operations executed",
                ["type"],
            )
            self._disco_info_duration_metric = prometheus_client.Summary(
                "muclumbus_scanner_disco_info_duration_seconds",
                "Duration of info requests",
                ["result"]
            )
            self._update_duration_metric = prometheus_client.Summary(
                "muclumbus_scanner_update_duration_seconds",
                "Duration of database updates",
                ["operation"]
            )
            self._version_duration_metric = prometheus_client.Summary(
                "muclumbus_scanner_version_duration_seconds",
                "Duration of software version requests",
                ["result"]
            )
            self._disco_items_duration_metric = prometheus_client.Summary(
                "muclumbus_scanner_disco_items_duration_seconds",
                "Duration of items requests",
                ["type", "result"],
            )
            self._pass_duration_metric = prometheus_client.Gauge(
                "muclumbus_scanner_pass_duration_seconds",
                "Duration of the last pass in seconds"
            )
            self._last_pass_end_metric = prometheus_client.Gauge(
                "muclumbus_scanner_last_pass_end_seconds",
                "Timestamp of the last pass"
            )

    async def _get_items(self, state):
        domains = state.get_scannable_domains()
        random.shuffle(domains)
        return domains

    def _update_domain(self, state, domain, info, version_info):
        if version_info is None:
            software_name = None
            software_version = None
            software_os = None
        else:
            software_name = version_info.name
            software_version = version_info.version
            software_os = version_info.os

        with time_optional(self._update_duration_metric, "update"):
            state.update_domain(
                domain,
                identities=[
                    (identity.category, identity.type_)
                    for identity in info.identities
                ],
                software_name=software_name,
                software_version=software_version,
                software_os=software_os,
            )

    async def _process_muc_domain(self, state, domain):
        suggester = await self._suggester_future
        total = 0
        async for item_batch in list_items(domain, self.client):
            self.logger.debug("found a batch of %d items for domain %s",
                              len(item_batch), domain)
            total += len(item_batch)
            for item in item_batch:
                address = item.jid
                if not address.localpart and not address.resource:
                    # drive-by domain find! but don’t try to use that as MUC
                    # here
                    with time_optional(self._update_duration_metric,
                                       "upsert"):
                        state.require_domain(address)
                    continue

                info = state.get_address_metadata(address)
                if info is None:
                    self.logger.debug("jid %s is not yet known, suggesting",
                                      address)
                    await suggester(address, privileged=True)
        self.logger.debug("processed %d items for domain %s",
                          total, domain)

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
            with time_optional(self._update_duration_metric,
                               "upsert"):
                state.require_domain(
                    address,
                    seen=-self.non_muc_rescan_delay
                )

    async def _process_item(self, state, item, fut):
        domain, last_seen, is_muc = item
        address = aioxmpp.JID(localpart=None, domain=domain, resource=None)

        if not is_muc:
            threshold = datetime.utcnow() - self.non_muc_rescan_delay
            if last_seen is not None and last_seen >= threshold:
                self.logger.debug(
                    "%s is not a MUC service and was scanned since %s; skipping"
                    " it in in this round",
                    domain, threshold
                )
                return
            else:
                self.logger.debug(
                    "%s is not a MUC service, but it was not successfully "
                    "scanned since %s; including it in this round",
                    domain, threshold,
                )
        else:
            self.logger.debug("%s is a MUC service, forcing scan", domain)

        with time_optional_late(self._domain_scanned_metric) as scan_info_m:
            scan_info_m["labels"] = [self.DOMAIN_TYPE_FAILED]

            with time_optional_late(
                    self._disco_info_duration_metric) as disco_info_m:
                disco_info_m["labels"] = ["timeout"]
                try:
                    info = await self._disco_svc.query_info(address)
                    disco_info_m["labels"] = ["success"]
                except aioxmpp.errors.XMPPError as exc:
                    disco_info_m["labels"] = [exc.condition.value[1]]
                    self.logger.error("failed to disco#info %s: %s",
                                      address,
                                      exc)
                    return

            with time_optional_late(
                    self._version_duration_metric) as version_info_m:
                version_info_m["labels"] = ["timeout"]
                try:
                    version_info = await aioxmpp.version.query_version(
                        self.client.stream,
                        address,
                    )
                    version_info_m["labels"] = ["success"]
                except aioxmpp.errors.XMPPError as exc:
                    version_info_m["labels"] = [exc.condition.value[1]]
                    self.logger.debug(
                        "failed to query software version of %s (%s), ignoring",
                        address,
                        exc
                    )
                    version_info = None

            self._update_domain(
                state,
                domain,
                info,
                version_info,
            )

            with time_optional_late(
                    self._disco_items_duration_metric) as items_info_m:
                domain_type = self.DOMAIN_TYPE_FAILED
                items_info_m["labels"] = [None, "timeout"]
                try:
                    if "http://jabber.org/protocol/muc" in info.features:
                        # is a MUC domain
                        domain_type = self.DOMAIN_TYPE_MUC
                        await self._process_muc_domain(state, address)
                    else:
                        # is unknown domain, use disco#items to find more
                        domain_type = self.DOMAIN_TYPE_OTHER
                        await self._process_other_domain(state, address)
                    items_info_m["labels"][1] = "success"
                except aioxmpp.errors.XMPPError as exc:
                    self.logger.error(
                        "received error response while scanning %s: %s",
                        address, exc
                    )
                    items_info_m["labels"][1] = exc.condition.value[1]
                except aioxmpp.errors.ErroneousStanza as exc:
                    self.logger.error(
                        "received invalid response while scanning %s: %s",
                        address, exc
                    )
                    items_info_m["labels"][1] = "invalid"
                finally:
                    items_info_m["labels"][0] = domain_type
                    scan_info_m["labels"][0] = domain_type

    async def _execute(self, state):
        with time_optional(self._pass_duration_metric):
            await super()._execute(state)

        set_optional(self._last_pass_end_metric, time.time())
        # now clean up all stale MUCs
        threshold = datetime.utcnow() - self.expire_after
        self.logger.debug("expiring domains which haven’t been seen since %s",
                          threshold)
        state.expire_domains(threshold)
