import json
from backend.config import settings

_client = None


def get_client():
    global _client
    if _client is not None:
        return _client
    if not settings.supabase_url or settings.supabase_url == "your_url_here":
        return None
    try:
        from supabase import create_client
        _client = create_client(settings.supabase_url, settings.supabase_key)
    except Exception:
        _client = None
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
    except Exception:
        pass


def load_search(search_id: str):
    client = get_client()
    if not client:
        return None
    try:
        res = client.table("searches").select("*").eq("id", search_id).execute()
        if res.data:
            return res.data[0]
    except Exception:
        pass
    return None
