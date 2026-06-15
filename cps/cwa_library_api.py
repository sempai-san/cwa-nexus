# -*- coding: utf-8 -*-
# CWA Multi-Library — admin + user API endpoints
# SPDX-License-Identifier: GPL-3.0-or-later

import os

from flask import Blueprint, abort, g, jsonify, redirect, request, session, url_for
from sqlalchemy import exc

from . import logger, ub
from .cw_login import current_user, login_required

log = logger.create()

library_api = Blueprint("library_api", __name__)


def _admin_required(f):
    """Decorator: abort 403 unless the current user is an admin."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.role_admin():
            abort(403)
        return f(*args, **kwargs)

    return decorated


# ── Admin endpoints ───────────────────────────────────────────────────────────

@library_api.route("/admin/libraries", methods=["GET"])
@login_required
@_admin_required
def list_libraries():
    libs = ub.session.query(ub.CalibreLibrary).order_by(ub.CalibreLibrary.name).all()
    return jsonify([
        {
            "id": lib.id,
            "name": lib.name,
            "path": lib.path,
            "description": lib.description,
            "is_active": lib.is_active,
        }
        for lib in libs
    ])


@library_api.route("/admin/libraries", methods=["POST"])
@login_required
@_admin_required
def add_library():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    path = (data.get("path") or "").strip()
    description = (data.get("description") or "").strip()

    if not name or not path:
        return jsonify({"error": "name and path are required"}), 400

    metadata_db = os.path.join(path, "metadata.db")
    if not os.path.isfile(metadata_db):
        return jsonify({"error": f"metadata.db not found at {metadata_db}"}), 400

    if ub.session.query(ub.CalibreLibrary).filter_by(path=path).first():
        return jsonify({"error": "A library with this path already exists"}), 409

    lib = ub.CalibreLibrary(name=name, path=path, description=description)
    ub.session.add(lib)
    try:
        ub.session.commit()
    except exc.SQLAlchemyError as e:
        ub.session.rollback()
        log.error("Failed to add library: %s", e)
        return jsonify({"error": "Database error"}), 500

    log.info("Admin %s added library '%s' at %s", current_user.name, name, path)
    return jsonify({"id": lib.id, "name": lib.name, "path": lib.path}), 201


@library_api.route("/admin/libraries/<int:lib_id>", methods=["DELETE"])
@login_required
@_admin_required
def remove_library(lib_id):
    lib = ub.session.get(ub.CalibreLibrary, lib_id)
    if not lib:
        abort(404)
    lib.is_active = False
    from .library_manager import invalidate_engine
    try:
        invalidate_engine(lib.path)
    except Exception:
        pass
    try:
        ub.session.commit()
    except exc.SQLAlchemyError as e:
        ub.session.rollback()
        log.error("Failed to deactivate library %d: %s", lib_id, e)
        return jsonify({"error": "Database error"}), 500
    log.info("Admin %s deactivated library %d (%s)", current_user.name, lib_id, lib.name)
    return jsonify({"status": "deactivated", "id": lib_id})


@library_api.route("/admin/users/<int:user_id>/libraries", methods=["PUT"])
@login_required
@_admin_required
def set_user_library_access(user_id):
    data = request.get_json(silent=True) or {}
    library_ids = data.get("library_ids", [])
    default_library_id = data.get("default_library_id")

    user = ub.session.get(ub.User, user_id)
    if not user:
        abort(404)

    try:
        ub.session.query(ub.UserLibraryAccess).filter_by(user_id=user_id).delete()
        for lid in library_ids:
            lib = ub.session.get(ub.CalibreLibrary, lid)
            if lib:
                access = ub.UserLibraryAccess(
                    user_id=user_id,
                    library_id=lid,
                    is_default=(lid == default_library_id),
                )
                ub.session.add(access)
        ub.session.commit()
    except exc.SQLAlchemyError as e:
        ub.session.rollback()
        log.error("Failed to update library access for user %d: %s", user_id, e)
        return jsonify({"error": "Database error"}), 500

    log.info("Admin %s updated library access for user %d", current_user.name, user_id)
    return jsonify({"status": "updated", "user_id": user_id})


@library_api.route("/admin/libraries/ui")
@login_required
@_admin_required
def admin_libraries_ui():
    from flask import render_template
    all_users = ub.session.query(ub.User).filter(ub.User.name != "Guest").order_by(ub.User.name).all()
    all_libs = ub.session.query(ub.CalibreLibrary).order_by(ub.CalibreLibrary.name).all()
    return render_template(
        "cwa_admin_libraries.html",
        title="Libraries",
        all_libraries=all_libs,
        all_users=all_users,
        active_page="admin",
    )


# ── User endpoints ────────────────────────────────────────────────────────────

@library_api.route("/library/switch/<int:lib_id>", methods=["POST", "GET"])
@login_required
def switch_library(lib_id):
    access = (
        ub.session.query(ub.UserLibraryAccess)
        .filter_by(user_id=current_user.id, library_id=lib_id)
        .first()
    )
    if not access:
        abort(403)
    lib = ub.session.get(ub.CalibreLibrary, lib_id)
    if not lib or not lib.is_active:
        abort(404)
    session["active_library_id"] = lib_id
    referrer = request.referrer or url_for("web.index")
    return redirect(referrer)


@library_api.route("/library/available", methods=["GET"])
@login_required
def available_libraries():
    accesses = (
        ub.session.query(ub.UserLibraryAccess)
        .filter_by(user_id=current_user.id)
        .join(ub.CalibreLibrary)
        .filter(ub.CalibreLibrary.is_active == True)
        .all()
    )
    active_id = session.get("active_library_id")
    return jsonify([
        {
            "id": a.library.id,
            "name": a.library.name,
            "is_default": a.is_default,
            "is_active": a.library.id == active_id,
        }
        for a in accesses
    ])
