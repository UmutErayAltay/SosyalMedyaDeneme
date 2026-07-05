"""Admin paneli: şikayet/rapor yönetimi + kullanıcı yönetimi (admin/ban).

Erişim `@admin_required` (decorators.py) ile korunur — `profiles.is_admin`
her istekte DB'den TAZE okunur (yetki geri alınırsa anında etkili olsun diye,
bkz. decorators.py docstring'i). Navbar linki ise performans için session'da
cache'lenen bayrağa göre gösterilir/gizlenir (bkz. auth.py `_save_session`).

sql/migration_admin_panel.sql henüz uygulanmamışsa `is_admin`/`is_banned`/
`reports.status` kolonları yoktur — bu durumda `admin_required` güvenli
tarafta kalır (erişimi KAPATIR, açmaz).
"""
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash
from .decorators import login_required, admin_required
from .supabase_client import get_sb, retry_on_connection_error

bp = Blueprint("admin", __name__)


def _enrich_reports(sb, reports: list) -> list:
    """Her rapora, hedefin (post/yorum/kullanıcı) küçük bir önizlemesini +
    raporlayanın kullanıcı adını ekler. Tüm ID'ler tek birer IN sorgusuyla
    toplu çekilir (N+1 önlenir — bkz. _attach_post_metrics ile aynı desen)."""
    post_ids = [r["target_id"] for r in reports if r["target_type"] == "post"]
    comment_ids = [r["target_id"] for r in reports if r["target_type"] == "comment"]
    user_ids = [r["target_id"] for r in reports if r["target_type"] == "user"]
    reporter_ids = list({r["reporter_id"] for r in reports})

    posts = {}
    if post_ids:
        rows = sb.table("posts").select(
            "id, content, user_id, profiles!posts_user_id_fkey(username)"
        ).in_("id", post_ids).execute().data
        posts = {r["id"]: r for r in rows}

    comments = {}
    if comment_ids:
        rows = sb.table("comments").select(
            "id, content, post_id, user_id, profiles!comments_user_id_fkey(username)"
        ).in_("id", comment_ids).execute().data
        comments = {r["id"]: r for r in rows}

    target_users = {}
    if user_ids:
        rows = sb.table("profiles").select("id, username, avatar_url").in_("id", user_ids).execute().data
        target_users = {r["id"]: r for r in rows}

    reporters = {}
    if reporter_ids:
        rows = sb.table("profiles").select("id, username").in_("id", reporter_ids).execute().data
        reporters = {r["id"]: r for r in rows}

    for r in reports:
        r["reporter"] = reporters.get(r["reporter_id"])
        if r["target_type"] == "post":
            r["target"] = posts.get(r["target_id"])
        elif r["target_type"] == "comment":
            r["target"] = comments.get(r["target_id"])
        else:
            r["target"] = target_users.get(r["target_id"])
    return reports


@bp.route("/")
@login_required
@admin_required
@retry_on_connection_error
def dashboard():
    sb = get_sb()
    stats = {
        "users": sb.table("profiles").select("id", count="exact", head=True).execute().count or 0,
        "posts": sb.table("posts").select("id", count="exact", head=True).execute().count or 0,
        "comments": sb.table("comments").select("id", count="exact", head=True).execute().count or 0,
    }
    try:
        stats["pending_reports"] = sb.table("reports").select(
            "id", count="exact", head=True
        ).eq("status", "pending").execute().count or 0
        stats["banned_users"] = sb.table("profiles").select(
            "id", count="exact", head=True
        ).eq("is_banned", True).execute().count or 0
    except Exception:
        stats["pending_reports"] = 0
        stats["banned_users"] = 0
    return render_template("admin/dashboard.html", stats=stats, me=session.get("user"))


@bp.route("/reports")
@login_required
@admin_required
@retry_on_connection_error
def reports_list():
    sb = get_sb()
    status = request.args.get("status", "pending")
    query = sb.table("reports").select("*").order("created_at", desc=True)
    if status != "all":
        query = query.eq("status", status)
    reports = query.execute().data
    reports = _enrich_reports(sb, reports)
    return render_template("admin/reports.html", reports=reports, status=status, me=session.get("user"))


@bp.route("/reports/<report_id>/resolve", methods=["POST"])
@login_required
@admin_required
@retry_on_connection_error
def resolve_report(report_id):
    sb = get_sb()
    me = session["user"]["id"]
    new_status = request.form.get("status")
    if new_status not in ("reviewed", "dismissed"):
        abort(400)
    sb.table("reports").update({
        "status": new_status, "resolved_by": me,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", report_id).execute()
    flash("Rapor güncellendi.", "success")
    return redirect(url_for("admin.reports_list", status=request.form.get("current_status", "pending")))


@bp.route("/reports/<report_id>/delete-target", methods=["POST"])
@login_required
@admin_required
@retry_on_connection_error
def delete_report_target(report_id):
    """Raporlanan post/yorumu doğrudan siler (post sahibi olmasan da) ve
    raporu 'reviewed' olarak işaretler. Kullanıcı hedefli raporlarda hesap
    SİLİNMEZ (geri alınamaz, çok riskli) — onun yerine kullanıcı listesinden
    yasaklama (ban) kullanılır."""
    sb = get_sb()
    me = session["user"]["id"]
    report = sb.table("reports").select("*").eq("id", report_id).execute().data
    if not report:
        abort(404)
    report = report[0]

    if report["target_type"] == "post":
        sb.table("posts").delete().eq("id", report["target_id"]).execute()
    elif report["target_type"] == "comment":
        sb.table("comments").delete().eq("id", report["target_id"]).execute()
    else:
        flash("Kullanıcı hesapları buradan silinemez — kullanıcıyı yasaklamak için Kullanıcılar sayfasını kullan.", "error")
        return redirect(url_for("admin.reports_list", status=request.args.get("status", "pending")))

    sb.table("reports").update({
        "status": "reviewed", "resolved_by": me,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", report_id).execute()
    flash("İçerik silindi, rapor çözümlendi olarak işaretlendi.", "success")
    return redirect(url_for("admin.reports_list"))


@bp.route("/users")
@login_required
@admin_required
@retry_on_connection_error
def users_list():
    sb = get_sb()
    q = request.args.get("q", "").strip()
    query = sb.table("profiles").select(
        "id, username, avatar_url, is_admin, is_banned, created_at"
    ).order("created_at", desc=True)
    if q:
        query = query.ilike("username", f"%{q}%")
    users = query.execute().data

    user_ids = [u["id"] for u in users]
    post_counts: dict = {}
    report_counts: dict = {}
    if user_ids:
        posts = sb.table("posts").select("user_id").in_("user_id", user_ids).execute().data
        for p in posts:
            post_counts[p["user_id"]] = post_counts.get(p["user_id"], 0) + 1
        try:
            reports_against = sb.table("reports").select("target_id").eq(
                "target_type", "user"
            ).in_("target_id", user_ids).execute().data
            for r in reports_against:
                report_counts[r["target_id"]] = report_counts.get(r["target_id"], 0) + 1
        except Exception:
            pass

    for u in users:
        u["post_count"] = post_counts.get(u["id"], 0)
        u["report_count"] = report_counts.get(u["id"], 0)

    return render_template("admin/users.html", users=users, q=q, me=session.get("user"))


@bp.route("/users/<user_id>/toggle-admin", methods=["POST"])
@login_required
@admin_required
@retry_on_connection_error
def toggle_admin(user_id):
    sb = get_sb()
    me = session["user"]["id"]
    if user_id == me:
        flash("Kendi admin yetkini kaldıramazsın.", "error")
        return redirect(url_for("admin.users_list"))

    prof = sb.table("profiles").select("is_admin, username").eq("id", user_id).execute().data
    if not prof:
        abort(404)
    new_val = not prof[0]["is_admin"]
    sb.table("profiles").update({"is_admin": new_val}).eq("id", user_id).execute()
    flash(f"{prof[0]['username']} artık {'admin' if new_val else 'admin değil'}.", "success")
    return redirect(url_for("admin.users_list"))


@bp.route("/users/<user_id>/toggle-ban", methods=["POST"])
@login_required
@admin_required
@retry_on_connection_error
def toggle_ban(user_id):
    """Yasaklama SADECE bir sonraki giriş denemesini engeller (bkz. auth.py
    _save_session) — o an açık olan bir oturumu anında sonlandırmaz (Flask'ın
    varsayılan imzalı-çerez session'ı sunucu tarafında iptal edilemez).
    Küçük ölçekli bir arkadaş grubu uygulaması için bu kabul edilebilir bir
    sınır; anlık zorla çıkış gerekiyorsa server-side session store gerekirdi."""
    sb = get_sb()
    me = session["user"]["id"]
    if user_id == me:
        flash("Kendini yasaklayamazsın.", "error")
        return redirect(url_for("admin.users_list"))

    prof = sb.table("profiles").select("is_banned, username").eq("id", user_id).execute().data
    if not prof:
        abort(404)
    new_val = not prof[0]["is_banned"]
    sb.table("profiles").update({"is_banned": new_val}).eq("id", user_id).execute()
    flash(f"{prof[0]['username']} {'yasaklandı' if new_val else 'yasağı kaldırıldı'}.", "success")
    return redirect(url_for("admin.users_list"))
