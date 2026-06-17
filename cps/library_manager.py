# -*- coding: utf-8 -*-
# CWA Multi-Library — per-request library session management
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import re
import threading
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool

from . import logger

log = logger.create()

_engine_cache: dict = {}
_session_cache: dict = {}
# RLock allows get_session_for_library → get_engine_for_library re-entry on the same thread
_cache_lock = threading.RLock()


def _lcase(s):
    return s.lower() if s else s


def _make_title_sort(title_regex):
    pat = re.compile(title_regex, re.IGNORECASE)

    def _sort(title):
        if not title:
            return title
        m = pat.search(title)
        if m:
            prep = m.group(1)
            title = title[len(prep):].strip() + ', ' + prep
        return title

    return _sort


def get_engine_for_library(library_path: str):
    """Return a cached SQLAlchemy engine for library_path.

    Mirrors CalibreDB.setup_db: uses an in-memory SQLite primary DB with
    ATTACH for both metadata.db (as 'calibre') and app.db (as 'app_settings'),
    so all ORM models — including ub.ReadBook / ub.ArchivedBook — resolve
    their tables correctly across the attached schemas.
    """
    with _cache_lock:
        if library_path in _engine_cache:
            return _engine_cache[library_path]

        from . import ub as _ub
        from . import db as _db

        app_db_path = _ub.app_DB_path
        metadata_db = os.path.join(library_path, "metadata.db")

        engine = create_engine(
            'sqlite://',
            echo=False,
            isolation_level="SERIALIZABLE",
            connect_args={'check_same_thread': False, 'timeout': 30},
            poolclass=StaticPool,
        )

        with engine.begin() as conn:
            conn.execute(text(f"ATTACH DATABASE '{metadata_db}' AS calibre"))
            conn.execute(text(f"ATTACH DATABASE '{app_db_path}' AS app_settings"))
            try:
                if os.access(metadata_db, os.W_OK):
                    conn.execute(text("PRAGMA calibre.journal_mode=WAL"))
                    conn.execute(text("PRAGMA app_settings.journal_mode=WAL"))
            except Exception:
                pass

            # Register the same SQLite custom functions that CalibreDB.create_functions sets up
            try:
                title_regex = getattr(
                    getattr(_db.CalibreDB, 'config', None), 'config_title_regex', None
                )
                try:
                    dbapi_conn = conn.connection.driver_connection
                except AttributeError:
                    dbapi_conn = conn.connection.connection
                if title_regex:
                    dbapi_conn.create_function("title_sort", 1, _make_title_sort(title_regex))
                dbapi_conn.create_function("uuid4", 0, lambda: str(uuid4()))
                dbapi_conn.create_function("lower", 1, _lcase)
            except Exception as e:
                log.warning("Could not register custom functions for library %s: %s", library_path, e)

        _engine_cache[library_path] = engine
        log.debug("Created engine for library: %s", library_path)
        return engine


def get_session_for_library(library_path: str):
    """Return a cached scoped_session bound to the library at library_path."""
    with _cache_lock:
        if library_path not in _session_cache:
            engine = get_engine_for_library(library_path)
            factory = sessionmaker(autocommit=False, autoflush=True, bind=engine, future=True)
            _session_cache[library_path] = scoped_session(factory)
        return _session_cache[library_path]


def invalidate_engine(library_path: str):
    """Dispose and remove the cached engine for a library (e.g. after deactivation)."""
    with _cache_lock:
        scoped = _session_cache.pop(library_path, None)
        engine = _engine_cache.pop(library_path, None)
    if scoped is not None:
        try:
            scoped.remove()
        except Exception:
            pass
    if engine is not None:
        engine.dispose()
        log.info("Disposed engine for library: %s", library_path)
