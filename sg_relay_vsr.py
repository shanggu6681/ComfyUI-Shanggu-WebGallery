# -*- coding: utf-8 -*-
"""Shanggu RelayVSR 超分放大节点。

基于 ComfyUI 原生 RelayVSR（UNET + Flash VAE，1 步 euler/simple，cfg=1），一条链
RelayVSRConditioning -> KSampler -> VSRDecode 封装成一个节点。相比 LC 原版的改进：

- 模型和 VAE 在界面上直接可见：下拉框列出本机 diffusion_models / vae 里所有
  relayvsr 文件（含子目录），模型缺什么一目了然；
- 分块 token 数（CHUNK_TOKENS）开放成控件，16GB 卡可按需调 VRAM 占用；
- 其余功能对齐 LC 原版：video/image 两种源、细节柔化（亮度软限幅 12 级）、
  protect_mask 人脸保护、relay 流式输出（视频不占满内存）。

模型来源：Comfy-Org/RelayVSR (Apache-2.0)，int8 量化版各 5.2 GB。
本节点不做自动下载——模型缺失时报错并给出文件名与存放目录。
"""
from __future__ import annotations

import json
import os
import struct

import torch

import comfy.model_management
import comfy.sd
import comfy.utils
import folder_paths
import nodes

try:
    import comfy.ldm.wan.model_vsr as _vsr
except Exception:  # ComfyUI 0.44.x stable 没有 RelayVSR，0.45 起内置
    _vsr = None

SOFT_CLIP = 12.0 / 127.5  # 亮度软限幅：模型新增细节最多 12/255 级（tanh），防墨线睫毛和光晕
LUMA = (0.299, 0.587, 0.114)
DEFAULT_CHUNK_TOKENS = 8192  # 约 4x 下一帧 960x544 关键帧的量；越小越省 VRAM 越慢
_CACHE = {"model": (None, None), "vae": (None, None)}

_face_hint = (
    "facexlib 未安装，自动人脸保护不可用（pip install facexlib），或手动接 protect_mask。")


def _scan(kind: str, keyword: str) -> list[str]:
    """列出某模型目录下文件名含 keyword 的所有文件（含子目录前缀）。"""
    return [n for n in folder_paths.get_filename_list(kind)
            if keyword.lower() in os.path.basename(n.replace("\\", "/")).lower()]


def _models() -> list[str]:
    return _scan("diffusion_models", "relayvsr")


def _vaes() -> list[str]:
    return _scan("vae", "relayvsr")


def _default_of(lst: list[str], *keywords) -> str | None:
    for kw in keywords:
        for n in lst:
            b = os.path.basename(n.replace("\\", "/")).lower()
            if kw in b and "bf16" not in b:
                return n
    return lst[0] if lst else None


_MODELS = _models()
_VAES = _vaes()
_DEF_MODEL = _default_of(_MODELS, "x4", "x2")
_DEF_VAE = _default_of(_VAES, "x4", "x2")


def _full(kind: str, name: str) -> str | None:
    names = folder_paths.get_filename_list(kind)
    return folder_paths.get_full_path(kind, name) if name in names else None


def _st_keys(path):
    """{key: shape}——只读 safetensors 头，不加载权重。"""
    try:
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n > 50_000_000:
                return {}
            h = json.loads(f.read(n))
    except (OSError, ValueError):
        return {}
    h.pop("__metadata__", None)
    return {k: v.get("shape") for k, v in h.items()}


def _is_bf16_relay(path, factor):
    """原版 bf16 发布文件判定：LQ 输入按 4*factor 像素重排。"""
    shape = _st_keys(path).get("lq_proj.conv1.weight")
    return bool(shape) and shape[1] == 3 * (4 * factor) ** 2


# diffusers Wan 键名 -> ComfyUI Wan 键名（int8 量化版本来就是 ComfyUI 命名）
_BLOCK_KEYS = (("attn1.to_out.0.", "self_attn.o."), ("attn2.to_out.0.", "cross_attn.o."),
               ("attn1.to_", "self_attn."), ("attn2.to_", "cross_attn."),
               ("attn1.", "self_attn."), ("attn2.", "cross_attn."),
               ("ffn.net.0.proj.", "ffn.0."), ("ffn.net.2.", "ffn.2."),
               ("norm2.", "norm3."), ("scale_shift_table", "modulation"))
_TOP_KEYS = {"condition_embedder.text_embedder.linear_1.": "text_embedding.0.",
             "condition_embedder.text_embedder.linear_2.": "text_embedding.2.",
             "condition_embedder.time_embedder.linear_1.": "time_embedding.0.",
             "condition_embedder.time_embedder.linear_2.": "time_embedding.2.",
             "condition_embedder.time_proj.": "time_projection.1.",
             "proj_out.": "head.head.",
             "lq_proj.out_proj.": "lq_proj_in.linear.", "lq_proj.": "lq_proj_in."}


def _from_diffusers(sd):
    out = {}
    for k, v in sd.items():
        if k.startswith("blocks."):
            _, idx, rest = k.split(".", 2)
            for a, b in _BLOCK_KEYS:
                if rest.startswith(a):
                    rest = b + rest[len(a):]
                    break
            k = f"blocks.{idx}.{rest}"
        elif k == "scale_shift_table":
            k = "head.modulation"
        elif k == "prompt_embeds":
            k, v = "positive_conditioning", v.reshape(v.shape[-2:])
        else:
            for a, b in _TOP_KEYS.items():
                if k.startswith(a):
                    k = b + k[len(a):]
                    break
        if k.startswith("lq_proj_in.norm") and v.ndim == 3:  # [C, 1, 1] -> [C]
            v = v.reshape(-1)
        out[k] = v
    return out


def _load_model(path):
    key, model = _CACHE["model"]
    if key != path:
        if model is not None:  # 换模型前先释放旧的
            _CACHE["model"] = (None, None)
            del model
            comfy.model_management.unload_all_models()
            comfy.model_management.soft_empty_cache()
        print(f"[Shanggu RelayVSR] 加载 {os.path.basename(path)}")
        if "lq_proj.conv1.weight" in _st_keys(path):  # 原版 bf16：先重命名键
            sd, metadata = comfy.utils.load_torch_file(path, return_metadata=True)
            model = comfy.sd.load_diffusion_model_state_dict(_from_diffusers(sd), metadata=metadata)
            del sd
        else:
            model = comfy.sd.load_diffusion_model(path)
        if model is None:
            raise RuntimeError(f"[Shanggu RelayVSR] {os.path.basename(path)} 不是 ComfyUI 能加载的 RelayVSR 模型。")
        _CACHE["model"] = (path, model)
    return model


def _load_vae(path):
    key, vae = _CACHE["vae"]
    if key != path:
        sd, metadata = comfy.utils.load_torch_file(path, return_metadata=True)
        vae = comfy.sd.VAE(sd=sd, metadata=metadata)
        vae.throw_exception_if_invalid()
        _CACHE["vae"] = (path, vae)
    return vae


def _require(kind, name):
    path = _full(kind, name) if name else None
    if not path:
        raise RuntimeError(
            f"[Shanggu RelayVSR] 未找到所选{ '模型' if kind == 'diffusion_models' else 'VAE' }文件：{name}。\n"
            f"放到 E:\\Comfy-Desktop\\ComfyUI-Shared\\models\\{kind}\\ 后刷新。\n"
            f"官方下载（Comfy-Org/RelayVSR）：int8 模型 relayvsr_x2_lightx2v_int8_convrot / "
            f"relayvsr_x4_int8_convrot.safetensors（各 5.2GB），Flash VAE relayvsr_flash_vae_x2 / x4_fp32.safetensors（各 128MB）。")
    return path


class ShangguRelayVSRUpscale:
    """RelayVSR 2x/4x 超分放大：模型/VAE 界面可见，支持人脸保护与流式视频输出。"""

    @classmethod
    def INPUT_TYPES(cls):
        models, vaes = _models(), _vaes()
        if not models:
            models = ["⚠ 未找到 relayvsr 模型（需下载后刷新）"]
        if not vaes:
            vaes = ["⚠ 未找到 relayvsr VAE（需下载后刷新）"]
        return {"required": {
            "image": ("IMAGE", {"tooltip": "单张图、图片批次，或一段视频的帧（看 source 设置）"}),
            "source": (["video", "image"], {"default": "video",
                       "tooltip": "video：帧与帧之间互相参考，结果更稳定（视频必须选这个）。"
                                  "image：每张独立放大，互不影响"}),
            "model": (models, {"default": _DEF_MODEL or models[0],
                      "tooltip": "diffusion_models 里所有 relayvsr 模型。x2 配视频、x4 配图片；"
                                 "bf16 是原版权重（10GB，更吃显存）。没有就先去 "
                                 "Comfy-Org/RelayVSR 下载 int8 版（x4 对应 relayvsr_x4_int8_convrot.safetensors）"}),
            "vae": (vaes, {"default": _DEF_VAE or vaes[0],
                    "tooltip": "vae 里所有 relayvsr Flash VAE。必须和模型倍数匹配"
                               "（x4 模型配 x4 VAE）"}),
            "seed": ("INT", {"default": 1, "min": 0, "max": 0xFFFFFFFFFFFFFFFF,
                             "control_after_generate": "fixed",
                      "tooltip": "单步采样，种子只影响最细微的噪点纹理"}),
            "detail": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01,
                      "tooltip": "保留多少模型新增的细节。1.0=完整输出；调低则向普通 bicubic 放大靠拢；"
                                 "0.5 左右可压掉过重的毛发墨线"}),
            "chunk_tokens": ("INT", {"default": DEFAULT_CHUNK_TOKENS, "min": 1024,
                                     "max": 65536, "step": 1024,
                      "tooltip": "分块解码大小（token 数）。显存不够就调小（如 4096），"
                                 "16GB 卡默认即可；对画质无影响，只影响速度/显存"}),
        }, "optional": {
            "protect_mask": ("MASK", {"tooltip": "可选。保护区域（如 detailer 处理过的脸和手）——"
                                           "区域内模型少加细节，保持原来的样子。输出端 protected 是它放大后的样子"}),
            "mask_protect_strength": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05,
                     "tooltip": "保护区内保留多少新增细节。0=完全保护，1=不保护；0.5 留一点细颗粒，"
                                "避免脸看起来像贴上去的。mask 边缘自动羽化"}),
            "detect_faces": ("BOOLEAN", {"default": True,
                     "tooltip": "没接 protect_mask 时自动找脸保护（需要 facexlib，约 55MB 首次自动下载）。"
                                "手绘风的脸（雀斑/排线）建议关掉。" + _face_hint}),
        }, "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"}}

    RETURN_TYPES = ("IMAGE", "RELAY", "MASK")
    RETURN_NAMES = ("image", "relay", "protected")
    OUTPUT_TOOLTIPS = ("放大后的帧。全尺寸解码时整批驻留内存：5 秒视频 4x 约 12GB，长视频请只用 relay 输出",
                       "接 LC Save Video 这类流式保存节点：逐帧解码保存，内存不爆。只接这个口时连全尺寸图都不生成",
                       "protect_mask 放大后的样子（没接时为空 mask）：可接给后续节点继续保护")
    FUNCTION = "run"
    CATEGORY = "Shanggu/Image"
    DESCRIPTION = ("RelayVSR 一键超分（ComfyUI 0.45+ 原生）。2x 配视频、4x 配图片，1 步采样，"
                   "快而锐，自动柔化新增细节防墨线。模型/VAE 下拉框直接看本机文件。")

    @classmethod
    def IS_CHANGED(cls, unique_id=None, **kwargs):
        prompt = _queued_prompt()
        if prompt is None or unique_id is None:
            return float("nan")
        return "image linked" if linked(prompt, unique_id, 0) else "relay only"

    def run(self, image, source, model, vae, seed, detail, chunk_tokens,
            protect_mask=None, mask_protect_strength=0.5, detect_faces=True,
            prompt=None, unique_id=None):
        if _vsr is None:
            raise RuntimeError("[Shanggu RelayVSR] 需要 ComfyUI 0.45 及以上（RelayVSR 原生支持）。")
        from comfy_extras.nodes_wan import RelayVSRConditioning, VSRDecode

        # 扩图等节点会输出 4 通道 RGBA（带透明通道），而 VSR 解码 / 人脸检测都按
        # 3 通道处理。统一在此压成 RGB：黑底合成，alpha=1 的区域保持原样。
        if image.dim() == 4 and int(image.shape[-1]) == 4:
            alpha = image[..., 3:4].clamp(0.0, 1.0)
            image = image[..., :3] * alpha
            print("[Shanggu RelayVSR] 输入为 RGBA，已按黑底合成压成 RGB。")

        m = _load_model(_require("diffusion_models", model))
        v = _load_vae(_require("vae", vae))
        chunk = max(1024, int(chunk_tokens))

        parts = [image[i:i + 1] for i in range(image.shape[0])] if source == "image" and image.shape[0] > 1 else [image]
        jobs = [(self._sample(m, p, seed, chunk), p) for p in parts]
        up = v.first_stage_model.upscale_factor
        out_hw = (int(image.shape[1]) * up, int(image.shape[2]) * up)

        prot = None
        if protect_mask is None and detect_faces and float(mask_protect_strength) < 0.999:
            protect_mask = _face_masks(image, 3 if source == "video" else 1)
        if protect_mask is not None and float(mask_protect_strength) < 0.999:
            prot = {"mask": protect_mask.reshape(-1, *protect_mask.shape[-2:]).float().cpu(),
                    "keep": float(mask_protect_strength), "factor": up}

        wired = prompt is None or unique_id is None or linked(prompt, unique_id, 2)
        protected = _protected(protect_mask if wired else None, out_hw)
        relay = {"vae": v, "jobs": jobs, "detail": detail,
                 "count": sum(int(p.shape[0]) for p in parts),
                 "size": (out_hw[1], out_hw[0]), "prot": prot}
        relay["frames"] = lambda batch=8: iter_relay_frames(relay, batch)

        if prompt is not None and unique_id is not None and not linked(prompt, unique_id, 0):
            return (None, relay, protected)  # 只接了 relay 保存：跳过全尺寸解码
        if prot is not None:  # 带保护的解码：自己的逐帧循环才知道当前是第几帧
            out = torch.empty((relay["count"], out_hw[0], out_hw[1], 3), dtype=torch.float32)
            n = 0
            for samples, p in jobs:
                for px in _decode_frames(v, samples, p, detail, prot, n):
                    out[n] = px.float().div_(255.0).cpu()
                    n += 1
            return (out, relay, protected)

        out = None
        for k, (samples, p) in enumerate(jobs):
            with _softened(v):
                dec = VSRDecode.execute(v, samples, p, detail).result[0]
            if len(jobs) == 1:
                return (dec, relay, protected)
            if out is None:
                out = torch.empty((len(jobs),) + tuple(dec.shape[1:]), dtype=dec.dtype)
            out[k:k + 1] = dec
            del dec
        return (out, relay, protected)

    @staticmethod
    def _sample(m, image, seed, chunk_tokens=DEFAULT_CHUNK_TOKENS):
        from comfy_extras.nodes_wan import RelayVSRConditioning

        old = (_vsr.RELAY_WAN_CACHE_KEYFRAMES, _vsr.RELAY_WAN_CHUNK_TOKENS)
        try:
            _vsr.RELAY_WAN_CACHE_KEYFRAMES = 0
            _vsr.RELAY_WAN_CHUNK_TOKENS = chunk_tokens
            positive, latent = RelayVSRConditioning.execute(m, image).result
            return nodes.common_ksampler(m, seed, 1, 1.0, "euler", "simple",
                                         positive, positive, latent, denoise=1.0)[0]
        finally:
            _vsr.RELAY_WAN_CACHE_KEYFRAMES, _vsr.RELAY_WAN_CHUNK_TOKENS = old


def _protected(mask, out_hw):
    """protect_mask 双线性放大到输出尺寸 (B, H, W)；没接就给一张空 mask。"""
    import torch.nn.functional as F

    if mask is None:
        return torch.zeros((1, out_hw[0], out_hw[1]), dtype=torch.float32)
    m = mask.reshape(-1, 1, *mask.shape[-2:]).float()
    return F.interpolate(m, size=out_hw, mode="bilinear", align_corners=False).clamp_(0.0, 1.0)[:, 0].cpu()


_FACE_NETS = {}
# BiSeNet 人脸解析类别："脸"=皮肤/眉/眼/镜/耳/鼻/嘴/唇（不含脖子和头发）
_FACE_CLASSES = (1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13)


def _face_net(name, dev):
    key = (name, str(dev))
    if key not in _FACE_NETS:
        root = os.path.join(folder_paths.models_dir, "facexlib")
        os.makedirs(root, exist_ok=True)
        if name == "detect":
            from facexlib.detection import init_detection_model
            _FACE_NETS[key] = init_detection_model("retinaface_mobile0.25", device=dev, model_rootpath=root)
        else:
            from facexlib.parsing import init_parsing_model
            _FACE_NETS[key] = init_parsing_model("bisenet", device=dev, model_rootpath=root)
    return _FACE_NETS[key]


@torch.no_grad()
def _face_masks(image, stride=1):
    """自动找脸：每帧一个脸 mask (B,H,W) 原尺寸，找不到返回 None。RetinaFace 检测（≤640px），
    BiSeNet 解析；视频每 stride 帧检测一次，中间帧插值。"""
    import time

    import numpy as np
    import torch.nn.functional as F

    import comfy.model_management as mm

    t0 = time.time()
    dev = mm.get_torch_device()
    try:
        det, parse = _face_net("detect", dev), _face_net("parse", dev)
    except Exception as exc:
        hint = f' 安装："{_python()}" -m pip install facexlib' if isinstance(exc, ImportError) else ""
        print(f"[Shanggu RelayVSR] detect_faces 跳过：脸模型不可用（{exc}）。{hint} 或手动接 protect_mask。")
        return None
    B, H, W = int(image.shape[0]), int(image.shape[1]), int(image.shape[2])
    if image.dim() == 4 and int(image.shape[-1]) > 3:
        image = image[..., :3]  # 兜底：RGBA 只取 RGB 通道（正常不应走到这里，run() 已压平）
    s = min(1.0, 640 / max(H, W))
    out = torch.zeros((B, H, W), dtype=torch.float32)
    mean = torch.tensor([0.485, 0.456, 0.406], device=dev).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device=dev).view(1, 3, 1, 1)
    keep = torch.tensor(_FACE_CLASSES, device=dev)
    found = 0
    keys = sorted(set(range(0, B, max(1, stride))) | {B - 1})
    for i in keys:
        frame = image[i].to(dev, torch.float32).permute(2, 0, 1)[None]
        small = F.interpolate(frame, scale_factor=s, mode="area") if s < 1 else frame
        bgr = (small[0].flip(0).permute(1, 2, 0) * 255).round().clamp(0, 255).byte().cpu().numpy()
        boxes = det.detect_faces(np.ascontiguousarray(bgr), 0.8)
        for row in (boxes if boxes is not None else []):
            x1, y1, x2, y2 = (float(v) / s for v in row[:4])
            if min(x2 - x1, y2 - y1) * s < 16:
                continue
            cx, cy, side = (x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, y2 - y1) * 1.7
            l, t = int(max(0, cx - side / 2)), int(max(0, cy - side / 2 - 0.05 * side))
            r, b = int(min(W, cx + side / 2)), int(min(H, cy + side / 2 + 0.05 * side))
            if r - l < 8 or b - t < 8:
                continue
            crop = F.interpolate(frame[:, :, t:b, l:r], size=(512, 512), mode="bilinear", align_corners=False)
            logits = F.interpolate(parse((crop - mean) / std)[0], size=(b - t, r - l),
                                   mode="bilinear", align_corners=False)
            face = torch.isin(logits.argmax(1)[0], keep).float().cpu()
            out[i, t:b, l:r] = torch.maximum(out[i, t:b, l:r], face)
            found += 1
    for a, b in zip(keys, keys[1:]):
        for i in range(a + 1, b):
            w = (i - a) / (b - a)
            out[i] = out[a] * (1 - w) + out[b] * w
    print(f"[Shanggu RelayVSR] detect_faces：{len(keys)}/{B} 帧里找到 {found} 张脸，{time.time() - t0:.1f}s")
    return out if found else None


def _python():
    import sys
    exe = sys.executable
    return "python_embeded\\python.exe" if "python_embeded" in exe.lower() else f'"{exe}"'


def _blur(x, sigma):
    """(1,1,H,W) 上的可分离高斯。"""
    import torch.nn.functional as F

    r = max(1, int(sigma * 3))
    k = torch.exp(-(torch.arange(-r, r + 1, device=x.device, dtype=x.dtype) ** 2) / (2 * sigma * sigma))
    k = k / k.sum()
    x = F.conv2d(F.pad(x, (r, r, 0, 0), mode="replicate"), k.view(1, 1, 1, -1))
    return F.conv2d(F.pad(x, (0, 0, r, r), mode="replicate"), k.view(1, 1, -1, 1))


def _weight(prot, index, out_hw, dec_hw, dev):
    """一帧内每个像素保留多少 Relay 新增细节：mask 外 1，mask 内 prot['keep']，边缘羽化
    （sigma 为源图 3px*倍数，过渡不出现环）。"""
    import torch.nn.functional as F

    if prot is None:
        return None
    mask = prot["mask"]
    m = mask[min(index, mask.shape[0] - 1)].to(dev, torch.float32)[None, None]
    m = F.interpolate(m, size=out_hw, mode="bilinear", align_corners=False).clamp_(0.0, 1.0)
    m = _blur(m, 3.0 * prot["factor"])
    m = F.pad(m, (0, dec_hw[1] - out_hw[1], 0, dec_hw[0] - out_hw[0]))  # 解码器 padding 不保护
    return 1.0 - (1.0 - prot["keep"]) * m


def _soft_frame(fsm, lr, dtype, freqs, freqs_past, condition, history, detail, weight=None):
    """fsm.decode_frame + 残差柔化（亮度软限幅 SOFT_CLIP，tanh），再按 weight 逐像素缩放。
    解码器 history 取自残差形成之前的层，所以只影响当前帧、不污染后续帧。"""
    import torch.nn.functional as F

    decoded, history = type(fsm).decode_frame(fsm, lr, dtype, freqs, freqs_past, condition, history, 1.0)
    base = F.interpolate(lr, scale_factor=fsm.upscale_factor, mode="bicubic", align_corners=False)
    res = (decoded - base).float()
    y = res[:, 0:1] * LUMA[0] + res[:, 1:2] * LUMA[1] + res[:, 2:3] * LUMA[2]
    y = torch.tanh(y / SOFT_CLIP).mul_(SOFT_CLIP * detail)
    if weight is not None:
        y = y * weight
    return (base.float() + y).to(decoded.dtype), history


class _softened:
    """在 with 块期间把原生 VSRDecode 路由到 _soft_frame。"""

    def __init__(self, vae):
        self.fsm = vae.first_stage_model

    def __enter__(self):
        fsm = self.fsm
        fsm.decode_frame = lambda *a: _soft_frame(fsm, *a)  # 实例属性遮蔽类方法
        return self

    def __exit__(self, *exc):
        self.fsm.__dict__.pop("decode_frame", None)
        return False


def _decode_frames(vae, samples, lq_images, detail, prot=None, start_index=0):
    """RelayVSR Flash 解码器自己的逐帧循环，每完成一帧就 yield（uint8 HxWx3，GPU 上）。
    history 跨帧传递，和一次过解码完全一致。prot 见 _weight；start_index 是本 job 首帧在全批里的序号。"""
    import torch.nn.functional as F

    fsm = vae.first_stage_model
    x = samples["samples"]
    mm = comfy.model_management
    mm.load_models_gpu([vae.patcher], memory_required=vae.memory_used_decode(x.shape, vae.vae_dtype),
                       force_full_load=getattr(vae, "disable_offload", False))
    x = x.to(device=vae.device, dtype=vae.vae_dtype)
    _, _, _, height, width = x.shape
    frames, lq_h, lq_w = lq_images.shape[:3]
    out_h, out_w = lq_h * fsm.upscale_factor, lq_w * fsm.upscale_factor
    depth = len(fsm.backbone.blocks)
    keyframes = _vsr.relay_keyframes(frames)
    dev = x.device
    pbar = comfy.utils.ProgressBar(frames)
    with torch.inference_mode():
        freqs, freqs_past = fsm.grid_rope(0, height, width, dev), fsm.grid_rope(-1, height, width, dev)
        start = fsm.prepare_condition(x[:, :, 0], freqs)
    for window, (first, last) in enumerate(zip(keyframes, keyframes[1:] or keyframes)):
        mm.throw_exception_if_processing_interrupted()
        with torch.inference_mode():
            end = fsm.prepare_condition(x[:, :, min(window + 1, len(keyframes) - 1)], freqs)
            condition = list(zip(start, end))
            history = [None] * depth
            lr = lq_images[first:last + 1].to(device=dev)[..., :3].movedim(-1, 1) * 2.0 - 1.0
            lr = F.pad(lr, (0, width * fsm.latent_stride - lq_w, 0, height * fsm.latent_stride - lq_h), mode="replicate")
        for i, frame in enumerate(range(first, last + 1)):
            with torch.inference_mode():
                dec_hw = (lr.shape[2] * fsm.upscale_factor, lr.shape[3] * fsm.upscale_factor)
                w = _weight(prot, start_index + frame, (out_h, out_w), dec_hw, dev)
                decoded, history = _soft_frame(fsm, lr[i:i + 1], x.dtype, freqs, freqs_past,
                                               condition, history, detail, w)
                px = None
                if window == 0 or frame > first:  # 共享关键帧上一窗口已输出
                    px = decoded[0, :, :out_h, :out_w].add_(1.0).mul_(127.5).clamp_(0.0, 255.0)\
                        .round_().to(torch.uint8).permute(1, 2, 0)
            if px is not None:
                pbar.update(1)
                yield px
        start = end


def iter_relay_frames(relay, batch=8):
    """relay 输出的流式帧迭代：每次 yield [k, H, W, 3] 的 uint8 numpy。整段视频从不驻留内存。"""
    pending = []
    n = 0
    for samples, lq in relay["jobs"]:
        for px in _decode_frames(relay["vae"], samples, lq, relay["detail"], relay.get("prot"), n):
            n += 1
            pending.append(px)
            if len(pending) >= batch:
                yield torch.stack(pending).cpu().numpy()
                pending = []
    if pending:
        yield torch.stack(pending).cpu().numpy()


def _queued_prompt():
    """IS_CHANGED 时拿当前 prompt：ComfyUI 把 dynprompt 挂在上层调用帧。"""
    import inspect

    frame = inspect.currentframe()
    try:
        while frame is not None:
            dp = getattr(frame.f_locals.get("self"), "dynprompt", None)
            if dp is not None and hasattr(dp, "get_original_prompt"):
                return dp.get_original_prompt()
            frame = frame.f_back
    finally:
        del frame
    return None


def linked(prompt, unique_id, slot):
    """队列 prompt 里是否有节点读了这个节点第 slot 号输出口。"""
    uid = str(unique_id)
    for node in (prompt or {}).values():
        for v in (node.get("inputs") or {}).values():
            if isinstance(v, (list, tuple)) and len(v) == 2 and str(v[0]) == uid and v[1] == slot:
                return True
    return False


NODE_CLASS_MAPPINGS = {"ShangguRelayVSRUpscale": ShangguRelayVSRUpscale}
NODE_DISPLAY_NAME_MAPPINGS = {"ShangguRelayVSRUpscale": "Shanggu RelayVSR 超分放大"}
