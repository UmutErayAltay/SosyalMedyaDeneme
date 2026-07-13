---
name: flask-smoke-test
description: Bu projede bir route/view/form akışını gerçek Supabase DB'sine karşı hızlıca doğrulaman gerektiğinde kullan (test suite yok — CLAUDE.md gereği test_client() ile doğrulama şart). Yeni endpoint, form gönderimi, yetki kontrolü veya bugfix sonrası "gerçekten çalışıyor mu" sorusuna cevap ararken; sunucu açıp Playwright ile tarayıcı sürmek gerekmeyen backend-only doğrulamalarda tercih et — CSRF/login boilerplate'ini elle yazmaktan çok daha az token harcar.
---

# Flask test_client() hızlı doğrulama

CLAUDE.md: "Test suite yok — Flask `test_client()` scriptiyle gerçek DB'ye karşı doğrula". Bu skill, her seferinde CSRF token çekme + login akışını elle yazmak yerine hazır yardımcı sağlar.

## Kullanım

```python
import sys
sys.path.insert(0, r"C:\Users\Artemis\Desktop\sosyal-medya")
sys.path.insert(0, r"C:\Users\Artemis\Desktop\sosyal-medya\.claude\skills\flask-smoke-test\scripts")
from flask_client_helper import make_client, login, post_form

client, app = make_client()
with app.app_context():
    login(client, "test@ornek.local", "sifre123")
    r = client.get("/feed")
    assert r.status_code == 200, r.status_code

    r = post_form(client, "/messages/1/send", {"content": "merhaba"}, get_path="/messages/1")
    assert r.status_code == 200, r.get_data(as_text=True)
    print("OK")
```

Bunu `C:\Users\Artemis\AppData\Local\Temp\claude\...\scratchpad\` altında tek seferlik bir `.py` dosyası olarak yaz ve çalıştır (scratchpad kuralı geçerli — proje köküne gömme).

## Ne zaman kullan

- Backend mantığını (route, form, yetki/sahiplik kontrolü) gerçek DB'ye karşı hızlı doğrularken, tarayıcı/JS gerekmiyorsa.
- Bir bug fix'in gerçekten düzeldiğini kanıtlamak için (regresyon iddiası değil, doğrudan kanıt).

## Ne zaman KULLANMA

- JS/realtime/Playwright gerektiren davranış (`test_client()` JS çalıştırmaz, gerçek tarayıcı değildir) — bunun için `server-preflight` + Playwright kullan.
- RLS/realtime broadcast kanal doğrulaması — bunun kendi metodolojisi var: `rls-migration-verify`.
- Kalıcı/tekrar çalıştırılacak test altyapısı kurmak — bu proje kasıtlı olarak test suite kullanmıyor, tek seferlik script yeterli.

## Temizlik

Oluşturduğun her test kullanıcısı/veri gerçek prod DB'de kalıcıdır — script'in sonunda veya ayrı bir cleanup adımıyla sildiğinden emin ol (bkz. `rls-migration-verify/scripts/cleanup_test_users.py` benzer bir desen sunar).
