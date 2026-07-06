"""Post gizliliği: herkese açık / sadece takipçiler / sadece yakın arkadaşlar.

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


def close_friend_author_ids(sb, me: str) -> set:
    """me'nin 'yakın arkadaş' olarak eklendiği yazarların id kümesi + me'nin kendisi.

    'Sadece yakın arkadaşlar' postlarını görebilecek yazar kümesi budur: bir
    yazar beni kendi close_friends listesine eklemişse onun bu tip postlarını
    görebilirim (+ kendi postlarımı her zaman görürüm, self-inclusive).
    """
    ids = {me}
    try:
        ids |= {
            r["owner_id"] for r in sb.table("close_friends").select("owner_id")
            .eq("friend_id", me).execute().data
        }
    except Exception:
        pass  # migration henüz uygulanmamış olabilir
    return ids


def visible_or_filter(sb, me: str) -> str:
    """feed() gibi sayfalı sorgularda `.or_()` için postgrest filtre string'i üretir.

    sql/migration_post_visibility.sql henüz uygulanmamışsa `visibility` kolonu
    yoktur ve bu filtreyle yapılan sorgu execute() sırasında hata fırlatır —
    çağıran taraf (routes.feed) bunu try/except ile yakalayıp filtresiz eski
    sorguya düşer.
    """
    visible_ids_csv = ",".join(followed_and_self_ids(sb, me))
    close_ids_csv = ",".join(close_friend_author_ids(sb, me))
    return (
        f"visibility.eq.public,"
        f"and(visibility.eq.followers,user_id.in.({visible_ids_csv})),"
        f"and(visibility.eq.close_friends,user_id.in.({close_ids_csv}))"
    )


def filter_visible(posts: list, visible_author_ids: set, close_friend_author_ids: set) -> list:
    """Zaten çekilmiş bir post listesini viewer'a göre süzer (3 katman: public/followers/close_friends).

    `visibility` kolonu henüz yoksa (`.get` varsayılanı 'public') hiçbir şey
    süzülmez — migration uygulanmadan önce eski (tam görünür) davranış korunur.
    """
    result = []
    for p in posts:
        vis = p.get("visibility", "public")
        author = p.get("user_id")
        if vis == "public":
            result.append(p)
        elif vis == "close_friends":
            if author in close_friend_author_ids:
                result.append(p)
        else:  # 'followers' veya bilinmeyen/eski değer
            if author in visible_author_ids:
                result.append(p)
    return result
