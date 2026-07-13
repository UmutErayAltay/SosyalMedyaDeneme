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
    """Viewer'ın kendisi + takip ettiği (ACCEPTED) kişilerin id kümesi.

    'Sadece takipçiler' postlarını görebilecek yazar kümesi tam olarak budur:
    bir post herkese açıksa zaten görünür, değilse SADECE yazarı (accepted olarak)
    takip edenler (+ yazarın kendisi) görebilir. Pending istekler takip sayılmaz.
    """
    ids = {me}
    ids |= {
        f["following_id"] for f in sb.table("follows").select("following_id")
        .eq("follower_id", me).eq("status", "accepted").execute().data
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


def filter_visible(*args, **kwargs) -> list:
    """Zaten çekilmiş bir post listesini viewer'a göre süzer (3 katman: public/followers/close_friends + is_private).

    Overload desteği:
    - Eski imza: filter_visible(posts, visible_author_ids, close_friend_author_ids) — is_private kontrolü YOK
    - Yeni imza: filter_visible(sb, posts, visible_author_ids, close_friend_author_ids, me) — is_private kontrolü VAR

    `visibility` kolonu henüz yoksa (`.get` varsayılanı 'public') hiçbir şey
    süzülmez — migration uygulanmadan önce eski (tam görünür) davranış korunur.
    """
    # Overload: imza sayısı
    if len(args) == 3:
        # Eski imza: (posts, visible_author_ids, close_friend_author_ids)
        posts, visible_author_ids, close_friend_author_ids = args
        sb = None
        me = None
    elif len(args) == 5:
        # Yeni imza: (sb, posts, visible_author_ids, close_friend_author_ids, me)
        sb, posts, visible_author_ids, close_friend_author_ids, me = args
    else:
        raise TypeError("filter_visible() expects 3 or 5 positional arguments")

    # Yeni imzada: yazar is_private durumunu toplu çek
    is_private_map = {}
    if sb is not None and me is not None:
        author_ids = {p.get("user_id") for p in posts if p.get("user_id")}
        if author_ids:
            try:
                profiles = sb.table("profiles").select("id, is_private").in_("id", list(author_ids)).execute().data
                is_private_map = {p["id"]: p.get("is_private", False) for p in profiles}
            except Exception:
                pass  # migration henüz uygulanmamış olabilir

    result = []
    for p in posts:
        vis = p.get("visibility", "public")
        author = p.get("user_id")

        # Yeni imzada: is_private kontrolü
        if sb is not None and me is not None:
            author_is_private = is_private_map.get(author, False)
            # is_private kontrolü: kendi postları VEYA profil açık VEYA accepted takipçi
            if author_is_private and author != me and author not in visible_author_ids:
                continue  # Bu postu gösterme

        if vis == "public":
            result.append(p)
        elif vis == "close_friends":
            if author in close_friend_author_ids:
                result.append(p)
        else:  # 'followers' veya bilinmeyen/eski değer
            if author in visible_author_ids:
                result.append(p)
    return result
