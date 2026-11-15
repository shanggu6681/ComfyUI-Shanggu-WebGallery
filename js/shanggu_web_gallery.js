// ComfyUI-Shanggu-WebGallery — 前端扩展
// 面板：网址抓取 / 粘贴源码 / 浏览器桥接；滚动加载；点选即等价于 LoadImage。
import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const NODE_TYPE = "ShangguWebImageGallery";
const MODE_QUEUE = "逐张完整执行";
const BASE = api.api_base || "";
// api.fetchApi 会自己补 api_base + "/api"，所以传纯路径；<img src> 走不带 /api 的直连路径
const EP = (p) => "/sg_gallery/" + p;
const FULL = (p) => BASE + "/sg_gallery/" + p;
const PRESET = "https://civitai.red/api/v1/images?limit=20";

/* ------------------------------------------------------------------ 样式 */
const CSS = `
.sg-root{box-sizing:border-box;display:flex;flex-direction:column;gap:6px;width:100%;height:100%;
  padding:8px;font:12px/1.45 system-ui,"Microsoft YaHei",sans-serif;color:var(--fg-color,#e6e6e6);}
.sg-root *{box-sizing:border-box}
.sg-root button,.sg-root input,.sg-root textarea,.sg-root select{
  font:inherit;color:var(--fg-color,#e6e6e6);background:var(--comfy-input-bg,#2b2b2b);
  border:1px solid var(--border-color,#4a4a4a);border-radius:4px;padding:4px 6px;outline:none}
.sg-root button{cursor:pointer;background:var(--comfy-menu-bg,#353535);white-space:nowrap}
.sg-root button:hover{border-color:#4c9ffe;color:#fff}
.sg-root button.sg-primary{background:#1d6fc4;border-color:#1d6fc4;color:#fff;font-weight:600}
.sg-root input:focus,.sg-root textarea:focus{border-color:#4c9ffe}
.sg-tabs{display:flex;gap:4px;flex:none}
.sg-tabs button{flex:1;padding:4px 2px;font-size:11px;opacity:.75}
.sg-tabs button.sg-on{opacity:1;border-color:#4c9ffe;color:#8ec5ff}
.sg-pane{display:flex;flex-direction:column;gap:5px;flex:none}
.sg-pane[hidden]{display:none}
.sg-row{display:flex;gap:5px;align-items:center}
.sg-row input{flex:1;min-width:0}
.sg-root textarea{width:100%;height:64px;resize:vertical;font-size:11px}
.sg-quick{display:flex;gap:4px;align-items:center;font-size:10px;opacity:.8;flex-wrap:wrap}
.sg-quick button{font-size:10px;padding:2px 5px}
.sg-status{flex:none;min-height:16px;font-size:10.5px;opacity:.85;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sg-status.sg-err{color:#ff8a80;opacity:1}
.sg-grid{flex:1;min-height:120px;overflow-y:auto;overflow-x:hidden;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(88px,1fr));gap:5px;align-content:start;
  background:rgba(0,0,0,.18);border:1px solid var(--border-color,#444);border-radius:4px;padding:5px}
.sg-tile{position:relative;aspect-ratio:1/1;border:2px solid transparent;border-radius:4px;overflow:hidden;
  background:#111;cursor:pointer;display:flex;align-items:center;justify-content:center}
.sg-tile img{width:100%;height:100%;object-fit:cover;display:block}
.sg-tile.sg-sel{border-color:#3ea6ff;box-shadow:0 0 0 1px #3ea6ff inset}
.sg-tile.sg-broken{background:#3a2020;color:#ffb4a8;font-size:10px;text-align:center;padding:4px}
.sg-badge{position:absolute;top:2px;right:2px;min-width:17px;height:17px;line-height:17px;text-align:center;
  border-radius:9px;background:#3ea6ff;color:#fff;font-size:10px;font-weight:700;padding:0 3px}
.sg-info{position:absolute;left:0;right:0;bottom:0;background:rgba(0,0,0,.62);color:#ddd;
  font-size:9px;padding:1px 3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;opacity:0}
.sg-tile:hover .sg-info{opacity:1}
.sg-footer{display:flex;gap:4px;flex:none}
.sg-footer button{flex:1;font-size:10.5px;padding:4px 2px}
.sg-adv{flex:none;font-size:10.5px;max-height:150px;overflow:auto}
.sg-adv summary{cursor:pointer;opacity:.8;padding:2px 0}
.sg-adv-grid{display:grid;grid-template-columns:78px 1fr;gap:3px 5px;padding:4px 0;align-items:center}
.sg-adv-grid label{opacity:.8;white-space:nowrap}
.sg-adv-grid input,.sg-adv-grid select{width:100%;font-size:10.5px;padding:2px 4px}
.sg-hint{font-size:10px;opacity:.75;line-height:1.5}
.sg-hint code{color:#8ec5ff}
.sg-bookmark{display:inline-block;margin-top:2px;color:#8ec5ff;text-decoration:underline;cursor:grab}
`;

function injectCSS() {
  if (document.getElementById("sg-gallery-css")) return;
  const s = document.createElement("style");
  s.id = "sg-gallery-css";
  s.textContent = CSS;
  document.head.appendChild(s);
}

/* ------------------------------------------------------------------ 工具 */
async function postJSON(path, body) {
  const r = await api.fetchApi(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs: 120000,
  });
  return await r.json();
}

function parseFiles(raw) {
  try {
    const v = JSON.parse(raw || "[]");
    if (Array.isArray(v)) return v;
    return [];
  } catch (e) {
    return [];
  }
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

/* 书签脚本：在你自己的浏览器页面上运行，把当前页所有图片网址复制到剪贴板 */
function sgBridgeBookmarklet() {
  var seen = {}, urls = [];
  function add(u) {
    if (!u) return;
    try { u = new URL(u, location.href).href; } catch (e) { return; }
    if (!/^https?:/.test(u)) return;
    if (/\.svg(\?|$)/i.test(u)) return;
    if (/(logo|icon|avatar|favicon|sprite|placeholder|1x1)/i.test(u)) return;
    if (!seen[u]) { seen[u] = 1; urls.push(u); }
  }
  function scanImg(n) {
    add(n.currentSrc || n.src);
    ["data-src", "data-original", "data-lazy-src", "data-url", "data-image"].forEach(function (a) {
      add(n.getAttribute(a));
    });
    var ss = n.getAttribute("srcset") || n.getAttribute("data-srcset");
    if (ss) ss.split(",").forEach(function (p) { add(p.trim().split(/\s+/)[0]); });
  }
  document.querySelectorAll("img,source").forEach(scanImg);
  document.querySelectorAll("a[href]").forEach(function (a) {
    if (/\.(jpe?g|png|webp|gif|avif|bmp)(\?|$)/i.test(a.href)) add(a.href);
  });
  document.querySelectorAll('[style*="background"]').forEach(function (n) {
    var m = /url\(["']?([^"')]+)/.exec(n.getAttribute("style") || "");
    if (m) add(m[1]);
  });
  var text = urls.join("\n");
  function fallback(t) {
    var ta = document.createElement("textarea");
    ta.value = t; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    ta.remove();
    alert("已复制 " + urls.length + " 个图片网址，去 ComfyUI 节点的「粘贴源码」里 Ctrl+V");
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () {
      alert("已复制 " + urls.length + " 个图片网址，去 ComfyUI 节点的「粘贴源码」里 Ctrl+V");
    }, function () { fallback(text); });
  } else fallback(text);
}
const BOOKMARKLET = "javascript:(" + sgBridgeBookmarklet.toString() + ")()";

/* ------------------------------------------------- 一次 Run 扇出 N 次执行 */
function installQueueFanout() {
  if (api.__sgGalleryFanout) return;
  api.__sgGalleryFanout = true;
  const original = api.queuePrompt.bind(api);
  api.queuePrompt = async function (number, payload, options) {
    try {
      const output = payload && payload.output;
      if (!output) return original(number, payload, options);
      let targetId = null, files = null;
      for (const [id, n] of Object.entries(output)) {
        if (!n || n.class_type !== NODE_TYPE) continue;
        if (n.inputs && n.inputs.multi_mode !== MODE_QUEUE) continue;
        const f = parseFiles(n.inputs && n.inputs.selected_files);
        if (f.length > 1) { targetId = id; files = f; break; }
      }
      if (!targetId) return original(number, payload, options);

      // 插队模式(-1)要倒序提交，队列里的执行顺序才和点选顺序一致
      const order = number === -1 ? files.slice().reverse() : files;
      let last = null;
      for (const one of order) {
        const clone = JSON.parse(JSON.stringify(output));
        clone[targetId].inputs.selected_files = JSON.stringify([one]);
        last = await original(number, { output: clone, workflow: payload.workflow }, options);
      }
      return last || {};
    } catch (err) {
      console.error("[ShangguWebGallery] 逐张排队失败，回退为普通执行:", err);
      return original(number, payload, options);
    }
  };
}

/* ------------------------------------------------------------------ 面板 */
function buildPanel(node) {
  const root = el("div", "sg-root");

  // 阻止事件冒泡到画布，否则拖节点/缩放画布/快捷键会抢走输入
  ["pointerdown", "mousedown", "mouseup", "click", "dblclick", "contextmenu", "wheel", "keydown"].forEach((ev) => {
    root.addEventListener(ev, (e) => e.stopPropagation(), { passive: true });
  });

  const tabs = el("div", "sg-tabs");
  const tabDefs = [["url", "网址抓取"], ["paste", "粘贴源码"], ["bridge", "浏览器桥接"]];
  const tabBtns = {};
  const panes = {};
  tabDefs.forEach(([key, label]) => {
    const b = el("button", key === "url" ? "sg-on" : "", label);
    b.onclick = () => {
      Object.entries(tabBtns).forEach(([k, v]) => v.classList.toggle("sg-on", k === key));
      Object.entries(panes).forEach(([k, v]) => (v.hidden = k !== key));
    };
    tabBtns[key] = b;
    tabs.appendChild(b);
  });
  root.appendChild(tabs);

  /* --- 网址抓取 --- */
  const paneUrl = el("div", "sg-pane");
  const rowUrl = el("div", "sg-row");
  const urlInput = el("input");
  urlInput.type = "text";
  urlInput.placeholder = "https://… 页面或 JSON API 地址";
  const btnSearch = el("button", "sg-primary", "搜索");
  rowUrl.append(urlInput, btnSearch);
  const quick = el("div", "sg-quick");
  quick.appendChild(el("span", "", "快捷:"));
  const btnPreset = el("button", "", "civitai.red 图库(API)");
  quick.appendChild(btnPreset);
  paneUrl.append(rowUrl, quick);
  panes.url = paneUrl;

  /* --- 粘贴源码 --- */
  const panePaste = el("div", "sg-pane");
  const pasteArea = el("textarea");
  pasteArea.placeholder = "粘贴 HTML 源码 / JSON 接口返回 / 每行一个图片网址";
  const pasteRow = el("div", "sg-row");
  const appendLabel = el("label", "", "追加到现有列表");
  const appendChk = el("input");
  appendChk.type = "checkbox";
  appendLabel.prepend(appendChk);
  const btnParse = el("button", "sg-primary", "解析并加入图库");
  pasteRow.append(appendLabel, btnParse);
  panePaste.append(pasteArea, pasteRow);
  panePaste.hidden = true;
  panes.paste = panePaste;

  /* --- 浏览器桥接 --- */
  const paneBridge = el("div", "sg-pane");
  const hint = el("div", "sg-hint");
  hint.innerHTML =
    "有些站点有 Cloudflare / 必须登录，ComfyUI 后台直接抓不到。" +
    "把下面的链接拖到书签栏，在该网站页面上点一下，它会复制当前页所有图片网址；" +
    "再回到「粘贴源码」Ctrl+V 即可（可先滚动网页加载更多，重复抓取时勾选「追加」）。";
  const bmLink = el("a", "sg-bookmark", "📌 拖我到书签栏：复制本页图片网址");
  bmLink.href = BOOKMARKLET;
  bmLink.onclick = (e) => e.preventDefault();
  const btnCopyBM = el("button", "", "复制书签代码");
  const bmRow = el("div", "sg-row");
  bmRow.append(btnCopyBM);
  paneBridge.append(hint, bmLink, bmRow);
  paneBridge.hidden = true;
  panes.bridge = paneBridge;

  root.append(paneUrl, panePaste, paneBridge);

  const status = el("div", "sg-status", "就绪：填写网址后点「搜索」");
  const grid = el("div", "sg-grid");
  const footer = el("div", "sg-footer");
  const btnMore = el("button", "", "加载更多");
  const btnAll = el("button", "", "全选可见");
  const btnClear = el("button", "", "清空选择");
  footer.append(btnMore, btnAll, btnClear);
  root.append(status, grid, footer);

  /* --- 高级设置 --- */
  const adv = el("details", "sg-adv");
  adv.appendChild(el("summary", "", "高级设置（Cookie / 代理 / 分页 / 正则）"));
  const advGrid = el("div", "sg-adv-grid");
  const fields = {};
  const defs = [
    ["cookie", "Cookie", "留空；被 Cloudflare/登录拦截时，从浏览器 DevTools 复制整条 Cookie"],
    ["ua", "User-Agent", "留空用默认 Chrome UA；填 Cookie 时 UA 要和浏览器一致"],
    ["referer", "Referer", "留空自动用站点根地址"],
    ["proxy", "代理", "http://127.0.0.1:10808 或 socks5://127.0.0.1:10808"],
    ["page_template", "分页模板", "静态站翻页用，如 ?page={page}；JSON API 一般留空自动用 nextPage"],
    ["url_regex", "URL 正则", "可选。特殊站点自定义提取规则，第一个捕获组是图片地址"],
    ["per_page", "每页上限", "200"],
    ["safe_only", "内容过滤", "yes"],
  ];
  defs.forEach(([k, label, ph]) => {
    advGrid.appendChild(el("label", "", label));
    let inp;
    if (k === "safe_only") {
      inp = el("select");
      inp.innerHTML = '<option value="yes">跳过标记为 NSFW 的图</option><option value="no">不过滤</option>';
      inp.dataset.kind = "select";
    } else {
      inp = el("input");
      inp.type = "text";
      inp.placeholder = ph;
      if (k === "per_page") inp.value = "200";
    }
    inp.dataset.key = k;
    fields[k] = inp;
    advGrid.appendChild(inp);
  });
  adv.appendChild(advGrid);
  const advHint = el("div", "sg-hint",
    "提示：输入 https://civitai.red/images 会自动切到同站 JSON 接口（网页版有 Cloudflare）；" +
    "其他站点若被拦，可填 Cookie、用「粘贴源码」，或用「浏览器桥接」书签。");
  adv.appendChild(advHint);
  root.appendChild(adv);

  const setStatus = (msg, isErr = false) => {
    status.textContent = msg;
    status.classList.toggle("sg-err", !!isErr);
  };

  const w = (name) => (node.widgets || []).find((x) => x.name === name);

  const state = {
    sid: (node.properties.sg_session || ("sg" + Math.random().toString(36).slice(2, 10))),
    items: (node.properties.sg_items || []).slice(0, 600),
    selected: [],
    selectedByUrl: new Map(),
    hasMore: false,
    loading: false,
    page: 0,
    rewriteHint: "",
  };
  node.properties.sg_session = state.sid;

  function collectSettings() {
    const s = {};
    Object.entries(fields).forEach(([k, inp]) => {
      s[k] = inp.value;
    });
    s.safe_only = fields.safe_only.value === "yes";
    try { s.per_page = Math.max(1, Math.min(400, parseInt(s.per_page || "200", 10))); }
    catch (e) { s.per_page = 200; }
    node.properties.sg_settings = s;
    return s;
  }

  function restoreSettings() {
    const s = node.properties.sg_settings || {};
    Object.entries(fields).forEach(([k, inp]) => {
      if (s[k] !== undefined && s[k] !== null) {
        inp.value = k === "safe_only" ? (s[k] === false ? "no" : "yes") : String(s[k]);
      }
    });
  }

  function syncWidget() {
    const ww = w("selected_files");
    if (ww) ww.value = JSON.stringify(state.selected);
    node.properties.sg_selected = state.selected;
    node.properties.sg_items = state.items.slice(0, 600);
    node.setDirtyCanvas(true, true);
    updateStatus();
  }

  function updateStatus() {
    const mode = (w("multi_mode") || {}).value || "批量一次执行";
    setStatus(
      `已加载 ${state.items.length} 张 · 已选 ${state.selected.length} 张` +
      (state.hasMore ? " · 向下滚动继续加载" : "") +
      ` · ${mode === MODE_QUEUE ? "逐张完整执行(跑 " + state.selected.length + " 次)" : "批量一次执行"}` +
      state.rewriteHint
    );
  }

  function refreshBadges() {
    [...grid.children].forEach((tile) => {
      const u = tile.dataset.url;
      const entry = state.selectedByUrl.get(u);
      const idx = entry ? state.selected.indexOf(entry) : -1;
      tile.classList.toggle("sg-sel", idx >= 0);
      let b = tile.querySelector(".sg-badge");
      if (idx >= 0) {
        if (!b) { b = el("div", "sg-badge"); tile.appendChild(b); }
        b.textContent = String(idx + 1);
      } else if (b) b.remove();
    });
  }

  function appendItems(items) {
    items.forEach((it) => {
      if (!it || !it.url) return;
      if (state.items.some((x) => x.url === it.url)) return;
      state.items.push(it);
      const tile = el("div", "sg-tile");
      tile.dataset.url = it.url;
      tile.title = (it.title || "") + (it.prompt ? "\n" + it.prompt.slice(0, 200) : "");
      const img = el("img");
      img.loading = "lazy";
      img.src = FULL("thumb?session_id=" + encodeURIComponent(state.sid) +
                     "&w=320&url=" + encodeURIComponent(it.thumb || it.url));
      img.onerror = () => {
        img.remove();
        tile.classList.add("sg-broken");
        tile.textContent = "缩略图加载失败\n(原图仍可点选)";
      };
      tile.appendChild(img);
      if (it.title || it.width) {
        tile.appendChild(el("div", "sg-info",
          (it.title ? it.title + "  " : "") + (it.width ? it.width + "×" + it.height : "")));
      }
      tile.onclick = () => toggleSelect(it);
      grid.appendChild(tile);
    });
    refreshBadges();
    updateStatus();
  }

  async function toggleSelect(it) {
    if (state.selectedByUrl.has(it.url)) {
      const entry = state.selectedByUrl.get(it.url);
      state.selectedByUrl.delete(it.url);
      state.selected = state.selected.filter((x) => x !== entry);
      syncWidget();
      refreshBadges();
      return;
    }
    setStatus("正在下载选中图片到 input 目录…");
    try {
      const r = await postJSON(EP("select"), {
        url: it.url, session_id: state.sid, settings: collectSettings(),
      });
      if (!r.ok) { setStatus("下载失败: " + (r.error || "未知错误"), true); return; }
      const entry = {
        filename: r.filename, url: it.url,
        prompt: it.prompt || "", id: it.id || "",
      };
      state.selected.push(entry);
      state.selectedByUrl.set(it.url, entry);
      syncWidget();
      refreshBadges();
    } catch (e) {
      setStatus("下载失败: " + e, true);
    }
  }

  async function doFetch(reset) {
    if (state.loading) return;
    if (!reset && !state.hasMore) return;
    state.loading = true;
    btnMore.disabled = true;
    if (reset) {
      grid.innerHTML = "";
      state.items = [];
      state.hasMore = true;
      state.page = 0;
    }
    setStatus("正在抓取网页…");
    try {
      const r = await postJSON(EP("fetch"), {
        session_id: state.sid,
        url: urlInput.value.trim(),
        reset: !!reset,
        settings: collectSettings(),
      });
      if (!r.ok) {
        setStatus(r.error || "抓取失败", true);
        state.hasMore = false;
      } else {
        state.rewriteHint = r.rewritten ? " · 网页被拦截，已自动改用 JSON 接口" : "";
        appendItems(r.items || []);
        state.hasMore = !!r.has_more;
        state.page = r.page || 0;
        if (r.message && !(r.items || []).length) setStatus(r.message, false);
        else updateStatus();
      }
    } catch (e) {
      setStatus("请求失败: " + e, true);
      state.hasMore = false;
    } finally {
      state.loading = false;
      btnMore.disabled = false;
    }
  }

  async function doParse() {
    const raw = pasteArea.value;
    if (!raw.trim()) { setStatus("请先粘贴内容", true); return; }
    setStatus("正在解析…");
    try {
      const r = await postJSON(EP("parse"), {
        session_id: state.sid,
        raw: raw,
        base_url: urlInput.value.trim(),
        reset: !appendChk.checked,
        settings: collectSettings(),
      });
      if (!r.ok) { setStatus(r.error || "解析失败", true); return; }
      if (!appendChk.checked) { grid.innerHTML = ""; state.items = []; }
      appendItems(r.items || []);
      state.hasMore = false;
      if (r.message && !(r.items || []).length) setStatus(r.message, false);
      else updateStatus();
    } catch (e) {
      setStatus("解析失败: " + e, true);
    }
  }

  grid.addEventListener("scroll", () => {
    if (grid.scrollTop + grid.clientHeight >= grid.scrollHeight - 160) doFetch(false);
  });

  function rememberUrl(value) {
    const ww = w("site_url");
    if (ww) ww.value = value;
    node.properties.sg_url = value;
  }
  btnSearch.onclick = () => {
    rememberUrl(urlInput.value.trim());
    doFetch(true);
  };
  urlInput.onchange = () => rememberUrl(urlInput.value.trim());
  btnPreset.onclick = () => {
    urlInput.value = PRESET;
    rememberUrl(PRESET);
    doFetch(true);
  };
  btnMore.onclick = () => doFetch(false);
  btnParse.onclick = doParse;
  btnCopyBM.onclick = async () => {
    try {
      await navigator.clipboard.writeText(BOOKMARKLET);
      setStatus("书签代码已复制，新建一个书签并把代码粘到网址栏即可");
    } catch (e) { setStatus("复制失败，请手动展开高级设置查看", true); }
  };
  btnAll.onclick = async () => {
    for (const it of state.items) {
      if (!state.selectedByUrl.has(it.url)) await toggleSelect(it);
    }
  };
  btnClear.onclick = () => {
    state.selected = [];
    state.selectedByUrl.clear();
    syncWidget();
    refreshBadges();
  };

  // 从工作流恢复：widget 值与 properties 双保险
  function restore() {
    const ww = w("selected_files");
    const saved = parseFiles(ww ? ww.value : "[]");
    const props = Array.isArray(node.properties.sg_selected) ? node.properties.sg_selected : [];
    const chosen = saved.length ? saved : props;
    state.selected = chosen.filter((x) => x && (x.filename || x.url));
    state.selectedByUrl = new Map(state.selected.filter((x) => x.url).map((x) => [x.url, x]));
    const sw = w("site_url");
    urlInput.value = (sw && sw.value) || node.properties.sg_url || PRESET;
    restoreSettings();
    state.items = (node.properties.sg_items || []).slice(0, 600);
    grid.innerHTML = "";
    appendItems(state.items);
    updateStatus();
  }

  const origConfigure = node.onConfigure;
  node.onConfigure = function () {
    const r = origConfigure ? origConfigure.apply(this, arguments) : undefined;
    try { restore(); } catch (e) { console.warn("[ShangguWebGallery] restore:", e); }
    return r;
  };

  // 隐藏只给程序用的 widget（值仍会随工作流保存并进入 prompt）
  ["selected_files", "site_url"].forEach((name) => {
    const ww = w(name);
    if (ww) ww.hidden = true;
  });
  // graphToPrompt 优先读 serializeValue，保证隐藏 widget 的值一定进 prompt
  const selWidget = w("selected_files");
  if (selWidget) {
    selWidget.serializeValue = () => JSON.stringify(state.selected);
    selWidget.beforeQueued = () => { selWidget.value = JSON.stringify(state.selected); };
  }

  const modeWidget = w("multi_mode");
  if (modeWidget) {
    const origCb = modeWidget.callback;
    modeWidget.callback = function () {
      if (origCb) origCb.apply(this, arguments);
      updateStatus();
    };
  }

  restore();
  return root;
}

/* ---------------------------------------------- 修复 DOM 覆盖层「半宽」
 * ComfyUI 新版前端把 DOM widget 画在独立 HTML 覆盖层上，覆盖层宽度由
 *   (widget.width ?? node.width) - margin*2
 * 决定，而 widget.width 常常在 widget 创建时按节点当时的默认宽度（约 210）
 * 缓存下来。之后节点被 setSize 拉宽、从工作流恢复尺寸、或面板内容重排触发
 * 重新布局，这个值都不会跟着更新 —— 面板就停在半宽，右边留一大块空白。
 * 这是新版前端的通用毛病，凡是带 DOM 面板的节点都会偶发。
 * 对策：让 widget.width 始终跟随节点真实宽度，并在启动初期校验覆盖层实际
 * 宽度，前端若仍算错就直接把容器撑开。
 */
function installWidthGuard(node, dw, root) {
  const targetWidth = () => {
    const a = node.size && node.size[0];
    if (typeof a === "number" && a > 0) return a;
    const b = node.width;
    return typeof b === "number" && b > 0 ? b : 0;
  };
  const sync = () => {
    const nw = targetWidth();
    if (!nw) return;
    // 主修法：覆盖层宽度公式读的就是这个字段
    try { if (dw.width !== nw) dw.width = nw; } catch (e) { /* 只读属性则走兜底 */ }
    // 部分版本还会拿 minNodeSize 参与布局计算（只在变化时写，避免每帧触发重排）
    const mns = dw.options && dw.options.minNodeSize;
    if (Array.isArray(mns) && mns[0] !== nw) {
      dw.options.minNodeSize = [nw, mns[1] || 560];
    }
  };
  sync();

  const prevDraw = node.onDrawForeground;
  node.onDrawForeground = function () {
    sync();
    return prevDraw ? prevDraw.apply(this, arguments) : undefined;
  };
  const prevResize = node.onResize;
  node.onResize = function () {
    const r = prevResize ? prevResize.apply(this, arguments) : undefined;
    sync();
    return r;
  };

  // 兜底：前端每帧会重写覆盖层的 inline width，若它仍按旧值算，就强行纠正
  // 容器宽度（容器处在 transform:scale() 里，px 值用画布单位）。
  let ticks = 0;
  const timer = setInterval(() => {
    sync();
    const host = root.parentElement;
    const nw = targetWidth();
    if (host && nw > 0) {
      const scale = (app.canvas && app.canvas.ds && app.canvas.ds.scale) || 1;
      const expectCss = (nw - 20) * scale;
      const actual = host.getBoundingClientRect().width;
      if (expectCss > 0 && actual > 0 && actual < expectCss * 0.8) {
        host.style.width = nw - 20 + "px";
        host.style.maxWidth = "none";
      }
    }
    if (++ticks >= 40) clearInterval(timer);
  }, 150);
}

/* ------------------------------------------------------------------ 注册 */
app.registerExtension({
  name: "Shanggu.WebImageGallery",
  async setup() {
    injectCSS();
    installQueueFanout();
  },
  async nodeCreated(node) {
    const cls = node.comfyClass || (node.constructor && node.constructor.comfyClass) || node.type;
    if (cls !== NODE_TYPE) return;
    injectCSS();
    // 先定好最终尺寸，再创建 DOM widget，让覆盖层按 460 而不是默认 210 初始化
    node.setSize([460, 660]);
    const root = buildPanel(node);
    const dw = node.addDOMWidget("gallery_panel", "sg_gallery", root, {
      serialize: false,
      hideOnZoom: false,
      getMinHeight: () => 480,
    });
    dw.serialize = false;
    // 0.45 新版前端用 computeLayoutSize / minNodeSize 计算 DOM widget 占位
    dw.computeLayoutSize = () => ({ minHeight: 480, minWidth: 420 });
    dw.options = dw.options || {};
    dw.options.minNodeSize = [420, 560];
    installWidthGuard(node, dw, root);
    node.setDirtyCanvas(true, true);
  },
});
