# -*- coding: utf-8 -*-
"""Shanggu Web Gallery 的本地 HTTP 接口（挂在 ComfyUI 自带 aiohttp 服务上）。

浏览器前端不能跨域抓别的网站，所以所有请求都由这里代发。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import urllib.parse
from typing import Any

from aiohttp import web

import sg_scraper as sg
import sg_adapters as adapters

SESSIONS: dict[str, dict] = {}
MAX_SESSIONS = 64
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "thumbs")


# --------------------------------------------------------------------------- #
# 会话：负责跨"滚动"去重、记住游标
# --------------------------------------------------------------------------- #
def get_session(sid: str, reset: bool = False) -> dict:
    sid = (sid or "default")[:64]
    if reset or sid not in SESSIONS:
        SESSIONS[sid] = {
            "seen": set(),
            "page": 1,
            "next": None,
            "base": "",
            "items": [],
            "settings": {},
            "exhausted": False,
            "created": time.time(),
        }
        if len(SESSIONS) > MAX_SESSIONS:
            oldest = sorted(SESSIONS, key=lambda k: SESSIONS[k]["created"])[: len(SESSIONS) - MAX_SESSIONS]
            for k in oldest:
                SESSIONS.pop(k, None)
    return SESSIONS[sid]


def _clean_settings(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    keys = ("cookie", "ua", "referer", "proxy", "url_regex", "page_template", "safe_only", "per_page",
            "content_filter", "rank", "topic")
    out = {k: raw.get(k, "") for k in keys}
    mode = str(raw.get("content_filter") or "").strip().lower()
    if mode not in ("sfw", "all", "nsfw"):
        mode = "sfw" if raw.get("safe_only", True) is not False else "all"
    out["content_filter"] = mode
    out["safe_only"] = mode == "sfw"
    # 保留旧工作流里的 r18 字段；Civitai 榜单由 rank 控制。
    rv = raw.get("r18", True)
    out["r18"] = rv is not False and str(rv).lower() not in ("false", "0", "no", "off", "")
    out["rank"] = str(raw.get("rank") or "newest").strip().lower()
    out["topic"] = str(raw.get("topic") or "").strip()
    try:
        out["per_page"] = max(1, min(400, int(raw.get("per_page") or 200)))
    except Exception:
        out["per_page"] = 200
    return out


def _filter_content(items: list[dict], mode: str) -> list[dict]:
    if mode == "all":
        return items
    want_nsfw = mode == "nsfw"
    return [it for it in items if bool(it.get("nsfw")) == want_nsfw]


def _new_items(s: dict, items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        key = sg.canonical_url(it.get("url", ""))
        if not key or key in s["seen"]:
            continue
        s["seen"].add(key)
        s["items"].append(it)
        out.append(it)
    return out


def rewrite_gallery_url(
    url: str,
    r18: bool = True,
    rank: str = "newest",
    topic: str = "",
    content_filter: str = "sfw",
) -> tuple[str, bool]:
    """网页版图库被 Cloudflare 挡住 / 是 SPA 时，自动改用同站的官方 JSON 接口。

    返回值第二个元素只表示「网页地址被换成接口」，用于前端提示；
    18+ / 榜单 / 分类参数的同步不算改写，但仍会返回调整后的地址。
    """
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.netloc or "").lower().split(":")[0]
        path = (p.path or "").rstrip("/")
        if host.startswith(("civitai.", "www.civitai.")):
            if path.startswith("/api/v1/images"):
                return _civitai_rank_url(
                    f"https://{p.netloc}{p.path}", rank, p.query, topic, content_filter
                ), False
            if path in ("", "/images", "/models"):
                return _civitai_rank_url(
                    f"https://{p.netloc}/api/v1/images", rank, "", topic, content_filter
                ), True
    except Exception:
        pass
    extra, hit = adapters.rewrite_extra(
        url, r18, rank, topic=topic, content_filter=content_filter
    )
    return extra or url, bool(hit)


def _civitai_rank_url(
    api_base: str,
    rank: str,
    old_query: str = "",
    topic: str = "",
    content_filter: str = "sfw",
) -> str:
    """给 civitai /api/v1/images 加上 sort + period，得到不同榜单。

    实测该接口支持：
      sort=Newest | Most Reactions | Most Comments | Most Collected
      period=AllTime | Day | Week | Month | Year
    「周榜/月榜/年榜/总榜」= Most Reactions + 对应 period；默认流 = sort=Newest。
    """
    params = dict(urllib.parse.parse_qsl(old_query, keep_blank_values=True))
    params.setdefault("limit", "20")
    params.pop("sort", None)
    params.pop("period", None)
    params.pop("tags", None)
    params.pop("tag", None)
    if str(topic or "").startswith("tag:"):
        params["tags"] = str(topic)[4:]
    params["nsfw"] = "true" if content_filter != "sfw" else "false"
    table = {
        "newest":     ("Newest", None),
        "reactions":  ("Most Reactions", "AllTime"),
        "all":        ("Most Reactions", "AllTime"),
        "week":       ("Most Reactions", "Week"),
        "month":      ("Most Reactions", "Month"),
        "year":       ("Most Reactions", "Year"),
        "day":        ("Most Reactions", "Day"),
        "comments":   ("Most Comments", None),
        "collected":  ("Most Collected", None),
        "comments_week":  ("Most Comments", "Week"),
        "collected_week": ("Most Collected", "Week"),
    }
    sort, period = table.get(rank, ("Newest", None))
    params["sort"] = sort
    if period:
        params["period"] = period
    return api_base + "?" + urllib.parse.urlencode(params)


def _render_template(tpl: str, base: str, page: int) -> str:
    out = (tpl or "").replace("{url}", base).replace("{page}", str(page)).replace(
        "{offset}", str((page - 1) * 20)
    )
    if out and not out.lower().startswith(("http://", "https://")):
        out = urllib.parse.urljoin(base, out)
    return out


def _settings_for(url: str, base: dict, sid: str = "") -> dict:
    """下载/出缩略图时补上防盗链 Referer。

    优先用用户在高级设置里显式填的；其次用抓取时该图片自带的 referer
    （SPA 适配器会记录）；最后按域名规则推导。
    """
    out = dict(base or {})
    if str(out.get("referer") or "").strip():
        return out
    sess = SESSIONS.get(sid or "default") or {}
    for it in sess.get("items", []):
        if it.get("url") == url and str(it.get("referer") or "").strip():
            out["referer"] = it["referer"]
            return out
    out["referer"] = sg.referer_for(url)
    return out


def _error(msg: str, **extra) -> web.Response:
    headers = extra.pop("headers", None)
    body = {"ok": False, "error": msg, "items": [], "has_more": False}
    body.update(extra)
    return web.json_response(body, headers=headers)


# --------------------------------------------------------------------------- #
# /fetch —— 抓一页（reset=true 是重新搜索，false 是向下滚动加载更多）
# --------------------------------------------------------------------------- #
async def handle_fetch(request: web.Request) -> web.Response:
    try:
        d = await request.json()
    except Exception:
        return _error("请求体不是合法 JSON")
    sid = str(d.get("session_id") or "default")
    reset = bool(d.get("reset", True))
    url = str(d.get("url") or "").strip()
    settings = _clean_settings(d.get("settings"))
    # PromptHero 对浏览器 UA 返回 Cloudflare 挑战，curl UA 可直接取得页面。
    if "prompthero.com" in url.lower() and not settings.get("ua"):
        settings["ua"] = "curl/8.0"
    s = get_session(sid, reset)
    s["settings"] = settings

    rewritten = False
    if reset:
        url, rewritten = rewrite_gallery_url(
            url,
            bool(settings.get("r18", True)),
            str(settings.get("rank") or "newest"),
            topic=str(settings.get("topic") or ""),
            content_filter=str(settings.get("content_filter") or "sfw"),
        )
        s["base"] = url
        s["next"] = None
        s["page"] = 1
        s["exhausted"] = False
    if not s["base"]:
        return _error("请先填写网址")
    if s["exhausted"]:
        return web.json_response({"ok": True, "items": [], "has_more": False, "total": len(s["items"]), "message": "已加载到底"})

    target = s["next"] or s["base"]
    next_page_no = s["page"] + 1
    import re as _re
    json_mode = bool(_re.search(r"/api/|\.json(\?|$)|\?.*format=json|/graphql", target, _re.I))
    try:
        status, ctype, body, final = await sg.fetch(
            target, settings, timeout=int(d.get("timeout") or 25),
            json_mode=json_mode,
        )
    except Exception as e:
        return _error(f"请求失败: {type(e).__name__}: {e}")

    if status >= 400:
        hint = ""
        if status in (401, 403):
                hint = (" —— 站点可能启用了 Cloudflare / 需要登录。"
                        "可尝试：① 换成该站的 JSON API 地址；"
                        "② 在高级设置里填浏览器 Cookie 和相同 UA；③ 配置代理。")
        return _error(f"HTTP {status}{hint}", status=status)

    text = body.decode("utf-8", errors="replace")
    try:
        adapted = await adapters.parse(final, text, settings, settings["per_page"])
        if adapted is not None:
            items, nxt, kind = adapted
        else:
            items, nxt, kind = sg.extract(
                text, ctype, final,
                url_regex=settings.get("url_regex", ""),
                safe_only=False,
                page_template=settings.get("page_template", ""),
                page=next_page_no,
                current_url=final,
                limit=settings["per_page"],
            )
    except Exception as e:
        return _error(f"解析失败: {e}")

    items = _filter_content(items, settings["content_filter"])
    new = _new_items(s, items)
    if nxt and nxt != target:
        s["next"] = nxt
    elif settings.get("page_template") and ("{page}" in settings["page_template"] or "{offset}" in settings["page_template"]):
        s["next"] = _render_template(settings["page_template"], s["base"], next_page_no)
    else:
        s["next"] = None
    s["page"] = next_page_no

    # 滚动加载不能因为「这一页恰好 0 张新图」就永久停住：站点偶发重复、
    # 限流或整页被去重时，下一页仍然有内容。只有连续 3 次空页才认定到底。
    if not s["next"]:
        s["exhausted"] = True
        s["empty_pages"] = 0
    elif new:
        s["empty_pages"] = 0
    else:
        s["empty_pages"] = int(s.get("empty_pages", 0)) + 1
        if s["empty_pages"] >= 3:
            s["next"] = None
            s["exhausted"] = True
    has_more = bool(s["next"])

    return web.json_response({
        "ok": True,
        "items": new,
        "has_more": has_more,
        "page": s["page"] - 1,
        "total": len(s["items"]),
        "kind": kind,
        "next": s["next"] or "",
        "status": status,
        "effective_url": s["base"],
        "rewritten": rewritten,
        "message": "" if new else ("这一页没有新图片，继续尝试下一页…" if has_more
                                   else "这一页没有发现新图片，已停止加载"),
    })


# --------------------------------------------------------------------------- #
# /parse —— 解析粘贴进来的 HTML / JSON / 网址清单（完全不走网络）
# --------------------------------------------------------------------------- #
async def handle_parse(request: web.Request) -> web.Response:
    try:
        d = await request.json()
    except Exception:
        return _error("请求体不是合法 JSON")
    raw = str(d.get("raw") or "")
    if not raw.strip():
        return _error("没有可解析的内容")
    sid = str(d.get("session_id") or "default")
    reset = bool(d.get("reset", True))
    settings = _clean_settings(d.get("settings"))
    s = get_session(sid, reset)
    if reset:
        s["base"] = str(d.get("base_url") or "")
        s["next"] = None
        s["seen"] = set()
        s["items"] = []
        s["exhausted"] = True
    try:
        items, _nxt, kind = sg.extract(
            raw, "text/plain", s["base"],
            url_regex=settings.get("url_regex", ""),
            safe_only=False,
            limit=settings["per_page"],
        )
    except Exception as e:
        return _error(f"解析失败: {e}")
    items = _filter_content(items, settings["content_filter"])
    new = _new_items(s, items)
    return web.json_response({
        "ok": True, "items": new, "has_more": False,
        "total": len(s["items"]), "kind": kind,
        "message": "" if new else "没有解析到新的图片地址",
    })


# --------------------------------------------------------------------------- #
# /expand —— 点开检索页封面时取回该作品的同系列图片（SPA 站点专用）
# --------------------------------------------------------------------------- #
async def handle_expand(request: web.Request) -> web.Response:
    import asyncio
    try:
        d = await request.json()
    except Exception:
        return _error("请求体不是合法 JSON")
    wid = str(d.get("work_id") or "").strip()
    if not wid:
        return _error("缺少 work_id")
    sid = str(d.get("session_id") or "default")
    settings = _clean_settings(d.get("settings")) or get_session(sid).get("settings", {})
    try:
        items = await asyncio.wait_for(
            adapters.expand(wid, settings, str(d.get("origin") or "")), timeout=25
        )
    except Exception as e:
        return _error(f"展开失败: {type(e).__name__}: {e}")
    if not items:
        return _error("没有取到该作品的图片，站点可能暂时不可用")
    return web.json_response({"ok": True, "items": items, "total": len(items)})


# --------------------------------------------------------------------------- #
# /thumb —— 缩略图代理（绕过防盗链 / CORS / 混合内容，并压缩到网格尺寸）
# --------------------------------------------------------------------------- #
async def handle_thumb(request: web.Request) -> web.Response:
    url = request.query.get("url", "")
    if not url:
        return web.Response(status=404)
    try:
        w = max(64, min(1024, int(request.query.get("w", 320))))
    except Exception:
        w = 320
    sid = request.query.get("session_id", "default")
    settings = _settings_for(url, get_session(sid).get("settings", {}), sid)
    # 展开出来的同系列图片不在 session.items 里，前端把各自的 Referer 带过来
    ref = str(request.query.get("referer") or "").strip()
    if ref:
        settings["referer"] = ref
    key = hashlib.sha1((url + "|" + str(w)).encode("utf-8")).hexdigest() + ".jpg"
    path = os.path.join(CACHE_DIR, key)
    if os.path.isfile(path):
        with open(path, "rb") as f:
            return web.Response(body=f.read(), content_type="image/jpeg",
                                headers={"Cache-Control": "public, max-age=604800"})
    try:
        status, ctype, body, _final = await sg.fetch(url, settings, timeout=25)
        if status != 200 or not body:
            return web.Response(status=404)
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(body))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((w, w * 4), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82)
        data = buf.getvalue()
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return web.Response(body=data, content_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=604800"})
    except Exception:
        try:
            return web.Response(body=body, content_type=(ctype or "application/octet-stream").split(";")[0])
        except Exception:
            return web.Response(status=404)


# --------------------------------------------------------------------------- #
# /select —— 把选中的原图下载到 ComfyUI 的 input 目录（等价于「加载图像」的文件）
# --------------------------------------------------------------------------- #
async def handle_select(request: web.Request) -> web.Response:
    try:
        d = await request.json()
    except Exception:
        return _error("请求体不是合法 JSON")
    url = str(d.get("url") or "").strip()
    if not url:
        return _error("缺少图片地址")
    sid = str(d.get("session_id") or "default")
    settings = _settings_for(
        url, _clean_settings(d.get("settings")) or get_session(sid).get("settings", {}), sid
    )
    ref = str(d.get("referer") or "").strip()
    if ref:
        settings["referer"] = ref

    try:
        import folder_paths
        input_dir = folder_paths.get_input_directory()
    except Exception:
        input_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "input")
    os.makedirs(input_dir, exist_ok=True)

    stem = "sg_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    ext = os.path.splitext(urllib.parse.urlsplit(url).path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    filename = stem + ext
    dest = os.path.join(input_dir, filename)

    # 下载时会按 Content-Type 改扩展名（.jpeg→.jpg / .jpg→.png），若只按
    # URL 后缀查缓存就会每次点选都重新下载几 MB 原图。同 hash 的任意变体
    # 都视为命中缓存，单图模式「点哪个换哪个」才不会卡。
    if not os.path.isfile(dest):
        for cand_ext in (".jpg", ".jpeg", ".png", ".webp"):
            cand = os.path.join(input_dir, stem + cand_ext)
            if os.path.isfile(cand):
                filename, dest = stem + cand_ext, cand
                break

    if not os.path.isfile(dest):
        try:
            status, ctype, body, _final = await sg.fetch(url, settings, timeout=60)
            if status != 200 or not body:
                return _error(f"下载失败 HTTP {status}", status=status)
            ctype = (ctype or "").lower()
            if "png" in ctype:
                ext = ".png"
            elif "webp" in ctype:
                ext = ".webp"
            elif "gif" in ctype or "avif" in ctype or not body[:4]:
                ext = ".png"
            elif "jpeg" in ctype or "jpg" in ctype:
                ext = ".jpg"
            filename = stem + ext
            dest = os.path.join(input_dir, filename)
            from PIL import Image, ImageOps
            try:
                img = Image.open(io.BytesIO(body))
                img.load()
                if ext == ".png" and getattr(img, "is_animated", False):
                    img.seek(0)
                    buf = io.BytesIO()
                    ImageOps.exif_transpose(img).convert("RGB").save(buf, "PNG")
                    body = buf.getvalue()
                elif ext == ".png" and "png" not in ctype:
                    buf = io.BytesIO()
                    ImageOps.exif_transpose(img).convert("RGB").save(buf, "PNG")
                    body = buf.getvalue()
            except Exception as e:
                return _error(f"图片格式无法识别: {e}")
            if not os.path.isfile(dest):
                with open(dest, "wb") as f:
                    f.write(body)
        except web.HTTPException:
            raise
        except Exception as e:
            return _error(f"下载失败: {type(e).__name__}: {e}")

    from PIL import Image
    try:
        with Image.open(dest) as im:
            width, height = im.size
    except Exception:
        width = height = 0
    return web.json_response({
        "ok": True, "filename": filename, "url": url,
        "width": width, "height": height,
        "size": os.path.getsize(dest),
    })


async def handle_import(request: web.Request) -> web.Response:
    """本机导入图片/文件夹（multipart 上传）：存进 input 目录，返回文件名列表。

    前端每张图发一个请求（form 字段名 files）；非图片扩展名的文件直接跳过，
    所以选文件夹时混进来的 desktop.ini / 缩略图缓存等不会被误存。
    """
    if not (request.content_type or "").startswith("multipart/"):
        return _error("需要 multipart/form-data 上传")
    try:
        reader = await request.multipart()
    except Exception as e:
        return _error(f"解析上传失败: {e}")
    try:
        import folder_paths
        input_dir = folder_paths.get_input_directory()
    except Exception:
        input_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "input")
    os.makedirs(input_dir, exist_ok=True)

    saved: list[dict] = []
    stamp = int(time.time() * 1000)
    idx = 0
    while True:
        part = await reader.next()
        if part is None:
            break
        if getattr(part, "name", "") != "files":
            continue
        orig = os.path.basename(getattr(part, "filename", "") or "")
        stem, ext = os.path.splitext(orig)
        ext = ext.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
            continue
        chunks = []
        while True:
            chunk = await part.read_chunk(1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            continue
        idx += 1
        safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", stem)[:60] or "img"
        filename = f"sg_import_{stamp}_{idx}_{safe}{ext or '.jpg'}"
        dest = os.path.join(input_dir, filename)
        with open(dest, "wb") as f:
            f.write(data)
        try:
            from PIL import Image
            with Image.open(dest) as im:
                width, height = im.size
        except Exception:
            width = height = 0
        saved.append({"filename": filename, "width": width, "height": height, "name": orig})
    if not saved:
        return _error("没有可导入的图片（支持 jpg / jpeg / png / webp / bmp / gif）")
    return web.json_response({"ok": True, "items": saved})


async def handle_list_loras(request: web.Request) -> web.Response:
    """列出本机所有 LoRA 文件（相对 loras 目录的路径，含子目录）。"""
    try:
        import folder_paths
        names = folder_paths.get_filename_list("loras") or []
    except Exception as e:
        return _error(f"读取 loras 目录失败: {e}")
    return web.json_response({"ok": True, "items": sorted(names)})


# 模型文件索引缓存：walk models 目录较慢，60 秒内复用
_MODEL_INDEX: dict = {"t": 0.0, "files": []}


def _model_index() -> list[str]:
    """返回所有模型文件的相对路径列表（如 diffusion_models/krea2_xxx.safetensors）。"""
    import folder_paths
    now = time.time()
    if _MODEL_INDEX["files"] and now - _MODEL_INDEX["t"] < 60:
        return _MODEL_INDEX["files"]
    files: list[str] = []
    seen_roots: dict[str, str] = {}
    # 新版 ComfyUI 没有 "models" 这个注册键（只有 diffusion_models/loras/vae 等子键），
    # 直接遍历全部注册键收集其目录，避免 KeyError
    registry = getattr(folder_paths, "folder_names_and_paths", {})
    for key, (paths, _exts) in registry.items():
        for p in paths or []:
            ap = os.path.abspath(p)
            if ap not in seen_roots:
                seen_roots[ap] = key
    for ap, key in seen_roots.items():
        if not os.path.isdir(ap):
            continue
        for dirpath, dirnames, filenames in os.walk(ap):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), ap).replace("\\", "/")
                files.append(f"{key}/{rel}")
    _MODEL_INDEX["t"] = now
    _MODEL_INDEX["files"] = files
    return files


async def handle_check_models(request: web.Request) -> web.Response:
    """检查给定的模型文件名在本机 models 目录里是否存在。

    请求体 {"names": ["krea2_turbo_fp8_scaled.safetensors", ...]}；
    按「相对路径精确匹配 -> 文件名（任意子目录）不区分大小写」匹配。
    返回 {"ok": true, "found": {名: 相对路径}, "missing": [名, ...]}
    """
    try:
        d = await request.json()
    except Exception:
        return _error("请求体不是合法 JSON")
    names = d.get("names")
    if not isinstance(names, list):
        return _error("names 必须是数组")
    try:
        index = _model_index()
    except Exception as e:
        return _error(f"扫描 models 目录失败: {e}")

    by_path = {f.lower(): f for f in index}
    by_name: dict[str, list[str]] = {}
    for f in index:
        by_name.setdefault(os.path.basename(f).lower(), []).append(f)

    found: dict[str, str] = {}
    missing: list[str] = []
    for raw in names:
        name = str(raw or "").strip()
        if not name:
            continue
        norm = name.replace("\\", "/").lower()
        hit = by_path.get(norm)
        if hit is None:
            cands = by_name.get(os.path.basename(norm), [])
            # 子目录名也可能出现在用户写法里，先试全路径后缀匹配
            for f in index:
                if f.lower().endswith("/" + norm) or f.lower().endswith("\\" + norm):
                    hit = f
                    break
            if hit is None and cands:
                hit = cands[0]
        if hit:
            found[name] = hit
        else:
            missing.append(name)
    return web.json_response({"ok": True, "found": found, "missing": missing})


async def handle_health(request: web.Request) -> web.Response:
    try:
        import folder_paths
        input_dir = folder_paths.get_input_directory()
    except Exception:
        input_dir = "unavailable"
    return web.json_response({
        "ok": True, "name": "ComfyUI-Shanggu-WebGallery", "version": "1.0.0",
        "sessions": len(SESSIONS), "input_dir": input_dir,
        "endpoints": ["/sg_gallery/fetch", "/sg_gallery/parse", "/sg_gallery/expand",
                      "/sg_gallery/thumb", "/sg_gallery/select", "/sg_gallery/import",
                      "/sg_gallery/list_loras", "/sg_gallery/check_models"],
    })


def install_routes() -> None:
    try:
        # 优先取已加载的 ComfyUI server 模块，避免被别的自定义节点同名文件遮蔽
        import sys
        srv = sys.modules.get("server")
        if srv is None:
            import importlib
            srv = importlib.import_module("server")
        PromptServer = getattr(srv, "PromptServer", None)
        instance = getattr(PromptServer, "instance", None)
        routes = getattr(instance, "routes", None)
        if routes is None:
            raise RuntimeError("PromptServer.instance.routes 尚不可用")
    except Exception as e:  # 允许被单独 import 做单元测试
        print(f"[ShangguWebGallery] 路由未注册（不在 ComfyUI 进程中）: {e}")
        return
    routes.get("/sg_gallery/health")(handle_health)
    routes.post("/sg_gallery/fetch")(handle_fetch)
    routes.post("/sg_gallery/parse")(handle_parse)
    routes.post("/sg_gallery/expand")(handle_expand)
    routes.get("/sg_gallery/thumb")(handle_thumb)
    routes.post("/sg_gallery/select")(handle_select)
    routes.post("/sg_gallery/import")(handle_import)
    routes.get("/sg_gallery/list_loras")(handle_list_loras)
    routes.post("/sg_gallery/check_models")(handle_check_models)
    print("[ShangguWebGallery] HTTP 接口已注册: /sg_gallery/*")


install_routes()
