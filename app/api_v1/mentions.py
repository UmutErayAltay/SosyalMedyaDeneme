"""JSON REST API — @etiketleme otomatik tamamlama (native Android).

app/social.py search_mentions()'ın BİREBİR mirror'ı. Ayrı bir dosyada
tutulmasının nedeni: interactions.py beğeni/yorum/post-oluşturma ile dolu,
"mention arama" post/yorum yaşam döngüsünden bağımsız bir konu — posts.py /
reposts.py ile AYNI dosya-başına-konu deseni.

URL şekli: web'deki `/mentions/search` çoğul formu KORUNUR, sadece /api/v1
prefix'i eklenir (api_v1'in geri kalanı da `/posts/`, `/stickers/` gibi çoğul).
"""
from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb
from ..blocks import blocked_user_ids


@bp.route("/mentions/search")
@api_login_required
def api_search_mentions():
    """Prefix eşleşen (case-insensitive) kullanıcı adları, takip ilişkisine göre
    sıralı döner (en olası önce) — social.py search_mentions() ile AYNI kural:
    karşılıklı takip > ben takip ediyorum > beni takip ediyor > diğerleri, sonra
    alfabetik; en fazla 3 sonuç. İki yönlü engellenenler ve kendim hiç dönmem.
    """
    sb = get_sb()
    me = request.api_user["id"]
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify(users=[])

    blocked = blocked_user_ids(sb, me)

    try:
        candidates = sb.table("profiles").select("id, username, avatar_url").ilike(
            "username", q + "%"
        ).eq("is_banned", False).limit(20).execute().data
    except Exception:
        candidates = []

    candidates = [c for c in candidates if c["id"] != me and c["id"] not in blocked]
    if not candidates:
        return jsonify(users=[])

    candidate_ids = [c["id"] for c in candidates]

    # Takip ilişkisi: iki toplu sorgu (N+1 yasak) — adaylar arasında ben
    # kimi takip ediyorum + kim beni takip ediyor.
    following_ids = set()
    follower_ids = set()
    try:
        following_ids = {
            r["following_id"] for r in sb.table("follows").select("following_id")
            .eq("follower_id", me).eq("status", "accepted")
            .in_("following_id", candidate_ids).execute().data
        }
        follower_ids = {
            r["follower_id"] for r in sb.table("follows").select("follower_id")
            .eq("following_id", me).eq("status", "accepted")
            .in_("follower_id", candidate_ids).execute().data
        }
    except Exception:
        pass

    def rank(c):
        cid = c["id"]
        is_following = cid in following_ids
        is_follower = cid in follower_ids
        if is_following and is_follower:
            return 0  # karşılıklı takip — en olası
        if is_following:
            return 1
        if is_follower:
            return 2
        return 3

    candidates.sort(key=lambda c: (rank(c), (c["username"] or "").lower()))
    top = candidates[:3]

    return jsonify(users=[
        {"username": c["username"], "avatar_url": c.get("avatar_url")} for c in top
    ])
