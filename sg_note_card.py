# -*- coding: utf-8 -*-
"""Shanggu 说明卡片 —— AusBoss 风格的工作流说明卡（纯前端节点）。

画布上一张说明卡：大标题、正文（自动换行）、可选的参考链接、以及一个
MODELS 区块列出工作流用到的模型文件，并实时检查本机是否已安装
（✅ installed / ❌ 缺少）。内容存节点 properties，随工作流保存。
纯 UI 节点，不参与执行，无输入无输出。
"""
from __future__ import annotations


class ShangguAusBossNote:
    """AusBoss 风格说明卡片：标题 + 正文 + 链接 + 模型清单（带安装状态）。"""

    CATEGORY = "Shanggu/Workflow"
    FUNCTION = "noop"
    OUTPUT_NODE = False
    RETURN_TYPES = ()

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    def noop(self):
        return ()

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    DESCRIPTION = ("AusBoss 风格的说明卡片：写清这个工作流怎么用，列出需要哪些模型"
                   "文件并自动检查本机是否已安装。纯 UI 节点，不参与执行。")


class ShangguTextCard:
    """纯文字小卡片：没有标题，只有一块文字区域，双击编辑，随工作流保存。"""

    CATEGORY = "Shanggu/Workflow"
    FUNCTION = "noop"
    OUTPUT_NODE = False
    RETURN_TYPES = ()

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    def noop(self):
        return ()

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    DESCRIPTION = ("纯文字小卡片：没有任何标题和按钮，就是一块文字区域，"
                   "用来在画布上随手记备注。双击编辑。纯 UI 节点，不参与执行。")


NODE_CLASS_MAPPINGS = {
    "ShangguAusBossNote": ShangguAusBossNote,
    "ShangguTextCard": ShangguTextCard,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ShangguAusBossNote": "说明卡片 (Shanggu)",
    "ShangguTextCard": "纯文字卡片 (Shanggu)",
}
