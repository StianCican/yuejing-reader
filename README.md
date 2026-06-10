# 阅境 · 小说聚合阅读器

本地小说聚合阅读器，基于 Legado（开源阅读）书源格式，聚合多个网站的小说内容，浏览器打开即用。

## 技术栈

- **后端**：Python 3 + Flask
- **JS 运行时**：Node.js（用于执行 Legado 书源的 `<js>` / `@js:` 规则）
- **前端**：Vanilla JS + CSS（零框架、零构建）

## 快速启动

```bash
cd D:\AI\novel-reader
.venv\Scripts\activate
python app.py [path-to-book-sources.json]
```

默认书源路径：`F:\86135\下载\墨辰整理书源大全7.1（禁止倒卖）【最新完整】.json`

浏览器打开 `http://localhost:5000`

## 目录结构

```
├── app.py                    # Flask 后端
├── js_runner.js              # Node.js Legado JS 执行器
├── requirements.txt          # Python 依赖
├── shelf.json                # 书架持久化
├── templates/
│   └── index.html            # HTML 骨架
├── static/
│   ├── css/
│   │   ├── base.css          # 变量、reset、布局、主题
│   │   ├── components.css    # 组件样式
│   │   ├── reader.css        # 阅读器样式
│   │   └── home.css          # 首页样式
│   └── js/
│       ├── app.js            # 主逻辑
│       ├── reader.js         # 阅读器
│       ├── shelf.js          # 书架
│       └── settings.js       # 设置
```

## API

| 路由 | 方法 | 用途 |
|------|------|------|
| `/` | GET | 前端页面 |
| `/api/sources` | GET | 书源列表 |
| `/api/toggle_source` | POST | 切换书源开关 |
| `/api/search?q=&page=` | GET | 聚合搜索 |
| `/api/detail?source=&url=` | GET | 书籍详情+目录 |
| `/api/chapter?source=&url=` | GET | 章节正文 |
| `/api/shelf` | GET/POST | 书架管理 |
