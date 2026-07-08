"""Klipy API üzerinden GIF arama proxy'si (Tenor key alınamadığı için Klipy'ye geçildi)."""
import os
import requests
from flask import Blueprint, request, jsonify
from .decorators import login_required

bp = Blueprint("gifs", __name__)


@bp.route("/gif/search")
@login_required
def gif_search():
    """Klipy API'sine GIF arama isteğini proxy'ler. KLIPY_API_KEY yoksa devre dışı.

    Boş sorgu = trending. API anahtarı URL path'inde taşınır (Klipy tasarımı),
    bu yüzden anahtar asla frontend'e sızmaz — proxy bunun için var.
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
