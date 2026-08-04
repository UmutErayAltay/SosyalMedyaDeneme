"""JSON REST API — Şikayet/raporlama, native Android.

app/reports.py create_report()'un BİREBİR mirror'ı: form yerine JSON body
kabul eder, redirect/flash yerine JSON döner. Admin paneli/rolü hâlâ YOK
(kaynak dosyadaki AYNI bilinçli karar, 2026-07-06) — sadece `reports`
tablosuna kayıt düşer.
"""
from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb

VALID_TARGET_TYPES = {"post", "comment", "user"}


@bp.route("/report", methods=["POST"])
@api_login_required
def api_create_report():
    """Bir post/yorum/kullanıcıyı şikayet eder — reports.py create_report()
    ile AYNI mantık (aynı hedefi 2. kez şikayet engeli, migration henüz
    uygulanmamışsa 503)."""
    sb = get_sb()
    me = request.api_user["id"]

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    target_type = data.get("target_type")
    target_id = data.get("target_id")

    if target_type not in VALID_TARGET_TYPES or not target_id:
        return jsonify(error="invalid_request"), 400

    try:
        existing = sb.table("reports").select("id").eq("reporter_id", me).eq(
            "target_type", target_type
        ).eq("target_id", target_id).execute().data
        if existing:
            return jsonify(error="already_reported"), 409
        sb.table("reports").insert({
            "reporter_id": me, "target_type": target_type, "target_id": target_id,
        }).execute()
    except Exception:
        return jsonify(error="unavailable"), 503

    return jsonify(ok=True)
