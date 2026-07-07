"""Üretim/günlük kullanım entrypoint'i: python serve.py

run.py'dan farkı: debug KAPALI (Werkzeug interaktif debugger'ı 0.0.0.0'da
uzaktan kod çalıştırma riskidir + debug modu template/statik cache'ini
kapatıp her isteği yavaşlatır) ve çok-thread'li gerçek bir WSGI sunucusu
(waitress) kullanılır.
"""
from app import create_app

app = create_app()
# Statik dosyalar için tarayıcı cache'i (debug modunda 0'a zorlanıyordu)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 43200  # 12 saat

if __name__ == "__main__":
    from waitress import serve
    print("http://0.0.0.0:5000 üzerinde çalışıyor (waitress, 8 thread)")
    serve(app, host="0.0.0.0", port=5000, threads=8)
