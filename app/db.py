"""Engine and session setup.

Every service operation runs in a single transaction. To make the
read-check-write sequence (count seats, then insert) atomic:

* SQLite: each transaction is opened with ``BEGIN IMMEDIATE``, which takes the
  database write lock up front, so concurrent writers are serialized and wait
  (up to ``timeout`` seconds) instead of interleaving.
* Other backends (e.g. PostgreSQL): the service locks the offering row with
  ``SELECT ... FOR UPDATE``, which serializes operations per offering.
"""

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def create_db_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return create_engine(database_url, pool_pre_ping=True)

    if url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        # Let SQLAlchemy emit BEGIN itself instead of pysqlite's implicit handling.
        dbapi_conn.isolation_level = None
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def create_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    from app import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(engine)
