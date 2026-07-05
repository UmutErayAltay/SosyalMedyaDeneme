"""Yetki decorator'ları."""
from functools import wraps
from flask import session, redirect, url_for, abort


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        # Oturum yoksa sessizce login'e yönlendir.
        # Flash mesajı BIRAKMİYORUZ: kullanıcı sadece ana sayfayı ziyaret etmiş olabilir,
        # "giriş yapmalısın" uyarısı burada gereksiz ve rahatsız edicidir.
        # Gerçek giriş hatası auth.py login() route'unda yakalanır.
        if not session.get("user"):
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Sadece profiles.is_admin=true olan kullanıcılar erişebilir.

    Navbar'daki "Admin" linki session'daki (login sırasında cache'lenen)
    is_admin bayrağına göre gösterilir/gizlenir (performans — her sayfada
    ekstra sorgu istemiyoruz), AMA burada, gerçek erişim kontrolünde, DB'den
    TAZE okunur — bir admin yetkisi geri alınırsa (is_admin=false yapılırsa)
    sonraki isteğinde ANINDA etkili olsun diye (session'daki bayrak ancak bir
    sonraki girişte güncellenir, bu satır o gecikmeyi güvenlik açısından
    kritik olan tarafta kapatır).
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("auth.login"))
        from .supabase_client import get_sb
        try:
            prof = get_sb().table("profiles").select("is_admin").eq(
                "id", session["user"]["id"]
            ).execute().data
            is_admin = bool(prof and prof[0].get("is_admin"))
        except Exception:
            is_admin = False  # migration henüz yoksa da admin erişimi güvenli tarafta (kapalı) kalır
        if not is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped
