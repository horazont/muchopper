import asyncio
import collections
import logging
import pathlib
import time
import typing

from datetime import datetime, timedelta

import sqlalchemy
import sqlalchemy.orm

import toml

import aioxmpp
import aioxmpp.cache
import aioxmpp.callbacks

from ..common import model
from . import utils


class _Unchanged:
    def __bool__(self):
        return False

    def __repr__(self):
        return "<unchanged>"


UNCHANGED = _Unchanged()
# after 24 updates, only 1% of the original value is left
NUSERS_MOVING_AVERAGE_FACTOR = 0.82
NUSERS_MOVING_AVERAGE_INTERVAL = timedelta(hours=0.95)


class State:
    def __init__(self,
                 engine,
                 logfile: pathlib.Path):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self._mucs = {}
        self._engine = engine
        self._sessionmaker = sqlalchemy.orm.sessionmaker(bind=self._engine)
        self._address_metadata_cache = aioxmpp.cache.LRUDict()
        self._address_metadata_cache.maxsize = 512
        self._active_addresses = set()

    def is_active(self, address: aioxmpp.JID) -> bool:
        return address in self._active_addresses

    def mark_active(self, address: aioxmpp.JID, is_active: bool = True):
        if not is_active:
            return self.mark_inactive(address)
        self._active_addresses.add(address)

    def mark_inactive(self, address: aioxmpp.JID):
        self._active_addresses.discard(address)

    def get_all_known_inactive_mucs(self) -> typing.Sequence[aioxmpp.JID]:
        with model.session_scope(self._sessionmaker) as session:
            all_jids = [
                jid
                for jid, in session.query(
                    model.MUC.address,
                )
                if not self.is_active(jid)
            ]
            session.rollback()

        return all_jids

    def get_all_domains(self) -> typing.Sequence[str]:
        with model.session_scope(self._sessionmaker) as session:
            all_domains = [
                domain
                for domain, in session.query(
                    model.Domain.domain,
                )
            ]
            session.rollback()

        return all_domains

    def get_joinable_mucs_with_user_count(self, minusers=0):
        def is_ok(address):
            metadata = self.get_address_metadata(address)
            if metadata is None:
                return True

            return (
                metadata.is_reachable and
                metadata.is_muc and
                metadata.is_joinable_muc and
                not metadata.is_banned
            )

        with model.session_scope(self._sessionmaker) as session:
            result = [
                (muc.address, muc.nusers)
                for muc in session.query(
                    model.MUC,
                ).filter(
                    model.MUC.is_open == True,  # NOQA
                    model.MUC.nusers >= minusers
                )
                if is_ok(muc.address)
            ]
            session.rollback()

        return result

    def get_address_metadata(self, address: aioxmpp.JID):
        with model.session_scope(self._sessionmaker) as session:
            try:
                muc, public_info = session.query(
                    model.MUC,
                    model.PubliclyListedMUC
                ).outerjoin(
                    model.PubliclyListedMUC
                ).filter(
                    model.MUC.address == address,
                ).one()
            except sqlalchemy.orm.exc.NoResultFound:
                pass
            else:
                return utils.AddressMetadata(
                    is_reachable=True,
                    is_muc=True,
                    is_joinable_muc=muc.is_open,
                    is_indexable_muc=public_info is not None,
                    is_banned=False
                )

        try:
            ttl, data = self._address_metadata_cache[address]
        except KeyError:
            return None

        now = time.monotonic()
        if now >= ttl:
            del self._address_metadata_cache[address]
            return None

        return data

    def cache_address_metadata(self,
                               address: aioxmpp.JID,
                               metadata, ttl):
        if metadata.is_joinable_muc or metadata.is_indexable_muc:
            # store useful muc metadata in db
            self.update_muc_metadata(
                address,
                is_open=metadata.is_joinable_muc,
                is_public=metadata.is_indexable_muc,
            )
            return

        if metadata.is_reachable:
            # if reachable, we know for sure whether it’s a MUC or not, so drop
            # the data if it exists
            self.delete_all_muc_data(address)

        if ((len(self._address_metadata_cache) ==
             self._address_metadata_cache.maxsize) and
                address not in self._address_metadata_cache):
            self.expire_metadata_cache()

        self._address_metadata_cache[address] = (time.monotonic() + ttl,
                                                 metadata)

    def expire_metadata_cache(self):
        now = time.monotonic()
        to_erase = [
            key
            for key, (ttl, _) in self._address_metadata_cache
            if ttl <= now
        ]
        for key in to_erase:
            del self._address_metadata_cache[key]

    def get_rejoinable_mucs(self) -> typing.Iterable[aioxmpp.JID]:
        items = []

        with model.session_scope(self._sessionmaker) as session:
            for muc in session.query(model.MUC):
                items.append(muc.address)

        return items

    def _require_domain(self, session, domain):
        if isinstance(domain, str):
            key = domain
        else:
            key = domain.domain

        try:
            with session.begin_nested():
                dom = model.Domain()
                dom.domain = key
                session.add(dom)
                return dom
        except sqlalchemy.exc.IntegrityError:
            # domain exists
            session.rollback()
            return session.query(model.Domain).filter(
                model.Domain.domain == key
            ).one()

    def require_domain(self, domain):
        with model.session_scope(self._sessionmaker) as session:
            result = self._require_domain(session, domain)
            session.commit()
        return result

    def update_muc_metadata(self, address,
                            nusers=UNCHANGED,
                            is_open=UNCHANGED,
                            is_public=UNCHANGED,
                            subject=UNCHANGED,
                            name=UNCHANGED,
                            description=UNCHANGED,
                            was_kicked=UNCHANGED):
        muc_created = False
        now = datetime.utcnow()

        with model.session_scope(self._sessionmaker) as session:
            muc = model.MUC.get(session, address)
            if muc is None:
                domain_id = self._require_domain(session,
                                                 address.domain).id_
                muc = model.MUC()
                muc.service_domain_id = domain_id
                muc.address = address
                muc.was_kicked = False
                muc_created = True
                session.add(muc)
            muc.is_open = (
                (muc.is_open or False)
                if is_open is UNCHANGED
                else is_open
            )
            muc.was_kicked = muc.was_kicked or was_kicked or False
            muc.nusers = muc.nusers if nusers is UNCHANGED else nusers
            if muc.nusers_moving_average is None:
                muc.nusers_moving_average = muc.nusers
                muc.moving_average_last_update = now
            if nusers is not UNCHANGED:
                if (muc.moving_average_last_update +
                        NUSERS_MOVING_AVERAGE_INTERVAL < now):
                    muc.nusers_moving_average = (
                        muc.nusers_moving_average *
                        NUSERS_MOVING_AVERAGE_FACTOR +
                        nusers * (1-NUSERS_MOVING_AVERAGE_FACTOR)
                    )
                    muc.moving_average_last_update = now

            if (is_public or
                    (is_public is UNCHANGED and
                     (subject or name or description))):
                public_muc = model.PubliclyListedMUC.get(session, address)
                if public_muc is None:
                    public_muc = model.PubliclyListedMUC()
                    public_muc.address = address
                    session.add(public_muc)
                public_muc.subject = \
                    public_muc.subject if subject is UNCHANGED else subject
                public_muc.name = \
                    public_muc.name if name is UNCHANGED else name
                public_muc.description = \
                    (public_muc.description
                     if description is UNCHANGED
                     else description)

            elif is_public is False:
                session.query(model.PubliclyListedMUC).filter(
                    model.PubliclyListedMUC.address == address
                ).delete()

            session.commit()

    def store_referral(self, from_address, to_address, *,
                       timestamp=None):
        timestamp = timestamp or datetime.utcnow()

        with model.session_scope(self._sessionmaker) as session:
            from_muc = model.PubliclyListedMUC.get(session, from_address)
            if from_muc is None:
                session.rollback()
                return

            to_muc = model.PubliclyListedMUC.get(session, to_address)
            if to_muc is None:
                session.rollback()
                return

            referral = model.MUCReferral.get(session, from_address, to_address)
            if referral is None:
                referral = model.MUCReferral()
                referral.from_address = from_address
                referral.to_address = to_address
                referral.count = 0
            referral.count += 1
            referral.last_referral_ts = timestamp
            session.add(referral)
            session.commit()

    def delete_all_muc_data(self, address):
        with model.session_scope(self._sessionmaker) as session:
            muc = session.query(model.MUC).filter(
                model.MUC.address == address,
            ).delete()
            session.commit()
