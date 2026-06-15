# -*- coding: utf-8 -*-
# CWA Multi-Library — per-request library session management
# SPDX-License-Identifier: GPL-3.0-or-later

import threading

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, scoped_session

from . import logger

log = logger.create()

_engine_cache: dict = {}
_engine_cache_lock = threading.Lock()


def _set_sqlite_pragmas(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def get_engine_for_library(library_path: str):
    """Return a cached SQLAlchemy engine for the given library path."""
    with _engine_cache_lock:
        if library_path not in _engine_cache:
            db_url = f"sqlite:///{library_path}/metadata.db"
            engine = create_engine(
                db_url,
                connect_args={"check_same_thread": False, "timeout": 30},
                echo=False,
            )
            event.listen(engine, "connect", _set_sqlite_pragmas)
            _engine_cache[library_path] = engine
            log.debug("Created engine for library: %s", library_path)
        return _engine_cache[library_path]


def get_session_for_library(library_path: str):
    """Return a scoped_session bound to the library at library_path."""
    engine = get_engine_for_library(library_path)
    factory = sessionmaker(bind=engine)
    return scoped_session(factory)


def invalidate_engine(library_path: str):
    """Dispose and remove the cached engine for a library (e.g. after removal)."""
    with _engine_cache_lock:
        engine = _engine_cache.pop(library_path, None)
    if engine:
        engine.dispose()
        log.info("Disposed engine for library: %s", library_path)
