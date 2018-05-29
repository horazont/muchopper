import abc
import asyncio
import collections
import contextlib
import logging
import time

from datetime import timedelta

import aioxmpp
import aioxmpp.service

from . import worker_pool


_logger = logging.getLogger(__name__)


AddressMetadata = collections.namedtuple(
    "AddressMetadata",
    [
        "is_reachable",
        "is_muc",
        "is_joinable_muc",
        "is_indexable_muc",
        "is_banned",
    ]
)


CACHE_TTL_UNREACHABLE = 300
CACHE_TTL_CLOSED = 3600
CACHE_TTL_NON_MUC = 3600
CACHE_TTL_BANNED = 86400


class InfoForm(aioxmpp.forms.Form):
    FORM_TYPE = 'http://jabber.org/protocol/muc#roominfo'

    contactjid = aioxmpp.forms.JIDMulti(
        var='muc#roominfo_contactjid',
        label='Contact Addresses (normally, room owner or owners)'
    )

    description = aioxmpp.forms.TextSingle(
        var='muc#roominfo_description',
        label='Short Description of Room'
    )

    description_alt = aioxmpp.forms.TextSingle(
        var='muc#roomconfig_roomdesc',
    )

    occupants = aioxmpp.forms.TextSingle(
        var='muc#roominfo_occupants',
        label='Current Number of Occupants in Room'
    )

    subject = aioxmpp.forms.TextSingle(
        var='muc#roominfo_subject',
        label='Current Discussion Topic'
    )

    language = aioxmpp.forms.TextSingle(
        var='muc#roominfo_lang',
    )


def get_roominfo(exts) -> InfoForm:
    for ext in exts:
        if ext.get_form_type() != InfoForm.FORM_TYPE:
            continue
        ext.type_ = aioxmpp.forms.xso.DataType.FORM
        return InfoForm.from_xso(ext)
    return InfoForm()


class MuchopperService:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = None
        self._state_future = asyncio.Future()
        self._suggester = None
        self._suggester_future = asyncio.Future()

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        if self._state_future.done():
            raise RuntimeError("state object cannot be exchanged")
        self._state = value
        self._state_future.set_result(value)

    @property
    def suggester(self):
        return self._suggester

    @state.setter
    def suggester(self, value):
        if self._suggester_future.done():
            raise RuntimeError("suggester object cannot be exchanged")
        self._suggester = value
        self._suggester_future.set_result(value)

    async def _shutdown(self):
        if not self._state_future.done():
            self._state_future.set_exception(
                RuntimeError("service is shutting down")
            )
        if not self._suggester_future.done():
            self._suggester_future.set_exception(
                RuntimeError("service is shutting down")
            )
        await super()._shutdown()


class RobustBackgroundJobService(metaclass=abc.ABCMeta):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__background_task = asyncio.ensure_future(self.__task_watcher())
        self.__background_task.add_done_callback(self.__task_watcher_done)

    def __task_watcher_done(self, task):
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except:  # NOQA
            self.logger.exception("TASK WATCHER BROKE")

    async def __task_watcher(self):
        while True:
            try:
                await self._background_job()
            except asyncio.CancelledError:
                return
            except Exception:
                self.logger.warning("background task failed", exc_info=True)
            else:
                self.logger.debug("background task exited")

            self.logger.debug("re-starting background task in 1s")

            # rate-limit restarts
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return

    @abc.abstractmethod
    async def _background_job(self):
        return

    async def _shutdown(self):
        self._background_job_task.cancel()
        try:
            await asyncio.wait_for(self._background_job_task, timeout=10)
        except asyncio.CancelledError:
            pass


class PeriodicBackgroundTask(MuchopperService,
                             RobustBackgroundJobService,
                             metaclass=abc.ABCMeta):
    WORKER_POOL_SIZE = 4
    MIN_INTERVAL = timedelta(minutes=1)
    MIN_PROCESS_INTERVAL = timedelta(seconds=1)
    TIMEOUT = timedelta(seconds=60)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._worker_pool = worker_pool.WorkerPool(
            self.WORKER_POOL_SIZE,
            self._handle_item,
            max_queue_size=self.WORKER_POOL_SIZE * 2,
            delay=(self.MIN_PROCESS_INTERVAL *
                   self.WORKER_POOL_SIZE).total_seconds(),
            logger=self.logger,
        )

    @abc.abstractmethod
    async def _get_items(self, state):
        pass

    @abc.abstractmethod
    async def _process_item(self, state, item, fut):
        pass

    async def _handle_item(self, argv):
        state, item, ctr, fut, = argv
        if ctr is None and fut is not None and fut.done():
            self.logger.debug("request for data for %s has been cancelled",
                              item)
            return

        try:
            coro = self._process_item(state, item, fut)

            if self.TIMEOUT is None:
                result = await coro
            else:
                try:
                    await asyncio.wait_for(
                        coro,
                        timeout=self.TIMEOUT.total_seconds()
                    )
                except asyncio.TimeoutError as exc:
                    self.logger.warning(
                        "processing of item %s timed out",
                        item,
                    )
                    if fut is not None and not fut.done():
                        fut.set_exception(exc)
                    return

            if fut is not None and not fut.done():
                fut.set_result(result)
        except Exception as exc:
            if fut is not None and not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            if ctr is not None:
                ctr.submit()
            if fut is not None and not fut.done():
                fut.set_exception(
                    RuntimeError("task got interrupted unexpectedly")
                )

    async def _collect_and_schedule(self, state):
        items = await self._get_items(state)
        self.logger.debug("scheduling for %d items", len(items))
        ctr = WaitCounter(len(items))

        t0 = time.monotonic()
        for address in items:
            await self._worker_pool.enqueue(
                (state, address, ctr, None)
            )

        self.logger.debug("enqueued all watches, waiting for results")

        await ctr.wait()

        t1 = time.monotonic()
        dt = timedelta(seconds=t1-t0)
        self.logger.debug("%d items processed in %s", len(items), dt)
        sleep = max(self.MIN_INTERVAL - dt,
                    timedelta())
        if sleep > timedelta():
            self.logger.debug(
                "sleeping for %s until next run to fulfill min interval of %s",
                sleep,
                self.MIN_INTERVAL,
            )
            await asyncio.sleep(sleep.total_seconds())

    async def _background_job(self):
        try:
            state = await self._state_future
        except Exception:
            self.logger.error("failed to get state", exc_info=True)
            return
        try:
            await self._suggester_future
        except Exception:
            self.logger.error("failed to get suggester", exc_info=True)
            return

        while True:
            await self._collect_and_schedule(state)


def disco_info_to_address_metadata(info):
    if (not (any(ident.category == "conference" and
                 ident.type_ == "text"  # we don’t want to enter IRC
                 for ident in info.identities) and
             "http://jabber.org/protocol/muc" in info.features)):
        return AddressMetadata(
            is_reachable=True,
            is_muc=False,
            is_joinable_muc=False,
            is_indexable_muc=False,
            is_banned=False,
        )

    is_indexable_muc = (
        "muc_public" in info.features and
        "muc_persistent" in info.features
    )
    is_joinable_muc = (
        "muc_open" in info.features and
        "muc_passwordprotected" not in info.features and
        "muc_persistent" in info.features
    )

    return AddressMetadata(
        is_reachable=True,
        is_muc=True,
        is_joinable_muc=is_joinable_muc,
        is_indexable_muc=is_indexable_muc,
        is_banned=False,
    )


async def collect_address_metadata(
        disco_svc: aioxmpp.DiscoClient,
        address: aioxmpp.JID,
        *,
        require_fresh=False,
        logger: logging.Logger = None) -> AddressMetadata:
    logger = logger or _logger.getChild("address_metadata")

    metadata = AddressMetadata(
        is_reachable=False,
        is_muc=False,
        is_joinable_muc=False,
        is_indexable_muc=False,
        is_banned=False,
    )

    try:
        info = await disco_svc.query_info(
            address,
            require_fresh=require_fresh,
        )
    except aioxmpp.errors.XMPPError as exc:
        logger.debug("jid %s: failed to discover information: %s",
                     address, exc)
        return metadata

    return disco_info_to_address_metadata(info)


async def collect_muc_metadata(
        disco_svc: aioxmpp.DiscoClient,
        address: aioxmpp.JID,
        require_fresh=False) -> dict:
    logger = _logger.getChild("muc_metadata")

    generic_info = await disco_svc.query_info(
        address,
        require_fresh=require_fresh,
    )
    address_metadata = disco_info_to_address_metadata(generic_info)
    room_info = get_roominfo(generic_info.exts)

    logger.debug("jid %s: features = %r, identities = %r, exts = %r, "
                 "room_info = %r",
                 address, generic_info.features, generic_info.identities,
                 generic_info.exts,
                 room_info)

    is_joinable = address_metadata.is_joinable_muc
    is_public = address_metadata.is_indexable_muc

    try:
        nusers = int(room_info.occupants.value)
    except (ValueError, TypeError):
        nusers = None

    kwargs = {
        "is_saveable": "muc_persistent" in generic_info.features,
        "is_open": is_joinable,
        "is_public": is_public,
        "nusers": nusers
    }

    if is_public:
        kwargs["name"] = generic_info.identities[0].name
        if room_info.subject.value is not None:
            kwargs["subject"] = room_info.subject.value or None
        if room_info.description.value is not None:
            kwargs["description"] = room_info.description.value or None
        elif room_info.description_alt.value is not None:
            kwargs["description"] = room_info.description_alt.value or None
        if room_info.language.value is not None:
            kwargs["language"] = room_info.language.value or None

    return kwargs


class WaitCounter:
    def __init__(self, max_, loop=None):
        super().__init__()
        self._max = max_
        self._value = 0
        self._event = asyncio.Event(loop=loop)

    def submit(self):
        self._value += 1
        if self._value >= self._max:
            self._event.set()

    def wait(self):
        return self._event.wait()
