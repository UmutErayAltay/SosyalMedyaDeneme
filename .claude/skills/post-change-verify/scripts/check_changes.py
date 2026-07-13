"""Değişen dosyaları türüne göre otomatik doğrular: .py -> py_compile,
.js -> node --check, .html -> Jinja2 parse. Argüman verilmezse `git diff`
+ `git status` ile değişen/yeni dosyaları kendisi bulur (tek tek elle
komut yazmayı/token harcamayı önler).

Kullanım:
    python .claude/skills/post-change-verify/scripts/check_changes.py
    python .claude/skills/post-change-verify/scripts/check_changes.py app/auth.py app/static/js/chat.js
"""
import subprocess
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")

ROOT = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
).stdout.strip()
os.chdir(ROOT)


def changed_files():
    files = set()
    diff = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], capture_output=True, text=True
    ).stdout.splitlines()
    status = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True
    ).stdout.splitlines()
    files.update(f.strip() for f in diff if f.strip())
    for line in status:
        # "?? path" veya " M path" formatı
        path = line[3:].strip()
        if path:
            files.add(path)
    return sorted(f for f in files if os.path.isfile(f))


def check_python(files):
    py = [f for f in files if f.endswith(".py")]
    if not py:
        return True
    print(f"-- py_compile: {len(py)} dosya")
    r = subprocess.run([sys.executable, "-m", "py_compile", *py])
    return r.returncode == 0


def check_js(files):
    js = [f for f in files if f.endswith(".js")]
    if not js:
        return True
    ok = True
    for f in js:
        r = subprocess.run(["node", "--check", f])
        if r.returncode != 0:
            ok = False
    if js:
        print(f"-- node --check: {len(js)} dosya")
    return ok


def check_jinja(files):
    html = [f for f in files if f.endswith(".html")]
    if not html:
        return True
    try:
        from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError
    except ImportError:
        print("!! jinja2 import edilemedi, şablon kontrolü atlandı")
        return True
    env = Environment(loader=FileSystemLoader(ROOT))
    ok = True
    for f in html:
        try:
            env.parse(open(f, encoding="utf-8").read())
        except TemplateSyntaxError as e:
            print(f"!! Jinja hata: {f}: {e}")
            ok = False
    print(f"-- jinja parse: {len(html)} dosya")
    return ok


def main():
    args = sys.argv[1:]
    files = args if args else changed_files()
    if not files:
        print("Değişen dosya bulunamadı.")
        return 0
    results = [check_python(files), check_js(files), check_jinja(files)]
    if all(results):
        print("OK — tüm kontroller temiz.")
        return 0
    print("HATA — yukarıdaki dosyaları düzelt.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
