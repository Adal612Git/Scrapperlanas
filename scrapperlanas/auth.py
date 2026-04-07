from __future__ import annotations

from functools import wraps

import bcrypt
from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .db import get_db


bp = Blueprint("auth", __name__, url_prefix="/auth")


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return view(**kwargs)

    return wrapped_view


@bp.before_app_request
def load_logged_in_user() -> None:
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
        return

    g.user = get_db().execute(
        """
        SELECT u.*, p.keywords, p.sectors, p.min_budget, p.preferred_sources
        FROM users u
        LEFT JOIN user_profiles p ON p.user_id = u.id
        WHERE u.id = ?
        """,
        (user_id,),
    ).fetchone()


@bp.route("/register", methods=("GET", "POST"))
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        db = get_db()
        error = None

        if "@" not in email:
            error = "El email debe tener un formato valido."
        elif len(password) < 8:
            error = "La contrasena debe tener al menos 8 caracteres."
        elif password != confirm_password:
            error = "La confirmacion de contrasena no coincide."
        elif db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            error = "Ese email ya esta registrado."

        if error is None:
            password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            insert_query = "INSERT INTO users (email, password_hash) VALUES (?, ?)"
            if db.engine == "postgres":
                insert_query += " RETURNING id"

            cursor = db.execute(insert_query, (email, password_hash))
            user_id = cursor.fetchone()["id"] if db.engine == "postgres" else cursor.lastrowid
            db.execute(
                "INSERT INTO user_profiles (user_id) VALUES (?)",
                (user_id,),
            )
            db.commit()
            session.clear()
            session.permanent = True
            session["user_id"] = user_id
            flash("Cuenta creada. Bienvenido a Scrapperlanas.", "success")
            return redirect(url_for("main.dashboard"))

        flash(error, "error")

    return render_template("auth/register.html")


@bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,),
        ).fetchone()

        if user is None or not bcrypt.checkpw(
            password.encode("utf-8"),
            user["password_hash"].encode("utf-8"),
        ):
            flash("Credenciales invalidas.", "error")
        else:
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            flash("Sesion iniciada.", "success")
            return redirect(url_for("main.dashboard"))

    return render_template("auth/login.html")


@bp.route("/logout", methods=("GET", "POST"))
def logout():
    session.clear()
    flash("Sesion cerrada.", "success")
    return redirect(url_for("auth.login"))
