# 🌐 多渠道动态 IP 代理池系统 (Dynamic Proxy IP Pool)

一个支持多渠道（网页爬虫、Telegram 群组消息/文件、GitHub 公开源等）自动化抓取、异步并发测活、国家区域识别、带账号密码认证、支持 Docker 一键部署与 Telegram Bot 交互式查询的动态代理池系统。

---

## 🌟 核心特性

1. **多数据源自动采集**：
   - **免费代理网站**：内置针对 `maskproxy.io` 等网站的抓取解析器，支持按国家（如菲律宾 `PH`）参数化采集。
   - **Telegram 群组与文件监听**：基于 `Telethon UserBot`，后台静默监听指定 TG 群组消息，自动下载 `.txt`、`.json`、`.csv` 附件并提取代理。
   - **公共节点列表**：内置 GitHub/开放源代理库抓取器，启动即有海量候选池。
   - **手动导入**：支持在 Web 界面或 API 直接批量粘贴或上传代理文本。
2. **账号密码认证支持**：
   - 完美兼容 `ip:port:user:pass`、`socks5://user:pass@ip:port` 等格式，自动提取并标准化存储。
3. **高并发异步测活与国家定位**：
   - 基于 `httpx` + 异步并发信号量（可轻松支撑数千节点调度）。
   - 通过 Cloudflare Trace 接口进行握手测试，一次握手同时完成**连通性判定、真实延迟测算、出口国家定位（`loc=XX`）与匿名度评估**。
4. **灵活多样的提取方式**：
   - **Telegram 机器人**：发送 `/get PH` 或点击内嵌国家按钮，一键复制格式化代理。
   - **RESTful API**：供爬虫程序或自动化脚本调用 (`GET /api/v1/proxy?country=PH`)。
   - **现代化 Web 控制台**：直观展示在线状态、统计图表、延迟指示器与一键复制功能。
5. **Docker 容器化一键部署**：
   - 挂载 `./data` 目录，SQLite 数据库与 Telegram 登录凭证无感持久化。

---

## 📁 目录结构

```
proxy-ip/
├── api.py                    # FastAPI 接口与 Web 控制台页面
├── main.py                   # 服务启动入口
├── config.py                 # 全局配置管理 (Pydantic Settings)
├── models.py                 # 数据模型 (ProxyItem, ProxyQuery, Stats)
├── storage.py                # SQLite 异步存储引擎 (aiosqlite)
├── checker.py                # 异步并发测活与国家定位引擎
├── extractor.py              # 通用正则与文件/HTML格式提取器
├── scheduler.py              # 定时采集与测活调度器
├── tg_bot.py                 # Telegram 查询 Bot (用户交互)
├── login_userbot.py          # Telegram UserBot 首次登录授权助手
├── scrapers/                 # 采集器包
│   ├── __init__.py
│   ├── maskproxy.py          # maskproxy.io 爬虫
│   ├── public_sources.py     # 公开节点源爬虫
│   └── telegram_userbot.py   # TG 群组/文件监听器
├── tests/                    # 自动化单元测试套件
├── data/                     # 数据持久化目录 (挂载卷)
│   ├── proxies.db            # SQLite 数据库
│   └── userbot.session       # TG 登录会话凭证 (运行 login 后生成)
├── Dockerfile                # Docker 镜像构建文件
├── docker-compose.yml        # Docker Compose 编排文件
├── requirements.txt          # Python 依赖清单
└── .env.example              # 环境变量配置模板
```

---

## 🚀 快速开始

### 方式一：Docker 一键部署（推荐）

#### 1. 配置环境变量
```bash
cp .env.example .env
# 按需编辑 .env 配置 TG_BOT_TOKEN 等参数
```

#### 2. (可选) 如果需要启用 Telegram UserBot 采集
在宿主机上运行一次登录助手，生成会话文件到 `data/`：
```bash
python3 login_userbot.py
```
*根据提示输入手机号与验证码，成功后会在 `data/userbot.session` 生成凭证。*

#### 3. 启动容器
```bash
docker-compose up -d --build
```

#### 4. 访问服务
- **Web 控制台**：浏览器打开 `http://localhost:8000`
- **Swagger API 文档**：`http://localhost:8000/docs`

---

### 方式二：本地运行

```bash
# 1. 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 复制配置文件
cp .env.example .env

# 3. 启动服务
python main.py
```

---

## 🤖 Telegram Bot 使用说明

在 Telegram 中私聊你创建的机器人：

| 指令 | 示例 | 作用说明 |
| :--- | :--- | :--- |
| `/start` | `/start` | 欢迎界面，显示常用国家快捷按钮 |
| `/get <国家> [协议]` | `/get PH`<br>`/get US socks5` | 提取指定国家（和协议）的最优可用代理，带账密与一键复制链接 |
| `/list` | `/list` | 查看当前代理池中所有存活的国家及其可用节点数量 |
| `/stats` | `/stats` | 查看代理池总体健康度、平均延迟与协议分布 |

---

## 🔌 API 接口文档

### 1. 获取单个最优可用代理
```http
GET /api/v1/proxy?country=PH&protocol=socks5&min_score=50
```
**响应示例**：
```json
{
  "id": 1,
  "ip": "103.152.112.5",
  "port": 1080,
  "protocol": "socks5",
  "username": "user123",
  "password": "pwd456",
  "country": "PH",
  "latency": 182,
  "anonymity": "elite",
  "score": 100,
  "is_active": true,
  "source": "tg_msg:老王技术交流分享群"
}
```

### 2. 批量筛选查询
```http
GET /api/v1/proxies?country=US&limit=20&is_active=true
```

### 3. 获取统计数据
```http
GET /api/v1/stats
```

### 4. 手动导入代理列表
```http
POST /api/v1/import
```
- 支持 `Form(text=...)` 或上传 `.txt` 文件，系统会自动正则清洗并异步测活入库。

---

## 🧪 自动化测试

运行内置的单元测试套件：
```bash
pytest tests/ -v
```
