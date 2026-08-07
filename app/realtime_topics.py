"""Realtime kanal adı (topic) üretimi — tahmin edilemez, yetenek-tabanlı (capability) erişim.

NEDEN VAR (2026-08-07, canlı arıza):
Supabase'in `private: true` kanal yetkilendirmesi platform tarafında BOZULDU —
kanal JOIN'inde Realtime veritabanına HİÇ sormadan "Unauthorized" dönüyor.
Kanıt zinciri `.context/active_context.md` "2026-08-07 (devam 6)"da: RLS
politikaları SQL simülasyonunda doğru çalışıyor, gözlemci politika iskelesi
gerçek join'lerde HİÇ tetiklenmiyor, hatta KOŞULSUZ izin veren politikayla
bile kanal açılmıyor. Aynı anda public (private olmayan) kanallar sorunsuz
SUBSCRIBED oluyor.

ÇÖZÜM (kullanıcı kararı): private kanallardan public kanallara geçildi, ama
güvenliği kaybetmemek için kanal ADI artık tahmin edilebilir bir kimlik
(`typing:<conversation_id>`) DEĞİL, sunucunun SECRET_KEY'iyle HMAC'lenmiş
tahmin edilemez bir dize (`t-9f3a...`). Kanal adını SADECE sunucu üretir ve
SADECE yetkili kullanıcıya (route'un kendi yetki kontrolünden geçmiş
şablona) gömer. Kanal adını bilmeyen abone olamaz.

GÜVENLİK MODELİ ve SINIRLARI:
- Topic 128 bit'lik HMAC-SHA256 kesiti — kaba kuvvetle bulunamaz.
- Yetkisiz kullanıcı topic'i HİÇBİR yerden öğrenemez (sunucu vermez).
- AMA sunucu tarafı zorlama YOK: topic'i ele geçiren biri (ekran görüntüsü,
  log, paylaşılan cihaz) o kanalı dinleyebilir. Bu, private kanallar
  düzelene kadar BİLİNÇLİ kabul edilen risk.
- Gerçek mesaj TESLİMİ bundan ETKİLENMEZ: mesajlar `postgres_changes` ile
  akar ve `messages` tablosunun KENDİ RLS'i tarafından korunur. Burada
  public'e açılan yalnızca broadcast (yazıyor göstergesi, arama sinyali,
  mesaj önizlemesi) yoludur.
- SECRET_KEY değişirse tüm topic'ler değişir (tüm istemciler yeni adı bir
  sonraki sayfa yüklemesinde alır) — bu, gerektiğinde topic rotasyonu için
  kullanılabilir.
"""
import hashlib
import hmac

from flask import current_app

# Kanal türü -> kısa ön ek. Ön ek sadece okunabilirlik/hata ayıklama içindir,
# güvenliğe katkısı yoktur (asıl koruma HMAC kesitidir).
_PREFIXES = {
    "calls": "c",
    "typing": "t",
    "messages": "m",
}


def realtime_topic(kind, ident):
    """`kind` türündeki `ident` kaynağı için tahmin edilemez kanal adı üretir.

    Şablonlarda `{{ realtime_topic('typing', conversation_id) }}` olarak
    kullanılır (app/__init__.py'de Jinja global olarak kayıtlı).

    `ident` boşsa boş dize döner — çağıran taraf (JS) boş topic'te kanal
    kurmaz, böylece 1:1 olmayan/eksik bağlamlarda sessizce devre dışı kalır.
    """
    if not ident:
        return ""
    prefix = _PREFIXES.get(kind)
    if prefix is None:
        raise ValueError(f"bilinmeyen realtime kanal türü: {kind!r}")

    secret = current_app.config["SECRET_KEY"]
    if isinstance(secret, str):
        secret = secret.encode("utf-8")

    digest = hmac.new(secret, f"{kind}:{ident}".encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{prefix}-{digest[:32]}"
