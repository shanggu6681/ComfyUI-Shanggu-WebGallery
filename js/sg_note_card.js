// Shanggu 说明卡片 —— AusBoss 风格工作流说明卡（DOM 版，v2）
// 正文区改为 DOM 覆盖层（真 HTML）：
//   - 所有文字可鼠标框选复制
//   - 网址是可点击的 <a>（新标签页打开），也能框选复制
//   - 节点包每项渲染成深色代码块 `git clone xxx` + 复制按钮（仿 AI 代码输出）
//   - 模型区保留本机安装状态检测（✓ installed / ✗ 缺少）
// 内容存 properties（纯数据），编辑走右上角铅笔 / 双击节点 / 右键菜单。
import { app } from "../../scripts/app.js";
import { keepDomWidgetWidthAuto } from "./shared/index.mjs";

/* ---------- 风格常量（画布底色与卡片边框，与保存图像 / 组开关一致） ---------- */
const BRAND = "#0ea5a4";
const CARD_BG = "rgba(0,0,0,0.28)";
const CARD_BORDER = "rgba(14,165,164,0.22)";

/* ---------- 一次性注入卡片内部样式 ---------- */
(function injectStyle() {
  if (document.getElementById("sg-note-style")) return;
  const st = document.createElement("style");
  st.id = "sg-note-style";
  st.textContent = `
.sg-note-root{width:100%;overflow:visible}
.sg-note{box-sizing:border-box;padding:10px 12px 12px;font:12px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#a9bcba}
.sg-note *{box-sizing:border-box}
.sg-note-head{display:flex;align-items:center;gap:8px;margin-bottom:2px}
.sg-note-title{flex:1;min-width:0;font-size:15px;font-weight:700;color:#eef7f5;user-select:text;cursor:text;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sg-note-pen{flex:none;background:rgba(255,255,255,0.06);border:1px solid #3a4448;border-radius:6px;color:#8ba3a1;font:600 12px sans-serif;padding:2px 8px;cursor:pointer}
.sg-note-pen:hover{color:#0ea5a4;border-color:rgba(14,165,164,0.6)}
.sg-note-byline{font-size:9px;font-weight:600;letter-spacing:.08em;color:#617574;text-transform:uppercase;margin-bottom:4px;user-select:text}
.sg-note-body{white-space:pre-wrap;word-break:break-word;font-size:11.5px;line-height:1.45;color:#a9bcba;user-select:text;cursor:text}
.sg-note-link{margin-top:6px}
.sg-note-link a{color:#0ea5a4;font-weight:600;font-size:11.5px;text-decoration:none;border-bottom:1px solid rgba(14,165,164,0.5);user-select:text}
.sg-note-sec{font-size:10px;font-weight:700;color:#8ba3a1;margin:10px 0 4px;letter-spacing:.04em;user-select:text;display:flex;align-items:center}
.sg-node-name{font-size:11.5px;font-weight:600;color:#cfe5e2;margin:6px 0 3px;user-select:text}
.sg-code{display:flex;align-items:stretch;gap:6px;background:#0b0d0f;border:1px solid #2c3336;border-radius:6px;padding:3px 4px 3px 8px;margin-bottom:2px}
.sg-code-text{flex:1;min-width:0;font:11px/1.7 Consolas,Menlo,monospace;color:#d7e5e2;white-space:nowrap;overflow-x:auto;user-select:text;cursor:text;scrollbar-width:thin}
.sg-copy{flex:none;align-self:center;background:rgba(14,165,164,0.14);border:1px solid rgba(14,165,164,0.5);border-radius:5px;color:#0ea5a4;font:600 10px sans-serif;padding:2px 8px;cursor:pointer}
.sg-copy:hover{background:rgba(14,165,164,0.28)}
.sg-copy.done{background:rgba(14,165,164,0.4)}
.sg-check{margin-left:auto;background:rgba(255,255,255,0.06);border:1px solid #3a4448;border-radius:5px;color:#8ba3a1;font:600 9.5px sans-serif;padding:1px 7px;cursor:pointer}
.sg-check:hover{color:#0ea5a4}
.sg-folder{font-size:10px;font-weight:700;color:#617574;margin:6px 0 1px;user-select:text}
.sg-mrow{display:flex;align-items:center;gap:6px;padding:2.5px 0}
.sg-dot{width:7px;height:7px;border-radius:50%;flex:none;background:#617574}
.sg-dot.ok{background:#0ea5a4}
.sg-dot.missing{background:#c2711d}
.sg-mname{flex:none;max-width:52%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;color:#cfe5e2;user-select:text}
.sg-murl{flex:1;min-width:0;font-size:10.5px;color:#0ea5a4;text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;user-select:text}
.sg-murl:hover{text-decoration:underline}
.sg-mnote{flex:1;min-width:0;font-size:10px;color:#617574;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;user-select:text}
.sg-badge{flex:none;font:600 9.5px sans-serif;border-radius:9px;padding:1.5px 8px;background:rgba(255,255,255,0.06);border:1px solid #3a4448;color:#617574}
.sg-badge.ok{background:rgba(14,165,164,0.16);border-color:rgba(14,165,164,0.5);color:#0ea5a4}
.sg-badge.missing{background:rgba(194,113,29,0.16);border-color:#c2711d;color:#f5b04c}
`;
  document.head.appendChild(st);
})();

/* ---------- 小工具 ---------- */
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

/* 网址识别：http(s) 开头，或 github.com/xxx 这类可补 https:// 的形式。
   其余（如「随包发布」）按纯文本备注渲染。 */
function toHref(u) {
  const s = String(u || "").trim();
  if (!s) return null;
  if (/^https?:\/\//i.test(s)) return s;
  if (/^[\w-]+(\.[\w-]+)+\//.test(s) || /^[\w-]+(\.[\w-]+)+$/.test(s)) return "https://" + s;
  return null;
}
function dispUrl(u) {
  return String(u || "").trim().replace(/^https?:\/\//i, "").replace(/\/+$/, "");
}
function repoName(u) {
  const s = String(u || "").replace(/^https?:\/\//i, "").replace(/\/+$/, "");
  const seg = s.split("/").filter(Boolean);
  return seg.length ? seg[seg.length - 1] : "";
}

/* 节点包行解析：`名称 | 地址` 或裸地址 */
function parseNodeEntry(line) {
  const s = String(line || "").trim();
  if (!s) return null;
  const i = s.indexOf("|");
  let name = "", url = s;
  if (i >= 0) { name = s.slice(0, i).trim(); url = s.slice(i + 1).trim(); }
  if (!name) name = repoName(url) || url;
  return { name, url };
}

/* 模型条目：兼容旧版纯文件名字符串；新版 {folder, name, url} */
function normalizeModel(m) {
  if (typeof m === "string") return { name: m.trim() };
  if (m && typeof m === "object") {
    return { folder: String(m.folder || "").trim(), name: String(m.name || "").trim(), url: String(m.url || "").trim() };
  }
  return null;
}
function modelNames(node) {
  return (node.properties?.models || []).map(normalizeModel).filter(Boolean).map((m) => m.name).filter(Boolean);
}
/* 模型条目 ↔ 编辑框行文本：文件夹 | 文件名 | 地址（文件夹可省略） */
function modelToLine(m) {
  const n = normalizeModel(m) || { name: "" };
  const parts = [];
  if (n.folder) parts.push(n.folder);
  parts.push(n.name);
  if (n.url) parts.push(n.url);
  return parts.join(" | ");
}
function parseModelLine(line) {
  const parts = String(line || "").split("|").map((s) => s.trim()).filter(Boolean);
  if (!parts.length) return null;
  if (parts.length === 1) return { name: parts[0] };
  if (parts.length === 2) return { name: parts[0], url: parts[1] };
  return { folder: parts[0], name: parts[1], url: parts.slice(2).join(" | ") };
}

/* ---------- 复制 ---------- */
async function copyText(text, btn) {
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; } catch (_e) { /* 降级 */ }
  if (!ok) {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;top:-999px;left:0;opacity:0";
      document.body.appendChild(ta);
      ta.select();
      ok = document.execCommand("copy");
      ta.remove();
    } catch (_e2) { /* 放弃 */ }
  }
  const old = btn.textContent;
  btn.textContent = ok ? "已复制 ✓" : "复制失败";
  btn.classList.toggle("done", ok);
  setTimeout(() => { btn.textContent = old; btn.classList.remove("done"); }, 1200);
}
function copyBtn(text) {
  const b = el("button", "sg-copy", "复制");
  b.type = "button";
  b.title = "点击复制";
  b.addEventListener("click", (e) => { e.stopPropagation(); copyText(text, b); });
  return b;
}

/* ---------- 模型安装状态检查 ---------- */
async function checkModels(node) {
  if (node._sgChecking) return;
  node._sgChecking = true;
  const names = modelNames(node);
  if (!names.length) {
    node._sgCheck = { found: {}, missing: [], checked: true };
    node._sgChecking = false;
    renderNote(node);
    return;
  }
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
  renderNote(node);
}
function ensureCheck(node) {
  if (node._sgCheck?.checked) return node._sgCheck;
  checkModels(node);
  return { found: {}, missing: modelNames(node), checked: false };
}

/* ---------- DOM 渲染 ---------- */
function renderNote(node) {
  const inner = node._sgInner;
  if (!inner) return;   // 不查 isConnected：addDOMWidget 挂载晚于首次调用，挂上后靠 RO 回调补测高度
  const p = node.properties || {};
  inner.textContent = "";

  /* 头部：标题 + 铅笔 */
  const head = el("div", "sg-note-head");
  head.append(el("div", "sg-note-title", p.title || "说明卡片"));
  const pen = el("button", "sg-note-pen", "✎");
  pen.type = "button";
  pen.title = "编辑说明卡片";
  pen.addEventListener("click", (e) => { e.stopPropagation(); openEditor(node); });
  head.append(pen);
  inner.append(head);

  if (p.byline) inner.append(el("div", "sg-note-byline", String(p.byline)));
  if (p.body) inner.append(el("div", "sg-note-body", String(p.body)));

  if (p.link && String(p.link).trim()) {
    const href = toHref(p.link);
    const row = el("div", "sg-note-link");
    const a = document.createElement("a");
    a.href = href || "#";
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = "▶ " + (p.linkText || dispUrl(p.link));
    row.append(a);
    inner.append(row);
  }

  /* 【节点包 Nodes】：名称 + git clone 代码块 + 复制按钮 */
  const nodes = (p.nodes || []).map(parseNodeEntry).filter(Boolean);
  if (nodes.length) {
    inner.append(el("div", "sg-note-sec", "【节点包 Nodes】"));
    for (const n of nodes) {
      inner.append(el("div", "sg-node-name", n.name));
      if (n.url) {
        const href = toHref(n.url);
        const cmd = "git clone " + (href || n.url);
        const code = el("div", "sg-code");
        const ct = el("div", "sg-code-text", cmd);
        code.append(ct, copyBtn(cmd));
        inner.append(code);
      }
    }
  }

  /* 【必需模型 Models】：文件夹 / 模型名 / 可点击地址 + 安装状态 */
  const models = (p.models || []).map(normalizeModel).filter((m) => m && m.name);
  if (models.length) {
    const sec = el("div", "sg-note-sec", "【必需模型 Models】");
    const chk = el("button", "sg-check", node._sgCheck?.checked ? "⟳ 重新检查" : "✓ 检查安装");
    chk.type = "button";
    chk.title = "检查本机 models 目录是否已安装这些文件";
    chk.addEventListener("click", (e) => { e.stopPropagation(); node._sgCheck = null; checkModels(node); });
    sec.append(chk);
    inner.append(sec);

    const check = ensureCheck(node);
    /* 按文件夹分组（保持输入顺序） */
    const groups = [];
    let cur = null, curFolder;
    for (const m of models) {
      const f = m.folder || "";
      if (f !== curFolder) { curFolder = f; cur = { folder: f, items: [] }; groups.push(cur); }
      cur.items.push(m);
    }
    for (const g of groups) {
      if (g.folder) inner.append(el("div", "sg-folder", "▸ " + g.folder));
      for (const m of g.items) {
        const row = el("div", "sg-mrow");
        const found = Object.prototype.hasOwnProperty.call(check.found, m.name);
        const ok = found && !check.missing.includes(m.name);
        const pending = !check.checked;
        row.append(el("span", "sg-dot" + (pending ? "" : ok ? " ok" : " missing")));
        row.append(el("span", "sg-mname", m.name));
        const href = toHref(m.url);
        if (href) {
          const a = document.createElement("a");
          a.className = "sg-murl";
          a.href = href;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = dispUrl(m.url);
          a.title = href;
          row.append(a);
        } else if (m.url) {
          row.append(el("span", "sg-mnote", m.url));
        } else {
          row.append(el("span", "sg-mnote", ""));
        }
        row.append(el("span", "sg-badge" + (pending ? "" : ok ? " ok" : " missing"),
          pending ? "检查中…" : ok ? "installed ✓" : "缺少 ✗"));
        inner.append(row);
      }
    }
  }
  syncNoteHeight(node);
}

/* ---------- 高度跟随内容（只增不减到内容高度，宽度用户拖） ---------- */
function syncNoteHeight(node) {
  const inner = node._sgInner;
  if (inner && inner.isConnected) {
    const h = inner.offsetHeight;
    if (h > 4) node._sgDomH = h;   // 折叠/隐藏时测得 0，保留缓存值
  }
  try {
    if (node.size && node._sgDomH) {
      const base = node.widgets?.[0]?.last_y ?? 2;
      const target = base + node._sgDomH + 8;
      if (Math.abs(node.size[1] - target) > 1.5) node.size[1] = target;
    }
  } catch (_e) { /* 忽略 */ }
  node.setDirtyCanvas?.(true, true);
}

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

  field("标题", p.title, (v) => { p.title = v; renderNote(node); });
  field("署名（如 BY SG）", p.byline, (v) => { p.byline = v; renderNote(node); });
  field("链接地址（可空）", p.link, (v) => { p.link = v; renderNote(node); });
  field("链接文字（可空）", p.linkText, (v) => { p.linkText = v; renderNote(node); });
  field("正文（自动换行，空行分段）", p.body, (v) => { p.body = v; renderNote(node); }, true);
  field("节点包（每行一个：名称 | git地址，也可只写地址）", (p.nodes || []).join("\n"), (v) => {
    p.nodes = v.split("\n").map((s) => s.trim()).filter(Boolean);
    renderNote(node);
  }, true);
  field("模型文件（每行一个：文件夹 | 文件名 | 下载地址；文件夹可省略，地址可填备注文字）",
    (p.models || []).map(modelToLine).join("\n"), (v) => {
      p.models = v.split("\n").map(parseModelLine).filter(Boolean);
      node._sgCheck = null;
      renderNote(node);
      ensureCheck(node);
    }, true);

  const doneRow = document.createElement("div");
  doneRow.style.cssText = "display:flex;justify-content:flex-end;margin-top:12px";
  const done = document.createElement("button");
  done.textContent = "完成";
  done.style.cssText = "background:#0ea5a4;color:#04201d;font-weight:600;border:none;border-radius:6px;padding:6px 18px;cursor:pointer";
  done.onclick = () => { wrap.remove(); renderNote(node); };
  doneRow.appendChild(done);
  card.appendChild(doneRow);

  wrap.appendChild(card);
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
  document.body.appendChild(wrap);
}

/* ---------- DOM widget 挂载 ---------- */
function attachDom(node) {
  if (!node._sgInner) {
    const root = document.createElement("div");
    root.className = "sg-note-root";
    const inner = document.createElement("div");
    inner.className = "sg-note";
    root.appendChild(inner);
    node._sgRoot = root;
    node._sgInner = inner;

    // 按钮 / 链接 / 代码块上的按下不透传给画布（避免触发节点拖拽）
    root.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button,a,.sg-code")) e.stopPropagation();
    });

    const dw = node.addDOMWidget("sg_note_card", "sg_note", root, {
      serialize: false,
      hideOnZoom: false,
    });
    node._sgDomWidget = dw;
    dw.serialize = false;
    keepDomWidgetWidthAuto(dw);
    dw.computeSize = () => [0, (node._sgDomH || 300) + 6];

    // 内容尺寸变化（改字 / 换行 / 检测结果回来）→ 节点高度跟随
    const ro = new ResizeObserver(() => syncNoteHeight(node));
    ro.observe(inner);
    node._sgRo = ro;
  }
  // 无论首次还是二次（onConfigure 带回工作流数据后）都重渲一次
  renderNote(node);
}

const DEFAULTS = () => ({
  title: "工作流说明",
  byline: "BY SG",
  body: "1. 先在下方选择要用的模型和 LoRA；\n2. 点提示词框编写提示词；\n3. 生成后点保存图像卡片右下角浏览历史。",
  link: "",
  linkText: "",
  nodes: [],
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

    // 覆盖引擎 computeSize：高度精确 = 排布起点 + DOM 内容高 + 8，
    // 拖拽缩小时不会留下大块无效区（与纯文字卡片同策略）
    nodeType.prototype.computeSize = function () {
      const base = this.widgets?.[0]?.last_y ?? 2;
      return [this.size?.[0] || 340, base + (this._sgDomH || 300) + 8];
    };

    const origCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function (...args) {
      const r = origCreated?.apply(this, args);
      this.properties = this.properties || {};
      for (const [k, v] of Object.entries(DEFAULTS())) {
        if (this.properties[k] === undefined) this.properties[k] = v;
      }
      if (!Array.isArray(this.properties.nodes)) this.properties.nodes = [];
      if (!Array.isArray(this.properties.models)) this.properties.models = [];
      if ((this.size?.[0] || 0) < 340) this.size = [340, this.size?.[1] || 300];
      attachDom(this);
      ensureCheck(this);
      return r;
    };

    const origConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (...args) {
      const r = origConfigure?.apply(this, args);
      for (const [k, v] of Object.entries(DEFAULTS())) {
        if (this.properties[k] === undefined) this.properties[k] = v;
      }
      if (!Array.isArray(this.properties.nodes)) this.properties.nodes = [];
      if (!Array.isArray(this.properties.models)) this.properties.models = [];
      attachDom(this);
      ensureCheck(this);
      // 首帧 last_y 还没值，等一帧再校一次高度
      setTimeout(() => syncNoteHeight(this), 60);
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

    const origRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function (...args) {
      try { this._sgRo?.disconnect(); } catch (_e) { /* 忽略 */ }
      this._sgRoot = null;
      this._sgInner = null;
      return origRemoved?.apply(this, args);
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

/* 文本测量用离屏 context（computeSize 里没有 ctx） */
const measureCtx = document.createElement("canvas").getContext("2d");
const FONT = `-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif`;
const BODY_COLOR = "#a9bcba";
const OFF_TEXT = "#8ba3a1";

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
