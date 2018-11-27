import asyncio
import functools
import itertools

from datetime import datetime, timedelta

import sqlalchemy.orm.exc

import aioxmpp
import aioxmpp.errors
import aioxmpp.forms
import aioxmpp.service

from muchopper.common import model, queries

from . import utils, worker_pool, xso


def chop_to_batches(iterable, batch_size):
    def clock_generator():
        for num in itertools.count():
            for i in range(batch_size):
                yield num

    clock = iter(clock_generator())
    for _, items in itertools.group(iterable, lambda x: clock()):
        yield list(item)


class MirrorServer(utils.MuchopperService, aioxmpp.service.Service):
    ORDER_AFTER = [
        aioxmpp.DiscoClient,
        aioxmpp.PubSubClient,
    ]

    WORKER_POOL_SIZE = 4
    MIN_PROCESS_INTERVAL = timedelta(seconds=0.01)

    def __init__(self, client, **kwargs):
        super().__init__(client, **kwargs)
        self._pubsub = self.dependencies[aioxmpp.PubSubClient]
        self._disco = self.dependencies[aioxmpp.DiscoClient]
        self.publish_target = None
        self._state_future.add_done_callback(self._initialise)
        self._event_buffer = {}
        self._first_enqueued = None
        self._enqueued_callback = None
        self._worker_pool = worker_pool.WorkerPool(
            self.WORKER_POOL_SIZE,
            self._handle_item,
            max_queue_size=self.WORKER_POOL_SIZE * 128,
            delay=(self.MIN_PROCESS_INTERVAL *
                   self.WORKER_POOL_SIZE).total_seconds(),
            logger=self.logger,
        )
        self.node_config = aioxmpp.forms.Data(
            aioxmpp.forms.DataType.SUBMIT
        )
        self.node_config.fields.append(
            aioxmpp.forms.Field(
                type_=aioxmpp.forms.FieldType.HIDDEN,
                var="FORM_TYPE",
                values=[
                    "http://jabber.org/protocol/pubsub#node_config"
                ]
            )
        )
        self.node_config.fields.append(
            aioxmpp.forms.Field(
                var="pubsub#access_model",
                values=["open"]
            )
        )
        self.node_config.fields.append(
            aioxmpp.forms.Field(
                var="pubsub#max_items",
                values=["16777216"]
            )
        )
        self.node_config.fields.append(
            aioxmpp.forms.Field(
                var="pubsub#persist_items",
                values=["1"]
            )
        )


    def _initialise(self, state_future):
        state = state_future.result()
        state.on_muc_changed.connect(
            self._on_muc_changed,
            state.on_muc_changed.ASYNC_WITH_LOOP(None)
        )
        state.on_muc_deleted.connect(
            self._on_muc_deleted,
            state.on_muc_deleted.ASYNC_WITH_LOOP(None)
        )
        state.on_domain_changed.connect(
            self._on_domain_changed,
            state.on_domain_changed.ASYNC_WITH_LOOP(None)
        )
        state.on_domain_deleted.connect(
            self._on_domain_deleted,
            state.on_domain_deleted.ASYNC_WITH_LOOP(None)
        )
        self.logger.debug("mirror server service initialised")

    @aioxmpp.service.depsignal(aioxmpp.Client, "on_stream_established",
                               defer=True)
    async def _stream_established(self):
        if self.publish_target is None:
            self.logger.warning(
                "skipping auto-creation of node, because publish target is " "unconfigured"
            )
            return

        try:
            await self._pubsub.create(
                self.publish_target,
                xso.StateTransferV1_0Namespaces.MUCS.value,
            )
            self.logger.debug("node created!")
        except aioxmpp.errors.XMPPCancelError as exc:
            # if it’s a conflict, it’s ok, this just means the node was already
            # created
            if exc.condition != aioxmpp.errors.ErrorCondition.CONFLICT:
                raise
            self.logger.debug("node exists already")

        try:
            await self._pubsub.set_node_config(
                self.publish_target,
                self.node_config,
                node=xso.StateTransferV1_0Namespaces.MUCS.value,
            )
        except aioxmpp.errors.XMPPError as exc:
            self.logger.warning("failed to configure node: %s", exc)

        self.logger.debug("init-sync: performing initial synchronisation")

        # we go ahead and delete all items which aren’t in our database anymore
        # this is a safety measure against lost deletes. likewise, we’ll enqueue
        # creations for all things which haven’t been created yet.
        try:
            existing_items = await self._disco.query_items(
                self.publish_target,
                node=xso.StateTransferV1_0Namespaces.MUCS.value,
                require_fresh=True
            )
        except aioxmpp.errors.XMPPError as exc:
            self.logger.error("init-sync: failed to query existing items",
                              exc_info=True)
            return

        existing_addresses = set(aioxmpp.JID.fromstr(item.name)
                                 for item in existing_items.items)

        ncreated = 0
        nok = 0

        with (await self._state_future).get_session() as session:
            updates = []
            for muc, public_info in queries.base_query(
                    session, include_closed=False):
                try:
                    existing_addresses.remove(muc.address)
                except KeyError:
                    self.logger.debug(
                        "init-sync: %s missing on remote, enqueue-ing update",
                        muc.address,
                    )
                    # item does not exist on remote, enqueue update
                    updates.append(
                        self._compose_muc_update(muc, public_info)
                    )
                    ncreated += 1
                else:
                    nok += 1

            session.rollback()

        for update in updates:
            await self._worker_pool.enqueue(update)

        for address in existing_addresses:
            self.logger.debug(
                "init-sync: %s exists on remote, enqueue-ing delete",
                address,
            )
            await self._worker_pool.enqueue(
                self._compose_muc_delete(address)
            )

        self.logger.info("init-sync: %d creates, %d deletes; %d items exist",
                         ncreated, len(existing_addresses), nok)

    async def _handle_item(self, item):
        # self.logger.debug("executing %s", item)
        await item()

    def _enqueue_update(self, item):
        try:
            self._worker_pool.enqueue_nowait(item)
        except asyncio.QueueFull:
            self.logger.warning(
                "lost update due to overloaded worker! %r",
                item,
            )

    async def _do_muc_delete(self, address):
        try:
            await self._pubsub.retract(
                self.publish_target,
                xso.StateTransferV1_0Namespaces.MUCS.value,
                id_=str(address),
                notify=True,
            )
        except aioxmpp.errors.XMPPCancelError as exc:
            if exc.condition == aioxmpp.errors.ErrorCondition.ITEM_NOT_FOUND:
                return
            raise

    async def _do_muc_update(self, data):
        await self._pubsub.publish(
            self.publish_target,
            xso.StateTransferV1_0Namespaces.MUCS.value,
            data,
            id_=str(data.address)
        )

    def _compose_muc_update(self, muc, public_info):
        data = xso.SyncItemMUC()
        data.address = muc.address
        data.is_open = muc.is_open
        data.anonymity_mode = muc.anonymity_mode
        data.nusers = muc.nusers_moving_average
        data.name = public_info.name
        data.language = public_info.language
        data.description = public_info.description

        return functools.partial(self._do_muc_update, data)

    def _compose_muc_delete(self, address):
        return functools.partial(self._do_muc_delete, address)

    def _on_muc_changed(self, address):
        if self.publish_target is None:
            self.logger.warning("lost update: no publish target configured!")
            return

        with self._state.get_session() as session:
            try:
                try:
                    muc_info = queries.base_query(
                        session, include_closed=False
                    ).filter(
                        model.MUC.address == address,
                    ).one()
                except sqlalchemy.orm.exc.NoResultFound:
                    self.logger.debug(
                        "turning MUC update into delete for %s: it has "
                        "disappeared from the database in the meantime or is "
                        "not public",
                        address,
                    )
                    self._enqueue_update(self._compose_muc_delete(address))
                    return

                muc, public_info = muc_info

                try:
                    item = self._compose_muc_update(muc, public_info)
                except Exception:
                    self.logger.error(
                        "lost update to %s: failed to compose update item",
                        address,
                        exc_info=True,
                    )
                    return
            finally:
                session.rollback()

        self.logger.debug("enqueing update for muc %s", address)
        self._enqueue_update(item)

    def _on_muc_deleted(self, address):
        if self.publish_target is None:
            self.logger.warning("lost update: no publish target configured!")
            return

        self._enqueue_update(self._compose_muc_delete(address))

    def _on_domain_changed(self, address):
        pass

    def _on_domain_deleted(self, address):
        pass


class MirrorClient(utils.MuchopperService, aioxmpp.service.Service):
    ORDER_AFTER = [
        aioxmpp.PubSubClient,
        aioxmpp.DiscoClient,
    ]

    @aioxmpp.service.depsignal(aioxmpp.Client, "before_stream_established")
    async def _on_stream_established(self):
        pubsub = self.dependencies[aioxmpp.PubSubClient]
        disco = self.dependencies[aioxmpp.DiscoClient]

        try:
            await pubsub.subscribe(
                self.source,
                node=xso.StateTransferV1_0Namespaces.MUCS.value,
            )
        except aioxmpp.errors.XMPPCancelError as exc:
            if exc.condition == aioxmpp.errors.ErrorCondition.CONFLICT:
                # already subscribed?
                self.logger.debug("it appears we’re already subscribed...")
                return
            raise

        self.logger.debug("subscribed to service at %s", self.source)

        if self._state is None:
            self.logger.warning("cannot execute initial transfer; "
                                "state is not ready yet")
            return

        self.logger.debug("init-sync: beginning transfer")

        try:
            existing_items = await disco.query_items(
                self.source,
                node=xso.StateTransferV1_0Namespaces.MUCS.value,
                require_fresh=True
            )
        except aioxmpp.errors.XMPPError as exc:
            self.logger.error("init-sync: failed to query existing items",
                              exc_info=True)
            return

        existing_ids = set(item.name for item in existing_items.items)

        self.logger.info(
            "init-sync: remote knows %d entries, downloading "
            "(this may take a moment -- turn on debug logging for "
            "detailed progress)...",
            len(existing_ids)
        )

        batches = list(chop_to_batches(existing_ids))

        # finish batchification
        async def _download_and_merge_single(id_):
            try:
                try:
                    item = (await pubsub.get_items_by_id(
                        self.source,
                        xso.StateTransferV1_0Namespaces.MUCS.value,
                        [id_],
                    )).payload.items[0].registered_payload
                except aioxmpp.errors.XMPPCancelError as exc:
                    if (exc.condition ==
                            aioxmpp.errors.ErrorCondition.ITEM_NOT_FOUND):
                        # discard to force deletion
                        existing_ids.discard(id_)
                        self.logger.debug(
                            "init-sync: %s vanished, deleting later"
                        )
                        return
                    raise

                self._unwrap_item_into_state(item, self._state)
                self.logger.debug("init-sync: updated %s", id_)
            finally:
                ctr.submit()

        download_workers = worker_pool.WorkerPool(
            32, _download_and_merge,
            max_queue_size=64,
            delay=timedelta(0),
        )
        ctr = utils.WaitCounter(len(existing_ids))
        try:
            for id_ in existing_ids:
                await download_workers.enqueue(id_)
            await ctr.wait()
        finally:
            download_workers.close()

        self.logger.info("init-sync: state download complete")

        with self._state.get_session() as session:
            for address, in session.query(model.MUC.address):
                if str(address) in existing_ids:
                    continue

                self.logger.debug(
                    "init-sync: %s not in remote, deleting",
                    address,
                )
                session.query(model.MUC).filter(
                    model.MUC.address == address
                ).delete()

        self.logger.info("init-sync: state transfer complete")

    def _unwrap_item_into_state(self, data, state):
        state.update_muc_metadata(
            data.address,
            nusers=data.nusers,
            is_open=data.is_open,
            name=data.name,
            description=data.description,
            language=data.language,
            anonymity_mode=data.anonymity_mode,
            is_saveable=True,
        )

    @aioxmpp.service.depsignal(aioxmpp.PubSubClient, "on_item_published")
    def _on_item_published(self, jid, node, item, **kwargs):
        if (jid != self.source or
                node != xso.StateTransferV1_0Namespaces.MUCS.value):
            self.logger.debug(
                "ignoring update from invalid source: %s (node %r)",
                jid,
                node,
            )
            return

        if self._state is None:
            self.logger.warning("lost update: state is not ready yet")
            return

        address = aioxmpp.JID.fromstr(item.id_)
        self.logger.debug("received update for %s", address)
        data = item.registered_payload
        self._unwrap_item_into_state(data, self._state)

    @aioxmpp.service.depsignal(aioxmpp.PubSubClient, "on_item_retracted")
    def _on_item_retracted(self, jid, node, id_, **kwargs):
        if (jid != self.source or
                node != xso.StateTransferV1_0Namespaces.MUCS.value):
            self.logger.debug(
                "ignoring delete from invalid source: %s (node %r)",
                jid,
                node,
            )
            return

        if self._state is None:
            self.logger.warning("lost delete: state is not ready yet")
            return

        address = aioxmpp.JID.fromstr(id_)
        self.logger.debug("received delete for %s", address)
        self._state.delete_all_muc_data(address)
