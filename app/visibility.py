"""Post gizliliği: herkese açık / sadece takipçiler.

Görünürlük iki farklı şekilde uygulanır:
- feed() gibi SAYFALI (range/limit) sorgularda `.or_()` ile SQL seviyesinde
  filtrelenir — aksi halde post-fetch sonrası Python'da süzmek sayfa
  boyutunu (PAGE_SIZE) tutarsız hale getirirdi.
- Profil/arama/hashtag gibi sayfalanmayan (ya da sadece `.limit()` ile üst
  sınırlı) listelerde postlar zaten çekildikten sonra Python'da süzülür —
  bu ölçekte (arkadaş grubu) performans sorunu yaratmaz.
"""


def followed_and_self_ids(sb, me: str) -> set:
    """Viewer'ın kendisi + takip ettiği kişilerin id kümesi.

    'Sadece takipçiler' postlarını görebilecek yazar kümesi tam olarak budur:
    bir post herkese açıksa zaten görünür, değilse SADECE yazarı takip
    edenler (+ yazarın kendisi) görebilir.
    """
    ids = {me}
    ids |= {
        f["following_id"] for f in sb.table("follows").select("following_id")
        .eq("follower_id", me).execute().data
    }
    return ids


def visible_or_filter(sb, me: str) -> str:
    """feed() gibi sayfalı sorgularda `.or_()` için postgrest filtre string'i üretir.

    sql/migration_post_visibility.sql henüz uygulanmamışsa `visibility` kolonu
    yoktur ve bu filtreyle yapılan sorgu execute() sırasında hata fırlatır —
    çağıran taraf (routes.feed) bunu try/except ile yakalayıp filtresiz eski
    sorguya düşer.
    """
    ids_csv = ",".join(followed_and_self_ids(sb, me))
    return f"visibility.eq.public,user_id.in.({ids_csv})"


def filter_visible(posts: list, visible_author_ids: set) -> list:
    """Zaten çekilmiş bir post listesini viewer'a göre süzer.

    `visibility` kolonu henüz yoksa (`.get` varsayılanı 'public') hiçbir şey
    süzülmez — migration uygulanmadan önce eski (tam görünür) davranış korunur.
    """
    return [
        p for p in posts
        if p.get("visibility", "public") == "public" or p.get("user_id") in visible_author_ids
    ]
