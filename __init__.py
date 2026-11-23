# -*- coding: utf-8 -*-
"""ComfyUI-Shanggu-WebGallery —— 三个节点：网页图库选图器 + Qwen 扩图草图 + RelayVSR 超分。

选图器：输入任意网页 / JSON API 地址 -> 滚动浏览缩略图 -> 点选 -> 等价于 LoadImage。
草图节点：9:16 竖图 -> 16:9 低像素草图（Qwen2.1 outpaint），再交给 MOSS 重绘。
超分节点：RelayVSR 2x/4x 一键放大，模型/VAE 界面可见。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from sg_nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS  # noqa: E402
import sg_qwen_outpaint  # noqa: E402
NODE_CLASS_MAPPINGS.update(sg_qwen_outpaint.NODE_CLASS_MAPPINGS)  # noqa: E402
NODE_DISPLAY_NAME_MAPPINGS.update(sg_qwen_outpaint.NODE_DISPLAY_NAME_MAPPINGS)  # noqa: E402
import sg_relay_vsr  # noqa: E402
NODE_CLASS_MAPPINGS.update(sg_relay_vsr.NODE_CLASS_MAPPINGS)  # noqa: E402
NODE_DISPLAY_NAME_MAPPINGS.update(sg_relay_vsr.NODE_DISPLAY_NAME_MAPPINGS)  # noqa: E402
import sg_server  # noqa: E402,F401  (导入即注册 /sg_gallery/* 路由)

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

print("[ShangguWebGallery] 节点已加载: 网页图库选图器 / Qwen扩图草图 / RelayVSR超分")
