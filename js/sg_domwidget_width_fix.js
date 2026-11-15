// ComfyUI 新版前端 DOM 面板「只占节点一半宽」通用修复（Shanggu）
//
// 现象：节点的普通 widget 是画在 canvas 上的，永远跟着节点宽度；而 DOM 面板
// （预览图 / Markdown 笔记 / 视频 / 自定义 HTML 面板）画在一层独立 HTML 覆盖层
// 上，覆盖层宽度由 (widget.width ?? node.width) - margin*2 决定。widget.width
// 往往在 widget 创建时按节点默认宽度（约 210）缓存，之后节点被拉宽、从工作流
// 恢复尺寸、或面板内容重排触发重新布局都不会同步，于是面板停在半宽。
//
// 本扩展每 250ms 巡检一次，只修「缓存宽度明显偏窄 + 节点明显偏宽」的 DOM
// widget，把它们同步为节点真实宽度。不会动刻意的窄面板布局。
//
// 关闭：localStorage.setItem("sg_domwidth_fix", "off") 后刷新页面
// 手动执行一次：在浏览器控制台输入 sgFixDomWidth()
import { app } from "/scripts/app.js";

const OFF_KEY = "sg_domwidth_fix";
const MIN_NODE_WIDTH = 320;   // 节点比这还窄就不动，避免误伤小节点
const STUCK_WIDTH = 260;      // 缓存宽度落在这个范围内才认为是「卡在默认值」
const BAD_RATIO = 0.8;        // 且不足节点宽度的 80%
const MARGIN = 10;

function disabled() {
  try { return localStorage.getItem(OFF_KEY) === "off"; } catch (e) { return false; }
}

function fixNode(node) {
  if (!node || !Array.isArray(node.widgets)) return;
  const nw = node.size && node.size[0];
  if (typeof nw !== "number" || nw < MIN_NODE_WIDTH) return;

  for (const wgt of node.widgets) {
    if (!wgt || !wgt.element || !wgt.element.nodeType) continue;
    const ww = wgt.width;
    if (typeof ww !== "number" || ww <= 0) continue;
    if (ww > STUCK_WIDTH || ww >= nw * BAD_RATIO) continue;

    let ok = false;
    try {
      wgt.width = nw;
      ok = wgt.width === nw;
    } catch (e) { /* 只读 getter：放弃，避免与前端每帧重绘打架 */ }
    if (!ok) continue;

    // 立即纠正一次覆盖层容器，省得等下一帧（容器在 transform:scale() 内，
    // 其 px 宽度用画布单位）。前端下帧读到新的 widget.width 后会自己算对。
    const host = wgt.element.parentElement;
    if (host && host.classList && host.classList.contains("dom-widget")) {
      const margin = typeof wgt.margin === "number" ? wgt.margin : MARGIN;
      const expect = nw - margin * 2;
      const cur = parseFloat(host.style.width);
      if (expect > 0 && (!isFinite(cur) || cur < expect * BAD_RATIO)) {
        host.style.width = expect + "px";
        host.style.maxWidth = "none";
      }
    }
  }
}

function fixAll() {
  const graph = app.graph;
  if (!graph) return 0;
  const nodes = graph._nodes || graph.nodes;
  if (!nodes || !nodes.length) return 0;
  let fixed = 0;
  for (const n of nodes) {
    if (!n || !n.widgets) continue;
    for (const w of n.widgets) {
      const nw = n.size && n.size[0];
      if (w && w.element && w.element.nodeType &&
          typeof nw === "number" && nw >= MIN_NODE_WIDTH &&
          typeof w.width === "number" && w.width > 0 &&
          w.width <= STUCK_WIDTH && w.width < nw * BAD_RATIO) {
        fixNode(n);
        fixed++;
        break;
      }
    }
  }
  return fixed;
}

// 暴露给控制台，方便手动验证 / 立即修复
window.sgFixDomWidth = () => {
  const n = fixAll();
  console.info("[Shanggu] 已检查 DOM 面板宽度，修正节点数：" + n);
  return n;
};

let last = 0;
function tick(ts) {
  requestAnimationFrame(tick);
  if (disabled() || ts - last < 250) return;
  last = ts;
  fixAll();
}

app.registerExtension({
  name: "Shanggu.DOMWidgetWidthFix",
  async setup() {
    requestAnimationFrame(tick);
    console.info(
      "[Shanggu] DOM 面板半宽修复已启用（关闭：localStorage.setItem('sg_domwidth_fix','off') 后刷新）"
    );
  },
});
