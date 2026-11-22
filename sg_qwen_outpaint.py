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

输出的是低像素草图（默认长边 768），拿它再去跑 MOSS 做最终风格重绘。
"""
from __future__ import annotations

import os

import numpy as np
import torch
from PIL import Image

from sg_outpaint import _trim_solid_border, ASPECTS, TRIMS

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
    "画面扩展 [0.8-1.0] qwen-image-2.1-outpaint-v2.safetensors",
    "Turbo加速 [1.0-1.0] p_qwen_image_2.1_8step_v0.1-comfyui-T8.safetensors",
])


def _default_choice(options, keyword):
    for name in options:
        if keyword in name.lower():
            return name
    return options[0] if options else keyword


def _align32(n: int) -> int:
    return max(32, int(round(n / 32.0)) * 32)


def _target_wh(aspect: str, long_side: int, custom_w: int = 0,
               custom_h: int = 0) -> tuple[int, int]:
    """目标画布尺寸，长边=long_side，两边对齐 32（Qwen latent 要求）。"""
    if aspect == "自定义" and custom_w > 0 and custom_h > 0:
        return _align32(custom_w), _align32(custom_h)
    try:
        a, b = aspect.split(":")
        ratio = float(a) / float(b)
    except Exception:
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

    RETURN_TYPES = ("IMAGE", "IMAGE", "STRING")
    RETURN_NAMES = ("draft", "reference", "info")
    OUTPUT_TOOLTIPS = ("低像素扩图草图，拿去跑 MOSS 重绘",
                       "喂给模型的灰底参考图（调试用）",
                       "尺寸/比例/参数明细（JSON）")
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
        outpaint_lora = outpaint_lora if outpaint_lora is not None else _default_choice(LORAS, "outpaint")
        turbo_lora = turbo_lora if turbo_lora is not None else _default_choice(LORAS, "8step")

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

        drafts, refs, infos = [], [], []
        for i in range(image.shape[0]):
            src = _to_pil(image[i])
            cut = [0, 0, 0, 0]
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
            draft = comfy_nodes.VAEDecode().decode(sampled, vae)[0]

            drafts.append(draft)
            refs.append(ref)
            infos.append({
                "index": i, "source_size": geo["source"],
                "trim_cut": cut, "aspect": aspect, "target": [tw, th],
                "content_visible": geo["content"], "paste_origin": geo["paste_origin"],
                "quality": quality, "steps": steps, "cfg": cfg, "denoise": 1.0,
                "sampler": "euler", "scheduler": "simple",
                "lora": outpaint_lora, "lora_strength": lora_strength,
                "turbo_lora": turbo_lora if fast else "",
                "prompt": full_prompt,
                "note": "no noise_mask (Qwen seam fix); draft only, redraw with MOSS",
            })

        import json
        return (torch.cat(drafts, 0), torch.cat(refs, 0),
                json.dumps(infos, ensure_ascii=False, indent=1))


NODE_CLASS_MAPPINGS = {
    "ShangguQwenOutpaintDraft": ShangguQwenOutpaintDraft,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ShangguQwenOutpaintDraft": "Qwen扩图草图→IMAGE (Shanggu)",
}
