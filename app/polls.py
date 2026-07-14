"""Anket (poll) postları: post'a bağlı çoklu seçenek + oylama.

Bir post EN FAZLA bir anketle ilişkilendirilir (polls.post_id UNIQUE) — post
içeriği (posts.content) anketin SORUSU olarak kullanılır, ayrı bir "question"
kolonu gerekmez. Anket, görsel/video ile AYNI POSTTA birlikte desteklenmiyor
(video/görsel'deki tek-medya-türü kuralıyla tutarlı, bkz. routes.create_post()).

sql/migration_polls.sql henüz uygulanmamışsa poll tabloları yoktur — post
paylaşımı/görüntülenmesi bundan etkilenmesin diye tüm okuma/yazma yardımcıları
try/except ile korunur.
"""
from flask import Blueprint, request, session, abort, jsonify
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error

bp = Blueprint("polls", __name__)


def create_poll(sb, options: list[str], post_id: str = None, story_id: str = None,
                 position_x: float = 0.5, position_y: float = 0.75, scale: float = 1.0) -> None:
    """Yeni bir anket oluşturur — post veya hikaye için.

    İkisinden TAM OLARAK BİRİ verilmeli. Seçenekler listesi 2+ eleman içermeli.
    position_x/y: 0-1 arasında, canvas'a göre oran (hikaye anketi için).
    scale: 0.3-3 arasında boyut çarpanı; varsayılan 1.0.
    """
    if not options or len(options) < 2:
        return
    if (post_id and story_id) or (not post_id and not story_id):
        return

    try:
        poll_data = {
            "position_x": position_x,
            "position_y": position_y,
            "scale": scale,
        }
        if post_id:
            poll_data["post_id"] = post_id
        if story_id:
            poll_data["story_id"] = story_id

        poll = sb.table("polls").insert(poll_data).execute()
        poll_id = poll.data[0]["id"]
        sb.table("poll_options").insert([
            {"poll_id": poll_id, "option_text": opt, "position": i}
            for i, opt in enumerate(options)
        ]).execute()
    except Exception:
        pass


def attach_polls(sb, posts: list, me: str) -> None:
    """Bir post listesine (varsa) anket verisini `p['poll']` olarak ekler.

    Sayaçlar/seçenekler tek birer IN sorgusuyla tüm post ID'leri üzerinden
    toplu çekilir (N+1 önlenir — bkz. _attach_post_metrics ile aynı desen).
    """
    post_ids = [p["id"] for p in posts]
    for p in posts:
        p["poll"] = None
    if not post_ids:
        return

    try:
        polls = sb.table("polls").select("id, post_id").in_("post_id", post_ids).execute().data
    except Exception:
        return
    if not polls:
        return
    poll_by_post = {p["post_id"]: p["id"] for p in polls}
    poll_ids = list(poll_by_post.values())

    options = sb.table("poll_options").select("id, poll_id, option_text, position").in_(
        "poll_id", poll_ids
    ).order("position").execute().data
    votes = sb.table("poll_votes").select("poll_id, option_id, user_id").in_("poll_id", poll_ids).execute().data

    options_by_poll: dict = {}
    for o in options:
        options_by_poll.setdefault(o["poll_id"], []).append(o)

    counts_by_poll: dict = {}
    my_vote_by_poll: dict = {}
    for v in votes:
        counts = counts_by_poll.setdefault(v["poll_id"], {})
        counts[v["option_id"]] = counts.get(v["option_id"], 0) + 1
        if v["user_id"] == me:
            my_vote_by_poll[v["poll_id"]] = v["option_id"]

    for p in posts:
        poll_id = poll_by_post.get(p["id"])
        if not poll_id:
            continue
        opts = options_by_poll.get(poll_id, [])
        counts = counts_by_poll.get(poll_id, {})
        total = sum(counts.values())
        opt_list = [{
            "id": o["id"], "text": o["option_text"],
            "votes": counts.get(o["id"], 0),
            "pct": round((counts.get(o["id"], 0) / total) * 100) if total else 0,
        } for o in opts]
        p["poll"] = {"id": poll_id, "options": opt_list, "total_votes": total,
                     "my_vote": my_vote_by_poll.get(poll_id)}


@bp.route("/poll/<poll_id>/vote", methods=["POST"])
@login_required
@retry_on_connection_error
def vote(poll_id):
    sb = get_sb()
    me = session["user"]["id"]
    option_id = request.form.get("option_id")
    if not option_id:
        return jsonify(error="option_id gerekli"), 400

    # option gerçekten bu ankete mi ait, doğrula (başka bir ankete ait id gönderilemesin)
    opt = sb.table("poll_options").select("poll_id").eq("id", option_id).execute().data
    if not opt or opt[0]["poll_id"] != poll_id:
        abort(404)

    existing = sb.table("poll_votes").select("option_id").eq(
        "poll_id", poll_id
    ).eq("user_id", me).execute().data
    if existing and existing[0]["option_id"] == option_id:
        # Aynı seçeneğe tekrar basıldı → oy kaldırılır (like toggle ile aynı desen)
        sb.table("poll_votes").delete().eq("poll_id", poll_id).eq("user_id", me).execute()
        my_vote = None
    elif existing:
        sb.table("poll_votes").update({"option_id": option_id}).eq(
            "poll_id", poll_id
        ).eq("user_id", me).execute()
        my_vote = option_id
    else:
        sb.table("poll_votes").insert({
            "poll_id": poll_id, "option_id": option_id, "user_id": me,
        }).execute()
        my_vote = option_id

    votes = sb.table("poll_votes").select("option_id").eq("poll_id", poll_id).execute().data
    counts: dict = {}
    for v in votes:
        counts[v["option_id"]] = counts.get(v["option_id"], 0) + 1
    total = sum(counts.values())

    options = sb.table("poll_options").select("id, option_text, position").eq(
        "poll_id", poll_id
    ).order("position").execute().data
    result = [{
        "id": o["id"], "text": o["option_text"],
        "votes": counts.get(o["id"], 0),
        "pct": round((counts.get(o["id"], 0) / total) * 100) if total else 0,
    } for o in options]

    return jsonify(options=result, total_votes=total, my_vote=my_vote)
