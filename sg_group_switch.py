# -*- coding: utf-8 -*-
"""Shanggu 组开关 —— Fast Groups Bypasser 的互斥版（纯前端节点）。

四种样式，全部不参与执行（无输入无输出），只在工作流画布上控制「组」的
bypass 状态。开关配置存在节点 properties 里，跟着工作流一起保存：

- 二选一组开关：两个组互斥，开一个自动 bypass 另一个；
- 三选一组开关：三个组互斥；
- 多组二选一开关：一个节点里放多个二选一（可继续添加）；
- 综合组开关：任意组合，可添加二选一 / 三选一 / 独立开关。

原理：把组内节点的 mode 设为 4（bypass，ComfyUI 的 NEVER）或 0（always）。
队列执行时前端按当前 mode 序列化 prompt，被 bypass 的组自动不跑。
"""
from __future__ import annotations


class _ShangguGroupSwitchBase:
    """组开关基类：UI 节点，FUNCTION 只是占位，永远不会被执行。"""

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
        # 永不重跑：没有输出，ComfyUI 不会执行它；返回 nan 双保险
        return float("nan")


class ShangguGroupSwitchPair(_ShangguGroupSwitchBase):
    """二选一组开关：一组开、另一组自动关。"""

    DESCRIPTION = ("二选一组开关：绑两个组，点哪个哪个开，另一个自动 bypass。"
                   "配置存在节点上，随工作流保存。纯 UI 节点，不参与执行。")


class ShangguGroupSwitchTrio(_ShangguGroupSwitchBase):
    """三选一组开关：三组互斥。"""

    DESCRIPTION = ("三选一组开关：绑三个组，同时只开一个，其余自动 bypass。"
                   "配置存在节点上，随工作流保存。纯 UI 节点，不参与执行。")


class ShangguGroupSwitchMulti(_ShangguGroupSwitchBase):
    """多组二选一开关：一个节点多个二选一，还能继续添加。"""

    DESCRIPTION = ("多组二选一开关：一个节点里放多行二选一，底部「添加开关」可继续加"
                   "二选一 / 独立开关。纯 UI 节点，不参与执行。")


class ShangguGroupSwitchMixer(_ShangguGroupSwitchBase):
    """综合组开关：二选一 / 三选一 / 独立开关随意组合。"""

    DESCRIPTION = ("综合组开关：底部「添加开关」可任意添加二选一 / 三选一 / 独立开关，"
                   "随意组合。纯 UI 节点，不参与执行。")


NODE_CLASS_MAPPINGS = {
    "ShangguGroupSwitchPair": ShangguGroupSwitchPair,
    "ShangguGroupSwitchTrio": ShangguGroupSwitchTrio,
    "ShangguGroupSwitchMulti": ShangguGroupSwitchMulti,
    "ShangguGroupSwitchMixer": ShangguGroupSwitchMixer,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ShangguGroupSwitchPair": "二选一组开关 (Shanggu)",
    "ShangguGroupSwitchTrio": "三选一组开关 (Shanggu)",
    "ShangguGroupSwitchMulti": "多组二选一开关 (Shanggu)",
    "ShangguGroupSwitchMixer": "综合组开关 (Shanggu)",
}
