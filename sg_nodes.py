# -*- coding: utf-8 -*-
"""节点本体：把图库里点选的图片变成 ComfyUI 的 IMAGE / MASK 张量。

本节点永远等价于「一个 LoadImage」：一次 prompt 只加载一张图。
执行次数完全由「单图 / 多图」这一个开关决定：

- 单图：点任意图片自动替换当前选中；点运行 → 这一张图跑 1 次工作流。
- 多图：可累加选中 N 张；前端把一次 Run 串行拆成 N 个独立 prompt，
  每个 prompt 只带一张图，整个下游工作流真正跑 N 遍。

不再提供「批量一次执行」：把 N 张塞进一个 IMAGE 批次里既要求下游全部支持
batch，又容易撑爆显存，和「每个图片执行一遍工作流」这个目标自相矛盾。

`multi_mode` 只是旧版工作流留下的隐藏占位参数，值被完全忽略；保留它是
为了让老工作流的 widgets_values 顺序不错位（删掉会让后面的 widget 值错位）。
"""
from __future__ import annotations

import json
import os

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageOps

# 兼容旧工作流的隐藏占位值，不参与任何逻辑判断
MODE_BATCH = "批量一次执行"
MODE_QUEUE = "逐张完整执行"
SEL_SINGLE = "单图(点选替换)"
SEL_MULTI = "多图(累加)"
SELECT_MODES = [SEL_SINGLE, SEL_MULTI]
SIZE_MODES = ["等比缩放+填充", "等比缩放+居中裁剪", "直接拉伸", "不统一(需同尺寸)"]

DEFAULT_SITE = "https://civitai.red/api/v1/images?limit=20"


def _entries(raw) -> list[dict]:
    """selected_files 既可以是 [{"filename":...}]，也可以是 ["a.png"] 或单个文件名。"""
    if isinstance(raw, (list, tuple)):
        seq = list(raw)
    else:
        s = (raw or "").strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                seq = json.loads(s)
            except Exception:
                seq = [s]
        else:
            seq = [s]
    out = []
    for x in seq:
        if isinstance(x, dict):
            name = x.get("filename") or x.get("file") or ""
            if name:
                out.append({"filename": name, "url": x.get("url", ""),
                            "prompt": x.get("prompt", ""), "id": x.get("id", "")})
        elif isinstance(x, str) and x.strip():
            out.append({"filename": x.strip(), "url": "", "prompt": "", "id": ""})
    return out


def _resolve(name: str) -> str:
    import folder_paths
    if os.path.isabs(name) and os.path.isfile(name):
        return name
    try:
        return folder_paths.get_annotated_filepath(name)
    except Exception:
        return os.path.join(folder_paths.get_input_directory(), name)


def _resize(img: Image.Image, mode: str, tw: int, th: int) -> Image.Image:
    if img.size == (tw, th):
        return img
    if mode == "直接拉伸":
        return img.resize((tw, th), Image.LANCZOS)
    if mode == "等比缩放+居中裁剪":
        scale = max(tw / img.width, th / img.height)
        nw, nh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
        r = img.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - tw) // 2, (nh - th) // 2
        return r.crop((left, top, left + tw, top + th))
    # 等比缩放 + 黑边填充
    scale = min(tw / img.width, th / img.height)
    nw, nh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    r = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (tw, th), (0, 0, 0))
    canvas.paste(r, ((tw - nw) // 2, (th - nh) // 2))
    return canvas


def _one_image(path: str):
    """返回 (RGB PIL, alpha mask 张量或 None)。"""
    import node_helpers
    img = node_helpers.pillow(Image.open, path)
    img = ImageOps.exif_transpose(img)
    if getattr(img, "is_animated", False):
        img.seek(0)
    if img.mode in ("RGBA", "LA") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        alpha = np.array(rgba.getchannel("A"), dtype=np.float32) / 255.0
        mask = torch.from_numpy(1.0 - alpha).unsqueeze(0)
        rgb = rgba.convert("RGB")
    else:
        rgb = img.convert("RGB")
        mask = None
    return rgb, mask


class ShangguWebImageGallery:
    """网页图库选图器（可替代 LoadImage；单图跑 1 次，多图每图各跑 1 次）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "site_url": ("STRING", {
                    "default": DEFAULT_SITE, "multiline": False,
                    "tooltip": "要浏览的页面或 JSON API 地址，在节点面板里改即可",
                }),
                "selected_files": ("STRING", {
                    "default": "[]", "multiline": True,
                    "tooltip": "由节点面板点选图片时自动写入，一般不用手改",
                }),
                # 已弃用：仅为兼容老工作流的隐藏占位参数，前端会隐藏它
                "multi_mode": ([MODE_QUEUE, MODE_BATCH], {
                    "default": MODE_QUEUE,
                    "tooltip": "已弃用（兼容旧工作流用）：运行次数现在完全由「单图/多图」决定",
                }),
                "size_mode": (SIZE_MODES, {"default": SIZE_MODES[0]}),
                "target_width": ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 64}),
                "target_height": ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 64}),
            },
            # 放在 optional：老版本工作流里没有这个键也不会触发 required 校验失败
            "optional": {
                "select_mode": (SELECT_MODES, {
                    "default": SEL_SINGLE,
                    "tooltip": "单图=点哪个换哪个，运行 1 次；多图=可累加多选，每张图各完整运行 1 次工作流",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK", "INT", "STRING")
    RETURN_NAMES = ("images", "masks", "count", "info_json")
    FUNCTION = "load_gallery"
    CATEGORY = "image/Shanggu 网页图库"
    DESCRIPTION = "浏览网页图片、点选加载，等价于 LoadImage；单图运行 1 次，多图按选中顺序每图各运行 1 次。"

    def load_gallery(self, site_url, selected_files, multi_mode, size_mode,
                     target_width, target_height, select_mode=SEL_SINGLE):
        # multi_mode 已弃用，值完全忽略。执行次数由前端按 select_mode 拆成
        # N 个独立 prompt，因此正常情况下每个 prompt 里只会有一张图。
        entries = _entries(selected_files)
        if not entries:
            raise ValueError(
                "[Shanggu 网页图库] 还没有点选任何图片：请先在节点面板里搜索网址，"
                "然后点击缩略图选中（可多选），再运行工作流。"
            )
        if select_mode == SEL_SINGLE:
            # 单图语义：无论旧工作流里残留了几张，只用当前这一张
            entries = entries[:1]
        elif len(entries) > 1:
            # 多图本该被前端拆成 N 次运行；走到这里说明扇出没生效，
            # 明确报错，避免又退化成「一个 prompt 塞 N 张」而卡住/爆显存。
            raise ValueError(
                f"[Shanggu 网页图库] 多图模式收到了 {len(entries)} 张图，但每次运行只允许一张。\n"
                "多图由前端自动拆成 N 次独立运行，请确认扩展 JS 已加载：浏览器 Ctrl+F5 强制刷新，"
                "然后用节点面板的「▶ 运行(N 次)」或 ComfyUI 原生运行按钮执行。\n"
                "另外，一个工作流里请只放一个处于「多图」模式的选图器节点。"
            )

        loaded = []
        for e in entries:
            path = _resolve(e["filename"])
            if not os.path.isfile(path):
                raise ValueError(
                    f"[Shanggu 网页图库] 找不到图片文件: {path}\n"
                    "可能是 input 目录被清理过，请在图库里重新点选一次该图片。"
                )
            rgb, mask = _one_image(path)
            loaded.append((rgb, mask, e))

        sizes = {rgb.size for rgb, _, _ in loaded}
        # 统一按 size_mode 缩放到 target_width/height：网页原图常常是 1500~3000px，
        # 直接送进 1024 工作流会让 latent 暴涨、16GB 显存直接卡住/爆显存。
        # 只有用户明确选「不统一(需同尺寸)」时才保留原分辨率。
        keep_original = (size_mode == "不统一(需同尺寸)")
        if keep_original:
            if len(sizes) > 1:
                raise ValueError(
                    f"[Shanggu 网页图库] 图片尺寸不一致 {sorted(sizes)}，"
                    "IMAGE 张量要求同宽高。请把 size_mode 改成「等比缩放+填充」等统一尺寸方式。"
                )
            tw, th = next(iter(sizes))
        else:
            tw = max(64, int(target_width) // 8 * 8)
            th = max(64, int(target_height) // 8 * 8)

        images, masks, infos = [], [], []
        for rgb, mask, e in loaded:
            final_rgb = rgb if keep_original else _resize(rgb, size_mode, tw, th)
            tensor = torch.from_numpy(
                np.array(final_rgb, dtype=np.float32) / 255.0
            ).unsqueeze(0)
            H, W = int(tensor.shape[1]), int(tensor.shape[2])
            if mask is None:
                mask = torch.zeros((1, H, W), dtype=torch.float32)
            elif (mask.shape[-2], mask.shape[-1]) != (H, W):
                mask = F.interpolate(
                    mask.unsqueeze(0), size=(H, W), mode="bilinear", align_corners=False
                ).squeeze(0)
            images.append(tensor)
            masks.append(mask)
            infos.append({
                "filename": e["filename"], "url": e.get("url", ""),
                "prompt": e.get("prompt", ""), "id": e.get("id", ""),
                "width": rgb.width, "height": rgb.height,
            })

        return (
            torch.cat(images, dim=0),
            torch.cat(masks, dim=0),
            len(entries),
            json.dumps(infos, ensure_ascii=False),
        )


class ShangguGalleryInfo:
    """把主节点的 info_json 拆成可直接接给文本编码 / 保存节点的字符串。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "info_json": ("STRING", {"default": "[]", "multiline": True, "forceInput": True}),
                "field": (["prompt", "filename", "url", "id", "全部(JSON)"],),
                "index": ("INT", {"default": 0, "min": 0, "max": 9999}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "pick"
    CATEGORY = "image/Shanggu 网页图库"

    def pick(self, info_json, field, index):
        try:
            data = json.loads(info_json or "[]")
        except Exception:
            data = []
        if field == "全部(JSON)":
            return (json.dumps(data, ensure_ascii=False),)
        if not isinstance(data, list) or index >= len(data):
            return ("",)
        return (str(data[index].get(field, "") or ""),)


NODE_CLASS_MAPPINGS = {
    "ShangguWebImageGallery": ShangguWebImageGallery,
    "ShangguGalleryInfo": ShangguGalleryInfo,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ShangguWebImageGallery": "网页图库选图器 (Shanggu)",
    "ShangguGalleryInfo": "图库信息提取 (Shanggu)",
}
