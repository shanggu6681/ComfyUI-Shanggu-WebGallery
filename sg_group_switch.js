// Shanggu 组开关 —— Fast Groups Bypasser 的互斥版。
// 四种节点：二选一 / 三选一 / 多组二选一 / 综合开关。
// 点开关按钮 = 把组内节点 mode 设为 0（激活）或 4（bypass），互斥组自动关掉
// 同一开关里的其他组。配置存在 node.properties.switches，随工作流保存。
import { app } from "../../scripts/app.js";

const MODE_ACTIVE = 0;
const MODE_BYPASS = 4;
const TYPE_LABEL = { pair: "二选一", trio: "三选一", solo: "独立开关" };
const TYPE_SLOTS = { pair: 2, trio: 3, solo: 1 };

// ---------------------------------------------------------------- 基础工具

function groupRect(g) {
  const p = Array.isArray(g.pos) ? g.pos : [g.pos?.x || 0, g.pos?.y || 0];
  const s = Array.isArray(g.size) ? g.size : [g.size?.x || 0, g.size?.y || 0];
  return [p[0], p[1], s[0], s[1]];
}

function allGroupTitles() {
  return (app.graph?.groups || [])
    .map((g) => g.title).filter((t) => t && String(t).trim());
}

function nodesInGroup(title) {
  const found = [];
  if (!title) return found;
  for (const g of app.graph?.groups || []) {
    if (g.title !== title) continue;
    const [gx, gy, gw, gh] = groupRect(g);
    for (const n of app.graph?._nodes || []) {
      const nx = n.pos?.[0] ?? 0, ny = n.pos?.[1] ?? 0;
      if (nx >= gx && ny >= gy && nx <= gx + gw && ny <= gy + gh) found.push(n);
    }
  }
  return found;
}

function applyState(sw, state) {
  sw.state = state;
  sw.groups.forEach((t, i) => {
    if (!t) return;
    const on = state !== -1 && i === state;
    for (const n of nodesInGroup(t)) n.mode = on ? MODE_ACTIVE : MODE_BYPASS;
  });
  app.canvas.setDirty(true);
  if (sw.node) sw.node.sgRender?.();
}

// ---------------------------------------------------------------- 样式

let _styleInjected = false;
function injectStyle() {
  if (_styleInjected) return;
  _styleInjected = true;
  const css = `
.sg-switch-root { padding: 4px 6px 6px; box-sizing: border-box; width: 100%; }
.sg-switch-row { background: rgba(0,0,0,.25); border: 1px solid rgba(255,255,255,.12);
  border-radius: 6px; padding: 5px 6px; margin-bottom: 5px; }
.sg-switch-head { display: flex; align-items: center; gap: 5px; margin-bottom: 4px; }
.sg-switch-chip { font-size: 10px; padding: 1px 6px; border-radius: 8px;
  background: #3a6ea5; color: #fff; white-space: nowrap; }
.sg-switch-label { font-size: 11px; color: #ccc; flex: 1; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.sg-switch-btn { cursor: pointer; user-select: none; }
.sg-btn-mini { font-size: 10px; line-height: 1; padding: 2px 4px; border-radius: 4px;
  border: 1px solid rgba(255,255,255,.25); color: #bbb; background: transparent; }
.sg-btn-mini:hover { color: #fff; background: rgba(255,255,255,.12); }
.sg-switch-body { display: flex; flex-wrap: wrap; gap: 4px; }
.sg-group-btn { font-size: 11px; padding: 3px 8px; border-radius: 5px;
  border: 1px solid rgba(255,255,255,.2); background: rgba(255,255,255,.06);
  color: #aaa; max-width: 100%; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; text-align: center; }
.sg-group-btn.on { background: #2f9e44; border-color: #37b24d; color: #fff;
  font-weight: 600; box-shadow: 0 0 6px rgba(47,158,68,.5); }
.sg-group-btn.warn { border-color: #e8590c; color: #e8590c; }
.sg-group-btn:hover { filter: brightness(1.25); }
.sg-add-bar { display: flex; gap: 4px; flex-wrap: wrap; }
.sg-add-btn { font-size: 11px; padding: 3px 8px; border-radius: 5px; cursor: pointer;
  border: 1px dashed rgba(255,255,255,.3); background: transparent; color: #999; }
.sg-add-btn:hover { color: #fff; border-color: rgba(255,255,255,.6); }
.sg-dlg-mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); z-index: 99999;
  display: flex; align-items: center; justify-content: center; }
.sg-dlg { background: #1e1e1e; border: 1px solid #444; border-radius: 8px;
  padding: 14px 16px; min-width: 320px; color: #ddd; font-size: 12px; }
.sg-dlg h4 { margin: 0 0 10px; font-size: 13px; color: #fff; }
.sg-dlg-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.sg-dlg-row label { width: 72px; color: #aaa; }
.sg-dlg input, .sg-dlg select { flex: 1; background: #111; color: #eee;
  border: 1px solid #444; border-radius: 4px; padding: 4px 6px; font-size: 12px; }
.sg-dlg-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
.sg-dlg-foot button { padding: 4px 14px; border-radius: 5px; cursor: pointer;
  border: 1px solid #555; background: #2a2a2a; color: #ddd; }
.sg-dlg-foot button.primary { background: #2f6fab; border-color: #3778bc; color: #fff; }
.sg-type-pick { display: flex; gap: 6px; margin-bottom: 10px; }
.sg-type-pick button { flex: 1; padding: 5px 0; border-radius: 5px; cursor: pointer;
  border: 1px solid #555; background: #222; color: #ccc; font-size: 12px; }
.sg-type-pick button.sel { background: #3a6ea5; color: #fff; border-color: #4a84c0; }
`;
  document.head.appendChild(Object.assign(document.createElement("style"), { textContent: css }));
}

// ---------------------------------------------------------------- 配置弹窗

function openDialog(node, existing, onSave) {
  injectStyle();
  let type = existing?.type || "pair";
  const slots = () => TYPE_SLOTS[type];
  const groups = existing?.groups?.slice() || [];

  const mask = document.createElement("div");
  mask.className = "sg-dlg-mask";
  const dlg = document.createElement("div");
  dlg.className = "sg-dlg";
  mask.appendChild(dlg);

  const render = () => {
    dlg.innerHTML = "";
    const h = document.createElement("h4");
    h.textContent = existing ? "编辑开关" : "添加开关";
    dlg.appendChild(h);

    const pick = document.createElement("div");
    pick.className = "sg-type-pick";
    for (const [t, label] of Object.entries(TYPE_LABEL)) {
      const b = document.createElement("button");
      b.textContent = label + `（${TYPE_SLOTS[t]} 组）`;
      if (t === type) b.classList.add("sel");
      b.onclick = () => { type = t; render(); };
      pick.appendChild(b);
    }
    dlg.appendChild(pick);

    const dl = document.createElement("datalist");
    dl.id = "sg-group-titles";
    for (const t of allGroupTitles()) {
      const o = document.createElement("option");
      o.value = t; dl.appendChild(o);
    }
    dlg.appendChild(dl);

    while (groups.length < slots()) groups.push("");
    groups.length = slots();
    for (let i = 0; i < slots(); i++) {
      const row = document.createElement("div");
      row.className = "sg-dlg-row";
      const lab = document.createElement("label");
      lab.textContent = i === 0 && type === "solo" ? "组名" : `组 ${i + 1}`;
      const inp = document.createElement("input");
      inp.value = groups[i] || "";
      inp.setAttribute("list", "sg-group-titles");
      inp.placeholder = "画布里已有的组名（可手输）";
      inp.oninput = () => { groups[i] = inp.value; };
      row.appendChild(lab); row.appendChild(inp);
      dlg.appendChild(row);
    }

    const foot = document.createElement("div");
    foot.className = "sg-dlg-foot";
    const cancel = document.createElement("button");
    cancel.textContent = "取消";
    cancel.onclick = () => mask.remove();
    const ok = document.createElement("button");
    ok.className = "primary";
    ok.textContent = "确定";
    ok.onclick = () => {
      const g = groups.map((x) => (x || "").trim());
      if (g.some((x) => !x)) { alert("组名不能留空"); return; }
      mask.remove();
      onSave({ type, groups: g });
    };
    foot.appendChild(cancel); foot.appendChild(ok);
    dlg.appendChild(foot);
  };
  render();
  document.body.appendChild(mask);
}

// ---------------------------------------------------------------- 行渲染

function buildRow(node, sw) {
  const row = document.createElement("div");
  row.className = "sg-switch-row";

  const head = document.createElement("div");
  head.className = "sg-switch-head";
  const chip = document.createElement("span");
  chip.className = "sg-switch-chip";
  chip.textContent = TYPE_LABEL[sw.type] || sw.type;
  const label = document.createElement("span");
  label.className = "sg-switch-label";
  label.textContent = sw.groups.filter(Boolean).join(" ⇄ ") || "未绑定组（点 ✎ 配置）";
  const edit = document.createElement("span");
  edit.className = "sg-switch-btn sg-btn-mini";
  edit.textContent = "✎";
  edit.title = "编辑这个开关绑定的组";
  edit.onclick = () => openDialog(node, sw, (cfg) => {
    Object.assign(sw, cfg);
    sw.state = cfg.type === "solo" ? -1 : 0;
    node.sgRender?.();
    if (nodesInGroup(cfg.groups[0]).length) applyState(sw, sw.state);
  });
  head.appendChild(chip); head.appendChild(label); head.appendChild(edit);

  if (node.sgAddable) {
    const del = document.createElement("span");
    del.className = "sg-switch-btn sg-btn-mini";
    del.textContent = "✕";
    del.title = "删除这个开关（组内节点状态不变）";
    del.onclick = () => {
      const list = node.properties.switches;
      list.splice(list.indexOf(sw), 1);
      node.sgRender?.();
    };
    head.appendChild(del);
  }
  row.appendChild(head);

  const body = document.createElement("div");
  body.className = "sg-switch-body";
  sw.groups.forEach((t, i) => {
    if (sw.type === "solo" && i > 0) return;
    const b = document.createElement("span");
    b.className = "sg-switch-btn sg-group-btn";
    b.textContent = t || "— 未指定组 —";
    const empty = !nodesInGroup(t).length;
    if (empty) { b.classList.add("warn"); b.title = "画布里找不到这个组（组被删了或改名了？）"; }
    const on = sw.state !== -1 && sw.state === i;
    if (on) b.classList.add("on");
    b.onclick = () => {
      const next = (sw.type === "solo")
        ? (sw.state === 0 ? -1 : 0)
        : (sw.state === i ? -1 : i);   // 点亮着的 = 全关；点灭的 = 换它开
      applyState(sw, next);
    };
    body.appendChild(b);
  });
  row.appendChild(body);
  return row;
}

function buildAddBar(node, allowed) {
  const bar = document.createElement("div");
  bar.className = "sg-add-bar";
  for (const t of allowed) {
    const b = document.createElement("button");
    b.className = "sg-add-btn";
    b.textContent = `＋${TYPE_LABEL[t]}`;
    b.onclick = () => openDialog(node, { type: t }, (cfg) => {
      const sw = { ...cfg, state: cfg.type === "solo" ? -1 : 0, node };
      node.properties.switches.push(sw);
      node.sgRender?.();
      if (nodesInGroup(cfg.groups[0]).length) applyState(sw, sw.state);
    });
    bar.appendChild(b);
  }
  return bar;
}

// ---------------------------------------------------------------- 节点接入

function renderAll(node, root) {
  root.innerHTML = "";
  for (const sw of node.properties.switches || []) {
    sw.node = node;
    root.appendChild(buildRow(node, sw));
  }
  if (node.sgAddable) root.appendChild(buildAddBar(node, node.sgAddable));
  node.sgSizeDirty = true;
}

function firstGroups(n) {
  const t = allGroupTitles();
  return [t[0] || "", t[1] || "", t[2] || ""].slice(0, n);
}

const VARIANTS = {
  ShangguGroupSwitchPair: {
    addable: null,
    initial: () => [{ type: "pair", groups: firstGroups(2), state: 0 }],
  },
  ShangguGroupSwitchTrio: {
    addable: null,
    initial: () => [{ type: "trio", groups: firstGroups(3), state: 0 }],
  },
  ShangguGroupSwitchMulti: {
    addable: ["pair", "solo"],
    initial: () => [],
  },
  ShangguGroupSwitchMixer: {
    addable: ["pair", "trio", "solo"],
    initial: () => [],
  },
};

app.registerExtension({
  name: "Shanggu.GroupSwitch",
  beforeRegisterNodeDef(nodeType, nodeDef) {
    const v = VARIANTS[nodeDef.name];
    if (!v) return;
    const origOnNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = origOnNodeCreated?.call(this);
      this.properties.switches = v.initial();
      this.sgAddable = v.addable;
      this.sgRender = () => renderAll(this, this.sgRoot);
      const root = document.createElement("div");
      root.className = "sg-switch-root";
      this.sgRoot = root;
      const w = this.addDOMWidget("组开关", "sg_switch", root);
      w.serializeValue = () => "";
      this.sgRender();
      // 新节点带默认绑定：组真实存在时立即应用一次，让画布状态和开关一致
      for (const sw of this.properties.switches) {
        if (sw.groups[0] && nodesInGroup(sw.groups[0]).length) applyState(sw, sw.state);
      }
      const origOnCfg = this.onConfigure;
      this.onConfigure = function (...a) {
        const rr = origOnCfg?.apply(this, a);
        setTimeout(() => this.sgRender?.(), 0);
        return rr;
      };
      return r;
    };
  },
});
