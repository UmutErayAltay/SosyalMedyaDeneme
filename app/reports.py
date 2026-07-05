"""Şikayet/raporlama: post/yorum/kullanıcı için basit kayıt.

Kullanıcı isteğiyle (2026-07-06) ŞU AN İÇİN görüntüleyecek bir admin paneli/
rolü YOK — bu bilinçli, en düşük kapsamlı bir karar (projede henüz bir admin
rolü kavramı yoktu, bunu tanımlamak ayrı bir karar gerektirirdi). Şikayetler
sadece `reports` tablosuna kaydedilir; ileride bir moderasyon arayüzü
eklenmek istenirse tablo zaten hazır olacak.
"""
from flask import Blueprint, request, redirect, url_for, session, flash
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error

bp = Blueprint("reports", __name__)

VALID_TARGET_TYPES = {"post", "comment", "user"}


@bp.route("/report", methods=["POST"])
@login_required
@retry_on_connection_error
def create_report():
    sb = get_sb()
    me = session["user"]["id"]

    target_type = request.form.get("target_type")
    target_id = request.form.get("target_id")

    if target_type not in VALID_TARGET_TYPES or not target_id:
        flash("Geçersiz şikayet isteği.", "error")
        return redirect(request.referrer or url_for("routes.feed"))

    try:
        existing = sb.table("reports").select("id").eq("reporter_id", me).eq(
            "target_type", target_type
        ).eq("target_id", target_id).execute().data
        if existing:
            flash("Bu içeriği zaten şikayet ettin.", "error")
        else:
            sb.table("reports").insert({
                "reporter_id": me, "target_type": target_type, "target_id": target_id,
            }).execute()
            flash("Şikayetin alındı, teşekkürler.", "success")
    except Exception:
        flash("Şikayet özelliği henüz aktif değil (migration uygulanmamış).", "error")

    return redirect(request.referrer or url_for("routes.feed"))
