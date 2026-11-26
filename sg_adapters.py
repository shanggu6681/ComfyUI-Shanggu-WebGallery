# -*- coding: utf-8 -*-
"""PromptHero 搜索接口、作品尺寸分组与分页。"""
from __future__ import annotations

import json
import re
import urllib.parse

import sg_scraper as sg


PH_HOSTS = ("prompthero.com", "www.prompthero.com")
PH_CDN = "https://img.prompthero.com"
PH_REFERER = "https://prompthero.com/"
PH_SEARCH_PATH = "/api/trpc/prompt.search"
PH_SORTS = {"newest", "favorites", "views"}

_GROUP_CACHE: dict[str, list[dict]] = {}
_GROUP_ORDER: list[str] = []
_GROUP_CACHE_MAX = 4000


def _remember_group(wid: str, children: list[dict]) -> None:
    if not wid or not children:
        return
    if wid not in _GROUP_CACHE:
        _GROUP_ORDER.append(wid)
    _GROUP_CACHE[wid] = children
    while len(_GROUP_ORDER) > _GROUP_CACHE_MAX:
        old = _GROUP_ORDER.pop(0)
        _GROUP_CACHE.pop(old, None)


def cached_group(wid: str) -> list[dict]:
    return _GROUP_CACHE.get(str(wid or "").strip(), [])


def ph_match(url: str) -> bool:
    try:
        host = urllib.parse.urlsplit(url).netloc.lower().split(":")[0]
        return host in PH_HOSTS
    except Exception:
        return False


def match(url: str) -> bool:
    return ph_match(url)


def _api_url(origin: str, payload: dict) -> str:
    return origin + PH_SEARCH_PATH + "?" + urllib.parse.urlencode({
        "input": json.dumps({"json": payload}, ensure_ascii=False, separators=(",", ":")),
    })


def _topic_fields(payload: dict, topic: str) -> None:
    payload.pop("category", None)
    payload.pop("model", None)
    if topic.startswith("cat:"):
        payload["category"] = topic[4:]
    elif topic.startswith("model:"):
        payload["model"] = topic[6:]


def rewrite_extra(
    url: str,
    r18: bool = False,
    rank: str = "newest",
    topic: str = "",
    content_filter: str = "sfw",
) -> tuple[str, bool]:
    if not ph_match(url):
        return url, False
    p = urllib.parse.urlsplit(url)
    if p.path == PH_SEARCH_PATH:
        query = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))
        try:
            payload = json.loads(query.get("input") or "")["json"]
            payload["page"] = max(1, int(payload.get("page") or 1))
            payload["pageSize"] = max(1, min(100, int(payload.get("pageSize") or 24)))
            if rank in PH_SORTS:
                payload["sort"] = rank
            _topic_fields(payload, topic)
            payload["nsfw"] = content_filter != "sfw"
            return _api_url(f"{p.scheme or 'https'}://{p.netloc}", payload), False
        except (ValueError, KeyError, TypeError):
            return url, False
    query = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))
    sort = query.get("sort") or (rank if rank in PH_SORTS else "newest")
    selected_topic = topic
    if not selected_topic and query.get("category"):
        selected_topic = "cat:" + query["category"]
    elif not selected_topic and query.get("model"):
        selected_topic = "model:" + query["model"]
    payload = {
        "query": query.get("q") or query.get("query") or "",
        "page": 1,
        "pageSize": 24,
        "sort": sort,
        "nsfw": content_filter != "sfw",
    }
    _topic_fields(payload, selected_topic)
    return _api_url(f"{p.scheme or 'https'}://{p.netloc}", payload), True


def _ph_entry(url: str, prompt_id: str, group_id: str, prompt: str,
              total: int, title: str, nsfw: bool = False) -> dict:
    return {
        "url": url, "thumb": url, "prompt": prompt,
        "title": f"PromptHero · {title}", "nsfw": nsfw, "id": prompt_id,
        "referer": PH_REFERER, "group_id": group_id, "group_total": total,
        "group_title": prompt[:80] or "PromptHero",
    }


def _asset_items(row: dict) -> list[dict]:
    prompt = row.get("prompt") if isinstance(row.get("prompt"), dict) else {}
    asset = row.get("asset") if isinstance(row.get("asset"), dict) else {}
    if not asset or asset.get("mediaType") not in (None, "image"):
        return []
    nsfw = bool(prompt.get("isNsfw"))
    prompt_id = str(prompt.get("id") or asset.get("recordId") or "")
    group_id = str(asset.get("storageKey") or "")
    if not group_id:
        match = re.fullmatch(re.escape(PH_CDN) + r"/([a-z0-9]+)/[a-z0-9]+\.webp",
                             str(asset.get("url") or ""), re.I)
        if match:
            group_id = match.group(1)
    copies = asset.get("displayCopies")
    copies = copies if isinstance(copies, list) else []
    by_name = {str(x.get("name")): str(x.get("url") or "")
               for x in copies if isinstance(x, dict) and x.get("url")}
    levels = [by_name[s] for s in ("1920", "1080", "640", "square") if by_name.get(s)]
    original = str(asset.get("url") or "")
    if original and original not in levels:
        levels.append(original)
    if not levels:
        return []
    prompt_text = str(prompt.get("prompt") or "")[:400]
    items = [_ph_entry(u, prompt_id, group_id, prompt_text, len(levels),
                       u.rsplit("/", 1)[-1].replace(".webp", "").upper()
                       if u.startswith(PH_CDN) else "原图", nsfw)
             for u in levels]
    return items


def _parse_ph(text: str, url: str, settings: dict, limit: int):
    try:
        data = json.loads(text)
        data = data["result"]["data"]["json"]
    except (ValueError, KeyError, TypeError):
        return [], None, "json"
    rows = data.get("items") if isinstance(data, dict) else []
    items: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        group = _asset_items(row)
        if not group:
            continue
        cover = next((x for x in group if "/640.webp" in x["url"]), group[0])
        _remember_group(cover["group_id"], group)
        items.append(cover)
        if len(items) >= limit:
            break
    pagination = data.get("pagination") if isinstance(data, dict) else {}
    nxt = None
    if rows and isinstance(pagination, dict) and pagination.get("hasNextPage"):
        p = urllib.parse.urlsplit(url)
        raw_input = dict(urllib.parse.parse_qsl(p.query)).get("input") or ""
        try:
            payload = json.loads(raw_input)["json"]
            payload["page"] = int(payload.get("page") or 1) + 1
            nxt = _api_url(f"{p.scheme}://{p.netloc}", payload)
        except (ValueError, KeyError, TypeError):
            pass
    return sg._finish(items, limit), nxt, "json"


async def parse(url: str, text: str, settings: dict, limit: int):
    if not match(url):
        return None
    return _parse_ph(text, url, settings, limit)


async def expand(work_id: str, settings: dict, origin: str = "") -> list[dict]:
    group_id = str(work_id or "").strip()
    cached = cached_group(group_id)
    if cached:
        return cached
    if group_id and re.fullmatch(r"[a-z0-9]{20,40}", group_id) and ph_match(origin):
        sizes = ("1920", "1080", "640", "square")
        items = [_ph_entry(f"{PH_CDN}/{group_id}/{s}.webp", "", group_id, "",
                           len(sizes), s.upper()) for s in sizes]
        _remember_group(group_id, items)
        return items
    return []
