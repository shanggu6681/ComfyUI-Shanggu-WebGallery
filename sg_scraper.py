# -*- coding: utf-8 -*-
"""
Shanggu Web Gallery - 网页/接口抓取与图片地址提取。

这个模块刻意不 import 任何 ComfyUI 内部模块，方便单独测试。
支持三种来源：
  1. 普通 HTML（img / srcset / 懒加载属性 / 背景图 / og:image）
  2. SPA 内嵌 JSON（__NEXT_DATA__、application/json、JS 字符串里的 URL）
  3. 纯 JSON API（递归遍历，自动识别 nextPage / nextCursor 游标分页）
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

import aiohttp

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".jxl", ".tif", ".tiff")
BAD_TOKENS = (
    "logo", "icon", "avatar", "sprite", "placeholder", "favicon", "badge",
    "emoji", "spinner", "loading", "1x1", "pixel.gif", "blank.gif", "gravatar",
)
PRIMARY_KEYS = (
    "originalurl", "original_url", "original", "url", "imageurl", "image_url",
    "image", "src", "srcurl", "file_url", "fullurl", "full", "download_url", "link", "path",
)
THUMB_KEYS = (
    "thumbnailurl", "thumbnail_url", "thumbnail", "thumburl", "thumb_url", "thumb",
    "previewurl", "preview_url", "preview", "smallurl", "small", "medium", "cover",
)
TEXT_KEYS = ("prompt", "description", "caption", "alttext", "alt", "title", "text", "name")
NEXT_KEYS = (
    "nextpage", "next_page", "nexturl", "next_url", "nextlink", "next_link",
    "@odata.nextlink", "next", "more", "loadmoreurl",
)
CURSOR_KEYS = ("nextcursor", "next_cursor", "cursor", "after", "next_offset")

_URL_RE = re.compile(r"https?://[^\s\"'<>)\\}]+", re.I)
_BG_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.I)
_SIZE_RE = re.compile(r"/(?:original=true|width=\d+|height=\d+|size=[^/]+)", re.I)


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def normalize_url(u: str, base: str = "") -> str:
    """把相对地址、被转义的地址还原成绝对 URL。"""
    if not isinstance(u, str):
        return ""
    u = u.strip().strip("\"'").replace("\\/", "/").replace("\\u002F", "/").replace("\\u0026", "&")
    u = u.rstrip("\\,;")
    if not u or u.startswith(("data:", "javascript:", "blob:", "mailto:", "#")):
        return ""
    if u.startswith("//"):
        u = "https:" + u
    if base and not re.match(r"^https?://", u, re.I):
        try:
            u = urllib.parse.urljoin(base, u)
        except Exception:
            return ""
    return u if re.match(r"^https?://", u, re.I) else ""


def canonical_url(u: str) -> str:
    """去重用的规范化地址（把 CDN 的尺寸变体折叠成同一个）。"""
    try:
        p = urllib.parse.urlsplit(u)
    except Exception:
        return u
    path = _SIZE_RE.sub("/@size@", p.path)
    if any(path.lower().endswith(e) for e in IMG_EXTS):
        return (p.netloc + path).lower()
    return (p.netloc + path + "?" + p.query).lower()


def is_image_url(u: str, regex: re.Pattern | None = None) -> bool:
    if not isinstance(u, str):
        return False
    u = u.strip()
    if len(u) < 10 or len(u) > 2000:
        return False
    if u.startswith(("data:", "javascript:", "blob:", "mailto:")):
        return False
    low = u.lower()
    if any(tok in low for tok in BAD_TOKENS):
        return False
    if regex is not None:
        return bool(regex.search(u))
    try:
        p = urllib.parse.urlsplit(u)
    except Exception:
        return False
    path = p.path.lower()
    if any(path.endswith(e) for e in IMG_EXTS):
        return True
    if "original=true" in low or "/width=" in low or "image.civitai.com" in low:
        return True
    if re.search(r"(?:^|&)(?:format|fm|mime)=(?:jpe?g|png|webp|avif|gif)", p.query, re.I):
        return True
    # 无扩展名的 CDN 直链（长 hash / uuid 形态）
    if re.search(r"(image|img|cdn|media|photo|static)", p.netloc, re.I) and re.search(
        r"/[0-9a-f]{8,}(?:[-_][0-9a-f]{4,}){0,4}(?:/|$)", path, re.I
    ):
        return True
    return False


def thumb_url(u: str) -> str:
    """Civitai CDN 支持把 original=true 换成 width=400，网格加载会快很多。"""
    if "original=true" in u:
        return u.replace("original=true", "width=400")
    if "/width=" in u:
        return re.sub(r"/width=\d+", "/width=400", u)
    return u


def _is_nsfw(obj: dict) -> bool:
    if obj.get("nsfw") is True or str(obj.get("nsfw", "")).lower() in ("true", "1", "yes"):
        return True
    for k in ("nsfwLevel", "browsingLevel", "rating"):
        v = obj.get(k)
        if isinstance(v, (int, float)) and v >= 8:
            return True
    return False


def _make_item(primary: str, thumb: str | None, obj: dict, base: str) -> dict:
    u = normalize_url(primary, base)
    t = normalize_url(thumb, base) if thumb else thumb_url(u)
    prompt = ""
    for k in TEXT_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and 10 < len(v.strip()) < 8000:
            prompt = v.strip()
            break
    meta = obj.get("meta")
    if isinstance(meta, dict) and not prompt:
        for k in ("prompt", "description", "caption"):
            v = meta.get(k)
            if isinstance(v, str) and v.strip():
                prompt = v.strip()
                break
    return {
        "url": u,
        "thumb": t or u,
        "prompt": prompt,
        "title": str(obj.get("title") or obj.get("username") or obj.get("id") or ""),
        "width": obj.get("width") if isinstance(obj.get("width"), int) else None,
        "height": obj.get("height") if isinstance(obj.get("height"), int) else None,
        "nsfw": _is_nsfw(obj),
        "id": str(obj.get("id") or obj.get("postId") or ""),
    }


# --------------------------------------------------------------------------- #
# JSON 递归提取
# --------------------------------------------------------------------------- #
def _json_walk(data: Any, base: str, regex: re.Pattern | None, safe_only: bool) -> list[dict]:
    out: list[dict] = []

    def rec(obj: Any, depth: int = 0) -> None:
        if depth > 14:
            return
        if isinstance(obj, dict):
            cands: list[tuple[str, str]] = []
            for k, v in obj.items():
                if isinstance(v, str) and is_image_url(v, regex):
                    cands.append((str(k).lower(), v))
            if cands:
                def rank(kv: tuple[str, str]) -> int:
                    k = kv[0]
                    for i, p in enumerate(PRIMARY_KEYS):
                        if k == p:
                            return i
                    for i, p in enumerate(THUMB_KEYS):
                        if k == p:
                            return 50 + i
                    return 20

                cands.sort(key=rank)
                primary = cands[0][1]
                thumb = None
                for k, v in obj.items():
                    if str(k).lower() in THUMB_KEYS and isinstance(v, str) and is_image_url(v, regex):
                        thumb = v
                        break
                if not (safe_only and _is_nsfw(obj)):
                    out.append(_make_item(primary, thumb, obj, base))
            for v in obj.values():
                rec(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj[:800]:
                rec(v, depth + 1)

    rec(data)
    return out


def _find_cursor(obj: Any, depth: int = 0) -> str | None:
    if depth > 8:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in CURSOR_KEYS and isinstance(v, (str, int)) and str(v):
                return str(v)
        for v in obj.values():
            got = _find_cursor(v, depth + 1)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj[:20]:
            got = _find_cursor(v, depth + 1)
            if got:
                return got
    return None


def _find_next_url(obj: Any, base: str, depth: int = 0) -> str | None:
    if depth > 8:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in NEXT_KEYS and isinstance(v, str):
                absu = normalize_url(v, base)
                if absu:
                    return absu
        for v in obj.values():
            got = _find_next_url(v, base, depth + 1)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj[:20]:
            got = _find_next_url(v, base, depth + 1)
            if got:
                return got
    return None


def render_page_template(tpl: str, base: str, page: int) -> str:
    """把 ?page={page} 之类的模板渲染成绝对 URL。"""
    out = (tpl or "").replace("{url}", base or "").replace("{page}", str(page)).replace(
        "{offset}", str((page - 1) * 20)
    )
    if out and not re.match(r"^https?://", out, re.I):
        out = urllib.parse.urljoin(base or "", out)
    return out


def next_page_url(data: Any, base: str, current: str, page_template: str, page: int) -> str | None:
    """优先级：接口返回的 nextPage > {cursor} 模板 > {page}/{offset} 模板。"""
    nxt = _find_next_url(data, base)
    if nxt:
        return nxt
    cursor = _find_cursor(data)
    tpl = (page_template or "").strip()
    if cursor and tpl and "{cursor}" in tpl:
        return tpl.replace("{cursor}", urllib.parse.quote(cursor, safe=""))
    if cursor and "cursor=" in current:
        return re.sub(
            r"([?&])cursor=[^&]*",
            lambda m: m.group(1) + "cursor=" + urllib.parse.quote(cursor, safe=""),
            current,
        )
    if tpl and ("{page}" in tpl or "{offset}" in tpl):
        return render_page_template(tpl, base or current, page)
    return None


# --------------------------------------------------------------------------- #
# HTML / 文本提取
# --------------------------------------------------------------------------- #
def _html_attrs(text: str, base: str, regex: re.Pattern | None) -> list[dict]:
    found: list[dict] = []
    if BeautifulSoup is None:
        return found
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup.find_all(True):
        for attr, val in tag.attrs.items():
            if not isinstance(val, str):
                continue
            aname = str(attr).lower()
            if "srcset" in aname:
                for part in val.split(","):
                    token = part.strip().split(" ")[0]
                    u = normalize_url(token, base)
                    if is_image_url(u, regex):
                        found.append(_make_item(u, None, {}, base))
                continue
            if aname == "style":
                for hit in _BG_RE.findall(val):
                    u = normalize_url(hit, base)
                    if is_image_url(u, regex):
                        found.append(_make_item(u, None, {}, base))
                continue
            u = normalize_url(val, base)
            if is_image_url(u, regex):
                obj: dict = {}
                if tag.name == "img":
                    obj = {"alt": tag.get("alt") or "", "title": tag.get("title") or ""}
                found.append(_make_item(u, None, obj, base))
    return found


def _embedded_json(text: str, base: str, regex: re.Pattern | None, safe_only: bool) -> tuple[list[dict], Any]:
    items: list[dict] = []
    data_for_next = None
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", text, re.I | re.S):
        chunk = m.group(1).strip()
        if len(chunk) < 20 or len(chunk) > 4_000_000:
            continue
        candidate = None
        if chunk.startswith(("{", "[")):
            try:
                candidate = json.loads(chunk)
            except Exception:
                candidate = None
        if candidate is None:
            # window.__NUXT__ = {...}; / self.__next_f.push([1,"..."])
            jm = re.search(r"=\s*(\{.*\}|\[.*\])\s*;?\s*$", chunk, re.S)
            if jm:
                try:
                    candidate = json.loads(jm.group(1))
                except Exception:
                    candidate = None
        if candidate is not None:
            data_for_next = data_for_next or candidate
            items.extend(_json_walk(candidate, base, regex, safe_only))
    return items, data_for_next


def _raw_urls(text: str, base: str, regex: re.Pattern | None) -> list[dict]:
    """从原始文本里捞 URL。自定义正则时，匹配结果直接采信（不再拿正则去校验 URL 本身）。"""
    flat = text.replace("\\/", "/").replace("\\u002F", "/").replace("\\u0026", "&")
    out: list[dict] = []
    if regex is not None:
        for m in regex.finditer(flat):
            u = normalize_url(m.group(1) if m.groups() else m.group(0), base)
            if not u or any(tok in u.lower() for tok in BAD_TOKENS) or u.lower().split("?")[0].endswith(".svg"):
                continue
            out.append(_make_item(u, None, {}, base))
        return out
    for raw in _URL_RE.findall(flat):
        u = normalize_url(raw, base)
        if is_image_url(u, None):
            out.append(_make_item(u, None, {}, base))
    return out


def _html_next(text: str, base: str) -> str | None:
    m = re.search(r"<(?:link|a)\b[^>]*rel=[\"'][^\"']*\bnext\b[^\"']*[\"'][^>]*href=[\"']([^\"']+)[\"']", text, re.I)
    if not m:
        m = re.search(r"<(?:link|a)\b[^>]*href=[\"']([^\"']+)[\"'][^>]*rel=[\"'][^\"']*\bnext\b", text, re.I)
    if m:
        return normalize_url(m.group(1), base)
    return None


def extract(
    text: str,
    content_type: str = "",
    base_url: str = "",
    *,
    url_regex: str = "",
    safe_only: bool = True,
    page_template: str = "",
    page: int = 1,
    current_url: str = "",
    limit: int = 200,
) -> tuple[list[dict], str | None, str]:
    """返回 (图片列表, 下一页地址, 来源类型)。"""
    regex = None
    if url_regex.strip():
        try:
            regex = re.compile(url_regex.strip(), re.I)
        except re.error as e:
            raise ValueError(f"自定义正则无效: {e}")

    ctype = (content_type or "").lower()
    stripped = text.lstrip()
    json_data = None
    looks_json = "json" in ctype or stripped.startswith(("{", "["))
    if looks_json:
        try:
            json_data = json.loads(stripped)
        except Exception:
            json_data = None

    # 纯 URL 清单（粘贴模式常见）
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines and all(re.match(r"^https?://", l, re.I) for l in lines[:50]) and not looks_json:
        raw_items = [_make_item(l, None, {}, base_url) for l in lines if is_image_url(l, None)]
        return _finish(raw_items, limit), None, "url-list"

    items: list[dict] = []
    nxt = None
    if regex is not None:
        items.extend(_raw_urls(text, base_url, regex))
    if json_data is not None:
        items.extend(_json_walk(json_data, base_url, None, safe_only))
        if not items:
            items.extend(_raw_urls(text, base_url, None))
        nxt = next_page_url(json_data, base_url, current_url or base_url, page_template, page)
        kind = "json"
    else:
        items.extend(_html_attrs(text, base_url, None))
        emb_items, emb_data = _embedded_json(text, base_url, None, safe_only)
        items.extend(emb_items)
        items.extend(_raw_urls(text, base_url, None))
        if emb_data is not None:
            nxt = _find_next_url(emb_data, base_url)
        if not nxt:
            nxt = _html_next(text, base_url)
        if not nxt and page_template and ("{page}" in page_template or "{offset}" in page_template):
            nxt = render_page_template(page_template, base_url or current_url, page)
        kind = "html"

    return _finish(items, limit), nxt, kind


def _finish(items: list[dict], limit: int) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        u = it.get("url") or ""
        key = canonical_url(u)
        if not u or not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= max(1, limit):
            break
    return out


# --------------------------------------------------------------------------- #
# 网络请求
# --------------------------------------------------------------------------- #
def build_headers(settings: dict, url: str = "", json_mode: bool = False) -> dict:
    headers = {
        "User-Agent": (settings.get("ua") or DEFAULT_UA).strip() or DEFAULT_UA,
        "Accept": "application/json, text/plain, */*" if json_mode
                  else "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Fetch-Dest": "empty" if json_mode else "document",
        "Sec-Fetch-Mode": "cors" if json_mode else "navigate",
        "Upgrade-Insecure-Requests": "0" if json_mode else "1",
    }
    referer = (settings.get("referer") or "").strip()
    if not referer and url:
        p = urllib.parse.urlsplit(url)
        referer = f"{p.scheme}://{p.netloc}/"
    if referer:
        headers["Referer"] = referer
    cookie = (settings.get("cookie") or "").strip()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _connector(settings: dict):
    """返回一个 (connector, proxy_arg)。SOCKS5 走 aiohttp_socks，HTTP 走 aiohttp 原生 proxy。"""
    proxy = (settings.get("proxy") or "").strip()
    if proxy.lower().startswith("socks"):
        try:
            from aiohttp_socks import ProxyConnector
            return ProxyConnector.from_url(proxy, ssl=False), None
        except Exception:
            pass
    return aiohttp.TCPConnector(ssl=False, limit=16), proxy or None


async def fetch(url: str, settings: dict, *, timeout: int = 25, json_mode: bool = False):
    """返回 (status, content_type, bytes, final_url)。"""
    connector, proxy_arg = _connector(settings)
    headers = build_headers(settings, url, json_mode)
    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        async with session.get(
            url,
            proxy=proxy_arg,
            allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            body = await resp.read()
            return resp.status, resp.headers.get("Content-Type", ""), body, str(resp.url)
