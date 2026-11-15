// ComfyUI-Shanggu-WebGallery — 前端扩展
// 面板：网址抓取 / 粘贴源码 / 浏览器桥接；滚动加载；点选即等价于 LoadImage。
// 运行次数只由「单图 / 多图」一个开关决定：
//   单图 → 1 张图，运行 1 次；多图 → 每张图各提交 1 个独立 prompt，运行 N 次。
import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const NODE_TYPE = "ShangguWebImageGallery";
const SEL_SINGLE = "单图(点选替换)";
const SEL_MULTI = "多图(累加)";
const BASE = api.api_base || "";
// api.fetchApi 会自己补 api_base + "/api"，所以传纯路径；<img src> 走不带 /api 的直连路径
const EP = (p) => "/sg_gallery/" + p;
const FULL = (p) => BASE + "/sg_gallery/" + p;
const PRESET = "https://civitai.red/api/v1/images?limit=20";
// 检索页现在是「一个作品一张封面」，每页 60 张，这个上限约等于 25 页。
// 以前把同系列图片全部摊平，每页 200 张，两页就撞满 400 导致滚动停摆。
const MAX_TILES = 1500;

/* 面板注册表：全局 Run 拦截器用它找到画布上处于「多图 + 多选」的图库节点 */
const PANELS = new Map();

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
.sg-root button.sg-go{background:#18804a;border-color:#18804a;color:#fff;font-weight:600}
.sg-root button.sg-on{border-color:#4c9ffe;color:#8ec5ff;background:#203a55}
.sg-root button:disabled{opacity:.45;cursor:default}
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
.sg-quick label{display:inline-flex;align-items:center;gap:3px;cursor:pointer;opacity:1;white-space:nowrap}
.sg-quick input[type=checkbox]{width:auto;flex:none;padding:0;margin:0;accent-color:#1d6fc4}
.sg-quick label.sg-r18{color:#ff9d7a;font-weight:600}
.sg-modes{display:flex;gap:4px;align-items:center;flex:none;flex-wrap:wrap;font-size:10px}
.sg-modes>span{opacity:.65;margin-left:3px}
.sg-modes button{font-size:10px;padding:3px 7px}
.sg-status{flex:none;min-height:16px;font-size:10.5px;opacity:.85;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sg-status.sg-err{color:#ff8a80;opacity:1}
.sg-status.sg-run{color:#7fe3a5;opacity:1;font-weight:600}
/* 关键：显式固定行高。依赖 aspect-ratio 的隐式行高在溢出 100+ 格时会被
   浏览器压缩成十几 px 的横条（图片挤在一起），这里由 JS 按实际列宽设定。 */
.sg-grid{flex:1 1 auto;min-height:120px;min-width:0;overflow-y:auto;overflow-x:hidden;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(88px,1fr));
  grid-auto-rows:96px;grid-auto-flow:row;align-content:start;gap:5px;
  background:rgba(0,0,0,.18);border:1px solid var(--border-color,#444);border-radius:4px;padding:5px}
.sg-tile{position:relative;width:100%;height:100%;min-width:0;min-height:0;
  border:2px solid transparent;border-radius:4px;overflow:hidden;
  background:#111;cursor:pointer;display:flex;align-items:center;justify-content:center}
.sg-tile img{width:100%;height:100%;object-fit:cover;display:block}
.sg-tile.sg-sel{border-color:#3ea6ff;box-shadow:0 0 0 1px #3ea6ff inset}
.sg-tile.sg-broken{background:#3a2020;color:#ffb4a8;font-size:10px;text-align:center;padding:4px}
.sg-badge{position:absolute;top:2px;right:2px;min-width:17px;height:17px;line-height:17px;text-align:center;
  border-radius:9px;background:#3ea6ff;color:#fff;font-size:10px;font-weight:700;padding:0 3px}
.sg-info{position:absolute;left:0;right:0;bottom:0;background:rgba(0,0,0,.62);color:#ddd;
  font-size:9px;padding:1px 3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;opacity:0}
.sg-tile:hover .sg-info{opacity:1}
/* 检索页封面 + 点开后紧挨着的同系列图片 */
.sg-tile.sg-cover{cursor:zoom-in}
.sg-group-count{position:absolute;left:2px;top:2px;max-width:calc(100% - 4px);
  background:rgba(0,0,0,.72);color:#ffd479;font-size:9px;font-weight:700;
  padding:0 4px;border-radius:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sg-tile.sg-expanded .sg-group-count{background:#1d6fc4;color:#fff}
.sg-tile.sg-child{box-shadow:inset 3px 0 0 #e0a44a}
.sg-collapse{position:absolute;right:2px;bottom:2px;z-index:2;cursor:pointer;
  background:rgba(0,0,0,.78);color:#fff;font-size:9px;padding:1px 4px;border-radius:3px}
.sg-collapse:hover{background:#c0392b}
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
    return Array.isArray(v) ? v : [];
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

/* --------------------------------------------- 循环执行：一次 Run => N 次
 * ComfyUI 单次 prompt 是一张静态 DAG，节点本身没法「回头」重跑下游。
 * 真正跑 N 遍的唯一稳妥办法是提交 N 个独立 prompt。
 *
 * 触发条件只看「单图 / 多图」：单图天然就是 1 次，不拦截；多图且选中
 * 2 张以上才扇出。以前还有一个「逐张 / 批量」开关，那是重复且自相矛盾的
 * ——「批量一次」把 N 张塞进一个 IMAGE 批次里，工作流其实只跑 1 次。
 *
 * 拦截点在 app.queuePrompt（不是 api.queuePrompt）：每轮临时把
 * selected_files 改成单张，再串行调用原始 queuePrompt(…, 1, …)。
 * 每一轮都是完整的一次提交（graphToPrompt、beforeQueued、job 记录、
 * processingQueue 复位全部走原生流程），所以不会卡在进度状态机里。
 */
let fanoutBusy = false;

function findLoopTarget() {
  const graph = app.graph;
  const nodes = graph && (graph._nodes || graph.nodes);
  const alive = Array.isArray(nodes) ? nodes : null;
  for (const p of PANELS.values()) {
    if (!p) continue;
    if (alive && !alive.includes(p.node)) continue;
    // 单图 = 1 次运行（不拦截）；多图 + 2 张以上才拆成 N 次
    if (p.getSelectMode() === SEL_MULTI && p.getState().selected.length > 1) return p;
  }
  return null;
}

/** 运行提交成功后自动取消所有已点选的图片。
 *  本次提交经过的图库节点全部清空（工作流里放多个节点时也不会漏），
 *  但图库网格本身保留，用户可以接着点下一批，不用重新搜索。 */
function clearSubmittedSelections() {
  for (const p of PANELS.values()) {
    if (!p || typeof p.clearSelection !== "function") continue;
    if (p.getState().selected.length === 0) continue;
    try {
      p.clearSelection("已提交运行 · 选区已自动清空，可继续点选新图");
    } catch (e) {
      console.warn("[ShangguWebGallery] 清空选区失败:", e);
    }
  }
}

function installRunLoop() {
  if (app.__sgGalleryRunLoop) return;
  if (typeof app.queuePrompt !== "function") {
    console.warn("[ShangguWebGallery] app.queuePrompt 不可用，多图逐张执行不可用；请用单图模式或检查前端版本");
    return;
  }
  app.__sgGalleryRunLoop = true;
  const original = app.queuePrompt.bind(app);

  app.queuePrompt = async function (number, batchCount = 1, options = {}) {
    if (fanoutBusy) return original(number, batchCount, options);
    const target = findLoopTarget();
    let result = null;
    let submitted = false;

    if (!target) {
      // 单图（或只选了 1 张）：走原生提交，成功后照样清空选区
      result = await original(number, batchCount, options);
      submitted = true;
    } else {
      const files = target.getState().selected.slice();
      const n = files.length;
      // 插队(-1)要倒序提交，队列里的实际执行顺序才等于点选顺序
      const order = number === -1 ? files.slice().reverse() : files;
      let last = null, done = 0;
      fanoutBusy = true;
      try {
        target.progress(0, n);
        for (const one of order) {
          target.setOverride(JSON.stringify([one]));
          last = await original(number, 1, options);
          done += 1;
          target.progress(done, n);
        }
        result = last;
        submitted = true;
      } catch (err) {
        console.error("[ShangguWebGallery] 循环执行失败:", err);
        if (done === 0) {
          result = await original(number, batchCount, options);
          submitted = true;
        } else {
          // 已经提交了一部分，也算跑过，选区一样清空
          result = last;
          submitted = true;
        }
      } finally {
        target.setOverride(null);
        fanoutBusy = false;
        setTimeout(() => target.progress(0, 0), 2500);
      }
    }

    // 必须放在扇出的 finally 之后：override 已复位，这里写 "[]" 才不会被恢复覆盖
    if (submitted) clearSubmittedSelections();
    return result;
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
  const btnPresetAitag = el("button", "", "aitag.win");
  quick.append(btnPreset, btnPresetAitag);
  // 18+ 开关：aitag.win 的主要内容在 show_r18=1 里，默认开启
  const r18Label = el("label", "sg-r18", "18+");
  const r18Chk = el("input");
  r18Chk.type = "checkbox";
  r18Chk.checked = true;
  r18Chk.title = "开启后请求 aitag.win 的 18+ 作品（默认开启）；关闭只看全年龄内容";
  r18Label.title = r18Chk.title;
  r18Label.prepend(r18Chk);
  quick.appendChild(r18Label);
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

  /* --- 唯一的开关：单图 / 多图，同时决定运行次数 --- */
  const modeBar = el("div", "sg-modes");
  modeBar.appendChild(el("span", "", "选图"));
  const btnSingle = el("button", "", "单图");
  const btnMulti = el("button", "", "多图");
  modeBar.append(btnSingle, btnMulti);
  modeBar.appendChild(el("span", "", "单图=运行 1 次 · 多图=每张各跑 1 次"));
  root.appendChild(modeBar);

  const status = el("div", "sg-status", "就绪：填写网址后点「搜索」");
  const grid = el("div", "sg-grid");
  const footer = el("div", "sg-footer");
  const btnRun = el("button", "sg-go", "▶ 运行");
  const btnMore = el("button", "", "加载更多");
  const btnAll = el("button", "", "全选可见");
  const btnClear = el("button", "", "清空选择");
  footer.append(btnRun, btnMore, btnAll, btnClear);
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
    "提示：输入 https://aitag.win/ 或 https://civitai.red/images 会自动切到同站 JSON 接口；" +
    "aitag.win 的检索页每个作品只显示 1 张封面，点封面才在它后面紧挨着展开同系列图片，未点开的不显示；" +
    "网址下方的「18+」勾选框控制是否请求 aitag.win 的成人内容（默认开启）。" +
    "运行次数只看「单图 / 多图」：单图一次只跑 1 张；多图按点选顺序，每张图各提交一次独立运行，" +
    "队列里会依次出现 N 个任务，属正常现象。运行提交成功后会自动清空已选图片，可直接点下一批。");
  adv.appendChild(advHint);
  root.appendChild(adv);

  const setStatus = (msg, isErr = false, running = false) => {
    status.textContent = msg;
    status.classList.toggle("sg-err", !!isErr);
    status.classList.toggle("sg-run", !!running);
  };

  const w = (name) => (node.widgets || []).find((x) => x.name === name);

  const state = {
    sid: (node.properties.sg_session || ("sg" + Math.random().toString(36).slice(2, 10))),
    items: (node.properties.sg_items || []).slice(0, 600),
    childItems: [],      // 已点开的同系列图片（只显示在封面后面，不算检索页条目）
    selected: [],
    selectedByUrl: new Map(),
    hasMore: false,
    loading: false,
    page: 0,
    autoChained: 0,      // 滚动没被撑开时自动续拉的次数
    rewriteHint: "",
    truncated: false,
    override: null,      // 循环执行期间临时写入 prompt 的单张 JSON
    runTotal: 0,
    runDone: 0,
  };
  node.properties.sg_session = state.sid;

  function collectSettings() {
    const s = {};
    Object.entries(fields).forEach(([k, inp]) => { s[k] = inp.value; });
    s.safe_only = fields.safe_only.value === "yes";
    s.r18 = r18Chk.checked;
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
    // 老工作流没存过 r18，默认开启（用户要的就是站点 18+ 内容）
    r18Chk.checked = s.r18 === undefined ? true : !!s.r18;
  }

  const currentSerialized = () =>
    state.override != null ? state.override : JSON.stringify(state.selected);

  function syncWidget() {
    const ww = w("selected_files");
    if (ww && state.override == null) ww.value = JSON.stringify(state.selected);
    node.properties.sg_selected = state.selected;
    node.properties.sg_items = state.items.slice(0, 600);
    node.setDirtyCanvas(true, true);
    updateStatus();
  }

  function selectMode() {
    const sm = w("select_mode");
    return (sm && sm.value) || node.properties.sg_select_mode || SEL_SINGLE;
  }

  function syncModeButtons() {
    const single = selectMode() === SEL_SINGLE;
    btnSingle.classList.toggle("sg-on", single);
    btnMulti.classList.toggle("sg-on", !single);
    const n = Math.max(1, state.selected.length);
    btnRun.textContent = single ? "▶ 运行(1 次)" : "▶ 运行(" + n + " 次)";
    btnRun.title = single
      ? "把当前选中的这一张图当作 LoadImage，完整工作流运行 1 次"
      : "按点选顺序，每张图各提交 1 个独立 prompt，完整工作流运行 N 次";
  }

  function updateStatus() {
    if (state.runTotal > 0) {
      setStatus(`多图执行中 ${state.runDone}/${state.runTotal} · 每张图各跑一次完整工作流`, false, true);
      // 进度状态期间也要刷新按钮，否则此时切换单/多图会让运行次数文案过期
      syncModeButtons();
      return;
    }
    const single = selectMode() === SEL_SINGLE;
    const sm = single ? "单图(点哪个换哪个)" : "多图(可累加)";
    const run = single
      ? "运行 1 次"
      : `每张各运行 1 次(共 ${Math.max(1, state.selected.length)} 次)`;
    setStatus(
      `已加载 ${state.items.length} 张 · 已选 ${state.selected.length} 张 · ${sm} · ${run}` +
      (state.childItems.length ? " · 已展开 " + state.childItems.length + " 张同系列" : "") +
      (state.hasMore ? " · 向下滚动继续加载" : "") +
      (state.truncated ? " · 已达显示上限" : "") + state.rewriteHint
    );
    syncModeButtons();
  }

  /* 按实际列宽固定 grid 行高，保证每格都是正方形，绝不被压成横条 */
  function syncRowHeight() {
    let cw = 0;
    const first = grid.querySelector(".sg-tile");
    if (first) cw = first.offsetWidth || first.clientWidth || 0;
    if (!cw) {
      const inner = grid.clientWidth - 10;
      cw = Math.max(64, Math.min(140, Math.floor(inner / 4)));
    }
    const row = Math.max(64, Math.round(cw));
    if (grid.__sgRow !== row) {
      grid.__sgRow = row;
      grid.style.gridAutoRows = row + "px";
    }
    return row;
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

  /** 造一个缩略图格子。
   *  it.group_id 存在时是检索页封面（点击 = 展开同系列）；
   *  opts.child 时是展开出来的同系列图片（点击 = 正常点选）。 */
  function makeTile(it, opts = {}) {
    const tile = el("div", "sg-tile");
    tile.dataset.url = it.url;
    const gid = opts.child ? "" : (it.group_id || "");
    const total = Number(it.group_total || 0);
    tile.title = (it.title || "") + (it.prompt ? "\n" + it.prompt.slice(0, 200) : "") +
      (gid && total > 1 ? "\n▸ 点击展开这个作品的其余 " + (total - 1) + " 张图" : "");
    const img = el("img");
    img.loading = "lazy";
    const ref = it.referer ? "&referer=" + encodeURIComponent(it.referer) : "";
    img.src = FULL("thumb?session_id=" + encodeURIComponent(state.sid) +
                   "&w=320&url=" + encodeURIComponent(it.thumb || it.url)) + ref;
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
    if (gid) {
      tile.classList.add("sg-cover");
      tile.dataset.group = gid;
      tile.dataset.total = String(total);
      if (total > 1) tile.appendChild(el("div", "sg-group-count", "1/" + total + " 张"));
      tile.onclick = () => onCoverClick(it, tile);
    } else {
      if (opts.child) {
        tile.classList.add("sg-child");
        tile.dataset.groupChild = opts.groupId || "";
      }
      tile.onclick = () => toggleSelect(it);
    }
    return tile;
  }

  function appendItems(items, opts = {}) {
    for (const it of items) {
      if (!it || !it.url) continue;
      if (!opts.fromRestore && state.items.some((x) => x.url === it.url)) continue;
      if (state.items.length >= MAX_TILES) { state.truncated = true; break; }
      state.items.push(it);
      grid.appendChild(makeTile(it));
    }
    syncRowHeight();
    refreshBadges();
    updateStatus();
  }

  /** 点检索页封面：把该作品其余图片紧挨着插到封面后面，未点开的作品保持折叠。 */
  async function onCoverClick(it, tile) {
    if (tile.dataset.expanded === "1" || !it.group_id || Number(it.group_total || 0) <= 1) {
      // 已展开 / 普通图 / 单图作品：就是普通的点选，不必请求展开
      await toggleSelect(it);
      return;
    }
    if (tile.dataset.expanding === "1") return;
    const gid = String(it.group_id);
    tile.dataset.expanding = "1";
    setStatus("正在展开这个作品的全部图片…");
    let children = Array.isArray(it.children) ? it.children : [];
    if (!children.length) {
      try {
        const r = await postJSON(EP("expand"), {
          work_id: gid, session_id: state.sid,
          settings: collectSettings(), origin: (urlInput.value || "").trim(),
        });
        if (!r.ok) {
          setStatus(r.error || "展开失败", true);
          tile.dataset.expanding = "";
          return;
        }
        children = r.items || [];
      } catch (e) {
        setStatus("展开失败: " + e, true);
        tile.dataset.expanding = "";
        return;
      }
    }
    tile.dataset.expanding = "";
    const rest = children.filter((c) => c && c.url && c.url !== it.url);
    if (rest.length) {
      const anchor = tile.nextSibling;   // 一直插到同一个锚点前 => 顺序与作品页一致
      for (const c of rest) {
        if (grid.children.length >= MAX_TILES + 600) break;
        grid.insertBefore(makeTile(c, { child: true, groupId: gid }), anchor);
        state.childItems.push(c);
      }
      tile.dataset.expanded = "1";
      tile.classList.add("sg-expanded");
      const badge = tile.querySelector(".sg-group-count");
      if (badge) badge.textContent = "共 " + children.length + " 张";
      const btn = el("div", "sg-collapse", "× 收起");
      btn.onclick = (e) => { e.stopPropagation(); collapseGroup(tile, gid); };
      tile.appendChild(btn);
    }
    syncRowHeight();
    refreshBadges();
    // 保留原来的习惯：点一下就把这张封面载入工作流，同时展开同系列供挑选
    await toggleSelect(it);
  }

  function collapseGroup(tile, gid) {
    [...grid.children].forEach((n) => {
      if (n.dataset && n.dataset.groupChild === gid) {
        state.childItems = state.childItems.filter((x) => x.url !== n.dataset.url);
        n.remove();
      }
    });
    tile.dataset.expanded = "";
    tile.classList.remove("sg-expanded");
    const badge = tile.querySelector(".sg-group-count");
    if (badge) badge.textContent = "1/" + (tile.dataset.total || "?") + " 张";
    const btn = tile.querySelector(".sg-collapse");
    if (btn) btn.remove();
    syncRowHeight();
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
        referer: it.referer || "",
      });
      if (!r.ok) { setStatus("下载失败: " + (r.error || "未知错误"), true); return; }
      const entry = {
        filename: r.filename, url: it.url,
        prompt: it.prompt || "", id: it.id || "",
      };
      if (selectMode() === SEL_SINGLE) {
        // 单图开关：点哪个换哪个，永远只保留最后点的这一张
        state.selected = [entry];
        state.selectedByUrl = new Map([[it.url, entry]]);
      } else {
        state.selected.push(entry);
        state.selectedByUrl.set(it.url, entry);
      }
      syncWidget();
      refreshBadges();
    } catch (e) {
      setStatus("下载失败: " + e, true);
    }
  }

  function nearGridBottom() {
    return grid.scrollTop + grid.clientHeight >= grid.scrollHeight - 160;
  }

  async function doFetch(reset) {
    if (state.loading) return;
    if (!reset && (!state.hasMore || state.truncated)) return;
    state.loading = true;
    btnMore.disabled = true;
    if (reset) {
      grid.innerHTML = "";
      state.items = [];
      state.childItems = [];
      state.hasMore = true;
      state.page = 0;
      state.autoChained = 0;
      state.truncated = false;
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
        state.rewriteHint = r.rewritten ? " · 已自动改用该站的 JSON 接口" : "";
        appendItems(r.items || []);
        state.hasMore = !!r.has_more && !state.truncated;
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
    // 这一页没能撑长滚动条时，浏览器不会再发 scroll 事件，无限滚动就
    // 卡死在底部了（整页被去重 / 接口偶发空页都会这样）。主动再拉几页。
    if (!reset && state.hasMore && !state.truncated && nearGridBottom()) {
      state.autoChained += 1;
      if (state.autoChained <= 12) setTimeout(() => doFetch(false), 80);
    } else {
      state.autoChained = 0;
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
      if (!appendChk.checked) {
        grid.innerHTML = ""; state.items = []; state.childItems = []; state.truncated = false;
      }
      appendItems(r.items || []);
      state.hasMore = false;
      if (r.message && !(r.items || []).length) setStatus(r.message, false);
      else updateStatus();
    } catch (e) {
      setStatus("解析失败: " + e, true);
    }
  }

  grid.addEventListener("scroll", () => {
    state.autoChained = 0;         // 用户真的滚动了，续拉计数重新开始
    if (nearGridBottom()) doFetch(false);
  });

  function rememberUrl(value) {
    const ww = w("site_url");
    if (ww) ww.value = value;
    node.properties.sg_url = value;
  }
  btnSearch.onclick = () => { rememberUrl(urlInput.value.trim()); doFetch(true); };
  urlInput.onchange = () => rememberUrl(urlInput.value.trim());
  btnPreset.onclick = () => { urlInput.value = PRESET; rememberUrl(PRESET); doFetch(true); };
  btnPresetAitag.onclick = () => {
    const u = "https://aitag.win/";
    urlInput.value = u;
    r18Chk.checked = true;
    rememberUrl(u);
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

  /* --- 模式开关 --- */
  function setSelectMode(v) {
    const sm = w("select_mode");
    if (sm) sm.value = v;
    node.properties.sg_select_mode = v;
    if (v === SEL_SINGLE && state.selected.length > 1) {
      const keep = state.selected[0];
      state.selected = [keep];
      state.selectedByUrl = new Map(keep.url ? [[keep.url, keep]] : []);
      syncWidget();
      refreshBadges();
    }
    updateStatus();
  }
  btnSingle.onclick = () => setSelectMode(SEL_SINGLE);
  btnMulti.onclick = () => setSelectMode(SEL_MULTI);

  /* --- 运行按钮：与 ComfyUI 的 Run 走同一条链路，多图时自动扇出 --- */
  btnRun.onclick = async () => {
    if (!state.selected.length) { setStatus("请先点选至少一张图片", true); return; }
    if (state.runTotal > 0) { setStatus("本轮执行尚未结束，请稍候", true); return; }
    try {
      await app.queuePrompt(0, 1, {});
    } catch (e) {
      setStatus("执行失败: " + e, true);
    }
  };

  btnAll.onclick = async () => {
    // 「全选可见」隐含多选意图，自动切到多图模式
    if (selectMode() === SEL_SINGLE) setSelectMode(SEL_MULTI);
    for (const it of state.items.concat(state.childItems)) {
      if (grid.children.length >= MAX_TILES && state.truncated) break;
      if (!state.selectedByUrl.has(it.url)) await toggleSelect(it);
    }
  };
  /** 清空选区（保留图库网格）。note 只在「运行后自动清空」时传，手动清空不弹提示。 */
  function clearSelection(note) {
    if (state.override != null) return;   // 扇出进行中，别动选区
    state.selected = [];
    state.selectedByUrl.clear();
    const sw = w("selected_files");
    if (sw) sw.value = "[]";
    node.properties.sg_selected = [];
    syncWidget();          // 同步 widget 值 / properties / 状态栏
    refreshBadges();       // 去掉每个格子上的序号和选中框
    if (note) setStatus(note, false);
  }
  btnClear.onclick = () => clearSelection();

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
    if (node.properties.sg_select_mode) {
      const sm = w("select_mode");
      if (sm) sm.value = node.properties.sg_select_mode;
    }
    // 单图模式下旧工作流可能残留多张选中图，恢复时收敛到 1 张
    if (selectMode() === SEL_SINGLE && state.selected.length > 1) {
      state.selected = state.selected.slice(0, 1);
      state.selectedByUrl = new Map(
        state.selected.filter((x) => x.url).map((x) => [x.url, x])
      );
      const sw0 = w("selected_files");
      if (sw0) sw0.value = JSON.stringify(state.selected);
    }
    // 从工作流恢复时先清空 items，否则 appendItems 的去重会把它们全跳过，
    // 网格会变成空白；封面只存 group_id，点开时由 /expand 现场取回同系列。
    const savedItems = (node.properties.sg_items || []).slice(0, 600);
    state.items = [];
    state.childItems = [];
    grid.innerHTML = "";
    appendItems(savedItems, { fromRestore: true });
    syncModeButtons();
    updateStatus();
  }

  const origConfigure = node.onConfigure;
  node.onConfigure = function () {
    const r = origConfigure ? origConfigure.apply(this, arguments) : undefined;
    try { restore(); } catch (e) { console.warn("[ShangguWebGallery] restore:", e); }
    return r;
  };

  // 隐藏只给程序用的 widget（值仍会随工作流保存并进入 prompt）
  // multi_mode 是旧版「逐张 / 批量」留下的占位参数，值已完全不被读取，
  // 保留它只为了让老工作流的 widgets_values 顺序不错位。
  ["selected_files", "site_url", "multi_mode"].forEach((name) => {
    const ww = w(name);
    if (ww) ww.hidden = true;
  });
  // graphToPrompt 优先读 serializeValue；循环执行期间必须让它读到单张覆盖值
  const selWidget = w("selected_files");
  if (selWidget) {
    selWidget.serializeValue = () => currentSerialized();
    selWidget.beforeQueued = () => { selWidget.value = currentSerialized(); };
  }

  // 节点上的原始 combo 也保持双向同步
  const selModeWidget = w("select_mode");
  if (selModeWidget) {
    const origCb = selModeWidget.callback;
    selModeWidget.callback = function () {
      if (origCb) origCb.apply(this, arguments);
      setSelectMode(selModeWidget.value);
    };
  }

  /* 供全局 Run 拦截器使用的接口 */
  const panelApi = {
    node,
    grid,                 // 容器尺寸变化时可手动校准
    syncRowHeight,
    getState: () => state,
    getSelectMode: selectMode,
    clearSelection: (note) => clearSelection(note),
    setOverride(v) {
      state.override = v;
      const sw = w("selected_files");
      if (sw) sw.value = currentSerialized();
    },
    progress(done, total) {
      state.runDone = done;
      state.runTotal = total;
      if (total > 0) {
        setStatus(`多图执行中 ${done}/${total} · 每张图各跑一次完整工作流`, false, true);
      } else {
        updateStatus();
      }
    },
  };
  const key = node.id != null ? String(node.id) : "sg" + Math.random().toString(36).slice(2);
  PANELS.set(key, panelApi);
  const origRemoved = node.onRemoved;
  node.onRemoved = function () {
    PANELS.delete(key);
    if (node.__sgRO) { try { node.__sgRO.disconnect(); } catch (e) {} }
    return origRemoved ? origRemoved.apply(this, arguments) : undefined;
  };

  // 容器宽度变化时（列数变化）重新计算行高
  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => syncRowHeight());
    ro.observe(root);
    node.__sgRO = ro;
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
    try { if (dw.width !== nw) dw.width = nw; } catch (e) { /* 只读属性则走兜底 */ }
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
    installRunLoop();
  },
  async nodeCreated(node) {
    const cls = node.comfyClass || (node.constructor && node.constructor.comfyClass) || node.type;
    if (cls !== NODE_TYPE) return;
    injectCSS();
    // 先定好最终尺寸，再创建 DOM widget，让覆盖层按 460 而不是默认 210 初始化
    node.setSize([460, 720]);
    const root = buildPanel(node);
    const dw = node.addDOMWidget("gallery_panel", "sg_gallery", root, {
      serialize: false,
      hideOnZoom: false,
      getMinHeight: () => 520,
    });
    dw.serialize = false;
    dw.computeLayoutSize = () => ({ minHeight: 520, minWidth: 420 });
    dw.options = dw.options || {};
    dw.options.minNodeSize = [420, 600];
    installWidthGuard(node, dw, root);
    node.setDirtyCanvas(true, true);
  },
});
