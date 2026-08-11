<<<<<<< HEAD
# Upstairs Reader

<p align="center">
  <strong>A self-hosted, multi-user RSS reader with AI summarization, TTS narration, and a newspaper-style reading experience.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python">
  <img src="https://img.shields.io/badge/flask-2.3+-green" alt="Flask">
  <img src="https://img.shields.io/badge/license-MIT-red" alt="License">
</p>

<p align="center">
  <a href="README.md">简体中文</a> | <strong>English</strong>
</p>

---

## Features

- **Multi-user support** — Registration, login, per-user read status, stars, and notes
- **RSS/Atom parsing** — Add any RSS or Atom feed URL; automatic periodic refresh
- **Feed organization** — Group feeds into custom categories (Tech, News, Blogs, etc.)
- **AI article summarization** — One-click summaries via OpenAI-compatible APIs (OpenAI, Aliyun Bailian, local LLMs)
- **TTS audio narration** — Automatically generates audio for starred articles (MiMo TTS or compatible)
- **Newspaper view** — A random curated newspaper-style reading mode with cover images
- **Personal profile pages** — Public pages showing starred articles and notes per user
- **Dark / Light themes** — Glass-morphism UI with system theme detection
- **Traditional / Simplified Chinese** — Built-in zh-Hant ↔ zh-Hans toggle
- **Docker support** — One-command deployment with `docker compose up`
- **Fever API compatible** — Works with RSS clients that support the Fever API protocol

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Flask (Python) |
| Database | SQLite |
| RSS parsing | feedparser |
| AI / TTS | OpenAI-compatible API |
| Frontend | Vanilla JS + CSS (no bundler) |
| Deployment | Docker + Docker Compose |

## Quick Start

### Local Development

```bash
git clone https://github.com/your-username/upstairs-reader.git
cd upstairs-reader
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5321**
=======
# Upstairs RSS

一个简洁的 RSS 阅读器，支持自托管部署。

## 功能特性

- **RSS 订阅管理**：添加、删除、分组管理 RSS 源
- **文章阅读**：支持列表/网格视图，已读/未读标记，收藏功能
- **AI 摘要**：集成大语言模型 API，自动为长文生成摘要（可选开启）
- **多语言界面**：支持简体中文、繁体中文切换
- **响应式设计**：适配桌面和移动端
- **用户系统**：支持多用户，管理员可控制开放注册
- **自动刷新**：后台定时抓取订阅源更新
>>>>>>> 36e641ef38fda4836716bbe5e7b1c0a137a509b6

> The first registered user automatically becomes the administrator.

<<<<<<< HEAD
### Docker

```bash
docker compose up -d
```

The app will be available at **http://localhost:5321**. Data persists in the `./data` directory.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `rss.db` (next to app.py) | SQLite database file path |

### Admin Settings

Once logged in as the administrator (first registered user), you can configure:

- **Registration** — Toggle open registration on/off
- **AI Summarization** — Configure API endpoint, key, model, and minimum article length
- **TTS** — Configure TTS API for article audio generation

### AI Summarization

Upstairs Reader supports any OpenAI-compatible API. Tested providers:

- OpenAI / Azure OpenAI
- Aliyun Bailian (阿里云百炼)
- Ollama / local LLMs
- Any service with a `/chat/completions` endpoint

In Settings → System Admin, set the API URL, key, and model name.

### TTS (Text-to-Speech)

Currently supports MiMo TTS and compatible APIs. Configure in Settings → System Admin. Audio files are cached in `audio_cache/`.

## API Reference

### Feeds

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/feeds` | List all feeds with item counts |
| `POST` | `/api/feeds` | Add a feed (`{url}`, optional `group_id`) |
| `PUT` | `/api/feeds/<id>` | Update feed title or group |
| `DELETE` | `/api/feeds/<id>` | Delete a feed and its items |
| `POST` | `/api/feeds/<id>/refresh` | Refresh a single feed |
| `PUT` | `/api/feeds/<id>/read-all` | Mark all items in feed as read |

### Items

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/items` | List items (`feed_id`, `group_id`, `q`, `unread`, `limit`, `offset`) |
| `GET` | `/api/items/<id>` | Get a single item |
| `PUT` | `/api/items/<id>/read` | Mark read/unread (`{is_read}`) |
| `PUT` | `/api/items/<id>/star` | Toggle star (`{is_starred}`) — triggers AI+TTS on star |
| `PUT` | `/api/items/<id>/note` | Set a note (`{note}`) |
| `GET` | `/api/items/starred` | List starred items |
| `GET` | `/api/items/notes` | List items with notes |

### Groups

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/groups` | List groups |
| `POST` | `/api/groups` | Create group (`{name}`) |
| `PUT` | `/api/groups/<id>` | Rename group |
| `DELETE` | `/api/groups/<id>` | Delete group |
| `PUT` | `/api/groups/<id>/feeds` | Assign feeds to group |

### Auth

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/register` | Register (`{username, password}`) |
| `POST` | `/api/auth/login` | Login (`{username, password}`) → returns `api_key` |

### AI & Stats

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/ai/summarize/<id>` | Generate AI summary for an article |
| `GET` | `/api/stats` | Feed/item/unread/starred counts |
| `GET` | `/api/settings/ai` | Get AI settings (admin) |
| `PUT` | `/api/settings/ai` | Update AI settings (admin) |
| `GET` | `/api/settings/registration` | Check registration status |
| `PUT` | `/api/settings/registration` | Toggle registration (admin) |

### Fever API (RSS Client Integration)

| Method | Path | Description |
|--------|------|-------------|
| `GET`/`POST` | `/api/fever` | Fever-compatible endpoint for RSS clients |

### User Profiles

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/user/<username>/profile` | Public profile with starred items & notes |
| `PUT` | `/api/user/profile` | Update display name & avatar |

## Database Schema

```sql
feeds(id, url, title, description, icon_url, site_url, custom_title, group_id, added_at, updated_at)
items(id, feed_id, guid, title, link, author, content, summary, published, is_read, is_starred, note, fetched_at, created_on_time)
users(id, username, password_md5, api_key, display_name, avatar, default_view, created_at)
groups(id, name, sort_order, created_at)
user_items(user_id, item_id, is_read, is_starred, note)
article_summaries(item_id, summary, created_at, audio_status, audio_path)
site_settings(key, value)
```

## Project Structure

```
upstairs-reader/
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── Dockerfile            # Docker build
├── docker-compose.yml    # Docker Compose config
├── static/
│   ├── index.css         # Stylesheet (dark/light themes)
│   ├── favicon.ico       # Favicon
│   ├── logo.png          # App logo
│   └── avatars/          # User avatar uploads
├── templates/
│   ├── index.html        # Main reader UI
│   ├── login.html        # Login page
│   ├── newspaper.html    # Newspaper reading mode
│   └── people.html       # Public profile page
├── data/                 # Persistent data (Docker volume)
└── audio_cache/          # TTS audio cache
```

## Deployment

### On a VPS

```bash
# Clone and start
git clone https://github.com/your-username/upstairs-reader.git
cd upstairs-reader
docker compose up -d

# Use nginx/Caddy as reverse proxy
# Point your domain to localhost:5321
```

### On Synology NAS

```bash
# Install Python 3, then:
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
python app.py
```

## Contributing

Contributions are welcome! Feel free to open issues or pull requests.

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

MIT License — see [LICENSE](LICENSE) file for details.

---

<p align="center">
  Built with ❤️ for people who love reading.
</p>
=======
- 后端：Python + Flask + SQLite
- 前端：原生 JavaScript + CSS
- 部署：Docker / Docker Compose

## 安装部署

### 方式一：Docker Compose（推荐）

```bash
git clone <仓库地址>
cd upstairs-rss
docker-compose up -d
```

服务将在 http://localhost:5321 启动。

### 方式二：手动运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
python app.py
```

默认监听 5321 端口。首次启动会自动创建数据库。

### 方式三：群晖 NAS

```bash
# 如果 SSL 证书报错，使用阿里云镜像
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

python app.py
```

## 初始配置

1. 访问 http://localhost:5321
2. 使用默认管理员账号登录：
   - 用户名：`zhengtu`
   - 密码：（首次启动时控制台会显示或需在数据库中预设）
3. 在「设置」→「安全设置」中可开启开放注册
4. 在「设置」→「AI 设置」中配置 API 密钥（可选）

## AI 摘要功能

支持以下服务商：
- 阿里云（DashScope）
- 腾讯云
- 火山引擎
- 硅基流动
- 七牛云
- 自定义 OpenAI 兼容接口

**注意**：AI 摘要为可选功能，不配置不影响 RSS 基础功能使用。

## 数据存储

- 数据库：`rss.db`（SQLite）
- 头像：`static/avatars/`
- 建议定期备份 `rss.db` 文件

## 常见问题

**Q: 如何修改端口号？**

修改 `app.py` 底部：
```python
app.run(host="0.0.0.0", port=5321, debug=False)
```

**Q: 数据库损坏怎么办？**

使用 SQLite 恢复：
```bash
sqlite3 rss.db ".dump" > backup.sql
# 重建数据库
sqlite3 rss_new.db < backup.sql
```

**Q: 如何备份数据？**

直接复制 `rss.db` 文件即可，SQLite 是单文件数据库。

**Q: AI 摘要显示"字数不足"？**

在 AI 设置中调整「字数阈值」，设为 0 可对所有文章启用摘要。

**Q: 如何切换繁简界面？**

在「设置」→「偏好设置」中选择界面语言。

## 目录结构

```
upstairs-rss/
├── app.py              # 主程序
├── rss.db              # SQLite 数据库
├── requirements.txt    # Python 依赖
├── Dockerfile          # Docker 构建
├── docker-compose.yml  # Docker Compose 配置
├── templates/          # HTML 模板
│   └── index.html
└── static/             # 静态资源
    ├── index.css
    ├── favicon.ico
    ├── logo.png
    └── avatars/        # 用户头像
```

## 开发说明

- 数据库迁移：修改 `init_db()` 函数，会自动执行 ALTER TABLE
- 前端缓存：开发时 CSS/JS 可能缓存，已配置 no-cache 头，或手动加版本号 `?v=xxx`
- API 文档：查看 `app.py` 中 `/api/` 开头的路由

## License

MIT
>>>>>>> 36e641ef38fda4836716bbe5e7b1c0a137a509b6
