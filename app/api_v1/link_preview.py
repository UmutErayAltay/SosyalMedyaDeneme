"""GET /api/v1/link-preview — native (Android) için link önizleme.

app/link_preview.py::get_or_fetch_preview()'ın BİREBİR AYNI SSRF-korumalı
fetch/parse/cache mantığını çağırır, kendi SSRF/parse kodunu TEKRARLAMAZ
(gifs.py <-> api_v1/gifs.py ile AYNI desen) — tek fark auth mekanizması
(Bearer token, `api_login_required`) ve response sözleşmesinin JSON olması
(web tarafı da zaten JSON döner, ikisi TAMAMEN aynı şekli kullanır).
"""
from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..link_preview import get_or_fetch_preview, is_valid_url_format
from ..rate_limit import is_rate_limited


@bp.route("/link-preview")
@api_login_required
def api_link_preview():
    url = request.args.get("url", "").strip()
    if not is_valid_url_format(url):
        return jsonify(ok=False, error="invalid_url"), 400

    me = request.api_user["id"]
    if is_rate_limited(f"link_preview:{me}", 30, 60):
        return jsonify(ok=False, error="rate_limited"), 429

    return jsonify(get_or_fetch_preview(url))
