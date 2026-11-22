# -*- coding: utf-8 -*-
"""图像扩图 -> Latent 节点（Shanggu）。

解决的问题：原图常常是竖屏（9:16），却要生成横屏（16:9）。老的「缩放图像」
节点要么居中裁剪（只剩中间一条、主体缺失），要么直接拉伸（变形）＋两侧黑框。
本节点的做法是标准 outpainting：

    原图 --可选裁切上下--> 等比缩放(不拉伸) --> 贴到目标画布中央
         --> 扩出来的区域标进 mask --> VAE 编码成带 noise_mask 的 latent
         --> 采样器只重绘扩展区，原图部分原样保留

关键：
* 不拉伸：只对原图做等比缩放；短出来的那一维（竖图就是左右）由采样器补全。
* 裁切上下：vertical_crop 先从原图上下各裁掉一个比例，让画面更接近目标比例、
  减少需要扩图的面积；0 = 完全不裁，整张竖图保留，左右扩得最多。
* latent 直接可用：输出 {"samples", "noise_mask"}，接 KSampler 的 latent_image
  即可；扩展区白、原图区黑，denoise 建议 0.9~1.0。
"""
from __future__ import annotations

import math

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageOps

ASPECTS = ["16:9", "3:2", "4:3", "1:1", "3:4", "9:16", "2:1", "21:9", "自定义"]
POSITIONS = ["居中", "偏左", "偏右", "偏上", "偏下"]
FILLS = ["中性灰(推荐)", "黑色", "白色"]


def _plural_to_wh(aspect: str, long_side: int, w: int, h: int) -> tuple[int, int]:
    """按长边像素 + 比例算目标宽高（都对齐到 8 的倍数，VAE 友好）。"""
    if aspect == "自定义":
        return max(64, (int(w) // 8) * 8), max(64, (int(h) // 8) * 8)
    if ":" in aspect:
        a, b = aspect.split(":", 1)
        try:
            ar, br = float(a), float(b)
        except ValueError:
            ar, br = 16.0, 9.0
    else:
        ar, br = 16.0, 9.0
    long_side = max(64, int(long_side))
    if ar >= br:  # 横屏：宽=长边
        tw, th = long_side, long_side * br / ar
    else:          # 竖屏：高=长边
        th, tw = long_side, long_side * ar / br
    tw = max(8, (int(round(tw)) // 8) * 8)
    th = max(8, (int(round(th)) // 8) * 8)
    return tw, th


def _compose_canvas(src: Image.Image, tw: int, th: int, crop: float,
                    position: str, fill_rgb: tuple[int, int, int]):
    """返回 (canvas RGB, mask L)。mask 白=要重绘(扩展区)，黑=保留(原图区)。

    src 先按 crop 裁掉上下（各裁 crop 比例），再等比缩放到尽量贴合目标画布，
    不拉伸；短出来的维度由 offset_position 决定原图偏向哪边，其余为扩图区。
    """
    sw, sh = src.size
    # 1) 裁切上下：各裁掉 crop 比例（0~0.45），让原图更接近目标比例
    crop = max(0.0, min(0.45, float(crop)))
    if crop > 0:
        top = int(round(sh * crop))
        bottom = sh - top
        if bottom - top >= 8:
            src = src.crop((0, top, sw, bottom))
            sh = src.height
    # 2) 等比缩放：scale 取「能装进画布」的最小值（contain），绝不放大到溢出，
    #    也绝不拉伸。竖图 9:16 -> 横画布 16:9 时，高度贴合、左右留空。
    scale = min(tw / sw, th / sh)
    nw = max(8, int(round(sw * scale)))
    nh = max(8, int(round(sh * scale)))
    nw, nh = min(nw, tw), min(nh, th)
    r = src.convert("RGB").resize((nw, nh), Image.LANCZOS)

    # 3) 放到画布上；短出来的维度按 position 决定偏移
    canvas = Image.new("RGB", (tw, th), fill_rgb)
    mask = Image.new("L", (tw, th), 255)  # 先全部标记为要重绘

    def axis_pos(total, span, which):
        free = total - span
        if which in ("偏左", "偏上"):
            return 0
        if which in ("偏右", "偏下"):
            return free
        return free // 2

    x = axis_pos(tw, nw, position if position in ("偏左", "偏右") else "居中")
    y = axis_pos(th, nh, position if position in ("偏上", "偏下") else "居中")
    canvas.paste(r, (x, y))
    # 原图区 mask 置 0（保留），扩展区保持 255（重绘）
    mask.paste(0, (x, y, x + nw, y + nh))
    return canvas, mask, {"x": x, "y": y, "nw": nw, "nh": nh,
                          "src_size": (sw, sh), "crop": crop}


class ShangguImageExpandToLatent:
    """竖图扩成横图并直接输出可采样的 latent（代替「缩放图像 + VAE 编码」）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {"tooltip": "输入图像，竖屏(9:16)也可；来自 LoadImage 或图库节点"}),
                "vae": ("VAE", {"tooltip": "用哪个 VAE 编码，要和采样用的模型匹配"}),
                "aspect": (ASPECTS, {"default": "16:9", "tooltip": "目标画布比例，如 16:9"}),
                "long_side": ("INT", {"default": 1024, "min": 256, "max": 4096, "step": 64,
                                      "tooltip": "目标长边像素；短边按比例自动算并对齐到 8 的倍数"}),
                "vertical_crop": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 0.45, "step": 0.01,
                                            "tooltip": "从原图上下各裁掉的比例。0=整张保留(左右扩最多)；"
                                                       "0.1~0.2=轻微裁切，减少扩图面积；越大越接近中心裁剪"}),
            },
            "optional": {
                "position": (POSITIONS, {"default": "居中",
                                         "tooltip": "原图贴在画布的位置；竖图扩横屏一般用居中"}),
                "fill": (FILLS, {"default": "中性灰(推荐)",
                                 "tooltip": "扩图区的底色。中性灰是 inpaint 的标准， latent 编码最稳"}),
                "mask_grow": ("INT", {"default": 8, "min": 0, "max": 64, "step": 1,
                                      "tooltip": "把 mask 向原图内侧膨胀几像素，让接缝更自然；0=不膨胀"}),
                "target_width": ("INT", {"default": 0, "min": 0, "max": 8192, "step": 8,
                                         "advanced": True, "tooltip": "仅当 aspect=自定义 时生效"}),
                "target_height": ("INT", {"default": 0, "min": 0, "max": 8192, "step": 8,
                                          "advanced": True, "tooltip": "仅当 aspect=自定义 时生效"}),
            },
        }

    RETURN_TYPES = ("LATENT", "IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("latent", "expanded_image", "expand_mask", "info")
    FUNCTION = "expand"
    CATEGORY = "image/Shanggu 扩图"
    DESCRIPTION = ("竖图扩成横图并直接输出可采样 latent：可选裁切上下、等比不拉伸、左右扩图。"
                   "把 latent 接 KSampler 的 latent_image，扩展区会被文本提示补全，原图区保持不变。"
                   "建议 denoise 0.9~1.0；可在 CONTROL_Apply Mask 后接 InpaintModelConditioning 获得更好接缝。")

    def expand(self, image, vae, aspect, long_side, vertical_crop,
               position="居中", fill="中性灰(推荐)", mask_grow=8,
               target_width=0, target_height=0):
        tw, th = _plural_to_wh(aspect, long_side, target_width, target_height)
        fill_rgb = {"黑色": (0, 0, 0), "白色": (255, 255, 255)}.get(fill, (128, 128, 128))

        # IMAGE 张量是 B x H x W x C，逐张处理（图库节点每次只送一张，这里仍兼容 batch）
        latents, masks, images, infos = [], [], [], []
        for i in range(image.shape[0]):
            arr = (image[i].detach().cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
            src = Image.fromarray(arr)
            try:
                src = ImageOps.exif_transpose(src)
            except Exception:
                pass
            canvas, mask_img, geo = _compose_canvas(
                src, tw, th, vertical_crop, position, fill_rgb
            )

            # --- 编码成 latent，参考 ComfyUI 内置 VAEEncodeForInpaint ---
            pixels = torch.from_numpy(
                np.array(canvas, dtype=np.float32) / 255.0
            ).unsqueeze(0)  # 1 x H x W x C

            m = torch.from_numpy(
                np.array(mask_img, dtype=np.float32) / 255.0
            ).reshape(1, 1, th, tw)  # 1=重绘

            downscale = int(getattr(vae, "spacial_compression_encode", lambda: 8)())
            downscale = max(1, downscale)
            x = (pixels.shape[1] // downscale) * downscale
            y = (pixels.shape[2] // downscale) * downscale
            pixels = pixels[:, :x, :y, :].contiguous()
            m = m[:, :, :x, :y].contiguous()

            # 膨胀 mask 几像素，让采样在接缝处有过渡（内置 VAEEncodeForInpaint 同款思路）
            grow = int(mask_grow or 0)
            if grow > 0:
                kernel = torch.ones((1, 1, grow, grow))
                pad = math.ceil((grow - 1) / 2)
                noise_mask = torch.clamp(
                    F.conv2d(m.round(), kernel, padding=pad), 0, 1
                )
                # conv2d 在偶数 kernel 下可能把 H/W 各撑大 1 像素，裁回对齐尺寸
                noise_mask = noise_mask[:, :, :x, :y]
            else:
                noise_mask = m

            # 扩展区像素置 0.5 灰再编码：避免把黑/白底色编进 latent 造成色偏
            inv = 1.0 - m.round()   # 原图区=1，扩展区=0
            px = pixels.clone()
            for c in range(3):
                px[:, :, :, c] = (px[:, :, :, c] - 0.5) * inv + 0.5

            t = vae.encode(px)
            latents.append({"samples": t, "noise_mask": noise_mask.round()})
            images.append(pixels)
            masks.append(m.squeeze(1))  # 1 x H x W（ComfyUI MASK 的 BxHxW 形状）
            infos.append({
                "index": i, "source": list(geo["src_size"]),
                "target": [tw, th], "crop": geo["crop"],
                "paste": [geo["x"], geo["y"], geo["nw"], geo["nh"]],
                "expanded_px": int(tw * th - geo["nw"] * geo["nh"]),
            })

        return (
            latents[0] if len(latents) == 1 else {"samples": torch.cat([l["samples"] for l in latents], 0),
                                                  "noise_mask": torch.cat([l["noise_mask"] for l in latents], 0)},
            torch.cat(images, 0),
            torch.cat(masks, 0),
            __import__("json").dumps(infos, ensure_ascii=False),
        )


NODE_CLASS_MAPPINGS = {
    "ShangguImageExpandToLatent": ShangguImageExpandToLatent,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ShangguImageExpandToLatent": "图像扩图→Latent (Shanggu)",
}
