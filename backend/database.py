import json
import logging

from backend.config import settings

log = logging.getLogger("acer-iq.db")

_client = None
_init_failed = False


def get_client():
    global _client, _init_failed
    if _client is not None:
        return _client
    if _init_failed:
        return None  # already logged; don't repeat it on every search
    if not settings.supabase_url or settings.supabase_url == "your_url_here":
        return None
    try:
        from supabase import create_client
        _client = create_client(settings.supabase_url, settings.supabase_key)
    except Exception as e:
        log.error("Supabase client init failed - searches will not survive a "
                  "restart and CSV export will 404: %s: %s", type(e).__name__, e)
        _client, _init_failed = None, True
    return _client


def save_search(search_id: str, city: str, industry: str, companies: list):
    client = get_client()
    if not client:
        return
    try:
        client.table("searches").upsert({
            "id": search_id,
            "city": city,
            "industry": industry,
            "results": json.dumps([c.model_dump() for c in companies]),
        }).execute()
    except Exception as e:
        log.error("save_search(%s) failed - CSV export will break after a "
                  "restart: %s: %s", search_id, type(e).__name__, e)


def load_search(search_id: str):
    client = get_client()
    if not client:
        return None
    try:
        res = client.table("searches").select("*").eq("id", search_id).execute()
        if res.data:
            return res.data[0]
    except Exception as e:
        log.error("load_search(%s) failed: %s: %s", search_id, type(e).__name__, e)
    return None
