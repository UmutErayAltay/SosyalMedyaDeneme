"""JSON REST API katmanı — Faz 1 (native Android yol haritası).

Mevcut web mimarisine (session-cookie + CSRF, app/auth.py + app/decorators.py)
HİÇ DOKUNMADAN, token tabanlı (Bearer) auth ile EKLEME (additive) bir katman.
Hiçbir HTML route/template etkilenmez; bu blueprint sadece /api/v1 altında
yaşar ve kendi auth mekanizmasını (api_tokens tablosu, sql/migration_api_tokens.sql)
kullanır.

CSRF muafiyeti: app/__init__.py'deki csrf_protect() bu blueprint'in tüm
endpoint'lerini (request.endpoint.startswith("api_v1.")) muaf tutar — CSRF,
tarayıcının bir isteğe OTOMATİK olarak ambient credential (cookie) eklemesinden
kaynaklanan bir saldırıdır; Bearer token header'ı üçüncü-parti bir site/form
tarafından OTOMATİK eklenemez, bu yüzden token-authenticated endpoint'ler
doğaları gereği CSRF'e bağışıktır.

2FA: aktif TOTP'si olan bir hesap API üzerinden login olmaya çalışırsa VE
istek body'sinde `code` yoksa, şifre doğru olsa bile ham token ÜRETİLMEZ
(403 mfa_required) — bu SESSİZCE bypass edilmez, client'a açıkça bildirilir.
Client AYNI isteği `code` alanıyla tekrar atar (tek endpoint, iki deneme —
ayrı bir "2fa doğrulama" endpoint'i YOK). Enroll/disable/status için ayrı
"2FA (TOTP)" bölümüne bak (Faz 4, native Android post-parity).

Kayıt: POST /auth/register — auth.py register()'ın çekirdek mantığı, ama
username-çakışması ÖNDEN kontrol edilir (register()'daki sessiz yutma YOK)
ve otomatik giriş için ayrı bir sign_in_with_password YAPILMAZ (admin.
create_user() zaten user.id döndürür, token doğrudan bununla üretilir).

Google ile giriş: POST /auth/google — web'in tarayıcı-redirect akışından
FARKLI, Android Credential Manager'ın ürettiği bir Google ID token'ı
`sign_in_with_id_token()` ile Supabase'e değiştirir (bkz. api_google_login()
docstring'i). Supabase'in Google provider Client ID'si web ile PAYLAŞILIR.

--- Paket organizasyonu (dosya sayısı arttıkça — Faz 1'den bugüne 62 route,
tek dosyada 3514 satır — okunabilirlik için app/routes/ ile AYNI desenle
konuya göre ayrıştırıldı):

- `_common.py`      — _str_field/_hash_token/api_login_required + API_DEFAULT_LIMIT/API_MAX_LIMIT
- `auth.py`         — /auth/*, /realtime-token
- `feed.py`         — /feed, /discover, /search + arama geçmişi
- `profile.py`      — /profile/*, /follow-requests/*
- `reels.py`        — /reels
- `messaging.py`    — /messages/* (1:1 + grup sohbeti)
- `interactions.py` — beğeni/yorum + post oluşturma
- `settings.py`     — /profile/edit, /notifications/preferences, /close-friends/*, /profile/deactivate
- `notifications.py`— /notifications, /notifications/unread-count
- `twofa.py`        — /2fa/*
- `blocks.py`       — /block/*, /blocked
- `sessions.py`     — /sessions/*
- `hashtags.py`     — /hashtag/*, /trending
- `polls.py`        — /polls/<id>/vote
- `stickers.py`     — /stickers/*

ÖNEMLİ: Bölünme SADECE dosya organizasyonu — endpoint isimleri (`api_v1.xxx`)
ve URL'ler DEĞİŞMEDİ, çünkü hepsi hâlâ AYNI `bp` nesnesine route ekliyor
(hangi dosyada tanımlandığı fark etmez). Blueprint adı da ("api_v1") AYNI
kaldı — app/__init__.py'deki `from .api_v1 import bp as api_v1_bp` importu
ve CSRF muafiyeti (`request.endpoint.startswith("api_v1.")`) değişmeden çalışır.
"""
from flask import Blueprint

bp = Blueprint("api_v1", __name__)

# Alt modüller `from . import bp` ile bu paketin `bp`sine route ekler —
# import edilmeleri (yan etkili) bu satırda tetiklenir, en sonda olmalı
# (yukarıdaki `bp` tanımından SONRA, aksi halde döngüsel import patlar).
from . import (  # noqa: E402,F401
    auth, feed, profile, reels, messaging, interactions, settings,
    notifications, twofa, blocks, sessions, hashtags, polls, stickers,
    mutes, stories, bookmarks, gifs, push,
)
