# -*- coding: utf-8 -*-
"""站点专用适配器：给「HTML 只是壳、图片全靠 JS 调接口」的 SPA 图库用。

以 aitag.win 为例：首页 HTML 里只有一张 og:image，真正的瀑布流来自
/api/ai_works_search，而每行的图片地址又要在 /api/work/{id} 里才能拿到。
通用提取器对此无能为力，所以这里按站点还原浏览器实际做的事：

    网页地址 -> 列表接口 -> 并发取每行详情 -> 拼出站点 CDN 的 webp 直链

**两层显示**：该站检索页是「一个作品 = 一张封面」，点开作品才列出同系列的
其它页图。适配器在抓列表时就把每个作品的全部图片缓存到 _GROUP_CACHE，
但只把封面交给前端；前端点击封面时走 /sg_gallery/expand 取回同系列图片，
插到封面后面。这样检索页的密度和站点一致，也不会一次性塞满网格。

模块刻意只依赖 sg_scraper，不 import ComfyUI，方便单独跑测试。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.parse
from typing import Any

import aiohttp

import sg_scraper as sg

AITAG_HOSTS = ("aitag.win", "www.aitag.win")
AITAG_ASSET_FALLBACK = "https://ai-img.10118899.xyz/"
AITAG_REFERER = "https://aitag.win/"
PIXIV_REFERER = "https://www.pixiv.net/"

_ASSET_CACHE: dict[str, Any] = {"url": "", "ts": 0.0}

# work_id -> 该作品的全部图片（含封面）。检索页只显示封面，点开时才用。
# 有上限的 FIFO，避免长时间抓取把内存撑大；缓存丢了也能按 id 现场重取。
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


# --------------------------------------------------------------------------- #
# 网址改写（由 sg_server.rewrite_gallery_url 调用）
# --------------------------------------------------------------------------- #
def rewrite_extra(url: str, r18: bool = False) -> tuple[str, bool]:
    """把图库网页地址改写成同站的 JSON 接口。

    r18=True 时请求该站的 18+ 作品集（show_r18=1）；该参数同时作用于
    用户自己粘贴的 /api/ai_works_search 地址，保证面板开关始终有效。
    """
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.netloc or "").lower().split(":")[0]
        path = (p.path or "").rstrip("/")
    except Exception:
        return url, False

    if host not in AITAG_HOSTS:
        return url, False
    show = "1" if r18 else "0"
    if path.startswith("/api/"):
        if "ai_works_search" in path:
            # 已经是接口地址了，只把 18+ 参数同步成面板开关的值
            fixed = sg.bump_query_param(url, "show_r18", show)
            return fixed or url, False
        return url, False

    origin = f"{p.scheme or 'https'}://{p.netloc}"
    q = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))

    # 单个作品页 /i/152099137 -> 直接取这一个作品的详情
    m = re.fullmatch(r"/i/(\d+)", path)
    if m:
        return f"{origin}/api/work/{m.group(1)}", True

    params = {
        "page": q.get("page") or "1",
        "page_size": q.get("page_size") or "60",  # 该接口要求 >= 60
        "sort": q.get("sort") or "new",
        "time_range": q.get("time_range") or "all",
        "show_r18": show,
    }
    # 用户在网址里带的搜索词要保留下来
    for k in ("q", "prompt", "tag", "tags", "sensitive_policy"):
        if q.get(k):
            params[k] = q[k]
    return f"{origin}/api/ai_works_search?" + urllib.parse.urlencode(params), True


def match(url: str) -> bool:
    """这个响应要不要交给适配器解析。"""
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.netloc or "").lower().split(":")[0]
        return host in AITAG_HOSTS and p.path.startswith("/api/")
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _origin(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme or 'https'}://{p.netloc}"


def _page_index(file_name: str) -> int:
    m = re.search(r"_p(\d+)$", file_name)
    return int(m.group(1)) if m else 0


def _image_url(asset: str, img: dict) -> str:
    """复刻站点 JS 的 buildImageUrl()。"""
    t = str(img.get("image_type") or "").strip()
    a = str(img.get("author_id") or "").strip()
    f = str(img.get("file_name") or "").strip()
    if t and a and f:
        return f"{asset}{t}/{a}/{f}.webp"
    p = str(img.get("image_path") or "")
    if p:
        p = re.sub(r"^/?(?:www/)?pixiv_ai_tag/", "", p)
        p = re.sub(r"\.png$", ".webp", p, flags=re.I).lstrip("/")
        return asset + p
    return ""


def _entry(url: str, work: dict, referer: str, prompt: str = "") -> dict:
    return {
        "url": url,
        "thumb": url,
        "prompt": prompt,
        "title": str(work.get("title") or ""),
        "width": None,
        "height": None,
        "nsfw": False,
        "id": str(work.get("id") or ""),
        "referer": referer,
    }


def _prompt_of(work: dict) -> str:
    """这个站主打「咒语」，把标题/说明/标签拼起来给 info_json 用。"""
    parts = [str(work.get(k) or "").strip() for k in ("title", "caption")]
    tags = work.get("tags")
    if isinstance(tags, str):
        try:
            parsed = json.loads(tags)
            if isinstance(parsed, list):
                tags = " ".join(str(t) for t in parsed)
        except Exception:
            pass
    if isinstance(tags, str):
        parts.append(tags.strip())
    return "\n".join(p for p in parts if p)[:2000]


def _fallback_urls(work: dict) -> list[str]:
    """详情接口失败时，退回列表里的 Pixiv 原图直链（需带 pixiv.net Referer）。"""
    raw = work.get("original_urls")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not isinstance(raw, list):
        return []
    return [u for u in raw if isinstance(u, str) and sg.is_image_url(u, None)]


async def _asset_base(origin: str, settings: dict) -> str:
    """CDN 域名从 /api/config 读，站点换了 CDN 也不会写死失效。"""
    now = time.time()
    if _ASSET_CACHE["url"] and now - _ASSET_CACHE["ts"] < 3600:
        return _ASSET_CACHE["url"]
    try:
        connector, proxy = sg._connector(settings)
        headers = sg.build_headers({**settings, "referer": origin + "/"},
                                   origin + "/api/config", json_mode=True)
        async with aiohttp.ClientSession(connector=connector, headers=headers) as sess:
            async with sess.get(origin + "/api/config", proxy=proxy,
                                timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    cfg = await resp.json(content_type=None)
                    base = str((cfg or {}).get("asset_base_url") or "").strip()
                    if base:
                        _ASSET_CACHE["url"] = base if base.endswith("/") else base + "/"
                        _ASSET_CACHE["ts"] = now
                        return _ASSET_CACHE["url"]
    except Exception:
        pass
    return AITAG_ASSET_FALLBACK


async def _details(origin: str, ids: list[str], settings: dict) -> dict[str, dict]:
    """并发取每行作品详情；复用一个 aiohttp 连接，60 条约 2~3 秒。"""
    if not ids:
        return {}
    headers = sg.build_headers({**settings, "referer": origin + "/"},
                               origin + "/api/work/1", json_mode=True)
    connector, proxy = sg._connector(settings)
    sem = asyncio.Semaphore(16)

    async def one(wid: str):
        async with sem:
            try:
                async with sess.get(f"{origin}/api/work/{wid}", proxy=proxy,
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return wid, await resp.json(content_type=None)
            except Exception:
                pass
            return wid, None

    async with aiohttp.ClientSession(connector=connector, headers=headers) as sess:
        results = await asyncio.gather(*[one(w) for w in ids])
    return {wid: d for wid, d in results if isinstance(d, dict)}


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
async def _parse_aitag(data: Any, url: str, settings: dict, limit: int):
    origin = _origin(url)
    asset = await _asset_base(origin, settings)

    # 单作品详情 /api/work/{id}
    if isinstance(data, dict) and isinstance(data.get("images"), list):
        work = data.get("work") if isinstance(data.get("work"), dict) else {}
        items = []
        for img in sorted(data["images"], key=lambda x: _page_index(str(x.get("file_name") or ""))):
            u = _image_url(asset, img) if isinstance(img, dict) else ""
            if u:
                items.append(_entry(u, work, AITAG_REFERER, _prompt_of(work)))
        return sg._finish(items, limit), None, "json"

    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None

    rows = [r for r in data["items"] if isinstance(r, dict)]
    ids = [str(r.get("id")) for r in rows if r.get("id")]
    details = await _details(origin, ids, settings)

    items: list[dict] = []
    for row in rows:
        wid = str(row.get("id") or "")
        detail = details.get(wid)
        images = detail.get("images") if isinstance(detail, dict) else None
        prompt = _prompt_of(row)
        children: list[dict] = []
        if isinstance(images, list) and images:
            for img in sorted(images, key=lambda x: _page_index(str(x.get("file_name") or ""))):
                u = _image_url(asset, img) if isinstance(img, dict) else ""
                if u:
                    children.append(_entry(u, row, AITAG_REFERER, prompt))
        if not children:
            # 详情接口偶发失败：退回 Pixiv 原图（体积大，但至少能出图）
            for u in _fallback_urls(row)[:1]:
                children.append(_entry(u, row, PIXIV_REFERER, prompt))
        if not children:
            continue

        # 只把封面交给检索页；同系列图片缓存起来，等用户点开再取
        _remember_group(wid, children)
        cover = dict(children[0])
        cover["group_id"] = wid
        cover["group_total"] = len(children)
        cover["group_title"] = str(row.get("title") or "")
        items.append(cover)
        if len(items) >= limit:
            break

    try:
        page = int(data.get("page") or 1)
        page_size = int(data.get("page_size") or 0)
        total = int(data.get("total") or 0)
    except (TypeError, ValueError):
        page, page_size, total = 1, 0, 0
    nxt = None
    if rows and page_size > 0 and total > page * page_size:
        nxt = sg.bump_query_param(url, "page", page + 1)

    return sg._finish(items, limit), nxt, "json"


async def expand(work_id: str, settings: dict, origin: str = "") -> list[dict]:
    """按作品 id 返回该作品的全部图片（含封面）。

    优先用抓取列表时缓存的结果；缓存丢失（例如 ComfyUI 重启后从工作流
    恢复了封面）就现场请求 /api/work/{id}，所以旧工作流也照样能点开。
    """
    wid = str(work_id or "").strip()
    if not wid.isdigit():
        return []
    cached = cached_group(wid)
    if cached:
        return cached

    # 前端传的是网址框里的整条地址（可能带 ?q= 或 /api/...），只取 origin
    try:
        p = urllib.parse.urlsplit(origin or "")
        base = f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else "https://aitag.win"
    except Exception:
        base = "https://aitag.win"
    asset = await _asset_base(base, settings)
    headers = sg.build_headers({**settings, "referer": base + "/"},
                               f"{base}/api/work/{wid}", json_mode=True)
    connector, proxy = sg._connector(settings)
    try:
        async with aiohttp.ClientSession(connector=connector, headers=headers) as sess:
            async with sess.get(f"{base}/api/work/{wid}", proxy=proxy,
                                timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
    except Exception:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("images"), list):
        return []

    work = data.get("work") if isinstance(data.get("work"), dict) else {}
    prompt = _prompt_of(work)
    children: list[dict] = []
    for img in sorted(data["images"],
                      key=lambda x: _page_index(str(x.get("file_name") or ""))):
        u = _image_url(asset, img) if isinstance(img, dict) else ""
        if u:
            children.append(_entry(u, work, AITAG_REFERER, prompt))
    if not children:
        for u in _fallback_urls(work)[:1]:
            children.append(_entry(u, work, PIXIV_REFERER, prompt))
    if children:
        _remember_group(wid, children)
    return children


async def parse(url: str, text: str, settings: dict, limit: int):
    """命中站点适配器时返回 (items, next_url, kind)，否则返回 None 交给通用提取器。"""
    if not match(url):
        return None
    try:
        data = json.loads(text.lstrip())
    except Exception:
        return None
    try:
        return await asyncio.wait_for(_parse_aitag(data, url, settings, limit), timeout=35)
    except asyncio.TimeoutError:
        raise ValueError("该站点图片详情请求超时，请重试或缩小每页数量")
    return None
