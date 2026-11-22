# -*- coding: utf-8 -*-
"""整图重绘（角色参考）节点 —— 彻底替代 mask 扩图。

为什么不做 mask 扩图：
    mask 扩图把画面切成「原图区 mask=0 / 扩展区 mask=1」，模型不知道哪是
    人、哪是背景，只看到断掉的皮肤/衣角像素，于是按统计规律往外续写 ——
    多手脚、接缝、边缘不和谐都来自这个架构，调填充色/denoise 都救不了。

本节点的做法（整图重绘 + 角色参考）：
    原图人物 ──去黑边/缩放──> Picture 1（参考图 latent + 视觉 token）
    Qwen 写的场景提示词 ──> positive（自动加 CharRef 触发句）
    MOSS + CharRef v2 LoRA ──> model
    目标尺寸 16:9（对齐 16）──> 空 latent
    =>
    model / positive / negative / latent 四个输出直接接 KSampler
    （denoise 用 1.0），VAEDecode 出图。

    整张图从头生成，没有 mask、没有「原图区」，所以根本没有接缝这个概念。
    人物身份由 CharRef v2 的参考 latent 锁住，场景由你的提示词决定。

依赖：
    * ComfyUI >= 0.43（内置 TextEncodeMOSSImageEdit）
    * 角色一致性 LoRA：*charref*.safetensors（自动从 loras 里找）
"""
from __future__ import annotations

import json

import numpy as np
import torch

try:  # 包内导入
    from .sg_outpaint import _trim_solid_border, _plural_to_wh_ex, TRIMS, ASPECTS
except ImportError:  # 直接 import 模块时
    from sg_outpaint import _trim_solid_border, _plural_to_wh_ex, TRIMS, ASPECTS

try:
    import folder_paths
except ImportError:
    folder_paths = None

CHARREF_PREFIX = "Same character and art style as Picture 1. "
EDIT_FORMATS = ["ai-toolkit", "ai-toolkit (kv_cache off)", "Moss-Image-Edit"]
_NO_LORA = "(未找到角色参考LoRA)"


def _lora_choices():
    """列出 loras，并把名字含 charref 的排到最前面（默认就选中它）。

    ComfyUI 正常启动会加载 extra_model_paths.yaml，共享目录里的 charref 会出现。
    """
    if folder_paths is None:
        return [_NO_LORA]
    try:
        all_loras = list(folder_paths.get_filename_list("loras"))
    except Exception:
        all_loras = []
    if not all_loras:
        return [_NO_LORA]
    def score(name):
        low = name.lower()
        if "charref" in low and "moss" in low:
            return 0
        if "charref" in low:
            return 1
        if "一致性" in name and "moss" in low:
            return 2
        return 9
    all_loras.sort(key=score)
    return all_loras


def _unwrap(out):
    """内置 V3 节点返回 NodeOutput，V1 返回 tuple，统一取第一个输出。"""
    if hasattr(out, "result"):
        r = out.result
        if isinstance(r, (tuple, list)):
            return r[0]
        return r
    return out[0]


def _prepare_reference(image, ref_max_side: int, trim: bool):
    """取 batch 第一张，去黑边，等比缩到 ref_max_side 以内。

    返回 (tensor BxHxWx3 float 0~1, info dict)。
    """
    if image.ndim != 4 or image.shape[0] < 1:
        raise ValueError("image 必须是 BxHxWxC 且至少有一张")
    arr = (image[0].detach().cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
    from PIL import Image, ImageOps
    src = Image.fromarray(arr)
    try:
        src = ImageOps.exif_transpose(src)
    except Exception:
        pass
    cut = [0, 0, 0, 0]
    if trim:
        src, cut, _ = _trim_solid_border(src)
    w, h = src.size
    side = max(16, int(ref_max_side))
    scale = min(1.0, side / float(max(w, h)))
    if scale < 1.0:
        nw = max(16, (int(round(w * scale)) // 16) * 16)
        nh = max(16, (int(round(h * scale)) // 16) * 16)
        src = src.resize((nw, nh), Image.LANCZOS)
    else:
        nw, nh = w, h
    ref = torch.from_numpy(
        np.array(src.convert("RGB"), dtype=np.float32) / 255.0
    ).unsqueeze(0)
    return ref, {
        "ref_size": [nw, nh], "ref_scale": round(scale, 4),
        "source": [w, h], "trim_cut": cut,
    }


def _encode_moss_edit(clip, prompt, edit_format, ref_tensor, vae, image_strength):
    """调用 ComfyUI 内置 TextEncodeMOSSImageEdit，把参考图编成 conditioning。"""
    try:
        from comfy_extras.nodes_moss_image import TextEncodeMOSSImageEdit
    except ImportError as exc:
        raise RuntimeError(
            "当前 ComfyUI 没有内置 TextEncodeMOSSImageEdit 节点，"
            "请先升级到 0.43 或更高版本（0.45 已验证）"
        ) from exc
    # Autogrow 模板前缀是 image_，槽位名 image_0..image_9
    images = {"image_0": ref_tensor}
    return _unwrap(TextEncodeMOSSImageEdit.execute(
        clip, prompt, edit_format, images, vae, image_strength
    ))


class ShangguCharRefScene:
    """角色参考整图重绘：原图人物 + 场景提示词 -> 可直接采样的整套输入。"""

    @classmethod
    def INPUT_TYPES(cls):
        loras = _lora_choices()
        return {
            "required": {
                "image": ("IMAGE", {
                    "tooltip": "原图（人物在这里）。会先自动去黑边、按 ref_max_side 缩放，作为 Picture 1 参考"}),
                "model": ("MODEL", {"tooltip": "MOSS-Image 主模型（int8 / bf16 都可）"}),
                "clip": ("CLIP", {"tooltip": "Qwen3.5-9B 文本编码器，类型要选 moss_image"}),
                "vae": ("VAE", {"tooltip": "flux2-vae，用来编码参考图与解码成图"}),
                "prompt": ("STRING", {
                    "multiline": True,
                    "default": "one character, full body, standing on a rainy street at night, cinematic lighting, detailed background",
                    "tooltip": "场景提示词（Qwen 看图后写的那种）。auto_prefix 开启时会自动补 CharRef 触发句"}),
            },
            "optional": {
                "charref_lora": (loras, {
                    "default": loras[0],
                    "tooltip": "角色一致性 LoRA（charref v2）。列表按 charref 优先排序"}),
                "lora_strength": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05,
                    "tooltip": "CharRef LoRA 强度，官方推荐 1.0；人物不像可加到 1.2~1.4"}),
                "auto_prefix": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "自动在提示词前加 Same character and art style as Picture 1. ——CharRef 必须"}),
                "image_strength": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 100.0, "step": 0.05,
                    "tooltip": "参考图注意力权重：1=标准，>1 更像原图人物，0=忽略参考图"}),
                "edit_format": (EDIT_FORMATS, {
                    "default": EDIT_FORMATS[0],
                    "tooltip": "CharRef v2 用 ai-toolkit（kv_cache on）；另外两项是别的 LoRA 布局"}),
                "aspect": (ASPECTS, {"default": "16:9", "tooltip": "成图比例，竖图转横屏选 16:9"}),
                "long_side": ("INT", {
                    "default": 1024, "min": 512, "max": 2048, "step": 64,
                    "tooltip": "成图长边像素，自动对齐到 16（MOSS 要求）。显存紧就保持 1024"}),
                "ref_max_side": ("INT", {
                    "default": 768, "min": 256, "max": 1536, "step": 64,
                    "tooltip": "参考图最长边。越小越省显存、但身份细节越少；16GB 建议 768"}),
                "trim": (TRIMS, {"default": TRIMS[0],
                                 "tooltip": "自动裁掉源图自带的纯黑/纯白 padding 边"}),
                "target_width": ("INT", {"default": 0, "min": 0, "max": 8192, "step": 16, "advanced": True}),
                "target_height": ("INT", {"default": 0, "min": 0, "max": 8192, "step": 16, "advanced": True}),
            },
        }

    RETURN_TYPES = ("MODEL", "CONDITIONING", "CONDITIONING", "LATENT", "STRING")
    RETURN_NAMES = ("model", "positive", "negative", "latent", "info")
    FUNCTION = "prepare"
    CATEGORY = "image/Shanggu 整图重绘"
    DESCRIPTION = (
        "整图重绘（角色参考）——替代 mask 扩图。原图人物当 Picture 1，"
        "Qwen 写的场景提示词当场景，内部加载 CharRef v2 LoRA + 内置 "
        "TextEncodeMOSSImageEdit，输出 model/positive/negative/latent 直接接 "
        "KSampler（denoise=1.0）。整图从头生成，没有 mask、没有接缝、不会只补出局部手脚。"
    )

    def prepare(self, image, model, clip, vae, prompt,
                charref_lora=None, lora_strength=1.0, auto_prefix=True,
                image_strength=1.0, edit_format=EDIT_FORMATS[0],
                aspect="16:9", long_side=1024, ref_max_side=768,
                trim=TRIMS[0], target_width=0, target_height=0):
        # --- 1) 参考图预处理 ---
        ref, ref_info = _prepare_reference(image, ref_max_side, str(trim).startswith("自动"))

        # --- 2) 目标尺寸（MOSS 要 16 的倍数）---
        tw, th = _plural_to_wh_ex(aspect, long_side, target_width, target_height, 16)

        # --- 3) 加载 CharRef LoRA ---
        applied = None
        if charref_lora and charref_lora != _NO_LORA and lora_strength > 0:
            try:
                from nodes import LoraLoaderModelOnly
                applied, = LoraLoaderModelOnly().load_lora_model_only(
                    model, charref_lora, lora_strength
                )
            except Exception as exc:
                raise RuntimeError("加载角色参考 LoRA 失败：{}".format(exc)) from exc
        if applied is None:
            applied = model

        # --- 4) 编码 positive / negative ---
        final_prompt = prompt or ""
        if auto_prefix and CHARREF_PREFIX.strip() not in final_prompt:
            final_prompt = CHARREF_PREFIX + final_prompt
        positive = _encode_moss_edit(clip, final_prompt, edit_format, ref, vae, image_strength)
        # CharRef 规定 negative 用同一个编码器、空 prompt
        negative = _encode_moss_edit(clip, "", edit_format, ref, vae, image_strength)

        # --- 5) 空 latent ---
        from nodes import EmptyLatentImage
        latent, = EmptyLatentImage().generate(tw, th, 1)

        info = {
            "mode": "charref-full-redraw",
            "target": [tw, th],
            "lora": charref_lora if applied is not model else None,
            "lora_strength": lora_strength if applied is not model else 0,
            "edit_format": edit_format,
            "image_strength": image_strength,
            "prompt_head": final_prompt[:120],
            "ksampler": "denoise=1.0（整图从头生成，不要调低）",
            **ref_info,
        }
        return (applied, positive, negative, latent,
                json.dumps(info, ensure_ascii=False))


NODE_CLASS_MAPPINGS = {
    "ShangguCharRefScene": ShangguCharRefScene,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ShangguCharRefScene": "角色参考整图重绘 (Shanggu)",
}
