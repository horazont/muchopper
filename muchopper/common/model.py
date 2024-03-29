import contextlib
import enum
import pathlib

import sqlalchemy

from sqlalchemy import (
    Column,
    Integer,
    DateTime,
    Unicode,
    ForeignKey,
    Boolean,
    Float,
    LargeBinary,
)
from sqlalchemy.orm import (
    relationship,
)
from sqlalchemy.ext.declarative import declarative_base

import aioxmpp


ABUSE_CONTACT_LENGTH_LIMIT = 1023


@contextlib.contextmanager
def session_scope(sessionmaker):
    """Provide a transactional scope around a series of operations."""
    session = sessionmaker()
    try:
        yield session
    except:  # NOQA
        session.rollback()
        raise
    finally:
        session.close()


def mkdir_exist_ok(path):
    try:
        path.mkdir(parents=True)
    except FileExistsError:
        if not path.is_dir():
            raise


def get_sqlite_engine(path: pathlib.Path) -> sqlalchemy.engine.Engine:
    mkdir_exist_ok(path.parent)
    engine = get_generic_engine("sqlite:///{}".format(path))

    # https://stackoverflow.com/questions/1654857/
    @sqlalchemy.event.listens_for(engine, "connect")
    def do_connect(dbapi_connection, connection_record):
        # disable pysqlite's emitting of the BEGIN statement entirely.
        # also stops it from emitting COMMIT before any DDL.
        dbapi_connection.isolation_level = None
        # holy smokes, enforce foreign keys!!k
        dbapi_connection.execute('pragma foreign_keys=ON')

    return engine


def get_generic_engine(uri: str) -> sqlalchemy.engine.Engine:
    return sqlalchemy.create_engine(uri)


class JID(sqlalchemy.types.TypeDecorator):
    cache_ok = True
    impl = sqlalchemy.types.VARCHAR

    def load_dialect_impl(self, dialect):
        return sqlalchemy.types.VARCHAR(3071)

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return aioxmpp.JID.fromstr(value, strict=False)


class SimpleEnum(sqlalchemy.types.TypeDecorator):
    impl = sqlalchemy.types.Unicode

    def __init__(self, enum_type):
        super().__init__()
        self.__enum_type = enum_type

    def load_dialect_impl(self, dialect):
        return sqlalchemy.types.Unicode(32)

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return value.value

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return self.__enum_type(value)


class AnonymityMode(enum.Enum):
    FULL = "full"
    SEMI = "semi"
    NONE = "none"


class Base(declarative_base()):
    __abstract__ = True
    __table_args__ = {}


class Domain(Base):
    __tablename__ = "domain"

    id_ = Column(
        "id",
        Integer(),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    domain = Column(
        "domain",
        sqlalchemy.types.VARCHAR(1023),
        unique=True,
        nullable=False,
    )

    last_seen = Column(
        "last_seen",
        DateTime(),
        nullable=True,
    )

    software_name = Column(
        "software_name",
        Unicode(128),
        nullable=True,
    )

    software_version = Column(
        "software_version",
        Unicode(128),
        nullable=True,
    )

    software_os = Column(
        "software_os",
        Unicode(128),
        nullable=True,
    )

    delisted = Column(
        "delisted",
        Boolean(),
        nullable=False,
        default=False,
    )


class DomainIdentity(Base):
    __tablename__ = "domain_identity"

    domain_id = Column(
        "domain_id",
        Integer(),
        ForeignKey(Domain.id_, ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
        nullable=False,
    )

    category = Column(
        "category",
        Unicode(64),
        primary_key=True,
        nullable=False,
    )

    type_ = Column(
        "type",
        Unicode(64),
        primary_key=True,
        nullable=False,
    )

    @classmethod
    def update_identities(cls, session, domain, identities):
        to_add = set(identities)

        for existing_item in session.query(cls).filter(
                cls.domain_id == domain.id_):
            key = existing_item.category, existing_item.type_
            try:
                to_add.remove(key)
            except KeyError:
                session.delete(existing_item)
                continue

        for category, type_ in to_add:
            item = cls()
            item.domain_id = domain.id_
            item.category = category
            item.type_ = type_
            session.add(item)


class DomainContact(Base):
    __tablename__ = "domain_contact"

    id_ = Column(
        "id",
        Integer(),
        primary_key=True,
        nullable=False,
        autoincrement=True,
    )

    domain_id = Column(
        "domain_id",
        Integer(),
        ForeignKey(Domain.id_, ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )

    role = Column(
        "role",
        Unicode(),
        nullable=False,
    )

    address = Column(
        "address",
        Unicode(),
        nullable=False,
    )

    @classmethod
    def update_abuse_contacts(cls, session, domain, contacts):
        to_add = set(contacts)

        for existing_item in session.query(cls).filter(
                cls.domain_id == domain.id_).filter(cls.role == "abuse"):
            try:
                to_add.remove(existing_item.address)
            except KeyError:
                session.delete(existing_item)
                continue

        for address in to_add:
            item = cls()
            item.domain_id = domain.id_
            item.role = "abuse"
            item.address = address
            session.add(item)


class Tag(Base):
    __tablename__ = "tag"

    key = Column(
        "key",
        Unicode(),
        nullable=False,
        primary_key=True,
    )


class MUC(Base):
    __tablename__ = "muc"

    address = Column(
        "address",
        JID(),
        primary_key=True,
        nullable=False,
    )

    service_domain_id = Column(
        "domain_id",
        Integer(),
        ForeignKey(Domain.id_),
        nullable=False,
    )

    last_seen = Column(
        "last_seen",
        DateTime(),
        nullable=True,
    )

    nusers = Column(
        "nusers",
        Integer(),
        nullable=True,
    )

    nusers_moving_average = Column(
        "nusers_moving_average",
        Float(),
        nullable=True,
    )

    moving_average_last_update = Column(
        "moving_average_last_update",
        DateTime(),
        nullable=True,
    )

    is_open = Column(
        "is_open",
        Boolean(),
        nullable=False,
    )

    is_hidden = Column(
        "is_hidden",
        Boolean(),
        nullable=False,
        default=False,
    )

    was_kicked = Column(
        "was_kicked",
        Boolean(),
        nullable=False,
    )

    anonymity_mode = Column(
        "anonymity_mode",
        SimpleEnum(AnonymityMode),
        nullable=True,
    )

    @classmethod
    def get(cls, session, address):
        try:
            return session.query(cls).filter(cls.address == address).one()
        except sqlalchemy.orm.exc.NoResultFound:
            return None


public_muc_tags = sqlalchemy.Table(
    "public_muc_tags",
    Base.metadata,
    sqlalchemy.Column(
        "tag",
        sqlalchemy.ForeignKey(
            "tag.key",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
    ),
    sqlalchemy.Column(
        "public_muc",
        sqlalchemy.ForeignKey(
            "public_muc.address",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
    ),
)


class PubliclyListedMUC(Base):
    __tablename__ = "public_muc"

    address = Column(
        "address",
        JID(),
        ForeignKey(MUC.address,
                   ondelete="CASCADE",
                   onupdate="CASCADE"),
        primary_key=True,
        nullable=False,
    )

    subject = Column(
        "subject",
        Unicode(),
        nullable=True,
    )

    name = Column(
        "name",
        Unicode(),
        nullable=True,
    )

    description = Column(
        "description",
        Unicode(),
        nullable=True,
    )

    language = Column(
        "language",
        Unicode(32),
        nullable=True,
    )

    http_logs_url = Column(
        "http_logs_url",
        Unicode(255),
        nullable=True,
    )

    web_chat_url = Column(
        "web_chat_url",
        Unicode(2047),
        nullable=True,
    )

    muc = relationship(MUC)
    tags = relationship(Tag, secondary=public_muc_tags)

    @classmethod
    def get(cls, session, address):
        try:
            return session.query(cls).filter(cls.address == address).one()
        except sqlalchemy.orm.exc.NoResultFound:
            return None


class Avatar(Base):
    __tablename__ = "avatar"

    address = Column(
        "address",
        JID(),
        ForeignKey(PubliclyListedMUC.address,
                   ondelete="CASCADE",
                   onupdate="CASCADE"),
        primary_key=True,
        nullable=False,
    )

    last_updated = Column(
        "last_updated",
        DateTime(),
        nullable=False,
    )

    mime_type = Column(
        "mime_type",
        Unicode(128),
        nullable=False,
    )

    data = Column(
        "data",
        LargeBinary(),
        nullable=False,
    )

    hash_ = Column(
        "hash",
        Unicode(64),
        nullable=False,
    )

    public_muc = relationship(PubliclyListedMUC)

    @classmethod
    def get(cls, session, address):
        try:
            return session.query(cls).filter(cls.address == address).one()
        except sqlalchemy.orm.exc.NoResultFound:
            return None
