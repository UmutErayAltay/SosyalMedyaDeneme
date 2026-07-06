"""'Bugün Ne Oldu' anılar kartı: kullanıcının bugünle aynı ay/gün'e (farklı
yıl) denk gelen KENDİ geçmiş postlarını feed'in üstünde hatırlatır. Yeni
tablo gerekmez — posts.created_at üzerinden Python'da ay/gün eşleştirmesi
yapılır (bu ölçekte kullanıcı başına post sayısı azdır, performans sorunu
yaratmaz — bkz. app/routes/profile.py'deki _day_of_week_counts ile aynı
"tüm postları çek, Python'da filtrele" deseni)."""
from datetime import datetime, timezone


def get_memories(sb, me: str) -> list[dict]:
    """`me`'nin bugünle aynı ay/gün'e denk gelen (ama farklı yıldaki) taslak-olmayan postlarını döner."""
    today = datetime.now(timezone.utc).date()
    mmdd = today.strftime("%m-%d")
    this_year = str(today.year)

    posts = sb.table("posts").select(
        "id, content, image_url, image_urls, created_at, likes(count), comments(count)"
    ).eq("user_id", me).eq("is_draft", False).order("created_at", desc=True).execute().data

    memories = []
    for p in posts:
        created = p["created_at"]
        if created[5:10] == mmdd and created[:4] != this_year:
            p["like_count"] = p["likes"][0]["count"] if p.get("likes") else 0
            p["comment_count"] = p["comments"][0]["count"] if p.get("comments") else 0
            p["years_ago"] = today.year - int(created[:4])
            memories.append(p)
    return memories
