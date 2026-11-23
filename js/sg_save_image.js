// Shanggu 保存图像 —— ausboss Save Image 同款卡片。
// Folder(+Browse 浏览 output 子文件夹)、Filename、Path 预览、
// Counter/Date/Time/Size/Batch# 标签、PNG/WebP/JXL 格式药丸、
// Embed workflow 开关、Preview 开关。
// 标准 widget 仍是数据源并正常序列化，卡片只负责读写它们的外观。
import { app } from "/scripts/app.js";

const NODE_CLASS = "ShangguSaveImage";
const WIDGET_NAME = "sg_save_image_card";
const CSS_ID = "sg-save-image-css";
const NODE_MIN_WIDTH = 340;
const CARD_HEIGHT = 306;
const HIDDEN = [
  "filename_prefix", "output_dir", "format", "save_metadata",
  "name_counter", "name_date", "name_time", "name_size", "name_batch", "preview",
];
const TAGS = [
  ["name_counter", "Counter", "追加 5 位递增序号，永不重名"],
  ["name_date", "Date", "追加保存日期 YYYY-MM-DD"],
  ["name_time", "Time", "追加保存时间 HH-MM-SS"],
  ["name_size", "Size", "追加图片尺寸，如 1024x1024"],
  ["name_batch", "Batch #", "批次图追加 b001/b002…"],
];
const FORMATS = [["png", "PNG"], ["webp", "WebP"], ["jxl", "JXL"]];
const FONT = `-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif`;
const MONO = `ui-monospace,SFMono-Regular,Menlo,Consolas,monospace`;
const BRAND = "#0ea5a4";

function ensureCss() {
  if (document.getElementById(CSS_ID)) return;
  const style = document.createElement("style");
  style.id = CSS_ID;
  style.textContent = `
.sg-save{box-sizing:border-box;width:100%;height:100%;display:flex;flex-direction:column;gap:5px;padding:8px 9px;overflow:hidden;border:1px solid rgba(14,165,164,.22);border-radius:8px;background:rgba(0,0,0,.28);font:12px/1.3 ${FONT};color:#c9d6d6;pointer-events:none}
.sg-save-cap{height:13px;flex:none;color:#617574;font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;user-select:none}
.sg-save-row{display:flex;align-items:center;gap:6px;flex:none;height:26px;pointer-events:auto}
.sg-save input.sg-card-text{flex:1;min-width:0;height:26px;padding:0 9px;border:1px solid #333d40;border-radius:6px;background:#111617;color:#dfe9e8;font:12px ${MONO};outline:none}
.sg-save input.sg-card-text:focus{border-color:${BRAND}}
.sg-save .sg-btn{flex:none;height:26px;padding:0 12px;border:1px solid #3a4448;border-radius:6px;background:#1a2123;color:#c9d6d6;font:600 11px ${FONT};cursor:pointer}
.sg-save .sg-btn:hover{border-color:${BRAND};color:#fff}
.sg-save-preview{flex:none;padding:5px 9px;border:1px solid #2a3437;border-radius:6px;background:#0b0f10;font-family:${MONO};overflow:hidden;pointer-events:auto}
.sg-save-preview .folder{color:#617574;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sg-save-preview .name{color:#d8ecea;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.sg-save-preview.saved .name{color:#37d67a}
.sg-save-chips{display:flex;flex-wrap:wrap;gap:5px;flex:none;pointer-events:auto}
.sg-save-chip{height:22px;padding:0 9px;border:1px solid #3a4448;border-radius:6px;background:transparent;color:#8ba3a1;font:600 10.5px/1 ${FONT};cursor:pointer;white-space:nowrap}
.sg-save-chip:hover{color:#fff;border-color:${BRAND}}
.sg-save-chip.on{background:${BRAND};border-color:${BRAND};color:#04201d}
.sg-save-seg{display:flex;flex:1;gap:0;border:1px solid #3a4448;border-radius:6px;overflow:hidden;pointer-events:auto}
.sg-save-seg button{flex:1;height:26px;border:none;background:transparent;color:#8ba3a1;font:600 11px ${FONT};cursor:pointer}
.sg-save-seg button:hover{color:#fff}
.sg-save-seg button.on{background:${BRAND};color:#04201d}
.sg-save-label{flex:0 0 110px;color:#8ba3a1;font-size:11px;user-select:none}
.sg-save-browse{position:fixed;z-index:10000;width:260px;max-height:320px;overflow:auto;padding:4px;border:1px solid #3a4448;border-radius:7px;background:#1c2123;box-shadow:0 8px 28px rgba(0,0,0,.5);font:12px/1.3 ${FONT};color:#d8ecea}
.sg-save-browse-head{padding:5px 8px 6px;border-bottom:1px solid #2c3238;color:#78908e;font:10px/1 ${FONT};letter-spacing:.08em;text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sg-save-browse button{display:block;width:100%;padding:6px 8px;border:none;border-radius:5px;background:transparent;color:inherit;font:inherit;text-align:left;cursor:pointer}
.sg-save-browse button:hover{background:#2a3037}
.sg-save-browse button.use{color:${BRAND};font-weight:600}
`;
  document.head.append(style);
}

const el = (tag, className = "", text) => {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text !== undefined) n.textContent = text;
  return n;
};

function widget(node, name) {
  return node.widgets?.find((w) => w.name === name) ?? null;
}
function value(node, name, fallback) {
  const w = widget(node, name);
  return w ? w.value : fallback;
}
function setValue(node, name, next) {
  const w = widget(node, name);
  if (!w || w.value === next) return false;
  w.value = next;
  w.callback?.(next, app.canvas, node, w);
  node.graph?.setDirtyCanvas?.(true, true);
  return true;
}
function hideWidget(w) {
  w.computeSize = () => [0, -4];
  w.hidden = true;
  if (w.element?.parentElement) w.element.style.display = "none";
}

// ---------------- Browse 弹层：浏览 output 下的子文件夹 ----------------

let openPopup = null;
function closeBrowse() {
  openPopup?.element.remove();
  openPopup = null;
}

async function listFolders(path) {
  const resp = await fetch(`/sg_gallery/save_folders?path=${encodeURIComponent(path)}`);
  const j = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(j.error || "无法列出文件夹");
  return j;
}

async function openBrowse(node, refresh, anchor) {
  closeBrowse();
  const popup = el("div", "sg-save-browse");
  const rect = anchor.getBoundingClientRect();
  popup.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 270))}px`;
  popup.style.top = `${rect.bottom + 4}px`;
  document.body.append(popup);
  openPopup = { element: popup };
  const current = String(value(node, "output_dir", "")).trim().replace(/\\/g, "/");
  let path = /^([A-Za-z]:[\\/]|\/|~)/.test(current) ? "" : current.replace(/^\/+|\/+$/g, "");
  const abort = new AbortController();
  const render = async () => {
    popup.textContent = "";
    popup.append(el("div", "sg-save-browse-head", `ComfyUI/output/${path}`));
    let listing;
    try {
      listing = await listFolders(path);
    } catch (error) {
      popup.append(el("div", "sg-save-browse-head", error.message));
      return;
    }
    path = listing.path;
    const use = el("button", "use", path ? `✓ 用 "${path}"` : "✓ 用 output 根目录");
    use.addEventListener("click", () => { setValue(node, "output_dir", path); refresh(); closeBrowse(); });
    popup.append(use);
    if (path) {
      const up = el("button", "", "↑ 上一级");
      up.addEventListener("click", () => { path = path.split("/").slice(0, -1).join("/"); render(); });
      popup.append(up);
    }
    for (const folder of listing.folders) {
      const b = el("button", "", `📁 ${folder}`);
      b.addEventListener("click", () => { path = path ? `${path}/${folder}` : folder; render(); });
      popup.append(b);
    }
    if (!listing.folders.length) popup.append(el("div", "sg-save-browse-head", "这里没有子文件夹了"));
  };
  window.addEventListener("pointerdown", (event) => {
    if (popup.contains(event.target) || event.target === anchor) return;
    closeBrowse();
  }, { capture: true, signal: abort.signal });
  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    closeBrowse();
  }, { capture: true, signal: abort.signal });
  await render();
}

// ---------------- 命名预览（与后端 plan_local_names 单张逻辑一致）----------------

function previewName(node) {
  const now = new Date();
  const p2 = (n) => String(n).padStart(2, "0");
  let base = String(value(node, "filename_prefix", "")).replace(/\\/g, "/");
  base = base.split("/").pop() || "Shanggu";
  const dot = base.lastIndexOf(".");
  if (dot >= 0) {
    const ext = base.slice(dot).toLowerCase();
    if ([".png", ".webp", ".jxl", ".jpg", ".jpeg", ".gif", ".bmp"].includes(ext)) base = base.slice(0, dot);
  }
  const parts = [base];
  if (value(node, "name_date", false)) parts.push(`${now.getFullYear()}-${p2(now.getMonth() + 1)}-${p2(now.getDate())}`);
  if (value(node, "name_time", false)) parts.push(`${p2(now.getHours())}-${p2(now.getMinutes())}-${p2(now.getSeconds())}`);
  const fmt = String(value(node, "format", "png"));
  let name = parts.join("_");
  if (value(node, "name_counter", true)) name += "_00001";
  if (value(node, "name_batch", false)) name += "_b001";
  return `${name}.${fmt}`;
}

// ---------------- 卡片 ----------------

function buildCard(node) {
  ensureCss();
  const root = el("div", "sg-save");
  const state = { node, savedPath: null };
  node.__sgSaveImage = state;
  for (const name of HIDDEN) {
    const w = widget(node, name);
    if (w) hideWidget(w);
  }

  // Folder
  root.append(el("div", "sg-save-cap", "Folder"));
  const folderRow = el("div", "sg-save-row");
  const folder = el("input", "sg-card-text");
  folder.placeholder = "ComfyUI/output";
  folder.spellcheck = false;
  folder.title = "output 目录下的子文件夹（如 sets/portraits），留空存到 output 根目录。只能保存在 output 内。";
  folder.addEventListener("change", () => { setValue(node, "output_dir", folder.value.trim()); state.savedPath = null; refresh(); });
  folder.addEventListener("keydown", (e) => {
    e.stopPropagation?.();
    if (e.key === "Enter") folder.blur();
  });
  const browse = el("button", "sg-btn", "Browse");
  browse.title = "浏览 output 下的子文件夹";
  browse.addEventListener("click", () => {
    if (openPopup) closeBrowse();
    openBrowse(node, refresh, browse);
  });
  folderRow.append(folder, browse);
  root.append(folderRow);

  // Filename
  root.append(el("div", "sg-save-cap", "Filename"));
  const nameRow = el("div", "sg-save-row");
  const nameField = el("input", "sg-card-text");
  nameField.placeholder = "Shanggu";
  nameField.spellcheck = false;
  nameField.title = "文件名前缀，可带子文件夹。下面的标签会追加到它后面。支持记号 %width% %height% %year% 等。";
  nameField.addEventListener("change", () => { setValue(node, "filename_prefix", nameField.value.trim() || "Shanggu"); state.savedPath = null; refresh(); });
  nameField.addEventListener("keydown", (e) => {
    e.stopPropagation?.();
    if (e.key === "Enter") nameField.blur();
  });
  nameRow.append(nameField);
  root.append(nameRow);

  // Path preview
  root.append(el("div", "sg-save-cap", "Path preview"));
  const preview = el("div", "sg-save-preview");
  const previewFolderLine = el("div", "folder");
  const previewNameLine = el("div", "name");
  preview.append(previewFolderLine, previewNameLine);
  preview.title = "本次保存将生成的路径；跑完后显示实际写入的第一个文件。";
  root.append(preview);

  // 标签 chips
  const chips = el("div", "sg-save-chips");
  const chipButtons = new Map();
  for (const [key, label, title] of TAGS) {
    const chip = el("button", "sg-save-chip", label);
    chip.type = "button"; chip.title = title;
    chip.addEventListener("click", () => {
      setValue(node, key, !Boolean(value(node, key, false)));
      state.savedPath = null; refresh();
    });
    chips.append(chip); chipButtons.set(key, chip);
  }
  root.append(chips);

  // 格式药丸
  const seg = el("div", "sg-save-seg");
  const formatButtons = new Map();
  const formatTitles = {
    png: "无损、兼容性最好，可嵌入工作流拖回 ComfyUI。",
    webp: "无损，通常比 PNG 小，工作流写进 EXIF。",
    jxl: "无损最紧凑，需要 pillow-jxl-plugin；浏览器预览支持有限。",
  };
  for (const [key, label] of FORMATS) {
    const b = el("button", "", label);
    b.type = "button"; b.title = formatTitles[key] ?? key;
    b.addEventListener("click", () => { setValue(node, "format", key); state.savedPath = null; refresh(); });
    seg.append(b); formatButtons.set(key, b);
  }
  root.append(seg);

  // Embed workflow 开关
  const embedRow = el("div", "sg-save-row");
  const embedSeg = el("div", "sg-save-seg");
  const embedButtons = new Map();
  for (const [on, text] of [[false, "Off"], [true, "On"]]) {
    const b = el("button", "", text);
    b.type = "button";
    b.title = "On：把 prompt 和工作流嵌进图片（可拖回 ComfyUI）；Off：干净图片，适合分享/数据集。";
    b.addEventListener("click", () => { setValue(node, "save_metadata", on); refresh(); });
    embedSeg.append(b); embedButtons.set(on, b);
  }
  embedRow.append(el("span", "sg-save-label", "Embed workflow"), embedSeg);
  root.append(embedRow);

  // Preview 开关
  const pvRow = el("div", "sg-save-row");
  const pvSeg = el("div", "sg-save-seg");
  const pvButtons = new Map();
  for (const [on, text] of [[false, "Off"], [true, "On"]]) {
    const b = el("button", "", text);
    b.type = "button";
    b.title = "在节点上显示已保存的图片（纯显示，文件照存）。";
    b.addEventListener("click", () => { setValue(node, "preview", on); refresh(); });
    pvSeg.append(b); pvButtons.set(on, b);
  }
  pvRow.append(el("span", "sg-save-label", "Preview"), pvSeg);
  root.append(pvRow);

  root.addEventListener("pointerdown", (event) => {
    if (event.target.closest("button,input,select")) event.stopPropagation();
  });

  function refresh() {
    const dir = String(value(node, "output_dir", "") ?? "");
    if (document.activeElement !== folder) folder.value = dir;
    if (document.activeElement !== nameField) nameField.value = String(value(node, "filename_prefix", "") ?? "");
    if (state.savedPath) {
      const slash = state.savedPath.lastIndexOf("/");
      previewFolderLine.textContent = slash >= 0 ? `ComfyUI/output/${state.savedPath.slice(0, slash + 1)}` : "ComfyUI/output/";
      previewNameLine.textContent = state.savedPath.slice(slash + 1);
      preview.classList.add("saved");
    } else {
      previewFolderLine.textContent = `ComfyUI/output/${dir ? dir.replace(/\/+$/, "") + "/" : ""}`;
      previewNameLine.textContent = previewName(node);
      preview.classList.remove("saved");
    }
    for (const [key, chip] of chipButtons) chip.classList.toggle("on", Boolean(value(node, key, false)));
    const fmt = String(value(node, "format", "png"));
    for (const [key, b] of formatButtons) b.classList.toggle("on", key === fmt);
    const embed = Boolean(value(node, "save_metadata", true));
    for (const [on, b] of embedButtons) b.classList.toggle("on", on === embed);
    const pv = Boolean(value(node, "preview", true));
    for (const [on, b] of pvButtons) b.classList.toggle("on", on === pv);
  }
  state.refresh = refresh;

  const domWidget = node.addDOMWidget(WIDGET_NAME, WIDGET_NAME, root, {
    serialize: false,
    hideOnZoom: false,
  });
  domWidget.serialize = false;
  // 固定高度卡片（内容是静态行，不需要跟手 ResizeObserver）
  domWidget.computeSize = (width) => [Math.max(NODE_MIN_WIDTH, Number(width || node.size?.[0] || NODE_MIN_WIDTH)), CARD_HEIGHT + 16];
  domWidget.options.minNodeSize = [NODE_MIN_WIDTH, 160];

  // 隐藏的原生 widget 有回调变化时（如工作流加载恢复值）刷新卡片
  for (const name of HIDDEN) {
    const w = widget(node, name);
    if (!w) continue;
    const orig = w.callback;
    w.callback = function (...a) {
      const r = orig?.apply(this, a);
      state.savedPath = null;
      refresh();
      return r;
    };
  }
  const origOnCfg = node.onConfigure;
  node.onConfigure = function (...a) {
    const r = origOnCfg?.apply(this, a);
    setTimeout(() => {
      for (const name of HIDDEN) { const w = widget(this, name); if (w) hideWidget(w); }
      state.savedPath = null; refresh();
    }, 0);
    return r;
  };
  const origOnExec = node.onExecuted;
  node.onExecuted = function (output) {
    const r = origOnExec?.apply(this, arguments);
    const saved = output?.sg_saved_path?.[0];
    if (typeof saved === "string" && saved) { state.savedPath = saved; refresh(); }
    return r;
  };
  const origRemoved = node.onRemoved;
  node.onRemoved = function (...a) { closeBrowse(); return origRemoved?.apply(this, a); };

  refresh();
  node.setSize?.([Math.max(node.size?.[0] || 0, NODE_MIN_WIDTH), node.computeSize?.()[1] || CARD_HEIGHT]);
  return state;
}

app?.registerExtension?.({
  name: "Shanggu.SaveImage",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE_CLASS) return;
    const origOnNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = origOnNodeCreated?.call(this);
      if (!this.__sgSaveImage) buildCard(this);
      return r;
    };
  },
});
