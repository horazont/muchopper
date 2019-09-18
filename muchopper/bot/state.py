import asyncio
import collections
import hashlib
import io
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


def merge(obj, attr, value):
    if value is UNCHANGED:
        return False
    if getattr(obj, attr) == value:
        return False
    setattr(obj, attr, value)
    return True


def process_text(text, length_soft_limit, length_hard_limit=None):
    length_hard_limit = length_hard_limit or length_soft_limit*2

    if len(text) > length_hard_limit:
        text = text[:length_hard_limit]

    text = " ".join(text.strip().split())
    if len(text) > length_soft_limit:
        text = text[:length_soft_limit-1] + "…"

    return text


def _scale_avatar(indata: bytes, mimetype: str) -> bytes:
    try:
        import PIL.Image
        import PIL.PngImagePlugin
        import PIL.JpegImagePlugin
    except ImportError:
        return None

    plugin = {
        "image/png": PIL.PngImagePlugin.Image,
        "image/jpeg": PIL.PngImagePlugin.Image,
    }.get(mimetype)

    if plugin is None:
        return None

    infile = io.BytesIO(indata)
    img = plugin.open(infile)
    if img.width > 64 or img.height > 64:
        aspect_ratio = img.width / img.height
        if aspect_ratio > 1:
            new_width = 64
            new_height = round(new_width / aspect_ratio)
        else:
            new_height = 64
            new_width = round(aspect_ratio * new_height)
        img = img.resize((new_width, new_height), PIL.Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="png", optimize=True)
    return out.getvalue(), "image/png"


async def scale_avatar(indata, mimetype, loop=None):
    if loop is None:
        loop = asyncio.get_event_loop()

    return await loop.run_in_executor(
        None,
        _scale_avatar,
        indata,
        mimetype,
    )


def hash_avatar(indata):
    hash_ = hashlib.sha256()
    hash_.update(indata)
    return hash_.hexdigest()


class State:
    on_muc_changed = aioxmpp.callbacks.Signal()
    on_muc_deleted = aioxmpp.callbacks.Signal()
    on_domain_changed = aioxmpp.callbacks.Signal()
    on_domain_deleted = aioxmpp.callbacks.Signal()

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

    def get_scannable_domains(self) -> typing.Sequence[str]:
        with model.session_scope(self._sessionmaker) as session:
            all_domains = [
                (domain, last_seen, type_ is not None and category is not None)
                for domain, last_seen, type_, category, in session.query(
                    model.Domain.domain,
                    model.Domain.last_seen,
                    model.DomainIdentity.type_,
                    model.DomainIdentity.category
                ).outerjoin(
                    # select all domains, but also extract a flag which
                    # indicates MUCiness of a domain
                    model.DomainIdentity,
                    sqlalchemy.sql.and_(
                        model.Domain.id_ == model.DomainIdentity.domain_id,
                        sqlalchemy.sql.and_(
                            model.DomainIdentity.category == "conference",
                            model.DomainIdentity.type_ == "text",
                        )
                    )
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

    def _require_domain(self, session, domain, seen=True):
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
                if seen:
                    dom.last_seen = datetime.utcnow()
                else:
                    dom.last_seen = None
                session.add(dom)
                return dom

    def require_domain(self, domain, seen=True):
        with model.session_scope(self._sessionmaker) as session:
            # we pass seen=False because we handle timestamping in any case
            result = self._require_domain(session, domain, seen=False)
            if seen:
                offset = seen if seen is not True else timedelta(0)
                result.last_seen = datetime.utcnow() + offset
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
                            anonymity_mode=UNCHANGED,
                            http_logs_url=UNCHANGED,
                            web_chat_url=UNCHANGED):
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

        has_changes = False

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
                has_changes = True
                session.add(muc)

            muc.last_seen = now
            has_changes = merge(muc, "is_open", is_open) or has_changes
            has_changes = (
                merge(muc, "anonymity_mode", anonymity_mode) or has_changes
            )
            muc.was_kicked = muc.was_kicked or was_kicked or False
            has_changes = merge(muc, "nusers", nusers) or has_changes
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
                    has_changes = True

                has_changes = \
                    merge(public_muc, "subject", subject) or has_changes
                has_changes = \
                    merge(public_muc, "name", name) or has_changes
                has_changes = \
                    merge(public_muc, "description", description) or has_changes
                has_changes = \
                    merge(public_muc, "language", language) or has_changes
                has_changes = \
                    merge(public_muc,
                          "http_logs_url",
                          http_logs_url) or has_changes
                has_changes = \
                    merge(public_muc,
                          "web_chat_url",
                          web_chat_url) or has_changes

            elif is_public is False:
                public_muc = model.PubliclyListedMUC.get(session, address)
                if public_muc is not None:
                    has_changes = True

                session.query(model.PubliclyListedMUC).filter(
                    model.PubliclyListedMUC.address == address
                ).delete()

            session.commit()

        if has_changes:
            self.on_muc_changed(address)

    async def update_muc_avatar(self, muc_address,
                                mimetype: str,
                                data: bytes):
        # We need to tread carefully here, because we cannot keep the session
        # when yielding. At the same time, we don’t want to waste time on
        # re-sizing the avatar if it is already in the database.
        #
        # So we risk the inconsistency here and fetch the hash of the avatar
        # first, do the resize only if necessary and then perform the update.

        if data is not None and len(data) > 1024*1024:
            self.logger.warning(
                "avatar for %s is larger than 1 MiB. dropping",
                muc_address,
            )
            data = None
            mimetype = None

        if data is not None and mimetype is not None:
            new_hash = hash_avatar(data)
            existing_hash = None
            with model.session_scope(self._sessionmaker) as session:
                public_muc = model.PubliclyListedMUC.get(session, muc_address)
                if public_muc is None:
                    return

                avatar = model.Avatar.get(session, muc_address)
                if avatar is not None:
                    existing_hash = avatar.hash_
                session.rollback()

            if existing_hash == new_hash:
                # skip update
                self.logger.info(
                    "skipping update of avatar for %s because it has not "
                    " changed",
                    muc_address,
                )
                return

            if mimetype != "image/svg+xml":
                data, mimetype = await scale_avatar(data, mimetype)
                if data is None:
                    self.logger.warning(
                        "failed to downscale/process avatar for %s; deleting",
                        muc_address,
                    )
            elif len(data) > 65535:
                self.logger.warning(
                    "SVG avatar for %s is larger than 64 kiB. dropping",
                    muc_address
                )
                data, mimetype = None, None

        with model.session_scope(self._sessionmaker) as session:
            if data is None or mimetype is None:
                session.query(model.Avatar).filter(
                    model.Avatar.address == muc_address,
                ).delete()
                session.commit()
                return

            public_muc = model.PubliclyListedMUC.get(session, muc_address)
            if public_muc is None:
                # drop update, the MUC is not publicly listed
                return

            avatar = model.Avatar.get(session, muc_address)
            if avatar is None:
                self.logger.debug(
                    "creating avatar for %s",
                    muc_address,
                )
                avatar = model.Avatar()
                avatar.address = muc_address
            else:
                self.logger.debug(
                    "updating avatar for %s",
                    muc_address,
                )

            avatar.last_updated = datetime.utcnow().replace(microsecond=0)
            avatar.data = data
            avatar.mime_type = mimetype
            avatar.hash_ = new_hash
            session.add(avatar)
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
