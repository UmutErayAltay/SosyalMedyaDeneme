"""Kullanıcı engelleme: iki yönlü karşılıklı görünmezlik + takip/mesaj engeli.

sql/migration_blocks.sql henüz uygulanmamışsa `blocks` tablosu yoktur — bu
modüldeki tüm okuma yardımcıları try/except ile korunur ve "kimse engellenmemiş
gibi" davranır (graceful degradation, bookmarks/hashtags ile aynı desen).
"""
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error

bp = Blueprint("blocks", __name__)


def blocked_user_ids(sb, me: str) -> set:
    """me'nin engellediği + me'yi engelleyen kullanıcıların id kümesi.

    Engelleme karşılıklı görünmezlik demektir: feed/profil/arama/hashtag'te
    içerik süzerken bu küme "yazarı bu kümede olan postu gösterme" anlamına gelir.
    """
    try:
        out = sb.table("blocks").select("blocked_id").eq("blocker_id", me).execute().data
        inn = sb.table("blocks").select("blocker_id").eq("blocked_id", me).execute().data
        return {r["blocked_id"] for r in out} | {r["blocker_id"] for r in inn}
    except Exception:
        return set()


def has_blocked(sb, blocker: str, blocked: str) -> bool:
    """`blocker`, `blocked`'ı engellemiş mi? (TEK yönlü kontrol)

    profile() gibi yerlerde "onlar beni engellemişse profili hiç gösterme
    (enumeration önleme), ama BEN onları engellemişsem profili göster (engeli
    kaldırabilmem için) sadece içeriği gizle" ayrımı için kullanılır.
    """
    try:
        rows = sb.table("blocks").select("blocker_id").eq(
            "blocker_id", blocker
        ).eq("blocked_id", blocked).execute().data
        return bool(rows)
    except Exception:
        return False


def is_blocked_either_way(sb, a: str, b: str) -> bool:
    """a ve b arasında HANGİ yönde olursa olsun bir engelleme var mı?"""
    try:
        rows = sb.table("blocks").select("blocker_id").or_(
            f"and(blocker_id.eq.{a},blocked_id.eq.{b}),and(blocker_id.eq.{b},blocked_id.eq.{a})"
        ).execute().data
        return bool(rows)
    except Exception:
        return False


def filter_not_blocked(posts: list, blocked_ids: set) -> list:
    """Zaten çekilmiş bir post listesinden engellenen/engelleyen yazarların postlarını çıkarır."""
    if not blocked_ids:
        return posts
    return [p for p in posts if p.get("user_id") not in blocked_ids]


@bp.route("/block/<username>", methods=["POST"])
@login_required
@retry_on_connection_error
def toggle_block(username):
    sb = get_sb()
    me = session["user"]["id"]

    target = sb.table("profiles").select("id").eq("username", username).execute().data
    if not target:
        abort(404)
    target_id = target[0]["id"]
    if target_id == me:
        flash("Kendini engelleyemezsin.", "error")
        return redirect(url_for("routes.profile", username=username))

    try:
        existing = sb.table("blocks").select().eq("blocker_id", me).eq(
            "blocked_id", target_id
        ).execute().data
        if existing:
            sb.table("blocks").delete().eq("blocker_id", me).eq("blocked_id", target_id).execute()
            flash(f"{username} engeli kaldırıldı.", "success")
        else:
            sb.table("blocks").insert({"blocker_id": me, "blocked_id": target_id}).execute()
            # Engelleyince karşılıklı takip ilişkisi de kopar (her iki yönde) —
            # engellenen biri "takipçi" gibi görünmeye devam etmemeli.
            sb.table("follows").delete().eq("follower_id", me).eq("following_id", target_id).execute()
            sb.table("follows").delete().eq("follower_id", target_id).eq("following_id", me).execute()
            flash(f"{username} engellendi.", "success")
    except Exception:
        flash("Engelleme özelliği henüz aktif değil (migration uygulanmamış).", "error")

    return redirect(url_for("routes.profile", username=username))


@bp.route("/blocked")
@login_required
@retry_on_connection_error
def blocked_list():
    sb = get_sb()
    me = session["user"]["id"]
    users = []
    try:
        rows = sb.table("blocks").select(
            "profiles!blocks_blocked_id_fkey(id, username, avatar_url, full_name)"
        ).eq("blocker_id", me).order("created_at", desc=True).execute().data
        users = [r["profiles"] for r in rows if r.get("profiles")]
    except Exception:
        pass
    return render_template("blocked_list.html", users=users, me=session.get("user"))
