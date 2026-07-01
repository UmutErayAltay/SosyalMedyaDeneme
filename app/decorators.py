"""Yetki decorator'ları."""
from functools import wraps
from flask import session, redirect, url_for


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        # Oturum yoksa sessizce login'e yönlendir.
        # Flash mesajı BIRAKMİYORUZ: kullanıcı sadece ana sayfayı ziyaret etmiş olabilir,
        # "giriş yapmalısın" uyarısı burada gereksiz ve rahatsız edicidir.
        # Gerçek giriş hatası auth.py login() route'unda yakalanır.
        if not session.get("user"):
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped
