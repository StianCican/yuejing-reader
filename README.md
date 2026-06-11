# 阅境 · 小说聚合阅读器

本地小说聚合阅读器，基于 Legado（开源阅读）书源格式，聚合多个网站的小说/漫画/听书内容，浏览器打开即用。**加载 411 个书源，支持 5 种内容类型。**

## 功能特性

### 📖 内容类型
| 类型 | 支持 | 说明 |
|------|------|------|
| 小说 | ✅ 全功能 | 正文阅读 + 字体/字号/行高/主题自定义 |
| 漫画 | ✅ 滚动阅读 | 纵向滚动 + 懒加载 + 图片代理防盗链 |
| 听书 | ✅ 音频播放 | HTML5 `<audio>` 播放器 + 折叠文本面板 |
| 影视 | ⚠️ 基础 | 数据可查，播放器待完善 |
| 文件 | ⚠️ 基础 | 目录可查 |

### 🔍 智能搜索
- **全源并发搜索**：411 个源 40 线程并发，1.5s 最低搜索时间确保慢源响应
- **结果排序**：精确书名匹配优先 → 健康源优先 → 小说类型优先
- **5 分钟缓存**：同关键词秒回
- **类型筛选**：一键切换小说/漫画/听书/影视
- **源失败自动降权**：连续 3 次搜索失败自动标记为失效

### 📚 阅读体验
- 段落式正文渲染 + 暗色/宣纸/青绿三种主题
- 书架阅读进度百分比 + 滚动位置记忆（24h）
- 键盘快捷键：←→ 翻章、空格翻页、↑↓ 漫画滚动

### 🗃️ 源管理
- 分组展示 + 实时筛选搜索
- 两阶段健康检测：域名 ping → 实际搜索测试
- 三级状态：可用 🟢 / 部分 🟡 / 失效 🔴
- 一键批量禁用失效源
- 状态持久化到 `source_status.json`

### 🔧 规则引擎
- JSON API / CSS 选择器 / JS 搜索三种源类型
- 完整 Legado DSL：`$.path`、`<js>`、`@js:`、`@put/@get`、`&&` 链、`||` 回退、`##` 正则、`{{}}` 模板插值
- 通用 CSS 回退选择器（规则失效时自动尝试）
- `replaceRegex` + `nextContentUrl` 多页内容拼接

### 🖼️ 图片代理
- 服务端图片代理绕过防盗链（伪造 Referer）
- SSRF 防护（屏蔽私有 IP）
- 漫画/封面图片自动走代理

## 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 后端 | Python 3 + Flask | 单文件 ~2100 行 |
| JS 快速路径 | PyMiniRacer (V8) | 进程内执行 `<js>` 变换，~1ms |
| JS 完整路径 | Node.js 持久 Worker | `@js:` 块执行（含 ajax），~5ms |
| 前端 | Vanilla JS + CSS | 零框架、零构建 |
| 解析 | BeautifulSoup4 + JSON | HTML/JSON 双栈解析 |

## 快速启动

```bash
cd D:\AI\novel-reader
.venv\Scripts\activate
pip install flask requests beautifulsoup4 lxml py_mini_racer
python app.py [path-to-book-sources.json]
```

浏览器打开 `http://localhost:5000`

## 项目结构

```
├── app.py                         # Flask 后端（~2100 行）
├── js_runtime.py                  # 双路径 JS 运行时（PyMiniRacer + NodeWorker）
├── js_worker.js                   # 持久 Node.js 工作进程
├── requirements.txt               # Python 依赖
├── shelf.json                     # 书架持久化
├── source_status.json             # 源健康状态持久化
├── templates/
│   └── index.html                 # HTML 骨架
├── static/
│   ├── css/
│   │   ├── base.css               # 变量、reset、布局、暗色主题
│   │   ├── components.css         # 组件：书卡、源项、按钮、toast、骨架屏
│   │   ├── reader.css             # 阅读器 + 漫画 + 音频播放器
│   │   └── home.css               # 首页样式
│   └── js/
│       ├── app.js                 # 全局状态、搜索、书源管理
│       ├── reader.js              # 文本阅读器 + 音频播放器
│       ├── comic_reader.js        # 漫画滚动阅读器（懒加载）
│       ├── shelf.js               # 书架管理
│       └── settings.js            # 阅读设置面板
└── .gitignore
```

## API

| 路由 | 方法 | 用途 | 参数 |
|------|------|------|------|
| `/` | GET | 前端页面 | — |
| `/api/sources` | GET | 书源列表（含健康状态） | — |
| `/api/health_check` | POST | 触发健康检测（异步） | — |
| `/api/toggle_source` | POST | 切换书源开关 | `{"url":"..."}` |
| `/api/search` | GET | 聚合搜索 | `q` `page` `source` `type`(0-4) |
| `/api/detail` | GET | 书籍详情+目录 | `source` `url` `name` `author` `cover` `intro` |
| `/api/chapter` | GET | 章节内容（文本/漫画/音频） | `source` `url` |
| `/api/shelf` | GET | 书架列表 | — |
| `/api/shelf` | POST | 添加/移除收藏（toggle） | `{"source_url":"...","book_url":"..."}` |
| `/api/proxy` | GET | 图片代理（防盗链） | `url` `referer` |

## 与同类项目对比

本项目灵感来源于朋友的 Node.js + Express 聚合阅读器（支持 7 种内容类型、461 个源），以下是两项目的架构差异和核心决策：

| 维度 | 本项目 (阅境) | 朋友项目 (yuedu) | 分析 |
|------|:---:|:---:|------|
| 语言 | Python + Flask | Node.js + Express | Python 生态更适合文本处理 |
| JS 执行 | PyMiniRacer(1ms) + NodeWorker(5ms) | vm.Script(~1ms) | 等同水平，Node 原生 JS 更简单 |
| JS 环境 | 手动 shim（btoa/atob/md5/curl） | Node 原生 | ⚠️ 本项目 shim 不完整时有兼容问题 |
| 规则引擎 | Legado DSL 80%覆盖 | Legado DSL 95%覆盖 | ⚠️ 缺少 `@Header`、部分 CSS 伪类 |
| 搜索策略 | 全源并发 + 结果排序 + 5min缓存 | 按类型分层 + 源池管理 | 各有优劣，本项目更激进 |
| 内容类型 | 5 种（小说/漫画/听书/文件/影视） | 7 种（+音乐/视频直链） | 可追赶 |
| 健康检测 | 域名ping + 搜索测试 + 持久化 | 基础连通性检测 | ✅ 本项目更完善 |
| 图片代理 | ✅ SSRF防护 + Referer伪造 | ✅ | 等同 |
| 前端架构 | Vanilla JS（零框架） | Vue + Element UI | Vue 组件化体验更好 |
| 源状态展示 | 绿/黄/红三色 + 筛选搜索 | 基本状态展示 | ✅ 本项目更丰富 |
| 源数量 | 411 | 461 | 兼容同一格式 |

### 核心教训

1. **规则引擎决定项目天花板** — Legado DSL 覆盖度直接影响可用源的比例。我们实现了约 80%，但剩余 20%（如 `@Header` 自定义请求头、复杂 CSS 伪类）仍然导致部分源无法正常工作
2. **JS 兼容性是最隐蔽的坑** — Legado 书源大量依赖 Android `java.*` API，纯 Node.js 环境需要 shim。py_mini_racer 的 V8 环境缺少 `btoa`/`atob`/`Buffer`，导致某些 `@js:` 规则执行失败
3. **搜索质量 > 源数量** — 411 个源中实际可用约 50-80 个。与其加更多源，不如做好源质量评估和降权
4. **源会持续过期** — 书源规则依赖网站结构，网站改版后规则立即失效。没有自动更新机制的话，源库会持续衰减

## License

MIT
