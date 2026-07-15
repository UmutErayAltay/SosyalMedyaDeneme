"""Aktif oturumlar yönetimi ve uzaktan çıkış.

User_id başına birden fazla aktif oturum (farklı cihazlar/tarayıcılar)
destekler. Her başarılı giriş → user_sessions tablosuna satır, satır id
→ Flask session'da saklanır. İsteklerde touch_session ile son aktivite
zamanı güncellenir; eski oturumlar uzaktan sonlandırılabilir.

In-memory throttle dict (presence.py deseni) ile DB üzerindeki yükü azaltır
(per-session 60 saniye throttle).
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from flask import request

# session_id (uuid) -> son kontrol zamanı (time.monotonic())
_session_last_touch: dict[str, float] = {}
_session_touch_lock = threading.Lock()

# Session son kontrolünün en az bu kadar aralıkla yapılması (sn)
_TOUCH_THROTTLE = 60

# DB yazması (Supabase UPDATE) request'i beklemesin diye arka plan havuzu —
# presence.py ve messaging/views.py deseniyle aynı
_write_pool = ThreadPoolExecutor(max_workers=2)


def create_session_record(sb, user_id: str) -> str | None:
    """Yeni oturum kaydını oluştur (giriş sonrası).

    User-Agent ve IP adresini kaydeder. Dönen id'yi Flask session'a yazacak.
    Hata durumunda None döner; giriş akışı hiçbir zaman kırılmaz (fail-open).
    """
    try:
        user_agent = request.headers.get("User-Agent", "")[:300]
        ip_addr = request.remote_addr or ""

        result = sb.table("user_sessions").insert({
            "user_id": user_id,
            "user_agent": user_agent,
            "ip": ip_addr,
            "created_at": "now()",
            "last_active_at": "now()",
        }).execute()

        if result.data and len(result.data) > 0:
            return result.data[0]["id"]
    except Exception:
        # Supabase erişim hatası — oturum hâlâ kurulur, session_record_id
        # sadece None kalır (touch_session bu durumu tolere eder)
        pass
    return None


def touch_session(session_record_id: str) -> bool:
    """Son aktivite zamanını güncelle (tüm isteklerde çağrılır).

    Throttle: son kontrolden 60sn geçmediyse True dön (DB'ye gitme).
    Geçtiyse:
      - Satır var mı check et (SENKRON sorgu)
      - VARSA arka planda update et, True dön
      - YOKSA False dön (oturum uzaktan sonlandırılmış)
      - EXCEPTION → True dön (fail-open) ama throttle NOT güncelle

    Returns:
        True: Oturum hâlâ geçerli
        False: Oturum uzaktan sonlandırılmış (kullanıcıyı logout et)
    """
    if not session_record_id:
        return True  # session_record_id yok → tolerans

    # Throttle kontrolü
    should_check = False
    with _session_touch_lock:
        now = time.monotonic()
        last_touch = _session_last_touch.get(session_record_id, 0)
        if now - last_touch > _TOUCH_THROTTLE:
            _session_last_touch[session_record_id] = now
            should_check = True

    if not should_check:
        return True  # Throttle aktif, DB'ye gitme

    # Satır var mı kontrol et (SENKRON)
    try:
        from .supabase_client import get_sb
        sb = get_sb()
        result = sb.table("user_sessions").select("id").eq(
            "id", session_record_id
        ).execute()

        if not result.data:
            # Satır yok → oturum uzaktan sonlandırılmış
            return False

        # Satır var → arka planda update
        _write_pool.submit(_update_last_active, session_record_id)
        return True

    except Exception:
        # DB hatası (Supabase inişi, ağ vs) → fail-open
        # Throttle damgasını GÜNCELLEME (fail-open aynı zamanda throttle reset demek)
        with _session_touch_lock:
            _session_last_touch.pop(session_record_id, None)
        return True


def _update_last_active(session_record_id: str) -> None:
    """Arka planda last_active_at güncelle.

    _write_pool üzerinden çalışır (fire-and-forget); başarısızlık sessizce yutulur.
    """
    try:
        from .supabase_client import get_sb
        sb = get_sb()
        sb.table("user_sessions").update({
            "last_active_at": "now()"
        }).eq("id", session_record_id).execute()
    except Exception:
        # Başarısız olsa da request'i etkilemez
        pass


def delete_session_record(session_record_id: str) -> None:
    """Oturum kaydını sil (logout sırasında).

    try/except'li; başarısızlık logout'u kırmaz.
    """
    if not session_record_id:
        return

    try:
        from .supabase_client import get_sb
        sb = get_sb()
        sb.table("user_sessions").delete().eq("id", session_record_id).execute()
    except Exception:
        # Silme başarısız → logout hâlâ gerçekleşir
        pass

    # Throttle dict'ten sil
    with _session_touch_lock:
        _session_last_touch.pop(session_record_id, None)
