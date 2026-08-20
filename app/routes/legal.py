"""Gizlilik Politikası + Kullanım Koşulları — 2026-08-21 yayın-öncesi denetimde
bulunan eksik giderildi. Statik içerik, login GEREKMEZ (Play Store/App Store
incelemesi veya çıkışta bir kullanıcı da erişebilmeli) — bu yüzden
@login_required YOK, diğer route'ların aksine.
"""
from flask import render_template, session
from . import bp


@bp.route("/gizlilik")
def privacy():
    return render_template("legal.html", me=session.get("user"))
