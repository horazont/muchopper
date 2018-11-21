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


def process_text(text, length_soft_limit, length_hard_limit=None):
    length_hard_limit = length_hard_limit or length_soft_limit*2

    if len(text) > length_hard_limit:
        text = text[:length_hard_limit]

    text = " ".join(text.strip().split())
    if len(text) > length_soft_limit:
        text = text[:length_soft_limit-1] + "…"

    return text


class State:
    def __init__(self,
                 engine,
                 logfile: pathlib.Path,
                 max_name_length: int,
                 max_description_length: int,
                 max_subject_length: int,
                 max_language_length: int):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self._mucs = {}
        self._engine = engine
        self._sessionmaker = sqlalchemy.orm.sessionmaker(bind=self._engine)
        self._address_metadata_cache = aioxmpp.cache.LRUDict()
        self._address_metadata_cache.maxsize = 512
        self._active_addresses = set()
        self._max_name_length = max_name_length
        self._max_description_length = max_description_length
        self._max_subject_length = max_subject_length
        self._max_language_length = max_language_length

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
                ).filter(
                    model.Domain.delisted != True
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
            return session.query(model.Domain).filter(
                model.Domain.domain == key
            ).one()
        except sqlalchemy.orm.exc.NoResultFound:
            with session.begin_nested():
                dom = model.Domain()
                dom.domain = key
                dom.last_seen = datetime.utcnow()
                session.add(dom)
                return dom

    def require_domain(self, domain):
        with model.session_scope(self._sessionmaker) as session:
            result = self._require_domain(session, domain)
            result.last_seen = datetime.utcnow()
            session.commit()
        return result

    def update_domain(self, domain,
                      identities=UNCHANGED,
                      software_version=UNCHANGED,
                      software_name=UNCHANGED,
                      software_os=UNCHANGED):
        with model.session_scope(self._sessionmaker) as session:
            domain_object = self._require_domain(session, domain)
            domain_object.last_seen = datetime.utcnow()
            if identities is not UNCHANGED:
                model.DomainIdentity.update_identities(
                    session,
                    domain_object,
                    identities,
                )

            if software_version is not UNCHANGED:
                domain_object.software_version = software_version

            if software_name is not UNCHANGED:
                domain_object.software_name = software_name

            if software_os is not UNCHANGED:
                domain_object.software_os = software_os

            session.commit()

    def expire_domains(self, threshold):
        with model.session_scope(self._sessionmaker) as session:
            session.query(model.Domain).filter(
                model.Domain.last_seen <= threshold,
                model.Domain.delisted != True
            ).delete()
            session.commit()

    def expire_mucs(self, threshold):
        with model.session_scope(self._sessionmaker) as session:
            session.query(model.MUC).filter(
                model.MUC.last_seen <= threshold
            ).delete()
            session.commit()

    def _prepare_text_update(self, value, max_length):
        if value is UNCHANGED:
            return value

        value = value or None
        if value is None:
            return value

        return process_text(value, max_length)

    def update_muc_metadata(self, address,
                            nusers=UNCHANGED,
                            is_open=UNCHANGED,
                            is_public=UNCHANGED,
                            subject=UNCHANGED,
                            name=UNCHANGED,
                            description=UNCHANGED,
                            language=UNCHANGED,
                            was_kicked=UNCHANGED,
                            is_saveable=UNCHANGED,
                            anonymity_mode=UNCHANGED):
        muc_created = False
        now = datetime.utcnow()

        self._address_metadata_cache.pop(address, None)

        if is_saveable is False:
            return self.delete_all_muc_data(address)

        description = self._prepare_text_update(
            description,
            self._max_description_length
        )
        # allow name to overflow if description is unset
        # in the UI, we’ll show the name at the place where the description
        # lives in those cases
        name = self._prepare_text_update(
            name,
            (self._max_name_length
             if description and description is not UNCHANGED
             else self._max_description_length)
        )
        subject = self._prepare_text_update(
            subject,
            self._max_subject_length
        )

        if language is not UNCHANGED:
            language = language or None
            if language is not None:
                language = language[:self._max_language_length]

        with model.session_scope(self._sessionmaker) as session:
            muc = model.MUC.get(session, address)
            if muc is None:
                domain = self._require_domain(session,
                                              address.domain)
                domain.last_seen = now
                muc = model.MUC()
                muc.service_domain_id = domain.id_
                muc.address = address
                muc.was_kicked = False
                muc_created = True
                session.add(muc)
            muc.last_seen = now
            muc.is_open = (
                (muc.is_open or False)
                if is_open is UNCHANGED
                else is_open
            )
            muc.anonymity_mode = (
                muc.anonymity_mode
                if anonymity_mode is UNCHANGED
                else anonymity_mode
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
                public_muc.language = \
                    (public_muc.language
                     if language is UNCHANGED
                     else language)

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

    def get_session(self):
        return model.session_scope(self._sessionmaker)
