# 网页图库选图器 · ComfyUI-Shanggu-WebGallery

> 在 ComfyUI 节点里输入任意图片网址、网页或 JSON API，直接浏览缩略图；**点一下就等于「加载图像」**，点选多张时，后面的图生图工作流会对每一张各跑一遍。

一个给 ComfyUI 用的「网页找图 → 选图 → 喂给图生图」入口节点。适合从图站、素材页、模型展示页批量挑参考图，再把挑中的图接进 img2img / 反推提示词 / 风格迁移等工作流，不用来回手动下载、拖进 `input` 目录、再改 LoadImage 节点。

---

## 它解决什么问题

- **不用离开 ComfyUI 找图**：网址填进节点，缩略图网格直接画在节点面板里，向下滚动自动续加载。
- **点选即加载**：被点中的原图由后端下载到 ComfyUI 的 `input/` 目录，节点输出标准 `IMAGE` / `MASK`，等价于内置「加载图像」节点，可以直接接给 VAE 编码、img2img、IP-Adapter 等。
- **多选批量出图**：选了 N 张，后面挂图生图工作流，就能得到 N 张结果图，而不是只处理第一张。
- **顺手拿到页面信息**：`info_json` 输出原图网址、文件名、尺寸以及页面上抓到的 prompt 等字段，配套的「图库信息提取」节点可以把其中某个字段拆成文本，接给 CLIP 文本编码。

---

## 主要功能

| 能力 | 说明 |
| --- | --- |
| 网址抓取 | 静态 HTML、SPA 内嵌 JSON、纯 JSON API、每行一个图片网址，都能提取图片 |
| 无限滚动 | 滚动到底自动加载下一页；支持 `nextPage` 游标、`{page}` / `{offset}` / `{cursor}` 分页模板 |
| 缩略图代理 | 后端代抓并缓存缩略图，解决跨域、防盗链、Referer 校验；Civitai CDN 自动改用 `width=400` 小图 |
| 多选与排序 | 点击选中/取消，选中顺序编号，顺序即出图顺序；选择结果随工作流保存 |
| 两种多图模式 | 批量一次执行 / 逐张完整执行（见下） |
| 站点兜底 | Cookie / UA / Referer / HTTP 或 SOCKS5 代理、自定义正则、粘贴源码、浏览器桥接书签 |
| 尺寸统一 | 等比缩放+填充、居中裁剪、直接拉伸、保持原尺寸 |

---

## 安装

1. 把整个文件夹放到 ComfyUI 的 `custom_nodes/` 下：
   ```
   ComfyUI/custom_nodes/ComfyUI-Shanggu-WebGallery/
   ```
2. 重启 ComfyUI（或刷新浏览器页面）。
3. 双击画布搜索 **网页图库选图器** 或 `ShangguWebImageGallery`。

无需 `pip install`：核心依赖 `aiohttp`、`PIL`、`numpy`、`torch` 都是 ComfyUI 自带的。

**可选依赖**（不用也能跑，只是能力增强）：

- `beautifulsoup4`：装了以后 HTML 标签解析更完整；没装会自动退回正则提取。
- `aiohttp-socks`：只有要用 `socks5://` 代理时才需要；HTTP 代理走 aiohttp 原生支持。

已在 **ComfyUI 0.45.0（前端 1.59.1）** 上开发验证。

---

## 快速上手

1. 节点面板的网址框里填地址，默认是 `https://civitai.red/api/v1/images?limit=20`。
2. 点 **搜索**，出现缩略图网格。
3. **向下滚动**加载更多；也可点「加载更多」。
4. **点缩略图** = 选中并加载这张图（再点一次取消）。
5. 把 `images` 输出接到图生图工作流，按正常方式点「运行」。

面板三个标签页：

- **网址抓取**：常规用法。
- **粘贴源码**：把网页 HTML、接口返回的 JSON、或每行一个图片网址直接粘进来解析，适合站点需要登录或纯前端渲染抓不到的情况。
- **浏览器桥接**：复制书签脚本，在自己已登录的浏览器页面点一下复制全部图片网址，再粘回来。

---

## 多选图片：两种执行模式

`multi_mode` 决定「选了多张」时怎么跑，这是本节点的关键设计：

| 模式 | 行为 | 适用场景 |
| --- | --- | --- |
| **批量一次执行**（默认） | 输出 `IMAGE` 批次 `[N, H, W, C]`，工作流只跑 1 次，下游节点对每张各出一张图。需要统一尺寸（默认 1024×1024，可用 `size_mode` / `target_width` / `target_height` 调） | 快、省显存；图尺寸本来接近，或不在意统一缩放 |
| **逐张完整执行** | 拦截一次 Run，把队列扇出成 **N 个 prompt**，每个 prompt 只带一张选中图，**完整下游工作流真正执行 N 遍**，且每张保留原图分辨率 | 需要每张图独立跑完整流程（如逐张反推提示词、逐张精修）；严格「每张图都生成一张」 |

选了「逐张完整执行」后**照常点运行即可**，不需要额外操作。插队运行（队列前置）时会按选中顺序提交。

---

## 节点与输出

### 网页图库选图器 `ShangguWebImageGallery`

| 输出 | 类型 | 说明 |
| --- | --- | --- |
| `images` | `IMAGE` | 一张或一批图像张量，等价于 LoadImage |
| `masks` | `MASK` | 对应掩码（无 alpha 时为全 1） |
| `count` | `INT` | 本次加载的图片数量 |
| `info_json` | `STRING` | 每张图的元信息 JSON：原图网址、文件名、尺寸、页面 prompt 等 |

主要输入：`site_url`、`selected_files`（由面板自动维护）、`multi_mode`、`size_mode`、`target_width`、`target_height`。

### 图库信息提取 `ShangguGalleryInfo`

把 `info_json` 里指定字段（如 `prompt`、`url`）按索引拆成 `STRING`，方便直接接文本编码节点做「看图写提示词」。

---

## 关于 civitai.red / 图片来源

`https://civitai.red/images` 网页版会返回 **Cloudflare 403**，纯 HTTP 抓不到图；但同站的 `https://civitai.red/api/v1/images?limit=20` 是 200 直出 JSON，带 `items[].url` 和 `metadata.nextPage` 游标分页。因此节点内置了**自动改写**：直接输入 `/images` 也会切到该 JSON 接口，照样出图。

抓取器能识别的来源：

- `<img>` 的 `src` / `data-src` / `srcset`、`<a href>` 指向的图片、`og:image`、CSS 背景图；
- `<script type="application/json">` 以及页面内嵌 JSON 里的图片字段；
- JSON API 响应（递归查找 url 字段）；
- 纯文本里每行一个网址；
- 自定义正则（`url_regex`）强制提取。

默认会过滤 logo / icon / avatar / placeholder / favicon 之类的非内容图，可在高级设置里关闭。

---

## 高级设置

| 项 | 用途 |
| --- | --- |
| `Cookie` / `User-Agent` / `Referer` | 站点需要登录或有 Cloudflare / 防盗链时，从浏览器 DevTools 复制整条 Cookie，UA 保持一致 |
| `代理` | `http://127.0.0.1:10808` 或 `socks5://127.0.0.1:10808` |
| `自定义正则` | 用捕获组或整条 URL 匹配，强制从页面文本里抠图 |
| `分页模板` | 支持 `{url}` `{page}` `{offset}` `{cursor}` 占位符 |
| `安全过滤` / `每页数量` / `超时` | 过滤非内容图、控制单页条数与请求超时 |

---

## 附带修复：DOM 面板「只占节点一半宽」

`js/sg_domwidget_width_fix.js` 是一个独立的通用前端补丁。ComfyUI 新版前端把 DOM 面板画在独立 HTML 覆盖层上，覆盖层宽度按 widget 创建时缓存的节点默认宽度计算，节点后来被拉宽或从工作流恢复尺寸时不会同步，于是面板只占一半宽——**所有带 DOM 面板的第三方节点都可能偶发**。

补丁每 250ms 巡检，只修明显卡在默认宽度的面板，不会动刻意做窄的布局。本节点自身也内置了宽度同步逻辑。

- 手动执行一次：浏览器控制台输入 `sgFixDomWidth()`
- 关闭：`localStorage.setItem('sg_domwidth_fix', 'off')` 后刷新页面

---

## 后端接口

节点通过 ComfyUI 的 `PromptServer` 注册以下路由（ComfyUI 0.45 会同时挂到 `/xxx` 和 `/api/xxx`）：

| 路由 | 作用 |
| --- | --- |
| `GET /sg_gallery/health` | 健康检查 |
| `POST /sg_gallery/fetch` | 抓取一页（带会话去重、游标分页、自动改写） |
| `POST /sg_gallery/parse` | 解析粘贴的 HTML / JSON / 网址列表 |
| `GET /sg_gallery/thumb` | 缩略图代理 + 磁盘缓存 |
| `POST /sg_gallery/select` | 下载选中原图到 `input/` 并返回文件名与尺寸 |

> 源码文件统一用 `sg_` 前缀命名（`sg_nodes.py` / `sg_server.py` / `sg_scraper.py`），避免自定义节点目录进入 `sys.path` 后遮蔽 ComfyUI 内建的 `nodes` / `server` 模块。

---

## 已知限制

- 纯浏览器渲染、且需要登录/有强反爬的站点，后端直接抓可能拿不到图；请用「粘贴源码」或「浏览器桥接」标签页。
- 「批量一次执行」输出的是单一 `IMAGE` 张量，要求同宽高；尺寸差异大时请改用「逐张完整执行」或选一种尺寸统一方式。
- 抓取与缩略图缓存写在节点目录下的 `.cache/`，已加入 `.gitignore`；缓存的是第三方站点图片，请勿随仓库再分发。
- 请遵守目标站点的服务条款与图片版权，本节点只提供技术能力。

---

## License

MIT（见 [LICENSE](LICENSE)）。
