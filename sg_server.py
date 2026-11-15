# -*- coding: utf-8 -*-
"""Shanggu Web Gallery 的本地 HTTP 接口（挂在 ComfyUI 自带 aiohttp 服务上）。

浏览器前端不能跨域抓别的网站，所以所有请求都由这里代发。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
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
    keys = ("cookie", "ua", "referer", "proxy", "url_regex", "page_template", "safe_only", "per_page")
    out = {k: raw.get(k, "") for k in keys}
    out["safe_only"] = raw.get("safe_only", True) is not False
    try:
        out["per_page"] = max(1, min(400, int(raw.get("per_page") or 200)))
    except Exception:
        out["per_page"] = 200
    return out


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


def rewrite_gallery_url(url: str) -> tuple[str, bool]:
    """网页版图库被 Cloudflare 挡住时，自动改用同站的官方 JSON 接口。"""
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.netloc or "").lower().split(":")[0]
        path = (p.path or "").rstrip("/")
        if host.startswith(("civitai.", "www.civitai.")) and path in ("", "/images"):
            return f"https://{p.netloc}/api/v1/images?limit=20", True
    except Exception:
        pass
    # SPA 图库（HTML 里没有图片，全靠 JS 调自家 JSON 接口）
    extra, hit = adapters.rewrite_extra(url)
    if hit:
        return extra, True
    return url, False


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
    body = {"ok": False, "error": msg, "items": [], "has_more": False}
    body.update(extra)
    return web.json_response(body)


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
    s = get_session(sid, reset)
    s["settings"] = settings

    rewritten = False
    if reset:
        url, rewritten = rewrite_gallery_url(url)
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
                    "可尝试：① 换成该站的 JSON API 地址（如 https://civitai.red/api/v1/images?limit=20）；"
                    "② 在高级设置里填浏览器 Cookie 和相同 UA；③ 用「粘贴源码」或「浏览器桥接」标签页。")
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
                safe_only=settings["safe_only"],
                page_template=settings.get("page_template", ""),
                page=next_page_no,
                current_url=final,
                limit=settings["per_page"],
            )
    except Exception as e:
        return _error(f"解析失败: {e}")

    new = _new_items(s, items)
    if nxt and nxt != target:
        s["next"] = nxt
    elif settings.get("page_template") and ("{page}" in settings["page_template"] or "{offset}" in settings["page_template"]):
        s["next"] = _render_template(settings["page_template"], s["base"], next_page_no)
    else:
        s["next"] = None
    s["page"] = next_page_no
    has_more = bool(s["next"]) and len(new) > 0
    if not has_more:
        s["exhausted"] = True

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
        "message": "" if new else "这一页没有发现新图片，已停止加载",
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
            safe_only=settings["safe_only"],
            limit=settings["per_page"],
        )
    except Exception as e:
        return _error(f"解析失败: {e}")
    new = _new_items(s, items)
    return web.json_response({
        "ok": True, "items": new, "has_more": False,
        "total": len(s["items"]), "kind": kind,
        "message": "" if new else "没有解析到新的图片地址",
    })


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


async def handle_health(request: web.Request) -> web.Response:
    try:
        import folder_paths
        input_dir = folder_paths.get_input_directory()
    except Exception:
        input_dir = "unavailable"
    return web.json_response({
        "ok": True, "name": "ComfyUI-Shanggu-WebGallery", "version": "1.0.0",
        "sessions": len(SESSIONS), "input_dir": input_dir,
        "endpoints": ["/sg_gallery/fetch", "/sg_gallery/parse", "/sg_gallery/thumb", "/sg_gallery/select"],
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
    routes.get("/sg_gallery/thumb")(handle_thumb)
    routes.post("/sg_gallery/select")(handle_select)
    print("[ShangguWebGallery] HTTP 接口已注册: /sg_gallery/*")


install_routes()
