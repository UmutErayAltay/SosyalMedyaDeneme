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
"""
from supabase import create_client, Client
from flask import current_app
from functools import lru_cache


@lru_cache(maxsize=1)
def _admin_client() -> Client:
    """Service role key'li client. RLS'i bypass eder."""
    return create_client(
        current_app.config["SUPABASE_URL"],
        current_app.config["SUPABASE_SECRET_KEY"],
    )


@lru_cache(maxsize=1)
def _anon_client() -> Client:
    """Publishable key'li client — sadece auth işlemleri (sign_up, sign_in, sign_out)."""
    return create_client(
        current_app.config["SUPABASE_URL"],
        current_app.config["SUPABASE_PUBLISHABLE_KEY"],
    )


def get_sb() -> Client:
    """Tüm veritabanı işlemleri için service role client döndürür.
    GÜVENLİĞİNİZ UYGULAMA KATMANINDA (user_id filtresi ile) SAĞLAMALISINIZ.
    """
    return _admin_client()


def get_auth() -> Client:
    """Auth işlemleri (sign_up, sign_in, sign_out) için anon client."""
    return _anon_client()
