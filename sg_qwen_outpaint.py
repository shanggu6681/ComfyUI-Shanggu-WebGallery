# -*- coding: utf-8 -*-
"""Qwen 2.1 一键扩图草图节点（Shanggu）。

目的：把 9:16 竖图变成 16:9 横图，人物完整、不裁不变形，左右扩出的区域
是自然场景而不是黑边/灰边/拉伸条纹。这是 ausboss Qwen-Image-2.1-Outpaint-LoRA
的官方做法，封装成一个节点：

    源图 --去黑边--> 等比缩放(完整保留) --> 贴到目标比例画布, 空处填灰 #808080
      --> 灰画布作为参考图喂给 TextEncodeQwenImage21(文本编码器直接看图)
      --> 加载 outpaint LoRA (known region 由 LoRA 自己钉住, 不用 mask)
      --> KSampler 25步/CFG1/euler/simple/denoise=1 全图重绘
      --> VAEDecode --> 低像素草图

为什么不用 mask：Qwen 2.1 上 Set Latent Noise Mask 会在接缝画出可见矩形；
outpaint LoRA 训练目标就是「灰色区补全、原图区不动」，全图重绘反而没有接缝。
官方原话：Do not pin the known area with Set Latent Noise Mask.

输出的是低像素草图（默认长边 768，draft / reference 两个端口都是这张生成后的图），拿它再去跑 MOSS 做最终风格重绘。
"""
from __future__ import annotations

import os
import re

import numpy as np
import torch
from PIL import Image

# 工具函数已内联（原 sg_outpaint.py 已删除，插件只保留两个节点）
ASPECTS = ["16:9", "3:2", "4:3", "1:1", "3:4", "9:16", "2:1", "21:9", "自定义"]
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


TRIGGER = ("Outpaint the image: replace the solid gray areas with a seamless "
           "continuation of the scene, keeping the existing picture unchanged.")

QUALITIES = ["质量(25步)", "快速(8步+turbo)"]
FILL_RGB = (128, 128, 128)  # #808080, 官方指定


def _fl(kind: str, fallback):
    """读 folder_paths 列表；本机确认能用的文件名始终放进列表并排在最前。

    绘世启动器启动时会加载 extra_model_paths.yaml（共享模型目录），列表完整；
    但直接跑脚本或某些启动方式没加载该配置时，共享目录的 Qwen 模型会缺席，
    导致默认选中错误的同类模型。把已知可用的文件名补进列表可避免这个坑。
    """
    items = []
    try:
        import folder_paths
        items = list(folder_paths.get_filename_list(kind))
    except Exception:
        pass
    for fb in fallback:          # 缺哪个补哪个，排前面
        if fb not in items:
            items.insert(0, fb)
    return items


UNETS = _fl("unet", ["qwen_image_2.1_int8_convrot.safetensors"])
CLIPS = _fl("clip", ["qwen3vl_8b_int8_convrot.safetensors"])
VAES = _fl("vae", ["qwen_image_2.1_vae_bf16.safetensors"])
LORAS = _fl("loras", [
    # 这两个 LoRA 在共享模型目录的子文件夹里，folder_paths 的名字必须带前缀
    "Qwen\\画面扩展 [0.8-1.0] qwen-image-2.1-outpaint-v2.safetensors",
    "Krea2\\Turbo加速 [1.0-1.0] p_qwen_image_2.1_8step_v0.1-comfyui-T8.safetensors",
])


def _default_choice(options, keyword):
    for name in options:
        if keyword in name.lower():
            return name
    return options[0] if options else keyword


def _resolve_lora(requested, keyword):
    """把旧工作流里缺子目录前缀的 LoRA 名字纠正成 folder_paths 真实名字。

    例如工作流存的是「画面扩展 ...outpaint-v2.safetensors」，
    ComfyUI 实际认的是「Qwen\\画面扩展 ...outpaint-v2.safetensors」，
    直接传给 LoraLoaderModelOnly 会 FileNotFoundError。这里按文件名
    （去掉子目录前缀后）或关键词自动映射到列表里的真实名字。
    """
    try:
        import folder_paths
        names = list(folder_paths.get_filename_list("loras"))
    except Exception:
        names = []
    if not names:
        names = list(LORAS)
    if requested in names:
        return requested
    base = str(requested or "").replace("\\", "/").split("/")[-1].lower()
    for name in names:
        if base and name.replace("\\", "/").split("/")[-1].lower() == base:
            return name
    for name in names:
        if keyword in name.lower():
            return name
    return requested


def _vae_decode(vae, latent):
    """兼容 ComfyUI 两种 VAEDecode 参数顺序：
    新版 0.4x 是 decode(self, vae, samples)，旧版是 decode(self, samples, vae)。
    用检查参数名判断，避免再次踩坑。返回 IMAGE tensor。
    """
    import inspect
    import nodes as comfy_nodes
    fn = comfy_nodes.VAEDecode().decode
    try:
        # fn 是绑定方法，signature 里已去掉 self，第一个参数就是 vae 或 samples
        first = next(iter(inspect.signature(fn).parameters.keys()))
        if first == "vae":
            return fn(vae, latent)[0]
    except (ValueError, TypeError, StopIteration):
        pass
    return fn(latent, vae)[0]


def _align32(n: int) -> int:
    return max(32, int(round(n / 32.0)) * 32)


def _target_wh(aspect: str, long_side: int, custom_w: int = 0,
               custom_h: int = 0) -> tuple[int, int]:
    """目标画布尺寸，长边=long_side，两边对齐 32（Qwen latent 要求）。

    aspect 兼容带后缀的旧工作流值，如 ausboss 原版节点的
    "9:16 (Portrait Widescreen)"——用正则提取 "数字:数字"，解析不到才兜底 16:9。
    """
    if aspect == "自定义" and custom_w > 0 and custom_h > 0:
        return _align32(custom_w), _align32(custom_h)
    m = re.search(r"(\d+)\s*:\s*(\d+)", str(aspect))
    if m:
        ratio = float(m.group(1)) / float(m.group(2))
    else:
        ratio = 16.0 / 9.0
    if ratio >= 1:
        w = _align32(long_side)
        h = _align32(round(w / ratio))
    else:
        h = _align32(long_side)
        w = _align32(round(h * ratio))
    return w, h


def _to_pil(t: torch.Tensor) -> Image.Image:
    arr = (t.detach().clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)
    return Image.fromarray(arr[0] if arr.ndim == 4 else arr)


def _to_tensor(pil: Image.Image) -> torch.Tensor:
    return torch.from_numpy(
        np.asarray(pil.convert("RGB"), dtype=np.float32) / 255.0
    ).unsqueeze(0)


def _compose_gray_canvas(src: Image.Image, tw: int, th: int):
    """整图可见（不裁），等比缩放后居中贴到灰画布。返回 (tensor, geo)。"""
    sw, sh = src.size
    scale = min(tw / float(sw), th / float(sh))
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    r = src.convert("RGB").resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (tw, th), FILL_RGB)
    x, y = (tw - nw) // 2, (th - nh) // 2
    canvas.paste(r, (x, y))
    return _to_tensor(canvas), {
        "source": [sw, sh], "canvas": [tw, th], "content": [nw, nh],
        "paste_origin": [x, y], "scale": round(scale, 4),
    }


class ShangguQwenOutpaintDraft:
    """Qwen 2.1 一键扩图草图：源图 + 目标比例 → 低像素 16:9 草图。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {"tooltip": "源图，例如 9:16 竖图；整图保留、不裁不变形"}),
                "aspect": (ASPECTS, {"default": "16:9",
                                     "tooltip": "目标比例，例如 16:9"}),
                "draft_long_side": ("INT", {"default": 768, "min": 256,
                                            "max": 2048, "step": 32,
                                            "tooltip": "草图长边像素。只是草图，768 够用且快"}),
                "prompt": ("STRING", {"multiline": True, "default": "",
                                      "tooltip": "场景描述（可留空）。建议用 Qwen3-VL 看图写；"
                                                 "节点会自动加上 outpaint 触发句"}),
                "negative": ("STRING", {"multiline": True, "default": "",
                                        "tooltip": "负面词，一般留空"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "quality": (QUALITIES, {"default": QUALITIES[0],
                                        "tooltip": "质量=25步；快速=8步叠加 turbo LoRA"}),
                "trim": (TRIMS, {"default": TRIMS[0],
                                 "tooltip": "自动去掉源图自带的纯黑/白边（图库常见 padding）"}),
                "auto_prefix": ("BOOLEAN", {"default": True,
                                            "tooltip": "自动在 prompt 前加 outpaint 触发句；"
                                                       "若你已手动写好可关掉"}),
            },
            "optional": {
                "custom_width": ("INT", {"default": 0, "min": 0, "max": 4096}),
                "custom_height": ("INT", {"default": 0, "min": 0, "max": 4096}),
                "unet_name": (UNETS, {"default": _default_choice(UNETS, "qwen_image_2.1")}),
                "clip_name": (CLIPS, {"default": _default_choice(CLIPS, "qwen3vl_8b")}),
                "vae_name": (VAES, {"default": _default_choice(VAES, "qwen_image_2.1_vae")}),
                "outpaint_lora": (LORAS, {"default": _default_choice(LORAS, "outpaint")}),
                "turbo_lora": (LORAS, {"default": _default_choice(LORAS, "8step")}),
                "lora_strength": ("FLOAT", {"default": 1.0, "min": 0.0,
                                            "max": 2.0, "step": 0.05}),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("draft", "reference")
    OUTPUT_TOOLTIPS = ("低像素扩图草图，拿去跑 MOSS 重绘",
                       "生成后的扩图草图（与 draft 同一张，可直接当参考图用）")
    FUNCTION = "generate"
    CATEGORY = "Shanggu/Image"

    @staticmethod
    def _load_models(unet_name, clip_name, vae_name, outpaint_lora,
                     turbo_lora, lora_strength, fast):
        """走 ComfyUI 内置 loader（有缓存，不会重复占显存）。"""
        import nodes as comfy_nodes
        model = comfy_nodes.UNETLoader().load_unet(unet_name, "default")[0]
        clip = comfy_nodes.CLIPLoader().load_clip(clip_name, "qwen_image")[0]
        vae = comfy_nodes.VAELoader().load_vae(vae_name)[0]
        if outpaint_lora:
            model = comfy_nodes.LoraLoaderModelOnly().load_lora_model_only(
                model, outpaint_lora, lora_strength)[0]
        if fast and turbo_lora and turbo_lora != outpaint_lora:
            model = comfy_nodes.LoraLoaderModelOnly().load_lora_model_only(
                model, turbo_lora, 1.0)[0]
        return model, clip, vae

    def generate(self, image, aspect, draft_long_side, prompt, negative,
                 seed, quality, trim, auto_prefix, custom_width=0,
                 custom_height=0, unet_name=None, clip_name=None,
                 vae_name=None, outpaint_lora=None, turbo_lora=None,
                 lora_strength=1.0):
        import nodes as comfy_nodes
        from comfy_extras.nodes_qwen import TextEncodeQwenImage21

        unet_name = unet_name or _default_choice(UNETS, "qwen_image_2.1")
        clip_name = clip_name or _default_choice(CLIPS, "qwen3vl_8b")
        vae_name = vae_name or _default_choice(VAES, "qwen_image_2.1_vae")
        outpaint_lora = _resolve_lora(
            outpaint_lora if outpaint_lora is not None else _default_choice(LORAS, "outpaint"),
            "outpaint")
        turbo_lora = _resolve_lora(
            turbo_lora if turbo_lora is not None else _default_choice(LORAS, "8step"),
            "8step")

        fast = quality.startswith("快速")
        steps = 8 if fast else 25
        cfg = 1.0
        tw, th = _target_wh(aspect, draft_long_side, custom_width, custom_height)

        # 触发句 + 场景描述
        scene = (prompt or "").strip()
        if auto_prefix:
            full_prompt = TRIGGER + ("\nScene: " + scene if scene else "")
        else:
            full_prompt = scene or TRIGGER

        model, clip, vae = self._load_models(
            unet_name, clip_name, vae_name, outpaint_lora, turbo_lora,
            lora_strength, fast)

        drafts, refs = [], []
        for i in range(image.shape[0]):
            src = _to_pil(image[i])
            if str(trim).startswith("自动"):
                src, cut, _colors = _trim_solid_border(src)
            ref, geo = _compose_gray_canvas(src, tw, th)

            # 文本编码器直接看灰底参考图；resolution=0 → 输出 latent 与参考图同尺寸
            enc = TextEncodeQwenImage21.execute(
                clip, full_prompt, negative or "", vae, 0, {"image_1": ref})
            positive, negative_cond, latent = enc[0], enc[1], enc[2]

            sampled = comfy_nodes.KSampler().sample(
                model, (int(seed) + i) & 0xffffffffffffffff, steps, cfg,
                "euler", "simple", positive, negative_cond, latent, 1.0)[0]
            draft = _vae_decode(vae, sampled)

            drafts.append(draft)
            # reference 端口输出生成后的草图，不再输出生成前的灰底参考图
            refs.append(draft)

        return (torch.cat(drafts, 0), torch.cat(refs, 0))


NODE_CLASS_MAPPINGS = {
    "ShangguQwenOutpaintDraft": ShangguQwenOutpaintDraft,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ShangguQwenOutpaintDraft": "Qwen扩图草图→IMAGE (Shanggu)",
}
