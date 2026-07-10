"""Üretim/günlük kullanım entrypoint'i: python serve.py

run.py'dan farkı: debug KAPALI (Werkzeug interaktif debugger'ı 0.0.0.0'da
uzaktan kod çalıştırma riskidir + debug modu template/statik cache'ini
kapatıp her isteği yavaşlatır) ve çok-thread'li gerçek bir WSGI sunucusu
(waitress) kullanılır.
"""
from app import create_app

app = create_app()
# Statik dosyalar için tarayıcı cache'i (debug modunda 0'a zorlanıyordu).
# NOT: Aktif geliştirme/arkadaşlarla test döneminde 12 saat cache, dağıtılan
# her JS düzeltmesinin kullanıcılara ULAŞMAMASI demek (herkes Ctrl+F5'e
# mahkûm kalıyordu) — 5 dakikaya çekildi; sürüm oturunca tekrar yükseltilir.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 300

if __name__ == "__main__":
    from waitress import serve
    print("http://0.0.0.0:5000 üzerinde çalışıyor (waitress, 8 thread)")
    print("Open: http://192.168.1.176:5000")
    print("Open: http://10.192.58.103:5000")
    serve(app, host="0.0.0.0", port=5000, threads=8)
