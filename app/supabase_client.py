"""Supabase bağlantı katmanı — basitleştirilmiş, sağlam versiyon.

Server-side Flask uygulaması için TEK bir client kullanırız:
  get_sb() -> admin client (service role key, RLS bypass)

Güvenlik modeli:
  - RLS defans-in-depth (iki katlı güvenlik).
  - Birincil güvenlik: uygulama katmanı, her sorguda user_id filtresi uygular.
  - İkincil güvenlik: RLS politikaları veritabanı katmanında kontrol eder.
  - service role key SUNUCU TARAFINDA kalır, asla tarayıcıya/şablona sızdırılmaz.

NOT (bağlantı kopması düzeltmesi):
  - Client'ın altındaki HTTP bağlantısı uzun süre boşta kaldığında (Supabase
    tarafı bağlantıyı sessizce kapattığında) hem SSL hem de
    httpx.RemoteProtocolError ("Server disconnected") hataları çıkabiliyordu.
  - reset_clients() + retry_on_connection_error decorator'ı ile, böyle bir
    hata olduğunda client'lar sıfırlanıp, hatayı alan ROUTE FONKSİYONUNUN
    TAMAMI bir kez daha çalıştırılıyor (fonksiyon içindeki tüm sorgular dahil).
"""
from supabase import create_client, Client
from supabase.client import ClientOptions
from flask import current_app
import ssl
import httpx
import functools

_admin_client_instance = None
_anon_client_instance = None


def _build_http_client() -> httpx.Client:
    """Supabase alt-istemcilerinin (postgrest/auth/storage) paylaştığı httpx
    istemcisi — bayat bağlantı kopmalarına karşı ayarlı.

    - http2=False: HTTP/2'de tek TCP bağlantısı çoklanır; Supabase/Cloudflare
      boştaki bağlantıyı kapatınca (GOAWAY) o anda uçuştaki TÜM istekler
      "Server disconnected" ile düşüyordu (canlıda 500 + retry'ın da aynı
      anda düşmesi). HTTP/1.1'de bağlantı başına tek istek — tek kopma tek
      isteği etkiler, retry taze bağlantıyla kurtarır.
    - keepalive_expiry=15sn: Supabase boşta ~60sn'de bağlantı kapatıyor;
      havuz 15sn'den eski boş bağlantıyı hiç yeniden kullanmaz → bayat
      bağlantıya denk gelme ihtimali kökten azalır.
    - timeout 30sn (varsayılan 120 yerine): asılı kalan bir istek waitress
      thread'ini 2 dakika kilitliyordu (task queue depth uyarıları).
    """
    return httpx.Client(
        http2=False,
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(
            max_connections=32,
            max_keepalive_connections=16,
            keepalive_expiry=15.0,
        ),
    )

# Bayat/kopmuş bağlantı belirtisi olan hata türleri
_CONNECTION_ERRORS = (
    ssl.SSLError,
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    ConnectionError,
)


def _build_admin_client() -> Client:
    return create_client(
        current_app.config["SUPABASE_URL"],
        current_app.config["SUPABASE_SECRET_KEY"],
        options=ClientOptions(httpx_client=_build_http_client()),
    )


def _build_anon_client() -> Client:
    return create_client(
        current_app.config["SUPABASE_URL"],
        current_app.config["SUPABASE_PUBLISHABLE_KEY"],
        options=ClientOptions(httpx_client=_build_http_client()),
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
    """Bayatlamış bağlantı şüphesiyle her iki client'ı da sıfırlar.
    Bir sonraki get_sb()/get_auth() çağrısı taze bir client (ve taze bir
    HTTP bağlantı havuzu) oluşturur.
    """
    global _admin_client_instance, _anon_client_instance
    _admin_client_instance = None
    _anon_client_instance = None


def call_with_ssl_retry(fn, *args, **kwargs):
    """Tek bir Supabase çağrısını bağlantı hatasına karşı bir kez retry eder.

    Kullanım:
        res = call_with_ssl_retry(
            lambda: get_auth().auth.sign_in_with_password({...})
        )
    """
    try:
        return fn(*args, **kwargs)
    except _CONNECTION_ERRORS:
        reset_clients()
        return fn(*args, **kwargs)
    except Exception as e:
        if "INVALID_SESSION_ID" in str(e) or "Server disconnected" in str(e):
            reset_clients()
            return fn(*args, **kwargs)
        raise


def retry_on_connection_error(view_func):
    """Route fonksiyonlarına eklenen decorator.

    Fonksiyon içinde (birden fazla sorgu olsa bile) bir bağlantı hatası
    oluşursa, client'lar sıfırlanır ve TÜM fonksiyon bir kez daha
    çalıştırılır. Kullanım:

        @bp.route("/")
        @login_required
        @retry_on_connection_error
        def feed():
            ...
    """
    @functools.wraps(view_func)
    def wrapper(*args, **kwargs):
        try:
            return view_func(*args, **kwargs)
        except _CONNECTION_ERRORS:
            reset_clients()
            return view_func(*args, **kwargs)
        except Exception as e:
            if "INVALID_SESSION_ID" in str(e) or "Server disconnected" in str(e):
                reset_clients()
                return view_func(*args, **kwargs)
            raise
    return wrapper