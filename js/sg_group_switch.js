// Shanggu 组开关（互斥版 Fast Groups Bypasser）
// 架构（v3，画布自绘）：每行开关是一个 litegraph 自定义 widget（draw + mouse +
// computeSize）。布局、命中检测、节点自动撑高全部由 litegraph 引擎自己完成
// （新版前端 _arrangeWidgets 会按 computeSize()[1]+4 累加并自动 setSize），
// 不再使用 DOM widget —— 从根上消灭「灰底和内容对不上」这一类问题。
// 开关配置存在 properties.switches（纯数据，不含节点引用，无循环序列化问题）。
import { app } from "../../scripts/app.js";

const MODE_ON = 0;      // LiteGraph ALWAYS
const MODE_BYPASS = 4;  // ComfyUI bypass（Ctrl+B 同款）
const TYPE_LABEL = { solo: "独立开关", pair: "二选一", trio: "三选一" };
const TYPE_SLOTS = { solo: 1, pair: 2, trio: 3 };
const BTN_H = 24;
const HEAD_H = 14;
const MX = 10;          // 卡片内边距（同保存图像卡片 padding 8~9px 的观感）

/* 风格与保存图像卡片（sg_save_image）一致 */
const BRAND = "#0ea5a4";
const BRAND_TEXT = "#04201d";
const BORDER = "#3a4448";
const OFF_TEXT = "#8ba3a1";
const CAP_TEXT = "#617574";
const FONT = `-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif`;
const CARD_BG = "rgba(0,0,0,0.28)";            // 同 .sg-save 卡片底
const CARD_BORDER = "rgba(14,165,164,0.22)";   // 同 .sg-save 青绿描边

const VARIANTS = {
  ShangguGroupSwitchPair: () => [{ type: "pair", groups: ["", ""], state: -1 }],
  ShangguGroupSwitchTrio: () => [{ type: "trio", groups: ["", "", ""], state: -1 }],
  ShangguGroupSwitchMulti: () => [
    { type: "pair", groups: ["", ""], state: -1 },
    { type: "pair", groups: ["", ""], state: -1 },
  ],
  ShangguGroupSwitchMixer: () => [
    { type: "solo", groups: [""], state: -1 },
    { type: "pair", groups: ["", ""], state: -1 },
    { type: "trio", groups: ["", "", ""], state: -1 },
  ],
};

/* ------------------------------------------------------------ 组成员查找 */
function graphGroups(node) {
  return node?.graph?.groups || node?.graph?._groups || [];
}

function nodesInGroup(name, node) {
  if (!name) return [];
  const g = graphGroups(node).find((x) => x.title === name);
  if (!g) return [];
  if (Array.isArray(g._nodes) && g._nodes.length) return g._nodes.slice();
  // 兜底：节点中心点落在组框内
  let b = null;
  try { b = Array.from(g.bounds ?? g._bounding ?? []); } catch (e) { b = g._bounding; }
  if (!b || b.length < 4) return [];
  const out = [];
  for (const n of node.graph._nodes || []) {
    const cx = n.pos[0] + (n.size?.[0] || 0) / 2;
    const cy = n.pos[1] + (n.size?.[1] || 0) / 2;
    if (cx >= b[0] && cx <= b[0] + b[2] && cy >= b[1] && cy <= b[1] + b[3]) out.push(n);
  }
  return out;
}

/* 成员缓存：500ms 失效，避免每帧全图扫描 */
const memberCache = new Map();
function cachedMembers(name, node) {
  const now = Date.now();
  const c = memberCache.get(name);
  if (c && now - c.t < 500) return c.list;
  const list = nodesInGroup(name, node);
  memberCache.set(name, { t: now, list });
  return list;
}

/* ------------------------------------------------------------ bypass 应用 */
function applyState(sw, node) {
  sw.groups.forEach((name, i) => {
    if (!name) return;
    const ns = cachedMembers(name, node);
    const on = sw.state === i;
    for (const n of ns) n.mode = on ? MODE_ON : MODE_BYPASS;
    console.log(`[Shanggu 组开关] ${name} -> ${on ? "激活" : "bypass"}（${ns.length} 个节点）`);
  });
  node.graph?.setDirtyCanvas?.(true, true);
  node.setDirtyCanvas?.(true, true);
}

function applyAll(node) {
  for (const sw of node.properties?.switches || []) applyState(sw, node);
}

/* ------------------------------------------------------------ 行内布局 */
/* 行高：首行带区头。是否首行在 rebuildWidgets 时一次算好存到 widget 上
   （sgIsFirst），不依赖运行时对象身份比对 —— properties.switches 的元素
   引用可能被前端包装层/响应式代理换掉，find(...)===sw 会随机失效。 */
function rowHeight(sw, node, isFirst) {
  return (isFirst ? HEAD_H : 0) + BTN_H + 4;
}

function buttonRects(sw, node, width, baseY, isFirst) {
  const n = TYPE_SLOTS[sw.type];
  const innerW = width - MX * 2;
  const bw = innerW / n;                       // 分段式：连体无间隙（同保存图像格式药丸）
  const top = baseY + 2 + (isFirst ? HEAD_H : 0);
  const rects = [];
  for (let i = 0; i < n; i++) rects.push([MX + i * bw, top, bw, BTN_H]);
  return rects;
}

/* ------------------------------------------------------------ 绘制 */
/* 分段药丸：整条一个圆角外框，选中的段填品牌色，段间细分隔线 */
function drawSegment(ctx, node, rect, sw) {
  const [x0, y, w, h] = rect;
  const n = sw.groups.length;
  const bw = w / n;
  const missing = sw.groups.some((g) => !g || cachedMembers(g, node).length === 0);
  ctx.save();
  // 外框
  ctx.beginPath();
  ctx.roundRect(x0, y, w, h, 6);
  ctx.fillStyle = missing ? "rgba(160,95,20,0.14)" : "rgba(255,255,255,0.03)";
  ctx.fill();
  // 选中段
  if (sw.state >= 0 && sw.state < n) {
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(x0, y, w, h, 6);
    ctx.clip();
    const sx = x0 + sw.state * bw;
    if (missing) {
      ctx.fillStyle = "#c2711d";
    } else {
      ctx.fillStyle = BRAND;
    }
    ctx.fillRect(sx, y, bw, h);
    ctx.restore();
  }
  // 分隔线
  ctx.strokeStyle = missing ? "#c2711d" : BORDER;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 1; i < n; i++) {
    const sx = Math.round(x0 + i * bw) + 0.5;
    ctx.moveTo(sx, y + 3);
    ctx.lineTo(sx, y + h - 3);
  }
  ctx.stroke();
  // 外描边（组名缺失时橙色虚线警示）
  ctx.beginPath();
  ctx.roundRect(x0, y, w, h, 6);
  if (missing) {
    ctx.setLineDash([4, 3]);
    ctx.strokeStyle = "#c2711d";
  } else {
    ctx.strokeStyle = BORDER;
  }
  ctx.stroke();
  ctx.setLineDash([]);
  // 各段文字
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (let i = 0; i < n; i++) {
    const on = sw.state === i;
    ctx.font = `600 11px ${FONT}`;
    let color = on ? (missing ? "#ffe9c9" : BRAND_TEXT) : missing ? "#f5b04c" : OFF_TEXT;
    ctx.fillStyle = color;
    let label = sw.groups[i] || "＋ 未指定组";
    const maxW = bw - 12;
    const isPlaceholder = label === "＋ 未指定组";
    while (ctx.measureText(label).width > maxW && label.length > 1) label = label.slice(0, -1);
    if (label !== sw.groups[i] && !isPlaceholder) label += "…";
    ctx.fillText(label, x0 + i * bw + bw / 2, y + h / 2 + 1);
  }
  ctx.restore();
}

function drawSwitchRow(ctx, node, width, baseY, sw, isFirst) {
  if (isFirst) {
    ctx.save();
    ctx.font = `600 9.5px ${FONT}`;
    ctx.fillStyle = CAP_TEXT;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(TYPE_LABEL[sw.type] || sw.type, MX + 2, baseY + 2 + 7);
    ctx.restore();
  }
  // 整行一个分段药丸（连体样式，同保存图像的格式选择）
  const [x, y] = [MX, baseY + 2 + (isFirst ? HEAD_H : 0)];
  drawSegment(ctx, node, [x, y, width - MX * 2, BTN_H], sw);
}

function drawGearRow(ctx, baseY) {
  ctx.save();
  ctx.font = `600 10px ${FONT}`;
  ctx.fillStyle = CAP_TEXT;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText("⚙ 编辑开关（双击节点也可）", MX + 2, baseY + 10);
  ctx.restore();
}

/* ------------------------------------------------------------ widget 构造 */
function makeSwitchWidget(sw, isFirst) {
  const w = {
    name: "sg_switch",
    type: "custom",
    sgIsShanggu: true,
    sw,
    sgIsFirst: !!isFirst,
    y: 0,
    options: { serialize: false },
    serializeValue() { return ""; },
    computeSize() { return [0, rowHeight(this.sw, this.node, this.sgIsFirst)]; },
    draw(ctx, node, width, y) { drawSwitchRow(ctx, node, width, y, this.sw, this.sgIsFirst); },
    mouse(e, pos, node) {
      if (e.type !== "pointerdown" && e.type !== "mousedown") return false;
      const sw = this.sw;
      const relY = pos[1] - (this.last_y ?? 0);
      // 命中判定用行内坐标（baseY=0），与 relY 同一坐标系
      const rects = buttonRects(sw, node, node.size[0], 0, this.sgIsFirst);
      const n = TYPE_SLOTS[sw.type];
      for (let i = 0; i < n; i++) {
        const [x, y, w, h] = rects[i];
        if (pos[0] >= x && pos[0] <= x + w && relY >= y && relY <= y + h) {
          sw.state = sw.state === i ? -1 : i;   // 点亮的=全关；点灭的=换它开
          applyState(sw, node);
          return true;
        }
      }
      return false;
    },
  };
  return w;
}

function makeGearWidget() {
  return {
    name: "sg_switch_gear",
    type: "custom",
    sgIsShanggu: true,
    y: 0,
    options: { serialize: false },
    serializeValue() { return ""; },
    computeSize() { return [0, 20]; },
    draw(ctx, node, width, y) { drawGearRow(ctx, y); },
    mouse(e, pos, node) {
      if (e.type !== "pointerdown" && e.type !== "mousedown") return false;
      const relY = pos[1] - (this.last_y ?? 0);
      if (relY >= 0 && relY <= 20 && pos[0] >= 0 && pos[0] <= node.size[0]) {
        openEditor(node);
        return true;
      }
      return false;
    },
  };
}

function rebuildWidgets(node) {
  node.widgets = (node.widgets || []).filter((w) => !w.sgIsShanggu);
  const list = node.properties?.switches || [];
  // 每种类型的第一行带区头：重建时一次算好，不再依赖运行时身份比对
  const firstIdx = {};
  list.forEach((sw, i) => { if (!(sw.type in firstIdx)) firstIdx[sw.type] = i; });
  for (let i = 0; i < list.length; i++) {
    const w = makeSwitchWidget(list[i], i === firstIdx[list[i].type]);
    w.node = node;               // widget 运行时引用（不参与序列化）
    node.widgets.push(w);
  }
  const gear = makeGearWidget();
  gear.node = node;
  node.widgets.push(gear);
  node.setDirtyCanvas?.(true, true);
}

/* 精确内容高度：排布起点 2 + Σ(每行 computeSize + 4)（引擎每行分配 +4 间距） */
function contentHeight(node) {
  let h = 2;
  for (const w of node.widgets || []) {
    if (w.sgIsShanggu && w.computeSize) h += w.computeSize()[1] + 4;
  }
  return h;
}

function fitNodeHeight(node) {
  try {
    if (node.size) node.size[1] = contentHeight(node);
    node.setDirtyCanvas?.(true, true);
  } catch (e) { /* 忽略 */ }
}

/* ------------------------------------------------------------ 编辑弹窗（浮层 DOM，不参与节点布局） */
function openEditor(node) {
  document.getElementById("sg-sw-editor")?.remove();
  const list = node.properties.switches;

  const wrap = document.createElement("div");
  wrap.id = "sg-sw-editor";
  wrap.style.cssText = [
    "position:fixed", "inset:0", "z-index:99999",
    "background:rgba(0,0,0,0.45)", "display:flex",
    "align-items:center", "justify-content:center",
  ].join(";");

  const card = document.createElement("div");
  card.style.cssText = [
    "background:#1e1f24", "color:#ddd", "border:1px solid #3a3b42",
    "border-radius:10px", "padding:14px", "width:440px", "max-height:70vh",
    "overflow:auto", "box-shadow:0 8px 30px rgba(0,0,0,0.5)",
    "font:12px/1.6 sans-serif",
  ].join(";");

  const title = document.createElement("div");
  title.textContent = "⚙ 编辑开关";
  title.style.cssText = "font-weight:700;font-size:14px;margin-bottom:10px;color:#fff";
  card.appendChild(title);

  const rowsBox = document.createElement("div");
  card.appendChild(rowsBox);

  function groupNames() {
    return graphGroups(node).map((g) => g.title).filter(Boolean);
  }

  function refresh() {
    rowsBox.innerHTML = "";
    list.forEach((sw, si) => {
      const row = document.createElement("div");
      row.style.cssText = "display:flex;gap:6px;align-items:center;margin-bottom:8px";
      const typeSel = document.createElement("select");
      for (const t of Object.keys(TYPE_LABEL)) {
        const o = document.createElement("option");
        o.value = t; o.textContent = TYPE_LABEL[t];
        if (sw.type === t) o.selected = true;
        typeSel.appendChild(o);
      }
      typeSel.style.cssText = "background:#2a2b31;color:#ddd;border:1px solid #444;border-radius:6px;padding:3px";
      typeSel.onchange = () => {
        sw.type = typeSel.value;
        sw.groups = Array.from({ length: TYPE_SLOTS[sw.type] }, (_, i) => sw.groups[i] || "");
        if (TYPE_SLOTS[sw.type] === 1 && sw.state > 0) sw.state = -1;
        sync();
      };
      row.appendChild(typeSel);

      const dl = document.createElement("datalist");
      dl.id = "sg-sw-groups-" + si;
      for (const n of groupNames()) {
        const o = document.createElement("option");
        o.value = n;
        dl.appendChild(o);
      }
      row.appendChild(dl);

      sw.groups.forEach((g, gi) => {
        const inp = document.createElement("input");
        inp.value = g;
        inp.setAttribute("list", dl.id);
        inp.placeholder = "组名";
        inp.style.cssText = "flex:1;min-width:0;background:#2a2b31;color:#ddd;border:1px solid #444;border-radius:6px;padding:4px 6px";
        inp.oninput = () => { sw.groups[gi] = inp.value; syncSoft(); };
        row.appendChild(inp);
      });

      const del = document.createElement("button");
      del.textContent = "✕";
      del.title = "删除这个开关";
      del.style.cssText = "background:#3a2530;color:#e77;border:1px solid #553;border-radius:6px;padding:3px 8px;cursor:pointer";
      del.onclick = () => { list.splice(si, 1); sync(); };
      row.appendChild(del);
      rowsBox.appendChild(row);
    });
  }

  // 改组名等轻量操作不整表重绘（避免打字时输入框失焦），只重画节点
  function syncSoft() {
    node.setDirtyCanvas?.(true, true);
  }
  function sync() {
    rebuildWidgets(node);
    fitNodeHeight(node);
    refresh();
  }

  const addRow = document.createElement("div");
  addRow.style.cssText = "display:flex;gap:6px;margin-top:6px";
  const addBtn = (label, type) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.style.cssText = "flex:1;background:#2f3350;color:#ccd;border:1px solid #4a4f6a;border-radius:6px;padding:5px;cursor:pointer";
    b.onclick = () => {
      list.push({ type, groups: Array.from({ length: TYPE_SLOTS[type] }, () => ""), state: -1 });
      sync();
    };
    return b;
  };
  addRow.append(addBtn("＋独立开关", "solo"), addBtn("＋二选一", "pair"), addBtn("＋三选一", "trio"));
  card.appendChild(addRow);

  const doneRow = document.createElement("div");
  doneRow.style.cssText = "display:flex;justify-content:flex-end;margin-top:10px";
  const done = document.createElement("button");
  done.textContent = "完成";
  done.style.cssText = "background:#0ea5a4;color:#04201d;font-weight:600;border:none;border-radius:6px;padding:6px 18px;cursor:pointer";
  done.onclick = () => { wrap.remove(); applyAll(node); };
  doneRow.appendChild(done);
  card.appendChild(doneRow);

  wrap.appendChild(card);
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
  document.body.appendChild(wrap);
  refresh();
}

/* ------------------------------------------------------------ 注册 */
app.registerExtension({
  name: "Shanggu.GroupSwitch",
  async beforeRegisterNodeDef(nodeType, nodeDef) {
    const variant = VARIANTS[nodeDef.name];
    if (!variant) return;

    // 覆盖引擎的 node.computeSize：通用公式对无插槽节点也强算 1 行插槽高
    // （20px）+ 尾部 8px，拖拽缩小时最小高度 = 内容+48px，底部永远留大条
    // 空白。这里返回精确内容高度，下边缘可以一直缩到最后一行按钮底部。
    nodeType.prototype.computeSize = function () {
      return [this.size?.[0] || 240, contentHeight(this)];
    };

    // 卡片底：画在 widget 之下（引擎先调 onDrawForeground 再画 widgets），
    // 与保存图像卡片的深色底 + 青绿描边一致
    const origFG = nodeType.prototype.onDrawForeground;
    nodeType.prototype.onDrawForeground = function (ctx) {
      const r = origFG?.call(this, ctx);
      if (this.flags?.collapsed) return r;
      const w = this.size?.[0] || 0, h = this.size?.[1] || 0;
      if (w < 24 || h < 12) return r;
      ctx.save();
      ctx.beginPath();
      ctx.roundRect(3, 1, w - 6, h - 5, 8);
      ctx.fillStyle = CARD_BG;
      ctx.fill();
      ctx.strokeStyle = CARD_BORDER;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.restore();
      return r;
    };

    const origCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function (...args) {
      const r = origCreated?.apply(this, args);
      this.properties = this.properties || {};
      if (!Array.isArray(this.properties.switches) || !this.properties.switches.length) {
        this.properties.switches = variant();
      }
      if ((this.size?.[0] || 0) < 240) this.size = [240, this.size?.[1] || 120];
      rebuildWidgets(this);
      fitNodeHeight(this);
      return r;
    };

    const origConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (...args) {
      const r = origConfigure?.apply(this, args);
      // 清理旧版本误存的节点引用（循环引用曾导致工作流保存失败）
      for (const sw of this.properties?.switches || []) delete sw.node;
      rebuildWidgets(this);
      fitNodeHeight(this);
      // 组在图配置阶段才完全就绪，延迟重放保存的开关状态
      setTimeout(() => applyAll(this), 300);
      return r;
    };

    const origMenu = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
      const r = origMenu?.call(this, canvas, options);
      options.unshift({
        content: "⚙ 编辑开关",
        callback: () => openEditor(this),
      });
      return r;
    };

    nodeType.prototype.onDblClick = function () {
      openEditor(this);
      return true;
    };
  },
});
