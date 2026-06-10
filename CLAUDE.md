# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

A local novel aggregation reader — a Flask web app that loads **Legado-format book source JSON**, aggregates novel content from multiple websites, and presents it in a browser-based reading UI.

## How to run

```bash
cd D:\AI\novel-reader
.venv\Scripts\activate
python app.py [path-to-book-sources.json]
```

If no source file is given on the command line, it defaults to a hardcoded path (`F:\86135\下载\墨辰整理书源大全7.1（禁止倒卖）【最新完整】.json`). The app starts on `http://localhost:5000` with debug mode on.

To test the API without a browser:

```bash
curl -s "http://localhost:5000/api/search?q=<keyword>"
```

## Stack

- **Python 3** + Flask (backend, app.py)
- **Node.js** (v24.16.0, for executing Legado `<js>` / `@js:` rules — `js_runner.js`)
- No database — bookshelf state is persisted to `shelf.json`
- Virtual env at `.venv/`; dependencies in `requirements.txt`: `flask`, `requests`, `beautifulsoup4`, `lxml`

## Architecture

`app.py` is a single-file backend (~1100 lines) containing three source-type classes, a source manager, and Flask routes. There is no ORM, no migrations, and no separate module structure.

### Source abstraction (3 types)

Every book source parsed from the Legado JSON gets one of three classes based on its search/rule structure:

| Class | When used | Data format |
|---|---|---|
| `JsonApiSource` | `ruleSearch.bookList` starts with `$`, `[`, or `[*]` | JSON API responses |
| `CssSource` | `ruleSearch.bookList` is a CSS selector (everything else) | HTML pages parsed with BeautifulSoup |
| `JsSource` | `ruleSearch.bookList` or `searchUrl` starts with `@js` | URL constructed by executing JS in Node.js, then JSON/HTML |

All three implement the same interface: `search(kw, page)`, `detail(book_url, search_data)`, `chapter_content(ch_url)`.

### Legado rule DSL

Book source rules use a mini-language from the Legado (开源阅读) Android app. The key rule format:

```
$.path.to.field <js>transformCode</js> https://tpl.com/{{result}}
```

- **`$.path`** — JSONPath-style extraction (parsed by `_split_rule()` / `_resolve_rule()`)
- **`<js>...</js>`** — inline JavaScript transform executed via Node.js subprocess
- **`@js:`** — block-level JS (search URL generation for `JsSource`)
- **`{{...}}`** — template interpolation with `$.path` lookups and `{{baseUrl.match(/regex/)[n]}}` patterns
- **`##`** — trailing discard markers (Legado processing annotations)
- **`@get:{...}`** — HTTP method/header overrides (stripped during rule parsing)

### CSS selector conversion (`css_conv()`)

Legado uses a non-standard CSS syntax that is normalized to standard CSS:
- `@` → ` > ` (descendant combinator)
- `class.x` → `.x`, `id.x` → `#x`
- `!N` → `:nth-child(n+N+1)` (1-based offset)
- `!0` suffix stripped, `tag` prefix stripped
- Suffixes like `@text`, `@src`, `@href`, `@html` stripped from selector end

### JS execution (`js_runner.js`)

Receives JSON on stdin with `{code, key, page, sourceUrl, headers, store}` and returns JSON on stdout. Simulates Legado's Android JS environment:
- `java.*` API (ajax, md5, base64, HMAC, DES, UUID, storage via put/get)
- `source.*` API (source key, headers, login, per-source storage)
- `cookie.*` API (stubs)
- `Packages.*` (Android SDK stubs)
- The last line of JS code is automatically wrapped in `return` so expression-style Legado snippets work

### SourceManager

Loads book sources from JSON on startup, classifies them into the three source types, and maintains an `enabled` set. Search fans out across enabled sources using `ThreadPoolExecutor` (max 20 workers, 10s timeout), collecting partial results.

### Routes

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serve the single-page reader UI |
| `/api/sources` | GET | List all loaded sources with enable state |
| `/api/toggle_source` | POST | Toggle a source on/off |
| `/api/search?q=&page=&source=` | GET | Fan-out search across sources |
| `/api/detail?source=&url=` | GET | Book detail + chapter list |
| `/api/chapter?source=&url=` | GET | Chapter text content |
| `/api/shelf` | GET | List bookshelf |
| `/api/shelf` | POST | Add/remove book from shelf (toggles) |

### Frontend (`templates/index.html`)

A single HTML file (~26KB) with embedded CSS and JS. Dark theme (GitHub-style color palette), three-panel layout: sidebar (shelf + source manager), main content area (search/home view, book detail, reader view). Vanilla JS — no framework.

## Key patterns

- **Response encoding**: `safe_json()` tries multiple encodings (charset → utf-8 → gbk → gb18030 → gb2312) with garbled-text detection to handle Chinese novel sites that misreport encoding.
- **Session sharing**: A global `requests.Session` with `verify=False` (many sources use self-signed certs) and a consistent User-Agent is reused across all HTTP calls.
- **Partial results**: Search returns whatever sources responded within the 10-second timeout. Failed sources are silently skipped.
- **The bookshelf is a toggle**: POSTing the same `(source_url, book_url)` pair to `/api/shelf` removes it if present, adds it if absent.
- **No pagination in chapters**: Chapter lists are fetched in full and sorted by index.
