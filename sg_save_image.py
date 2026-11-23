"""Shanggu 保存图像 —— ausboss Save Image 同款卡片的后端。

保存到 ComfyUI output 目录（含子文件夹），命名 = 前缀 + 可选
日期/时间/尺寸标签 + 防碰撞 5 位计数器（+ 批次 b001），格式支持
PNG / 无损 WebP / 无损 JXL，可选择性嵌入 prompt 与 workflow。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path, PureWindowsPath

import numpy as np
import torch
from PIL import Image

try:
    import folder_paths
except ImportError:  # 离线语法检查
    folder_paths = None

FORMATS = ("png", "webp", "jxl")
FORMAT_EXTENSIONS = {"png": "png", "webp": "webp", "jxl": "jxl"}
COUNTER_WIDTH = 5
BATCH_WIDTH = 3
IMAGE_EXTENSIONS = {
    ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".jxl",
    ".png", ".tif", ".tiff", ".webp",
}


def metadata_disabled() -> bool:
    """ComfyUI 以 --disable-metadata 启动时，服务端决定一律不嵌工作流。"""
    try:
        from comfy.cli_args import args
        return bool(getattr(args, "disable_metadata", False))
    except Exception:
        return False


def jxl_available() -> bool:
    try:
        import pillow_jxl  # noqa: F401
        return True
    except Exception:
        return False


def sanitize_rel(name: str, source: str = "文件名") -> str:
    """把输入规整成安全的相对路径；盘符/绝对路径/.. 直接拒绝。"""
    text = str(name or "").strip().replace("\\", "/")
    if not text:
        return ""
    if ":" in text or PureWindowsPath(text).drive or text.startswith("/"):
        raise ValueError(f"{source} 必须是相对路径（不能带盘符或以 / 开头）。")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError(f"{source} 不能包含 '..'。")
    return "/".join(parts)


def resolve_output_root(output_dir: str, default_root: Path) -> Path:
    """'' -> output 根目录；否则限定在 output 内的子文件夹。"""
    text = str(output_dir or "").strip()
    root = Path(default_root)
    if not text:
        return root
    written = text.replace("\\", "/")
    parts = [p for p in written.split("/") if p not in ("", ".")]
    if written.startswith(("/", "~")) or ":" in text or PureWindowsPath(text).drive or ".." in parts:
        raise ValueError("只能保存到 ComfyUI output 目录内的子文件夹。")
    if not parts:
        return root
    r = root.resolve()
    resolved = (r / "/".join(parts)).resolve()
    if resolved != r and r not in resolved.parents:
        raise ValueError("只能保存到 ComfyUI output 目录内的子文件夹。")
    return resolved


def require_output_path(path: Path, output_root: Path) -> Path:
    r = Path(output_root).resolve()
    resolved = path.resolve()
    if r not in resolved.parents and resolved != r:
        raise ValueError("保存目标必须位于 ComfyUI output 目录内。")
    return path


def strip_image_extension(name: str) -> str:
    text = str(name or "").strip()
    dot = text.rfind(".")
    if dot >= 0 and text[dot:].lower() in IMAGE_EXTENSIONS:
        return text[:dot]
    return text


def fill_name_tokens(text: str, now: datetime, width: int, height: int) -> str:
    """填 %width% %height% %year% %month% %day% %hour% %minute% %second% 记号。"""
    text = str(text or "")
    if "%" not in text:
        return text
    values = {
        "width": str(int(width)), "height": str(int(height)),
        "year": f"{now.year:04d}", "month": f"{now.month:02d}", "day": f"{now.day:02d}",
        "hour": f"{now.hour:02d}", "minute": f"{now.minute:02d}", "second": f"{now.second:02d}",
    }
    for name, value in values.items():
        text = text.replace(f"%{name}%", value)
    return text


def name_stem(base: str, flags: dict, now: datetime, width: int, height: int) -> str:
    parts = [base]
    if flags.get("date"):
        parts.append(now.strftime("%Y-%m-%d"))
    if flags.get("time"):
        parts.append(now.strftime("%H-%M-%S"))
    if flags.get("size"):
        parts.append(f"{int(width)}x{int(height)}")
    return "_".join(parts)


def counter_pattern(stem: str) -> re.Pattern:
    return re.compile(
        rf"^{re.escape(stem)}_(\d{{{COUNTER_WIDTH},}})(?:_b\d{{{BATCH_WIDTH},}})?\.[A-Za-z0-9]+$"
    )


def next_free_counter(existing_names, stem: str) -> int:
    pattern = counter_pattern(stem)
    highest = 0
    for name in existing_names:
        m = pattern.match(str(name))
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def plan_local_names(stem: str, ext: str, count: int, *, counter: bool,
                     batch: bool, next_counter: int = 1) -> list[str]:
    if count <= 0:
        return []
    names = []
    for index in range(count):
        parts = [stem]
        if counter:
            number = next_counter if batch else next_counter + index
            parts.append(f"{number:0{COUNTER_WIDTH}}")
        if batch and count > 1:
            parts.append(f"b{index + 1:0{BATCH_WIDTH}}")
        elif not counter and count > 1:
            parts.append(f"{index + 1:0{max(3, len(str(count)))}}")
        names.append("_".join(parts) + f".{ext}")
    return names


def frame_to_pil(frame: torch.Tensor) -> Image.Image:
    array = np.clip(frame.detach().cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(array)


def _png_info(metadata: dict | None):
    if not metadata:
        return None
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    for key, value in metadata.items():
        info.add_text(key, value)
    return info


def _exif_bytes(metadata: dict | None) -> bytes | None:
    if not metadata:
        return None
    exif = Image.Exif()
    prompt = metadata.get("prompt")
    if prompt is not None:
        exif[0x010F] = "prompt:" + prompt
    rest = {k: v for k, v in metadata.items() if k != "prompt"}
    if rest:
        exif[0x010E] = json.dumps({k: json.loads(v) for k, v in rest.items()})
    return exif.tobytes()


def encode_image(path: Path, frame: torch.Tensor, fmt: str, metadata: dict | None) -> None:
    image = frame_to_pil(frame)
    if fmt == "png":
        image.save(path, format="PNG", pnginfo=_png_info(metadata), compress_level=4)
        return
    if fmt == "webp":
        kwargs = {"format": "WEBP", "lossless": True, "quality": 100, "method": 4}
        exif = _exif_bytes(metadata)
        if exif is not None:
            kwargs["exif"] = exif
        image.save(path, **kwargs)
        return
    if fmt == "jxl":
        if not jxl_available():
            raise RuntimeError(
                "JXL 需要可选依赖 pillow-jxl-plugin（装到 ComfyUI 的 python 里），"
                "没有就选 PNG 或 WebP。"
            )
        kwargs = {"format": "JXL", "lossless": True}
        exif = _exif_bytes(metadata)
        if exif is not None:
            kwargs["exif"] = exif
        try:
            image.save(path, **kwargs)
        except TypeError:
            kwargs.pop("exif", None)
            image.save(path, **kwargs)
        return
    raise ValueError(f"未知格式 '{fmt}'。")


def _register_folder_route() -> None:
    """GET /sg_gallery/save_folders?path=相对路径 -> 列 output 下的子文件夹。"""
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:
        return
    if getattr(PromptServer.instance, "_sg_save_folders_route", False):
        return
    PromptServer.instance._sg_save_folders_route = True

    @PromptServer.instance.routes.get("/sg_gallery/save_folders")
    async def list_folders(request):
        root = Path(folder_paths.get_output_directory()).resolve()
        rel = str(request.rel_url.query.get("path", "")).strip().replace("\\", "/").strip("/")
        if rel.startswith((";", "~")) or ":" in rel or ".." in rel.split("/"):
            return web.json_response({"error": "bad path"}, status=400)
        try:
            target = (root / rel).resolve() if rel else root
        except Exception:
            return web.json_response({"error": "bad path"}, status=400)
        if target != root and root not in target.parents:
            return web.json_response({"error": "outside output"}, status=400)
        if not target.is_dir():
            return web.json_response({"error": "not a folder"}, status=404)
        folders = sorted(
            e.name for e in target.iterdir()
            if not e.name.startswith(".") and not e.is_symlink() and e.is_dir()
        )
        shown = "" if target == root else target.relative_to(root).as_posix()
        return web.json_response({"path": shown, "folders": folders})


class ShangguSaveImage:
    """保存图像（ausboss 卡片同款）：Folder/Filename/标签/格式/嵌入工作流。"""

    CATEGORY = "Shanggu/Image"
    FUNCTION = "save"
    OUTPUT_NODE = True
    RETURN_TYPES = ("STRING", "IMAGE")
    RETURN_NAMES = ("file_path", "images")
    DESCRIPTION = (
        "把图片批次保存为 PNG / 无损 WebP / 无损 JXL，可选嵌入工作流。"
        "文件名 = 前缀 + 标签(日期/时间/尺寸) + 防碰撞计数器；"
        "只写 ComfyUI output 目录（可含子文件夹）。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "BHWC 批次，每张都会保存。"}),
                "filename_prefix": ("STRING", {
                    "default": "Shanggu",
                    "tooltip": "本地文件名前缀，可带子文件夹（如 sets/portraits）。"
                               "支持记号 %width% %height% %date:...% 等。",
                }),
                "output_dir": ("STRING", {
                    "default": "",
                    "tooltip": "output 目录下的子文件夹，如 sets/portraits；"
                               "留空存到 output 根目录。",
                }),
                "format": (FORMATS, {
                    "default": "png",
                    "tooltip": "png 兼容性最好；webp 无损通常更小；"
                               "jxl 无损最紧凑但需要 pillow-jxl-plugin。",
                }),
                "save_metadata": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "开：把 prompt 和工作流嵌进图片（可拖回 ComfyUI）。"
                               "关：干净的图，适合分享/数据集。",
                }),
                "name_counter": ("BOOLEAN", {"default": True,
                                             "tooltip": "追加 5 位递增序号，永不重名。"}),
                "name_date": ("BOOLEAN", {"default": False,
                                          "tooltip": "追加保存日期 YYYY-MM-DD。"}),
                "name_time": ("BOOLEAN", {"default": False,
                                          "tooltip": "追加保存时间 HH-MM-SS。"}),
                "name_size": ("BOOLEAN", {"default": False,
                                          "tooltip": "追加图片尺寸，如 1024x1024。"}),
                "name_batch": ("BOOLEAN", {"default": False,
                                           "tooltip": "批次图追加 b001/b002…。"}),
                "preview": ("BOOLEAN", {"default": True,
                                        "tooltip": "在节点上显示已保存的图片。纯显示。"}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    def save(self, images, filename_prefix, output_dir, format, save_metadata,
             name_counter=True, name_date=False, name_time=False, name_size=False,
             name_batch=False, preview=True, prompt=None, extra_pnginfo=None):
        if folder_paths is None:
            raise RuntimeError("保存图像需要 ComfyUI 的 folder_paths。")
        ext = FORMAT_EXTENSIONS[format]
        embed = save_metadata and not metadata_disabled()
        metadata = None
        if embed:
            metadata = {"prompt": json.dumps(prompt) if prompt is not None else ""}
            if extra_pnginfo:
                for key, value in extra_pnginfo.items():
                    metadata[key] = json.dumps(value)

        output_root = Path(folder_paths.get_output_directory())
        root = resolve_output_root(output_dir, output_root)
        count = int(images.shape[0])
        height, width = int(images.shape[1]), int(images.shape[2])
        now = datetime.now()
        clean = sanitize_rel(strip_image_extension(filename_prefix), "文件名前缀") or "Shanggu"
        if "/" in clean:
            subfolder, base = clean.rsplit("/", 1)
        else:
            subfolder, base = "", clean
        folder = root / subfolder if subfolder else root
        require_output_path(folder, output_root)

        stem = name_stem(base, {"date": bool(name_date), "time": bool(name_time),
                                "size": bool(name_size)}, now, width, height)
        existing = [e.name for e in folder.iterdir()] if folder.is_dir() else []
        names = plan_local_names(stem, ext, count,
                                 counter=bool(name_counter), batch=bool(name_batch),
                                 next_counter=next_free_counter(existing, stem))
        planned = [folder / n for n in names]
        for path in planned:
            require_output_path(path, output_root)

        saved: list[Path] = []
        for path, frame in zip(planned, images):
            path.parent.mkdir(parents=True, exist_ok=True)
            encode_image(path, frame, format, metadata)
            saved.append(path)

        resolved_root = output_root.resolve()
        previews: list[dict] = []
        shown: list[str] = []
        for path in saved:
            resolved = path.resolve()
            relative = resolved.parent.relative_to(resolved_root)
            previews.append({
                "filename": resolved.name,
                "subfolder": "" if relative == Path(".") else str(relative),
                "type": "output",
            })
            shown.append(resolved.relative_to(resolved_root).as_posix())
        if not preview:
            previews = []   # 纯显示开关：不显示预览，文件照存
        return {
            "ui": {"images": previews, "sg_saved_path": shown[:1]},
            "result": (str(saved[0]) if saved else "", images),
        }

    @classmethod
    def IS_CHANGED(cls, **_values):
        return float("nan")   # 保存节点永远重跑

    @classmethod
    def VALIDATE_INPUTS(cls, filename_prefix="", output_dir=""):
        try:
            if isinstance(filename_prefix, str) and filename_prefix.strip():
                sanitize_rel(strip_image_extension(filename_prefix), "文件名前缀")
            if isinstance(output_dir, str) and output_dir.strip() and folder_paths is not None:
                resolve_output_root(output_dir, Path(folder_paths.get_output_directory()))
        except ValueError as exc:
            return str(exc)
        return True


_register_folder_route()

NODE_CLASS_MAPPINGS = {"ShangguSaveImage": ShangguSaveImage}
NODE_DISPLAY_NAME_MAPPINGS = {"ShangguSaveImage": "保存图像 (Shanggu)"}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
