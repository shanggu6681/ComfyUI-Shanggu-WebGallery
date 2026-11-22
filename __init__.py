# -*- coding: utf-8 -*-
"""ComfyUI-Shanggu-WebGallery —— 网页图库选图器。

在 ComfyUI 节点里输入任意网页 / JSON API 地址 -> 滚动浏览缩略图 ->
点选 -> 等价于「加载图像」输出 IMAGE/MASK -> 接图生图工作流。
单图运行 1 次；多图按点选顺序，每张图各完整运行 1 次工作流。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from sg_nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS  # noqa: E402
import sg_outpaint  # noqa: E402
NODE_CLASS_MAPPINGS.update(sg_outpaint.NODE_CLASS_MAPPINGS)  # noqa: E402
NODE_DISPLAY_NAME_MAPPINGS.update(sg_outpaint.NODE_DISPLAY_NAME_MAPPINGS)  # noqa: E402
import sg_charref  # noqa: E402
NODE_CLASS_MAPPINGS.update(sg_charref.NODE_CLASS_MAPPINGS)  # noqa: E402
NODE_DISPLAY_NAME_MAPPINGS.update(sg_charref.NODE_DISPLAY_NAME_MAPPINGS)  # noqa: E402
import sg_server  # noqa: E402,F401  (导入即注册 /sg_gallery/* 路由)

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

print("[ShangguWebGallery] 节点已加载: 网页图库选图器 / 图库信息提取 / 图像扩图→Latent / 图像重绘准备→Latent / 角色参考整图重绘")
