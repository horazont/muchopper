import contextlib
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
)
from sqlalchemy.orm import (
    relationship,
)
from sqlalchemy.ext.declarative import declarative_base

import aioxmpp


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

    @sqlalchemy.event.listens_for(engine, "begin")
    def do_begin(conn):
        # emit our own BEGIN
        conn.execute("BEGIN")

    return engine


def get_generic_engine(uri: str) -> sqlalchemy.engine.Engine:
    return sqlalchemy.create_engine(uri)


class JID(sqlalchemy.types.TypeDecorator):
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
        return aioxmpp.JID.fromstr(value)


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

    @classmethod
    def get(cls, session, address):
        try:
            return session.query(cls).filter(cls.address == address).one()
        except sqlalchemy.orm.exc.NoResultFound:
            return None


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

    muc = relationship(MUC)

    @classmethod
    def get(cls, session, address):
        try:
            return session.query(cls).filter(cls.address == address).one()
        except sqlalchemy.orm.exc.NoResultFound:
            return None
