"""/gif/search — Klipy API proxy'si (native Android GIF arama/paylaşma, Faz 5 Dalga 3B).

app/gifs.py::gif_search()'ün BİREBİR AYNI Klipy proxy mantığı, sadece
token-authenticated (`@api_login_required`) JSON API sözleşmesiyle — API
anahtarı sunucuda kalır, native tarafa hiç sızmaz (aynı gerekçe, web ile
kod tekrarı BİLİNÇLİ: iki ayrı auth mekanizması/decorator kullanıyorlar).

GIF'lerin bir DB tablosu OLMADIĞINI, düz Klipy CDN URL'leri olduğunu ve kabul
kuralının (`is_valid_klipy_url`, app/gifs.py'de) post/mesaj/yorum oluşturma
uç noktalarında ayrıca uygulandığını unutma.
"""
import os
import requests
from flask import request, jsonify

from . import bp
from ._common import api_login_required


@bp.route("/gif/search")
@api_login_required
def api_gif_search():
    """gif_search() (app/gifs.py) ile AYNI Klipy proxy mantığı: boş `q` =
    trending, doluysa arama. KLIPY_API_KEY ortam değişkeni yoksa hata DEĞİL,
    sessiz devre dışı (`{"gifs": [], "disabled": true}`) — native taraf bunu
    "GIF sekmesini gizle" olarak yorumlamalı.
    """
    key = os.environ.get("KLIPY_API_KEY")
    if not key:
        return jsonify(gifs=[], disabled=True)

    q = request.args.get("q", "").strip()
    endpoint = "search" if q else "trending"
    params = {"per_page": 12}
    if q:
        params["q"] = q
    try:
        r = requests.get(
            "https://api.klipy.com/api/v1/%s/gifs/%s" % (key, endpoint),
            params=params,
            timeout=6
        )
        items = (r.json().get("data") or {}).get("data") or []
        gifs = []
        for g in items:
            f = g.get("file") or {}
            hd = (f.get("hd") or {}).get("gif") or {}
            # Önizleme için küçük boy: xs yoksa sm, o da yoksa hd
            xs = ((f.get("xs") or f.get("sm") or {}).get("gif")) or hd
            if hd.get("url"):
                gifs.append({"url": hd["url"], "preview": xs.get("url") or hd["url"]})
        return jsonify(gifs=gifs)
    except Exception:
        return jsonify(gifs=[]), 502
