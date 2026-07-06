"""Post görüntülenme sayacı: Instagram tarzı, SADECE yazara görünen 'views'
sayısı. Aynı kullanıcının aynı postu tekrar görüntülemesi sayıya bir kez
eklenir (upsert, story_views ile aynı desen — bkz. app/stories.py)."""


def record_view(sb, post_id: str, viewer_id: str, author_id: str) -> None:
    """Bir postun görüntülendiğini işaretler. Yazarın kendi görüntülemesi sayılmaz."""
    if viewer_id == author_id:
        return
    try:
        sb.table("post_views").upsert({"post_id": post_id, "user_id": viewer_id}).execute()
    except Exception:
        pass  # migration henüz uygulanmamış olabilir


def get_view_count(sb, post_id: str) -> int:
    """Bir postu kaç FARKLI kullanıcının görüntülediğini döner."""
    try:
        return sb.table("post_views").select(
            "user_id", count="exact", head=True
        ).eq("post_id", post_id).execute().count or 0
    except Exception:
        return 0
