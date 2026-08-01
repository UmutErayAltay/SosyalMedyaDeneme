from flask import request, jsonify

from . import bp
from ._common import api_login_required, _str_field
from ..supabase_client import get_sb


# ----------------------- ANKET OY VERME (Faz 5, native Android) -----------------------
# app/polls.py vote()'un AYNI mantığı JSON'a taşınır. Native ZATEN her post
# JSON'unda `poll` alanını alıyordu (attach_polls/enrich_post_json feed, discover,
# hashtag, profile ve post detay uçlarının HEPSİNDE çağrılıyor) — eksik olan tek
# şey oy VERME ucuydu.
#
# Web'den TEK SAPMA: gövde form-encoded değil JSON (api_v1 sözleşmesi) ve
# `abort(404)` yerine `jsonify(error="not_found"), 404`. 3-yönlü toggle,
# seçenek-anket doğrulaması ve dönen şekil (options/total_votes/my_vote)
# BİREBİR web ile aynıdır — bu şekil attach_polls()'un post'a eklediği `poll`
# şeklinin (id hariç) aynısıdır, böylece client dönen veriyi mevcut poll'un
# üstüne doğrudan yazabilir.

@bp.route("/polls/<poll_id>/vote", methods=["POST"])
@api_login_required
def api_vote_poll(poll_id):
    """Ankete oy ver / oyu değiştir / oyu geri al — polls.py vote()'un AYNI mantığı.

    3-yönlü toggle (like toggle deseniyle aynı):
      - aynı seçeneğe tekrar basıldı → oy SİLİNİR (my_vote=None)
      - farklı seçenek → mevcut oy GÜNCELLENİR
      - hiç oy yok → yeni oy EKLENİR

    Seçenek gerçekten bu ankete mi ait doğrulanır (başka bir ankete ait option_id
    gönderilemesin). poll_votes PK'sı (poll_id, user_id) olduğu için kullanıcı
    başına tek oy DB seviyesinde de garanti.
    """
    sb = get_sb()
    me = request.api_user["id"]

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    option_id = _str_field(data, "option_id")
    if not option_id:
        return jsonify(error="option_id_required"), 400

    # Migration toleransı SADECE bu ilk okumada: sql/migration_polls.sql henüz
    # uygulanmamışsa poll_options tablosu yoktur ve istek burada 503'e düşer
    # (blocks.py'deki AYNI desen). Aşağıdaki YAZMA adımları bilerek bu try'ın
    # DIŞINDA — geçici bir bağlantı hatası "tablo yok" gibi maskelenip 503
    # dönmemeli, gerçek hata olarak yüzeye çıkmalı.
    try:
        opt = sb.table("poll_options").select("poll_id").eq("id", option_id).execute().data
    except Exception:
        return jsonify(error="polls_not_available"), 503
    if not opt or opt[0]["poll_id"] != poll_id:
        return jsonify(error="not_found"), 404

    existing = sb.table("poll_votes").select("option_id").eq(
        "poll_id", poll_id
    ).eq("user_id", me).execute().data
    if existing and existing[0]["option_id"] == option_id:
        # Aynı seçeneğe tekrar basıldı → oy kaldırılır (vote() ile AYNI toggle)
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
