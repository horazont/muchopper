"""
Inside Man
##########

This component joins a given number of rooms and observes the message flow in
those rooms. The rooms to join are picked based on a number of metrics:

- A configurable amount of randomly chosen rooms (1-fixed_share)
- The fixed share is chosen based on the following criteria:

    - Number of users (as found by Watcher or Inside Man)
    - Message rate (as found by Inside Man)
"""
import asyncio
import random
import re

from datetime import timedelta

import aioxmpp
import aioxmpp.service

from . import utils
UPDATE_DELAY = 30


def connect(connections, signal, handler):
    connections.append((signal, signal.connect(handler)))


class RoomHandler:
    MUCJID_RE = re.compile(
        r"(?P<scheme>xmpp:)?(?P<addr>[^?\s]+)(?P<query>\?join)?",
        re.I,
    )

    on_stopped = aioxmpp.callbacks.Signal()

    def __init__(self, loop, state, process_jid, room, logger):
        super().__init__()
        self._loop = loop
        self._state = state
        self.process_jid = process_jid
        self.room = room
        self.logger = logger
        self._last_message_ts = None

        self._queued_updates = {}

        self._connections = []

        connect(
            self._connections,
            self.room.on_message,
            self._on_message,
        )

        connect(
            self._connections,
            self.room.on_exit,
            self._on_exit,
        )

        connect(
            self._connections,
            self.room.on_failure,
            self._on_failure,
        )

        connect(
            self._connections,
            self.room.on_topic_changed,
            self._on_topic_changed,
        )

        connect(
            self._connections,
            self.room.on_join,
            self._on_join,
        )

        connect(
            self._connections,
            self.room.on_leave,
            self._on_leave,
        )

    def _queue_update(self, **kwargs):
        to_queue = not self._queued_updates
        self._queued_updates.update(kwargs)
        to_queue = to_queue and self._queued_updates

        if to_queue:
            self._loop.call_later(
                UPDATE_DELAY,
                self._execute_update,
            )

    def _execute_update(self):
        updates = self._queued_updates
        self._queued_updates = {}

        self.logger.debug("updating MUC %s: %r",
                          self.room.jid,
                          updates)
        if updates:
            self._state.update_muc_metadata(
                self.room.jid,
                **updates,
            )

    def _on_failure(self, exc, **kwargs):
        self.logger.warning(
            "failed to join MUC: %s", exc,
        )
        self._stop(exc, None)

    def _on_exit(self, muc_leave_mode=None, **kwargs):
        self._stop(None, muc_leave_mode)

    def _on_topic_changed(self, _, new_topic, **kwargs):
        self._queue_update(subject=new_topic.any())

    def _on_join(self, *args, **kwargs):
        # minus one for ourselves
        self._queue_update(nusers=len(self.room.members)-1)

    def _on_leave(self, *args, **kwargs):
        # minus two for the one who left and for ourselves
        self._queue_update(nusers=len(self.room.members)-2)

    def _stop(self, exc, leave_mode):
        for signal, token in self._connections:
            signal.disconnect(token)

        self.on_stopped(exc, leave_mode)

    def _extract_jids(self, text):
        jids = []
        for url in self.MUCJID_RE.finditer(text):
            score = 0
            url_info = url.groupdict()
            score += bool(url_info["scheme"])
            score += bool(url_info["query"])

            jid = aioxmpp.JID.fromstr(urllib.parse.unquote(url_info["addr"]))

            score += bool(jid.localpart)

            if not score:
                continue

            jids.append((score, jid))

        return jids

    def _handle_message(self, timestamp, msg):
        rounded_ts = timestamp.replace(minute=0, second=0, microsecond=0)
        if self._last_message_ts != rounded_ts:
            self._last_message_ts = rounded_ts
            self._queue_update(last_message_ts=rounded_ts)

        jids = []

        for text in msg.body.values():
            try:
                jids.extend(self._extract_jids(text))
            except:  # NOQA
                self.logger.warning("failed to process body",
                                    exc_info=True)

        for score, jid in jids:
            self.process_jid(timestamp, self.room.jid, jid, score)

    def _on_message(self, msg, member, source, **kwargs):
        if self.room.muc_state != aioxmpp.muc.RoomState.ACTIVE:
            return

        if not msg.body:
            return

        ts = datetime.utcnow()

        self._loop.call_soon(
            self._handle_message,
            ts,
            msg,
        )


class InsideMan(aioxmpp.service.Service,
                utils.MuchopperService,
                utils.RobustBackgroundJobService):

    ORDER_AFTER = [
        aioxmpp.MUCClient,
    ]

    def __init__(self, client, **kwargs):
        super().__init__(client, **kwargs)
        self._muc_svc = self.dependencies[aioxmpp.MUCClient]
        self.shuffle_interval = timedelta(hours=3)
        self._joined = {}
        self._loop = asyncio.get_event_loop()
        self.default_nickname = "muchopper"
        self.nrooms = 500
        self.fixed_share = 0.4
        self.minusers = 2

    async def _shuffle(self, state):
        self.logger.info("re-shuffling joined rooms")
        rooms = state.get_joinable_mucs_with_user_count(self.minusers)
        # sort by user count
        rooms.sort(key=lambda x: x[1], reverse=True)

        nfixed_rooms = min(round(self.fixed_share * self.nrooms),
                           len(rooms))
        nrandom_rooms = self.nrooms - nfixed_rooms

        self.logger.debug("%d fixed rooms, %d random rooms (out of %d)",
                          nfixed_rooms, nrandom_rooms, self.nrooms)

        fixed_rooms = [address
                       for address, nusers in rooms[:nfixed_rooms]
                       if nusers > 2]
        del rooms[:nfixed_rooms]

        # recalculate random share if there were not enough rooms with more
        # than one user
        nrandom_rooms = self.nrooms - len(fixed_rooms)

        random.shuffle(rooms)
        random_rooms = [address for address, _ in rooms[:nrandom_rooms]]

        current_rooms = set(self._joined.keys())
        next_rooms = set(fixed_rooms + random_rooms)

        to_join = next_rooms - current_rooms
        to_leave = current_rooms - next_rooms

        self.logger.debug("leaving %d rooms, joining %d rooms",
                          len(to_leave), len(to_join))

        for room in to_join:
            self.logger.debug("queue-ing join for %s", room)
            room, _ = self._muc_svc.join(
                room,
                self.default_nickname,
                history=aioxmpp.muc.xso.History(maxstanzas=0)
            )
            self._joined[room] = RoomHandler(
                self._loop,
                state,
                self._suggester,
                room,
                self.logger.getChild(str(room)),
            )
            state.mark_active(room)

        leave_tasks = []

        for room in to_leave:
            self.logger.debug("queue-ing leave for %s", room)
            handler = self._joined[room]
            leave_tasks.append(asyncio.ensure_future(
                handler.room.leave()
            ))

        if leave_tasks:
            try:
                await asyncio.wait(leave_tasks,
                                   timeout=120,
                                   return_when=asyncio.ALL_COMPLETED)
            except asyncio.TimeoutError:
                self.logger.debug(
                    "not all leave operations finished in time ... continuing "
                    "in background"
                )

        self.logger.debug("sleeping for %s until next reshuffle",
                          self.shuffle_interval)
        await asyncio.sleep(self.shuffle_interval.total_seconds())

    async def _background_job(self):
        state = await self._state_future
        while True:
            await self._shuffle(state)
            await asyncio.sleep(self.shuffle_interval.total_seconds())

    def _room_handler_stopped(self, jid, exc, leave_mode):
        self.logger.debug("got removed from room %s: %s/%s",
                          jid, exc, leave_mode)
        state.mark_inactive(room)
        handler = self._joined.pop(jid)
        if (isinstance(exc, aioxmpp.errors.XMPPAuthError) or
                leave_mode == aioxmpp.muc.LeaveMode.BANNED):
            # treat failure to join as banned
            self.logger.warning("got banned from %s (%s): deleting all data",
                                self.room.jid,
                                exc)
            self._state.cache_address_metadata(
                self.room.jid,
                utils.AddressMetadata(
                    is_reachable=True,
                    is_muc=True,
                    is_indexable_muc=False,
                    is_joinable_muc=False,
                    is_banned=True,
                ),
                utils.CACHE_TTL_BANNED,
            )
            self._state.delete_all_muc_data(self.room.jid)
        elif leave_mode == aioxmpp.muc.LeaveMode.KICKED:
            handler._queue_update({"was_kicked": True})
        elif exc is not None:
            # failed to join MUC for other reasons, treat as unreachable
            self._state.cache_address_metadata(
                self.room.jid,
                utils.AddressMetadata(
                    is_reachable=False,
                    is_muc=False,
                    is_indexable_muc=False,
                    is_joinable_muc=False,
                    is_banned=False,
                ),
                utils.CACHE_TTL_UNREACHABLE,
            )
        else:
            # got removed from MUC in another way, we will re-join later
            pass
        handler._execute_update()
