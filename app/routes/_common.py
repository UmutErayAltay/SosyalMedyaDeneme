"""routes/ paketindeki tüm alt-modüllerin paylaştığı küçük yardımcılar.

Bu dosya sadece bp'ye route TANIMLAMAZ — sadece paylaşılan helper'ları
barındırır (bkz. `app/routes/__init__.py`'nin bunları neden re-export ettiği:
`hashtags.py` `_attach_post_metrics`'i döngüsel import'u önlemek için lazy
import ediyor, `from .routes import _attach_post_metrics` şeklinde — bu paket
haline gelince de aynı import yolu çalışmaya devam etmeli).
"""
from flask import session
from ..supabase_client import get_sb

PAGE_SIZE = 20


def _profile(username: str | None = None, uid: str | None = None) -> dict | None:
    """Verilen username veya id ile profil döndürür."""
    sb = get_sb()
    if uid:
        res = sb.table("profiles").select("*").eq("id", uid).execute()
    elif username:
        res = sb.table("profiles").select("*").eq("username", username).execute()
    else:
        return None
    return res.data[0] if res.data else None


def _my_id() -> str:
    return session["user"]["id"]


def _attach_post_metrics(sb, posts: list, me: str) -> None:
    """Postlara like_count / comment_count / liked_by_me / my_reaction /
    bookmarked_by_me ekler.

    Sayılar embedded count ile tek sorguda gelir; kullanıcıya özel alanlar için
    tüm postlar üzerinden tek birer IN sorgusu yapılır (N+1 önlenir).
    """
    post_ids = [p["id"] for p in posts]
    my_reactions: dict = {}
    my_bookmarks: set = set()
    if post_ids:
        my_reactions = {
            l["post_id"]: l.get("reaction_type") or "like"
            for l in sb.table("likes").select("post_id, reaction_type")
            .eq("user_id", me).in_("post_id", post_ids).execute().data
        }
        try:
            my_bookmarks = {
                b["post_id"] for b in sb.table("bookmarks").select("post_id")
                .eq("user_id", me).in_("post_id", post_ids).execute().data
            }
        except Exception:
            pass  # sql/migration_bookmarks.sql henüz uygulanmamışsa sessizce atla
    for p in posts:
        p["like_count"] = p["likes"][0]["count"] if p.get("likes") else 0
        p["comment_count"] = p["comments"][0]["count"] if p.get("comments") else 0
        p["liked_by_me"] = p["id"] in my_reactions
        p["my_reaction"] = my_reactions.get(p["id"])
        p["bookmarked_by_me"] = p["id"] in my_bookmarks
