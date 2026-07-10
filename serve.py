"""Üretim/günlük kullanım entrypoint'i: python serve.py

run.py'dan farkı: debug KAPALI (Werkzeug interaktif debugger'ı 0.0.0.0'da
uzaktan kod çalıştırma riskidir + debug modu template/statik cache'ini
kapatıp her isteği yavaşlatır) ve çok-thread'li gerçek bir WSGI sunucusu
(waitress) kullanılır.
"""
from app import create_app

app = create_app()
# Statik dosyalar artık ?v=<mtime> ile sürümleniyor (bkz. app/__init__.py
# _static_cache_bust) — dosya değişince URL de değişir, tarayıcı yeni halini
# çekmek ZORUNDA kalır. Bu sayede uzun cache hem güvenli hem hızlı: eski
# "5 dk" değeri (her JS düzeltmesi kullanıcılara ulaşsın diye kısaltılmıştı)
# artık gereksiz, 1 güne çıkarıldı.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400

if __name__ == "__main__":
    from waitress import serve
    print("http://0.0.0.0:5000 üzerinde çalışıyor (waitress, 8 thread)")
    print("Open: http://192.168.1.176:5000")
    print("Open: http://10.192.58.103:5000")
    serve(app, host="0.0.0.0", port=5000, threads=8)
