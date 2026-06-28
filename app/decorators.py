"""Yetki decorator'ları."""
from functools import wraps
from flask import session, redirect, url_for, flash


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            flash("Bu sayfa için giriş yapmalısın.", "error")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped
