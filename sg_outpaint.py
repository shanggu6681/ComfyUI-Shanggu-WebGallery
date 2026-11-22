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
from PIL import Image, ImageOps, ImageFilter

ASPECTS = ["16:9", "3:2", "4:3", "1:1", "3:4", "9:16", "2:1", "21:9", "自定义"]
POSITIONS = ["居中", "偏左", "偏右", "偏上", "偏下"]
FILLS = ["中性灰(推荐)", "黑色", "白色"]
TRIMS = ["自动去黑边(推荐)", "不去黑边"]


def _trim_solid_border(img: Image.Image, tol: int = 12,
                       min_frac: float = 0.005) -> tuple[Image.Image, list, dict]:
    """去掉源图四周自带的纯色边（黑边/白边/灰边）。

    图库下载的图经常被人为 pad 成固定比例，四周留一圈纯黑（letterbox）。
    这圈黑边如果留在原图区（mask=0，保留不重绘），采样器不会碰它，
    成图就会带着黑边——这正是用户看到的「灰色和人物之间夹着黑条」。

    只处理「整条边几乎全是同一纯色」的情况，避免误伤正常暗背景：
    对上下左右各扫描若干行/列，若某条边 >=95% 的像素都接近同一颜色
    （且该颜色很暗/很亮，即亮度 <40 或 >215），就认为它是 padding 并裁掉。
    返回 (裁后的图, [left,top,right,bottom] 各边裁掉的像素, 每边颜色)。
    """
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    h, w, _ = a.shape
    cut = [0, 0, 0, 0]          # left, top, right, bottom
    colors: dict[str, list] = {}

    def edge_uniform(strip: np.ndarray) -> tuple[bool, list]:
        """strip: (N, W, 3) 或 (H, N, 3)。返回是否整条为纯色及该颜色。

        判定严格，避免误伤暗色照片：
        * 该条边 >=99% 像素与中位色差 <=tol
        * 颜色本身必须极暗(亮度<18)或极亮(亮度>238)——padding 通常是纯黑/纯白
        * 相邻 RGB 三通道差异也要小（纯灰阶），排除有色彩的暗背景
        """
        flat = strip.reshape(-1, 3)
        med = np.median(flat, axis=0)
        close = (np.abs(flat - med).max(axis=1) <= tol)
        lum = float(med.mean())
        spread = float(med.max() - med.min())  # 三通道是否接近（灰阶）
        uniform = (close.mean() >= 0.99 and spread <= 6
                   and (lum < 18 or lum > 238))
        return uniform, [int(round(v)) for v in med]

    max_side = min(h, w) // 4  # 单边最多裁 1/4，防止误裁整图

    # 左边：从左往右逐列
    i = 0
    while i < max_side:
        ok, col = edge_uniform(a[:, i:i + 1, :])
        if not ok:
            break
        colors.setdefault("left", col)
        i += 1
    cut[0] = i

    # 右边
    i = 0
    while i < max_side:
        ok, col = edge_uniform(a[:, w - 1 - i:w - i, :])
        if not ok:
            break
        colors.setdefault("right", col)
        i += 1
    cut[2] = i

    # 上边
    i = 0
    while i < max_side:
        ok, col = edge_uniform(a[i:i + 1, :, :])
        if not ok:
            break
        colors.setdefault("top", col)
        i += 1
    cut[1] = i

    # 下边
    i = 0
    while i < max_side:
        ok, col = edge_uniform(a[h - 1 - i:h - i, :, :])
        if not ok:
            break
        colors.setdefault("bottom", col)
        i += 1
    cut[3] = i

    l, t, r, b = cut
    # 至少要裁掉一点（>=0.5% 边长）才认为有效；裁完必须还剩足够画面
    if (l + r) / w < min_frac and (t + b) / h < min_frac:
        return img, [0, 0, 0, 0], {}
    if w - l - r < 32 or h - t - b < 32:
        return img, [0, 0, 0, 0], {}
    return img.crop((l, t, w - r, h - b)), cut, colors


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
                    position: str, fill_rgb: tuple[int, int, int],
                    trim: bool = True):
    """返回 (canvas RGB, mask L, geo)。mask 白=要重绘(扩展区)，黑=保留(原图区)。

    处理顺序（关键）：
      0) 先去掉源图自带的纯色黑边（pad）——否则黑边会留在保留区，成图带黑边
      1) 按 crop 裁掉上下（各裁 crop 比例）
      2) 等比缩放到「长边对齐目标短边」，不拉伸，居中（或按 position 偏移）
      3) 其余区域作为扩图区，填 fill 颜色并标进 mask
    """
    orig_size = src.size
    trim_info: dict = {"cut": [0, 0, 0, 0], "colors": {}}
    if trim:
        src, cut, colors = _trim_solid_border(src)
        trim_info = {"cut": cut, "colors": colors}
    sw, sh = src.size
    # 1) 裁切上下：各裁掉 crop 比例（0~0.45），让原图更接近目标比例、人像更充盈。
    #    但裁太多会让图比画布还"横"——此时若硬贴合高度就必然左右溢出。
    #    所以把有效 crop 限制在「裁完后高度贴合画布时宽度仍不超过画布」的上限，
    #    保证永远是「高度贴合 + 左右扩图」，不拉伸、不出现上下留空。
    crop = max(0.0, min(0.45, float(crop)))
    if sw > 0 and sh > 0:
        min_h = sw * th / float(tw)          # 裁后至少要这么高，宽度才不超画布
        crop_cap = max(0.0, (1.0 - min_h / sh) / 2.0)
        crop = min(crop, crop_cap)
    if crop > 0:
        top = int(round(sh * crop))
        bottom = sh - top
        if bottom - top >= 8:
            src = src.crop((0, top, sw, bottom))
            sh = src.height
    # 2) 等比缩放：源图是竖的、目标 canvas 是横的，所以固定「高度贴合画布」，
    #    宽度按比例算——绝不拉伸、绝不出现上下留空（上下留空=纵向黑边）。
    #    裁掉越多上下，nw 越大、人像越充盈；nw 超过画布宽时再夹到 tw。
    scale = th / float(sh)
    nh = th
    nw = int(round(sw * scale))
    nw = max(8, min(nw, tw))
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
                          "src_size": (sw, sh), "orig_size": orig_size,
                          "crop": crop, "trim": trim_info}


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
                "trim": (TRIMS, {"default": "自动去黑边(推荐)",
                                 "tooltip": "自动去掉源图自带的纯黑/纯白 padding 边。"
                                            "图库图常被 pad 成 9:16，那圈黑边若不去掉会被当成「保留区」原样留在成图里"}),
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
    DESCRIPTION = ("竖图扩成横图并直接输出可采样 latent：自动去掉源图黑边、可选裁切上下、"
                   "等比不拉伸、高度贴合左右扩图（无黑边）。"
                   "把 latent 接 KSampler 的 latent_image，扩展区会被文本提示补全，原图区保持不变。"
                   "建议 denoise 0.9~1.0；可在 CONTROL_Apply Mask 后接 InpaintModelConditioning 获得更好接缝。")

    def expand(self, image, vae, aspect, long_side, vertical_crop,
               position="居中", fill="中性灰(推荐)", mask_grow=8,
               trim="自动去黑边(推荐)", target_width=0, target_height=0):
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
            do_trim = str(trim).startswith("自动")
            canvas, mask_img, geo = _compose_canvas(
                src, tw, th, vertical_crop, position, fill_rgb, trim=do_trim
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
                "source_original": list(geo["orig_size"]),
                "trim_cut": geo["trim"]["cut"], "trim_colors": geo["trim"]["colors"],
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


FITS = ["完整保留(内容不裁切,推荐)", "完整保留(补灰边)", "裁切填满(无留白,会裁上下)"]
PADS = ["模糊背景(推荐)", "镜像填充", "边缘延展(会拉伸)", "中性灰", "黑色", "白色"]


def _plural_to_wh_ex(aspect: str, long_side: int, w: int, h: int, align: int = 16) -> tuple[int, int]:
    """和 _plural_to_wh 一样，但对齐粒度可选。

    MOSS-Image 要求边长是 16 的倍数；SDXL/Flux/H3 用 8 就够。
    """
    if aspect == "自定义":
        tw, th = int(w), int(h)
    elif ":" in aspect:
        a, b = aspect.split(":", 1)
        try:
            ar, br = float(a), float(b)
        except ValueError:
            ar, br = 16.0, 9.0
        long_side = max(64, int(long_side))
        if ar >= br:
            tw, th = long_side, long_side * br / ar
        else:
            th, tw = long_side, long_side * ar / br
    else:
        tw, th = 16.0, 9.0
    a = max(8, int(align))
    tw = max(a, (int(round(tw)) // a) * a)
    th = max(a, (int(round(th)) // a) * a)
    return tw, th


def _fit_cover_canvas(src: Image.Image, tw: int, th: int, position: str,
                      fill_rgb: tuple[int, int, int]):
    """把源图铺满目标画布。

    cover = 等比放大到宽高都不小于画布 → 按 position 裁掉多出来的部分。
    整个过程不拉伸、不留白，最适合 img2img（画布每个像素都有内容可重绘）。
    contain = 等比缩小到整图可见 → 短出来的部分补 fill 颜色。

    返回 (canvas RGB, geo)。裁切量记进 geo.info 方便调 position。
    """
    sw, sh = src.size
    scale = max(tw / float(sw), th / float(sh))
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    r = src.convert("RGB").resize((nw, nh), Image.LANCZOS)
    x = nw - tw
    y = nh - th
    if position == "偏左":
        cx = 0
    elif position == "偏右":
        cx = x
    else:
        cx = x // 2
    if position == "偏上":
        cy = 0
    elif position == "偏下":
        cy = y
    else:
        cy = y // 2
    canvas = r.crop((cx, cy, cx + tw, cy + th))
    return canvas, {"scale": scale, "resized": [nw, nh], "crop_origin": [cx, cy],
                    "crop_px": [x, y], "mode": "cover"}


def _edge_extend_rgb(r: Image.Image, tw: int, th: int, cx: int, cy: int) -> Image.Image:
    """把边缘那一列/行像素直线拉满空区（会留条纹，仅作兼容保留）。"""
    a = np.asarray(r.convert("RGB"), dtype=np.uint8)
    rh, rw = a.shape[:2]
    out = np.empty((th, tw, 3), dtype=np.uint8)
    row = np.empty((rh, tw, 3), dtype=np.uint8)
    if cx > 0:
        row[:, :cx, :] = a[:, :1, :]
    row[:, cx:cx + rw, :] = a
    if cx + rw < tw:
        row[:, cx + rw:, :] = a[:, -1:, :]
    if cy > 0:
        out[:cy, :, :] = row[:1, :, :]
    out[cy:cy + rh, :, :] = row
    if cy + rh < th:
        out[cy + rh:, :, :] = row[-1:, :, :]
    return Image.fromarray(out)


def _blur_bg_pad(r: Image.Image, tw: int, th: int, cx: int, cy: int) -> Image.Image:
    """整图放大铺满画布 + 强高斯模糊做底，再把清晰原图贴回中间。

    9:16 → 16:9 时左右空区约各占 34%。模糊底保留原图的色彩分布和大致明暗，
    但没有直线条纹、没有硬边；模型在 denoise 0.35~0.5 下会把模糊区续绘成
    真实背景，而中间清晰的人像是锚点，不会变成无关场景。
    """
    sw, sh = r.size
    scale = max(tw / float(sw), th / float(sh))
    bw, bh = max(tw, int(sw * scale) + 2), max(th, int(sh * scale) + 2)
    bg = r.convert("RGB").resize((bw, bh), Image.LANCZOS)
    left, top = (bw - tw) // 2, (bh - th) // 2
    bg = bg.crop((left, top, left + tw, top + th))
    radius = max(18, tw // 28)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=radius))
    bg.paste(r.convert("RGB"), (cx, cy))
    return bg


def _mirror_pad(r: Image.Image, tw: int, th: int, cx: int, cy: int) -> Image.Image:
    """左右用原图镜像平铺填满空区（保持纹理连续，可能出现对称重影）。"""
    a = np.asarray(r.convert("RGB"), dtype=np.uint8)
    rh, rw = a.shape[:2]
    out = np.empty((th, tw, 3), dtype=np.uint8)
    row = np.empty((rh, tw, 3), dtype=np.uint8)
    for x in range(0, tw, rw):
        tile = a if (x // rw) % 2 == 0 else a[:, ::-1, :]
        w = min(rw, tw - x)
        row[:, x:x + w, :] = tile[:, :w, :]
    for y in range(0, th, rh):
        tile = row if (y // rh) % 2 == 0 else row[::-1, :, :]
        h = min(rh, th - y)
        out[y:y + h, :, :] = tile[:h, :, :]
    out[cy:cy + rh, cx:cx + rw, :] = a
    return Image.fromarray(out)


def _fit_contain_canvas(src: Image.Image, tw: int, th: int, position: str,
                        fill_rgb: tuple[int, int, int], pad: str = PADS[0]):
    """整图可见（不裁内容），短出来的边按 pad 填充。返回 (canvas RGB, geo)。"""
    sw, sh = src.size
    scale = min(tw / float(sw), th / float(sh))
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    r = src.convert("RGB").resize((nw, nh), Image.LANCZOS)
    x = tw - nw
    y = th - nh
    cx = 0 if position == "偏左" else (x if position == "偏右" else x // 2)
    cy = 0 if position == "偏上" else (y if position == "偏下" else y // 2)
    if str(pad).startswith("模糊"):
        canvas = _blur_bg_pad(r, tw, th, cx, cy)
    elif str(pad).startswith("镜像"):
        canvas = _mirror_pad(r, tw, th, cx, cy)
    elif str(pad).startswith("边缘"):
        canvas = _edge_extend_rgb(r, tw, th, cx, cy)
    else:
        canvas = Image.new("RGB", (tw, th), fill_rgb)
        canvas.paste(r, (cx, cy))
    return canvas, {"scale": scale, "resized": [nw, nh], "paste_origin": [cx, cy],
                    "pad_px": [x, y], "mode": "contain", "pad": pad}


class ShangguImagePrepForImg2Img:
    """图生图预处理：整图铺到目标尺寸 → VAE 编码成 latent（不带 mask）。

    和「图像扩图→Latent」的区别（很重要，别混用）：
    * 扩图节点：原图贴中间，四周补灰 + noise_mask，采样器只重绘扩展区，
      原图像素一动不动 —— 适合「扩边、原图必须一模一样」。
    * 本节点：整图裁切/缩放到目标尺寸，输出**不带 noise_mask** 的 latent，
      KSampler 的 denoise 决定整图重绘强度 —— 适合「人物/风格大致保持、
      动作可变、尺寸只要达标」的 img2img 重绘。
      MOSS-Image（shift 5 的 flow schedule）：完整保留+模糊背景时 denoise 0.35~0.45
      （要够高才能把模糊区续绘成真实背景，但别到 0.5 以上，否则整图按文字重画、
      人物变场景）。cover 铺满整图重绘时 0.18~0.30。SDXL/Flux 可用 0.4~0.6。

    MOSS-Image 要求边长是 16 的倍数，size_multiple 默认就是 16；
    SDXL / Flux / H3 用 8 即可（也兼容 16）。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {"tooltip": "输入图像（图库节点 / LoadImage 均可）"}),
                "vae": ("VAE", {"tooltip": "和采样模型匹配的 VAE；MOSS 用 flux2-vae"}),
                "aspect": (ASPECTS, {"default": "16:9", "tooltip": "目标比例"}),
                "long_side": ("INT", {"default": 1024, "min": 256, "max": 4096, "step": 16,
                                      "tooltip": "目标长边像素"}),
                "fit": (FITS, {"default": FITS[0],
                               "tooltip": "完整保留=整图可见、一个像素都不裁（9:16→16:9 必选，"
                                          "否则人物会被切掉大半）；裁切填满=铺满无空边但会裁掉上下"}),
                "pad": (PADS, {"default": PADS[0],
                               "tooltip": "仅 fit=完整保留 时生效。模糊背景=整图放大高斯模糊后铺满、"
                                          "再贴回清晰原图（推荐，模型会把模糊区续绘成真实场景）；"
                                          "镜像填充=左右镜像原图；边缘延展=边缘色直线拉伸（会留条纹）"}),
            },
            "optional": {
                "position": (POSITIONS, {"default": "居中",
                                         "tooltip": "裁切/补边时人物贴在哪个位置"}),
                "trim": (TRIMS, {"default": "自动去黑边(推荐)",
                                 "tooltip": "先去掉源图自带的黑边/白边 padding"}),
                "size_multiple": ("INT", {"default": 16, "min": 8, "max": 64, "step": 8,
                                          "advanced": True,
                                          "tooltip": "宽高对齐到几的倍数。MOSS 必须 16；其他模型 8 即可"}),
                "target_width": ("INT", {"default": 0, "min": 0, "max": 8192, "step": 16,
                                         "advanced": True, "tooltip": "仅 aspect=自定义 时生效"}),
                "target_height": ("INT", {"default": 0, "min": 0, "max": 8192, "step": 16,
                                          "advanced": True, "tooltip": "仅 aspect=自定义 时生效"}),
            },
        }

    RETURN_TYPES = ("LATENT", "IMAGE", "STRING")
    RETURN_NAMES = ("latent", "prepared_image", "info")
    FUNCTION = "prep"
    CATEGORY = "image/Shanggu 扩图"
    DESCRIPTION = ("图生图(img2img)预处理：整图裁切/缩放到目标尺寸并 VAE 编码成 latent，"
                   "不带 mask。接 KSampler 的 latent_image，denoise 控制重绘强度。"
                   "默认「完整保留」＋模糊背景：9:16 竖图转 16:9 时整图可见、一个像素不裁，"
                   "左右空区用整图放大高斯模糊铺底再贴回清晰原图，没有拉伸条纹和硬边。"
                   "MOSS-Image 此模式 denoise 0.35~0.45（低于 0.35 模糊区续绘不出来、"
                   "高于 0.5 人物会被文字覆盖画成别的场景）；SDXL/Flux 用 0.4~0.6。"
                   "MOSS 的 size_multiple=16。"
                   "和「扩图→Latent」不同：那个只重绘灰边、原图不动；这个整图重绘。")

    def prep(self, image, vae, aspect, long_side, fit,
             position="居中", pad=PADS[0], trim="自动去黑边(推荐)", size_multiple=16,
             target_width=0, target_height=0):
        tw, th = _plural_to_wh_ex(aspect, long_side, target_width, target_height,
                                  align=int(size_multiple or 16))
        do_trim = str(trim).startswith("自动")
        cover = str(fit).startswith("裁切")
        latents, prepped, infos = [], [], []
        for i in range(image.shape[0]):
            arr = (image[i].detach().cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
            src = Image.fromarray(arr)
            try:
                src = ImageOps.exif_transpose(src)
            except Exception:
                pass
            orig_size = list(src.size)
            cut = [0, 0, 0, 0]
            if do_trim:
                src, cut, _colors = _trim_solid_border(src)
            if cover:
                canvas, geo = _fit_cover_canvas(src, tw, th, position, (128, 128, 128))
            else:
                canvas, geo = _fit_contain_canvas(src, tw, th, position, (128, 128, 128), pad=pad)
            pixels = torch.from_numpy(
                np.array(canvas, dtype=np.float32) / 255.0
            ).unsqueeze(0)
            # 保险：按 VAE 压缩率裁到整数倍（_plural_to_wh_ex 已对齐 8/16，正常不会触发）
            ds = max(1, int(getattr(vae, "spacial_compression_encode", lambda: 8)()))
            ph = (pixels.shape[1] // ds) * ds   # shape[1] = 高
            pw = (pixels.shape[2] // ds) * ds   # shape[2] = 宽
            pixels = pixels[:, :ph, :pw, :].contiguous()
            t = vae.encode(pixels)
            # 关键：不带 noise_mask，KSampler 对整张 latent 按 denoise 重绘
            latents.append({"samples": t})
            prepped.append(pixels)
            infos.append({
                "index": i, "source_original": orig_size,
                "trim_cut": cut, "target": [pw, ph], "fit": geo["mode"],
                "pad": geo.get("pad", ""), "content_visible_px": geo["resized"] if not cover else [pw, ph],
                "note": ("img2img denoise: MOSS 完整保留+模糊背景 0.35~0.45 / cover 0.18~0.30; "
                         "变场景=denoise 太高，模糊区续绘不出=太低；边缘延展会产生拉伸条纹"),
            })
        return (
            latents[0] if len(latents) == 1 else {"samples": torch.cat([l["samples"] for l in latents], 0)},
            torch.cat(prepped, 0),
            __import__("json").dumps(infos, ensure_ascii=False),
        )


NODE_CLASS_MAPPINGS = {
    "ShangguImageExpandToLatent": ShangguImageExpandToLatent,
    "ShangguImagePrepForImg2Img": ShangguImagePrepForImg2Img,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ShangguImageExpandToLatent": "图像扩图→Latent (Shanggu)",
    "ShangguImagePrepForImg2Img": "图像重绘准备→Latent img2img (Shanggu)",
}
