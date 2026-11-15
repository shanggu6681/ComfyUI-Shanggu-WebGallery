# -*- coding: utf-8 -*-
"""ComfyUI-Shanggu-WebGallery —— 网页图库选图器。

在 ComfyUI 节点里输入任意网页 / JSON API 地址 -> 滚动浏览缩略图 ->
点选（可多选） -> 等价于「加载图像」输出 IMAGE/MASK -> 接图生图工作流。
多图时可选「批量一次执行」或「逐张完整执行」。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from sg_nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS  # noqa: E402
import sg_server  # noqa: E402,F401  (导入即注册 /sg_gallery/* 路由)

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

print("[ShangguWebGallery] 节点已加载: 网页图库选图器 / 图库信息提取")
