"""Tenor API üzerinden GIF arama proxy'si."""
import os
import requests
from flask import Blueprint, request, jsonify
from .decorators import login_required

bp = Blueprint("gifs", __name__)


@bp.route("/gif/search")
@login_required
def gif_search():
    """Tenor API'sine GIF arama isteğini proxy'ler. TENOR_API_KEY yoksa devre dışı."""
    key = os.environ.get("TENOR_API_KEY")
    if not key:
        return jsonify(gifs=[], disabled=True)

    q = request.args.get("q", "").strip() or "trending"
    try:
        r = requests.get(
            "https://tenor.googleapis.com/v2/search",
            params={"q": q, "key": key, "limit": 12, "media_filter": "gif,tinygif"},
            timeout=6
        )
        results = r.json().get("results", [])
        gifs = []
        for g in results:
            if g.get("media_formats", {}).get("gif"):
                gifs.append({
                    "url": g["media_formats"]["gif"]["url"],
                    "preview": g["media_formats"].get("tinygif", g["media_formats"]["gif"])["url"]
                })
        return jsonify(gifs=gifs)
    except Exception:
        return jsonify(gifs=[]), 502
