"""Supabase bağlantı katmanı — basitleştirilmiş, sağlam versiyon.

Server-side Flask uygulaması için TEK bir client kullanırız:
  get_sb() -> admin client (service role key, RLS bypass)

Güvenlik modeli:
  - RLS defans-in-depth (iki katlı güvenlik).
  - Birincil güvenlik: uygulama katmanı, her sorguda user_id filtresi uygular.
  - İkincil güvenlik: RLS politikaları veritabanı katmanında kontrol eder.
  - service role key SUNUCU TARAFINDA kalır, asla tarayıcıya/şablona sızdırılmaz.

Neden böyle yapıyoruz:
  - supabase-py 2.x'de create_client'a access_token verilemez.
  - set_session() ile lru_cache birlikte kullanmak race condition üretir.
  - Admin client (service role) ile tüm işlemler güvenle yapılabilir,
    yeterki uygulama katmanında user_id doğru filtrelesin.

NOT (SSL INVALID_SESSION_ID düzeltmesi):
  - lru_cache ile client'ı sonsuza kadar canlı tutmak, altındaki HTTPS
    bağlantı havuzunun bayatlamasına (sunucu tarafında TLS session'ın
    kapanmasına) yol açıyordu. Artık modül seviyesinde tek bir instance
    tutuyoruz ama SSL hatası alındığında client'ı sıfırlayıp bir kez
    daha deniyoruz.
"""
from supabase import create_client, Client
from flask import current_app
import ssl

_admin_client_instance = None
_anon_client_instance = None


def _build_admin_client() -> Client:
    return create_client(
        current_app.config["SUPABASE_URL"],
        current_app.config["SUPABASE_SECRET_KEY"],
    )


def _build_anon_client() -> Client:
    return create_client(
        current_app.config["SUPABASE_URL"],
        current_app.config["SUPABASE_PUBLISHABLE_KEY"],
    )


def get_sb() -> Client:
    """Tüm veritabanı işlemleri için service role client döndürür.
    GÜVENLİĞİNİZ UYGULAMA KATMANINDA (user_id filtresi ile) SAĞLAMALISINIZ.
    """
    global _admin_client_instance
    if _admin_client_instance is None:
        _admin_client_instance = _build_admin_client()
    return _admin_client_instance


def get_auth() -> Client:
    """Auth işlemleri (sign_up, sign_in, sign_out) için anon client."""
    global _anon_client_instance
    if _anon_client_instance is None:
        _anon_client_instance = _build_anon_client()
    return _anon_client_instance


def reset_clients() -> None:
    """Bayatlamış SSL bağlantısı şüphesiyle her iki client'ı da sıfırlar.
    Bir sonraki get_sb()/get_auth() çağrısı taze bir client (ve taze bir
    HTTPS bağlantı havuzu) oluşturur.
    """
    global _admin_client_instance, _anon_client_instance
    _admin_client_instance = None
    _anon_client_instance = None


def call_with_ssl_retry(fn, *args, **kwargs):
    """Supabase çağrılarını SSL session hatasına karşı bir kez retry eder.

    Kullanım:
        res = call_with_ssl_retry(
            lambda: get_auth().auth.sign_in_with_password({...})
        )
    """
    try:
        return fn(*args, **kwargs)
    except ssl.SSLError as e:
        if "INVALID_SESSION_ID" in str(e) or "SSL" in str(e):
            reset_clients()
            return fn(*args, **kwargs)
        raise
    except Exception as e:
        # Bazı ortamlarda SSL hatası bir wrapper exception içine sarılmış
        # olarak gelebilir (örn. httpx.ConnectError). Mesaj içinde arıyoruz.
        if "INVALID_SESSION_ID" in str(e):
            reset_clients()
            return fn(*args, **kwargs)
        raise