// Shanggu 说明卡片 —— AusBoss 风格工作流说明卡（画布自绘，v1）
// 卡片内容：大标题 + 正文（自动换行）+ 可选链接 + MODELS 清单（实时检查
// 本机 models 目录里是否已安装，✅ installed / ❌ 缺少）。
// 内容存 properties（纯数据），编辑走右上角铅笔 / 双击节点 / 右键菜单。
import { app } from "../../scripts/app.js";

/* ---------- 风格常量（与保存图像 / 组开关 / LoRA 加载器一致） ---------- */
const BRAND = "#0ea5a4";
const BRAND_TEXT = "#04201d";
const BORDER = "#3a4448";
const OFF_TEXT = "#8ba3a1";
const CAP_TEXT = "#617574";
const MISSING = "#c2711d";
const TITLE_COLOR = "#eef7f5";
const BODY_COLOR = "#a9bcba";
const FONT = `-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif`;
const CARD_BG = "rgba(0,0,0,0.28)";
const CARD_BORDER = "rgba(14,165,164,0.22)";

/* ---------- 布局常量 ---------- */
const MX = 12;              // 卡片左右内边距
const TITLE_H = 26;
const BYLINE_H = 16;
const BODY_LH = 15;         // 正 文行高
const LINK_H = 20;
const SEC_H = 24;           // MODELS 区头
const MODEL_H = 22;         // 每个模型行
const PAD_BOT = 10;

/* 文本测量用离屏 context（computeSize 里没有 ctx） */
const measureCtx = document.createElement("canvas").getContext("2d");

function bodyFont(ctx) { ctx.font = `400 11px ${FONT}`; }
function wrapLines(text, maxW) {
  const out = [];
  for (const raw of String(text || "").split("\n")) {
    if (!raw) { out.push(""); continue; }
    let line = "";
    for (const word of raw.split(/(\s+)/)) {
      const cand = line + word;
      if (measureCtx.measureText(cand).width > maxW && line) {
        out.push(line);
        line = word.trimStart();
      } else {
        line = cand;
      }
    }
    out.push(line);
  }
  return out;
}

/* ---------- 模型检查 ---------- */
async function checkModels(node) {
  if (node._sgChecking) return;           // 请求进行中不重复发（每帧 draw 都会触发 ensureCheck）
  node._sgChecking = true;
  const names = node.properties?.models || [];
  if (!names.length) { node._sgCheck = { found: {}, missing: [], checked: true }; node._sgChecking = false; return; }
  try {
    const r = await fetch("/sg_gallery/check_models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names }),
    });
    const d = await r.json();
    node._sgCheck = d.ok
      ? { found: d.found || {}, missing: d.missing || [], checked: true }
      : { found: {}, missing: names.slice(), checked: false };
  } catch (e) {
    node._sgCheck = { found: {}, missing: names.slice(), checked: false };
  }
  node._sgChecking = false;
  node.setDirtyCanvas?.(true, true);
}

function ensureCheck(node) {
  if (node._sgCheck?.checked) return node._sgCheck;
  checkModels(node);
  return { found: {}, missing: node.properties?.models || [], checked: false };
}

/* ---------- 布局 ---------- */
function layout(node) {
  const p = node.properties || {};
  const W = node.size[0];
  const innerW = W - MX * 2 - 4;
  measureCtx.font = `400 11px ${FONT}`;
  const bodyLines = wrapLines(p.body, innerW);
  const models = p.models || [];
  const hasLink = !!(p.link && String(p.link).trim());
  let H = 8 + TITLE_H + 4;
  if (p.byline) H += BYLINE_H;
  H += bodyLines.length * BODY_LH;
  if (hasLink) H += LINK_H;
  if (models.length) H += SEC_H + models.length * MODEL_H;
  H += PAD_BOT;
  return { W, H, p, innerW, bodyLines, models, hasLink };
}

/* ---------- 绘制 ---------- */
function rr(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.roundRect(x, y, w, h, r); }

function ellipsize(ctx, text, maxW) {
  if (ctx.measureText(text).width <= maxW) return text;
  let s = text;
  while (s.length > 1 && ctx.measureText(s + "…").width > maxW) s = s.slice(0, -1);
  return s + "…";
}

function drawCard(ctx, node, width, baseY) {
  const L = layout(node);
  let y = baseY + 8;
  const p = L.p;

  /* 标题 + 铅笔 */
  ctx.save();
  ctx.font = `700 15px ${FONT}`;
  ctx.fillStyle = TITLE_COLOR;
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  ctx.fillText(ellipsize(ctx, p.title || "说明卡片", L.innerW - 26), MX, y + TITLE_H / 2);
  // 铅笔按钮（右上）
  const pen = penRect(L);
  ctx.font = `600 12px ${FONT}`;
  ctx.fillStyle = CAP_TEXT;
  ctx.textAlign = "center";
  ctx.fillText("✎", pen[0] + pen[2] / 2, baseY + 8 + TITLE_H / 2 + 1);
  y += TITLE_H + 4;
  /* 署名 */
  if (p.byline) {
    ctx.font = `600 9px ${FONT}`;
    ctx.fillStyle = CAP_TEXT;
    ctx.textAlign = "left";
    ctx.fillText(String(p.byline).toUpperCase(), MX, y + BYLINE_H / 2);
    y += BYLINE_H;
  }
  /* 正文 */
  bodyFont(ctx);
  ctx.fillStyle = BODY_COLOR;
  ctx.textAlign = "left";
  for (const line of L.bodyLines) {
    ctx.fillText(line, MX, y + BODY_LH / 2);
    y += BODY_LH;
  }
  /* 链接行 */
  if (L.hasLink) {
    y += 4;
    const lr = linkRect(L, y - baseY - 4);
    ctx.font = `600 11px ${FONT}`;
    ctx.fillStyle = BRAND;
    ctx.textAlign = "left";
    ctx.fillText("▶ " + (p.linkText || p.link), MX, y + 8);
    // 下划线
    const tw = Math.min(ctx.measureText("▶ " + (p.linkText || p.link)).width, L.innerW);
    ctx.strokeStyle = "rgba(14,165,164,0.5)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(MX + 2, y + 15.5);
    ctx.lineTo(MX + 2 + tw, y + 15.5);
    ctx.stroke();
    y += LINK_H;
  }
  /* MODELS 区 */
  if (L.models.length) {
    y += 4;
    // 区头
    ctx.font = `700 10px ${FONT}`;
    ctx.fillStyle = OFF_TEXT;
    ctx.textAlign = "left";
    ctx.fillText("MODELS", MX, y + SEC_H / 2);
    const chk = checkRect(L, y - baseY - 4);
    const chkLabel = node._sgCheck?.checked ? "⟳ check files" : "✓ check files";
    ctx.font = `600 9.5px ${FONT}`;
    ctx.fillStyle = CAP_TEXT;
    ctx.textAlign = "right";
    ctx.fillText(chkLabel, chk[0] + chk[2] - 4, y + SEC_H / 2);
    y += SEC_H;
    // 每个模型行
    const check = ensureCheck(node);
    L.models.forEach((name, i) => {
      const ry = y + i * MODEL_H;
      const found = Object.prototype.hasOwnProperty.call(check.found, name);
      const ok = found && !check.missing.includes(name);
      const pending = !check.checked;
      // 状态点
      ctx.beginPath();
      ctx.arc(MX + 5, ry + MODEL_H / 2, 3.5, 0, Math.PI * 2);
      ctx.fillStyle = pending ? "#617574" : ok ? BRAND : MISSING;
      ctx.fill();
      // 文件名
      bodyFont(ctx);
      ctx.fillStyle = ok ? "#cfe5e2" : pending ? BODY_COLOR : "#f5b04c";
      ctx.textAlign = "left";
      ctx.fillText(ellipsize(ctx, name, L.innerW - 96), MX + 16, ry + MODEL_H / 2);
      // 右侧徽章
      ctx.font = `600 9.5px ${FONT}`;
      const label = pending ? "检查中…" : ok ? "installed ✓" : "缺少 ✗";
      const bw = ctx.measureText(label).width + 14;
      const bx = L.W - MX - bw;
      rr(ctx, bx, ry + 3, bw, MODEL_H - 6, 9);
      ctx.fillStyle = pending ? "rgba(255,255,255,0.06)"
        : ok ? "rgba(14,165,164,0.16)" : "rgba(194,113,29,0.16)";
      ctx.fill();
      ctx.strokeStyle = pending ? BORDER : ok ? "rgba(14,165,164,0.5)" : MISSING;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = pending ? CAP_TEXT : ok ? BRAND : "#f5b04c";
      ctx.textAlign = "center";
      ctx.fillText(label, bx + bw / 2, ry + MODEL_H / 2 + 1);
    });
    y += L.models.length * MODEL_H;
  }
  ctx.restore();
}

/* ---------- 命中区域 ---------- */
function penRect(L) { return [L.W - MX - 22, 8, 20, 20]; }
function linkRect(L, relY) { return [MX - 4, relY, L.innerW + 8, LINK_H]; }
function checkRect(L, relY) { return [L.W - MX - 90, relY, 90, SEC_H]; }

/* ---------- 编辑弹窗 ---------- */
function openEditor(node) {
  document.getElementById("sg-note-editor")?.remove();
  const p = node.properties;
  const wrap = document.createElement("div");
  wrap.id = "sg-note-editor";
  wrap.style.cssText = "position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,0.45);display:flex;align-items:center;justify-content:center";
  const card = document.createElement("div");
  card.style.cssText = "background:#1e1f24;color:#ddd;border:1px solid #3a3b42;border-radius:10px;padding:14px;width:480px;max-height:74vh;overflow:auto;box-shadow:0 8px 30px rgba(0,0,0,0.5);font:12px/1.6 sans-serif";
  const title = document.createElement("div");
  title.textContent = "✎ 编辑说明卡片";
  title.style.cssText = "font-weight:700;font-size:14px;margin-bottom:10px;color:#fff";
  card.appendChild(title);

  const INP = "background:#2a2b31;color:#ddd;border:1px solid #444;border-radius:6px;padding:5px 8px;width:100%;box-sizing:border-box";
  const LAB = "font-size:10px;color:#617574;font-weight:600;margin:8px 0 3px;text-transform:uppercase";

  function field(label, value, onChange, textarea) {
    const lab = document.createElement("div");
    lab.textContent = label;
    lab.style.cssText = LAB;
    card.appendChild(lab);
    const inp = textarea ? document.createElement("textarea") : document.createElement("input");
    inp.value = value ?? "";
    inp.style.cssText = INP + (textarea ? ";min-height:110px;resize:vertical;line-height:1.6" : "");
    inp.oninput = () => { onChange(inp.value); node.setDirtyCanvas?.(true, true); };
    card.appendChild(inp);
    return inp;
  }

  field("标题", p.title, (v) => (p.title = v));
  field("署名（如 BY SG）", p.byline, (v) => (p.byline = v));
  field("链接地址（可空）", p.link, (v) => (p.link = v));
  field("链接文字（可空）", p.linkText, (v) => (p.linkText = v));
  field("正文（自动换行，空行分段）", p.body, (v) => (p.body = v), true);
  const modelsInp = field("模型文件（每行一个，自动检查是否已安装）", (p.models || []).join("\n"), (v) => {
    p.models = v.split("\n").map((s) => s.trim()).filter(Boolean);
    node._sgCheck = null;
    ensureCheck(node);
  }, true);

  const doneRow = document.createElement("div");
  doneRow.style.cssText = "display:flex;justify-content:flex-end;margin-top:12px";
  const done = document.createElement("button");
  done.textContent = "完成";
  done.style.cssText = "background:#0ea5a4;color:#04201d;font-weight:600;border:none;border-radius:6px;padding:6px 18px;cursor:pointer";
  done.onclick = () => { wrap.remove(); node.setDirtyCanvas?.(true, true); };
  doneRow.appendChild(done);
  card.appendChild(doneRow);

  wrap.appendChild(card);
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
  document.body.appendChild(wrap);
}

/* ---------- widget 构造 ---------- */
function makeCardWidget(node) {
  const w = {
    name: "sg_note_card",
    type: "custom",
    sgIsShanggu: true,
    y: 0,
    options: { serialize: false },
    serializeValue() { return ""; },
    computeSize() {
      const L = layout(this.node);
      return [0, L.H + 6];
    },
    draw(ctx, nodeRef, width, y) { drawCard(ctx, this.node, width, y); },
    mouse(e, pos, nodeRef) {
      if (e.type !== "pointerdown" && e.type !== "mousedown") return false;
      const node = this.node;
      const L = layout(node);
      const x = pos[0], y0 = pos[1] - (this.last_y ?? 0);
      // 铅笔
      const pen = penRect(L);
      const penAbs = [pen[0], pen[1], pen[2], pen[3]];
      if (x >= penAbs[0] && x <= penAbs[0] + penAbs[2] && y0 >= penAbs[1] && y0 <= penAbs[1] + penAbs[3]) {
        openEditor(node);
        return true;
      }
      // 链接行
      if (L.hasLink) {
        let ly = 8 + TITLE_H + 4 + (L.p.byline ? BYLINE_H : 0) + L.bodyLines.length * BODY_LH + 4;
        if (y0 >= ly && y0 <= ly + LINK_H) {
          try { window.open(L.p.link, "_blank"); } catch (err) { /* 忽略 */ }
          return true;
        }
      }
      // check files 按钮
      if (L.models.length) {
        let sy = 8 + TITLE_H + 4 + (L.p.byline ? BYLINE_H : 0) + L.bodyLines.length * BODY_LH + (L.hasLink ? LINK_H : 0) + 4;
        const chk = checkRect(L, sy);
        if (x >= chk[0] && x <= chk[0] + chk[2] && y0 >= sy && y0 <= sy + SEC_H) {
          node._sgCheck = null;
          checkModels(node);
          return true;
        }
      }
      return false;
    },
  };
  w.node = node;
  return w;
}

function rebuildWidgets(node) {
  node.widgets = (node.widgets || []).filter((x) => !x.sgIsShanggu);
  node.widgets.push(makeCardWidget(node));
  node.setDirtyCanvas?.(true, true);
}

const DEFAULTS = () => ({
  title: "工作流说明",
  byline: "BY SG",
  body: "1. 先在下方选择要用的模型和 LoRA；\n2. 点提示词框编写提示词；\n3. 生成后点保存图像卡片右下角浏览历史。",
  link: "",
  linkText: "",
  models: [],
});

/* ---------- 注册 ---------- */
app.registerExtension({
  name: "Shanggu.AusBossNote",
  async beforeRegisterNodeDef(nodeType, nodeDef) {
    if (nodeDef.name !== "ShangguAusBossNote") return;

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
      for (const [k, v] of Object.entries(DEFAULTS())) {
        if (this.properties[k] === undefined) this.properties[k] = v;
      }
      if (!Array.isArray(this.properties.models)) this.properties.models = [];
      if ((this.size?.[0] || 0) < 300) this.size = [300, this.size?.[1] || 180];
      rebuildWidgets(this);
      ensureCheck(this);
      return r;
    };

    const origConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (...args) {
      const r = origConfigure?.apply(this, args);
      for (const [k, v] of Object.entries(DEFAULTS())) {
        if (this.properties[k] === undefined) this.properties[k] = v;
      }
      rebuildWidgets(this);
      ensureCheck(this);
      return r;
    };

    const origMenu = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
      const r = origMenu?.call(this, canvas, options);
      options.unshift({ content: "✎ 编辑说明卡片", callback: () => openEditor(this) });
      return r;
    };

    nodeType.prototype.onDblClick = function () {
      openEditor(this);
      return true;
    };
  },
});

/* ================================================================ */
/* 纯文字小卡片：节点标题栏就是标题（双击标题栏用原生改名），           */
/* 卡片内只有正文文字区域（双击正文编辑），高度恒贴合内容              */
/* ================================================================ */
const TXT_MX = 10;
const TXT_LH = 15;          // 正文行高
const TXT_PAD_TOP = 6;
const TXT_PAD_BOT = 8;

function textCardLayout(node) {
  const W = node.size?.[0] || 220;
  const innerW = W - TXT_MX * 2 - 4;
  measureCtx.font = `400 11px ${FONT}`;
  const lines = wrapLines(node.properties?.text || "", innerW);
  const H = TXT_PAD_TOP + lines.length * TXT_LH + TXT_PAD_BOT;
  return { W, H, lines, innerW };
}

function drawTextCard(ctx, node, width, baseY) {
  const L = textCardLayout(node);
  ctx.save();
  bodyFont(ctx);
  ctx.fillStyle = BODY_COLOR;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  L.lines.forEach((line, i) => {
    ctx.fillText(line, TXT_MX, baseY + TXT_PAD_TOP + i * TXT_LH + TXT_LH / 2);
  });
  ctx.restore();
}

/* 高度精确贴合内容（可增可减，消灭底部无效区）。
   引擎排布起点 startY 恒为 2（无输入槽节点），widget 高度由
   node.computeSize 覆盖精确控制（见注册处）。 */
function fitTextCardHeight(node) {
  try {
    const L = textCardLayout(node);
    const base = node.widgets?.[0]?.last_y ?? 2;
    if (node.size) node.size[1] = base + L.H;
    node.setDirtyCanvas?.(true, true);
  } catch (e) { /* 忽略 */ }
}

/* ---------- 编辑弹窗（正文） ---------- */
function openTextEditor(node) {
  document.getElementById("sg-text-editor")?.remove();
  const wrap = document.createElement("div");
  wrap.id = "sg-text-editor";
  wrap.style.cssText = "position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,0.45);display:flex;align-items:center;justify-content:center";
  const card = document.createElement("div");
  card.style.cssText = "background:#1e1f24;color:#ddd;border:1px solid #3a3b42;border-radius:10px;padding:14px;width:420px;box-shadow:0 8px 30px rgba(0,0,0,0.5);font:12px/1.6 sans-serif";
  card.appendChild(Object.assign(document.createElement("div"), {
    textContent: "✎ 编辑文字",
    style: "font-weight:700;font-size:14px;margin-bottom:10px;color:#fff",
  }));
  const inp = document.createElement("textarea");
  inp.value = node.properties?.text || "";
  inp.style.cssText = "background:#2a2b31;color:#ddd;border:1px solid #444;border-radius:6px;padding:6px 8px;width:100%;box-sizing:border-box;min-height:120px;resize:vertical;line-height:1.6;font:12px/1.6 sans-serif";
  inp.oninput = () => {
    node.properties.text = inp.value;
    fitTextCardHeight(node);
  };
  card.appendChild(inp);
  const doneRow = document.createElement("div");
  doneRow.style.cssText = "display:flex;justify-content:flex-end;margin-top:10px";
  const done = document.createElement("button");
  done.textContent = "完成";
  done.style.cssText = "background:#0ea5a4;color:#04201d;font-weight:600;border:none;border-radius:6px;padding:6px 18px;cursor:pointer";
  done.onclick = () => { wrap.remove(); fitTextCardHeight(node); };
  doneRow.appendChild(done);
  card.appendChild(doneRow);
  wrap.appendChild(card);
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
  document.body.appendChild(wrap);
  inp.focus();
}

app.registerExtension({
  name: "Shanggu.TextCard",
  async beforeRegisterNodeDef(nodeType, nodeDef) {
    if (nodeDef.name !== "ShangguTextCard") return;

    // 覆盖引擎的 node.computeSize：其通用公式对无插槽节点也强算 1 行插槽高
    // + 尾部 8px，导致拖拽缩小时最小高度 = 内容+32px，底部永远留大条空白。
    // 这里返回精确内容高度（内容底 = 排布起点 2 + 正文高），缩到即贴合。
    nodeType.prototype.computeSize = function () {
      const L = textCardLayout(this);
      const base = this.widgets?.[0]?.last_y ?? 2;
      return [this.size?.[0] || 220, base + L.H];
    };

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

    function rebuild(node) {
      node.widgets = (node.widgets || []).filter((x) => !x.sgIsShanggu);
      // 注意：新版前端会把 push 进去的 widget 包装成 LegacyWidget，
      // node 属性只读（赋值会抛 TypeError），所以全部用闭包引用 node
      node.widgets.push({
        name: "sg_text_card",
        type: "custom",
        sgIsShanggu: true,
        y: 0,
        options: { serialize: false },
        serializeValue() { return ""; },
        computeSize() { return [0, textCardLayout(node).H - 4]; },
        draw(ctx, nodeRef, width, y) { drawTextCard(ctx, node, width, y); },
        mouse(e, pos, nodeRef) {
          if (e.type !== "pointerdown" && e.type !== "mousedown") return false;
          try {
            // widget 行内坐标：pos 是节点本地坐标，减 last_y 得 widget 内偏移
            const lx = pos[0], ly = pos[1] - (this.last_y ?? 0);
            const now = performance.now();
            const prev = node._sgTap;
            if (prev && now - prev.t < 400 && Math.abs(prev.x - lx) < 16 && Math.abs(prev.y - ly) < 16) {
              // 自己判定双击：正文区 → 编辑正文
              node._sgTap = null;
              openTextEditor(node);
              return true;
            }
            node._sgTap = { t: now, x: lx, y: ly };
          } catch (err) { /* 忽略 */ }
          // 单击不占事件：卡片任意位置都能拖着节点走
          return false;
        },
      });
      node.setDirtyCanvas?.(true, true);
    }

    const origCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function (...args) {
      const r = origCreated?.apply(this, args);
      try {
        this.properties = this.properties || {};
        if (typeof this.properties.text !== "string") this.properties.text = "双击编辑这段文字…";
        if ((this.size?.[0] || 0) < 220) this.size = [220, this.size?.[1] || 90];
        rebuild(this);
        fitTextCardHeight(this);
      } catch (err) {
        console.error("[Shanggu 文字卡片] 初始化失败:", err);
      }
      return r;
    };

    const origConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (...args) {
      const r = origConfigure?.apply(this, args);
      try {
        if (typeof this.properties.text !== "string") this.properties.text = "";
        rebuild(this);
        // last_y 首帧还没值，等一帧再贴合高度
        setTimeout(() => fitTextCardHeight(this), 60);
      } catch (err) {
        console.error("[Shanggu 文字卡片] 恢复失败:", err);
      }
      return r;
    };

    const origMenu = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
      const r = origMenu?.call(this, canvas, options);
      options.unshift({ content: "✎ 编辑文字", callback: () => openTextEditor(this) });
      return r;
    };

    // 双击标题栏（pos[1]<0）：前端原生会弹出标题改名输入框（对所有节点通用），
    // 这里不注册 onNodeTitleDblClick、onDblClick 返回 false 不拦截，
    // 保证原生改名能正常弹出；正文双击由 widget 自己检测处理
    nodeType.prototype.onDblClick = function (e, pos, canvas) {
      return false;
    };
  },
});
