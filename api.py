import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List
from fastapi import FastAPI, Query, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from models import ProxyItem, ProxyQuery, StatsResponse
from storage import storage
from checker import checker
from extractor import extract_proxies_from_text
from scheduler import scheduler
from scrapers.telegram_userbot import tg_userbot
from tg_bot import tg_query_bot
from config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database
    await storage.init_db()
    # Start periodic scraping & health checking scheduler
    await scheduler.start()
    # Start Telegram background collectors & query bot (if configured)
    asyncio.create_task(tg_userbot.start())
    asyncio.create_task(tg_query_bot.start())
    yield
    # Graceful shutdown
    await scheduler.stop()
    await tg_userbot.stop()
    await tg_query_bot.stop()


app = FastAPI(
    title="Dynamic Multi-Source Proxy Pool",
    description="Automated Proxy Collector, Health Checker, and TG Bot Service",
    version="1.1.0",
    lifespan=lifespan
)

# Enable CORS for external scripts/tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/v1/proxy", response_model=ProxyItem, summary="Get one optimal active proxy by country, protocol & purity")
async def get_single_proxy(
    country: Optional[str] = Query(None, description="2-letter ISO code, e.g. PH, US, HK"),
    protocol: Optional[str] = Query(None, description="Protocol: http, https, socks4, socks5"),
    min_score: int = Query(50, description="Minimum reliability score (0-100)"),
    clean_only: bool = Query(False, description="Filter for clean proxies only (Level A/B)"),
    ip_type: Optional[str] = Query(None, description="residential, datacenter, mobile")
):
    proxy = await storage.get_proxy(
        country=country,
        protocol=protocol,
        min_score=min_score,
        clean_only=clean_only,
        ip_type=ip_type
    )
    if not proxy:
        raise HTTPException(
            status_code=404,
            detail=f"No active proxy found for country='{country or 'ANY'}', protocol='{protocol or 'ANY'}', clean_only={clean_only}"
        )
    return proxy


@app.get("/api/v1/proxies", response_model=List[ProxyItem], summary="Query list of proxies with filters")
async def list_proxies(
    country: Optional[str] = Query(None),
    protocol: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(True),
    clean_only: Optional[bool] = Query(None),
    ip_type: Optional[str] = Query(None),
    min_score: Optional[int] = Query(0),
    max_latency: Optional[int] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0)
):
    q = ProxyQuery(
        country=country,
        protocol=protocol,
        is_active=is_active,
        clean_only=clean_only,
        ip_type=ip_type,
        min_score=min_score,
        max_latency=max_latency,
        limit=limit,
        offset=offset
    )
    return await storage.list_proxies(q)


@app.get("/api/v1/stats", response_model=StatsResponse, summary="Get proxy pool health and distribution statistics")
async def get_stats():
    return await storage.get_stats()


@app.post("/api/v1/import", summary="Manually import raw text or text file containing proxies")
async def import_proxies(
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    default_protocol: str = Form("http")
):
    raw_content = ""
    source_name = "manual_import"

    if file:
        content_bytes = await file.read()
        raw_content = content_bytes.decode("utf-8", errors="ignore")
        source_name = f"upload:{file.filename}"
    elif text:
        raw_content = text
        source_name = "manual_text"

    if not raw_content.strip():
        raise HTTPException(status_code=400, detail="No content provided for import.")

    extracted = extract_proxies_from_text(raw_content, default_protocol=default_protocol, source=source_name)
    if not extracted:
        return {"imported": 0, "message": "No valid proxies could be extracted from input."}

    count = await storage.upsert_many(extracted)
    # Trigger background check for newly imported proxies
    asyncio.create_task(checker.check_batch(extracted[:50]))

    return {
        "imported": count,
        "message": f"Successfully parsed and saved {count} candidate proxies. Initial validation queued."
    }


@app.get("/", response_class=HTMLResponse, summary="Web Management Dashboard")
async def web_dashboard():
    return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dynamic Proxy IP Pool 控制台</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        body { background-color: #0f172a; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        .card { background-color: #1e293b; border: 1px solid #334155; border-radius: 12px; }
        .table { color: #f8fafc; }
        .table-dark { --bs-table-bg: #1e293b; --bs-table-border-color: #334155; }
        .badge-country { font-weight: 700; font-size: 0.85rem; padding: 5px 9px; background: #3b82f6; border-radius: 6px; }
        .badge-protocol { font-size: 0.8rem; text-transform: uppercase; background: #6366f1; border-radius: 6px; padding: 4px 8px; }
        .badge-socks5 { background: #8b5cf6; }
        .badge-http { background: #06b6d4; }
        .badge-https { background: #10b981; }
        .code-box { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; background: #090d16; padding: 3px 8px; border-radius: 4px; border: 1px solid #1e293b; }
        .stat-val { font-size: 1.8rem; font-weight: 700; }
        .btn-copy { padding: 2px 8px; font-size: 0.8rem; }
    </style>
</head>
<body class="py-4">
    <div class="container">
        <!-- Header -->
        <div class="d-flex justify-content-between align-items-center mb-4">
            <div>
                <h2 class="fw-bold m-0"><i class="bi bi-shield-shaded text-primary me-2"></i>多渠道动态 IP 代理池</h2>
                <small class="text-secondary">实时抓取 · Telegram 监听 · 自动测活 · 纯净度检测 · 极速出池</small>
            </div>
            <div>
                <button class="btn btn-outline-primary me-2" onclick="refreshData()"><i class="bi bi-arrow-clockwise"></i> 刷新</button>
                <button class="btn btn-primary" data-bs-toggle="modal" data-bs-target="#importModal"><i class="bi bi-plus-lg"></i> 导入代理</button>
            </div>
        </div>

        <!-- Stat Cards -->
        <div class="row g-3 mb-4">
            <div class="col-md-2 col-6">
                <div class="card p-3">
                    <span class="text-secondary small">在线存活代理</span>
                    <div class="stat-val text-success" id="stat-active">-</div>
                </div>
            </div>
            <div class="col-md-2 col-6">
                <div class="card p-3">
                    <span class="text-secondary small">🛡️ 纯净节点 (A/B)</span>
                    <div class="stat-val text-info" id="stat-clean">-</div>
                </div>
            </div>
            <div class="col-md-2 col-6">
                <div class="card p-3">
                    <span class="text-secondary small">🏠 住宅宽带 (ISP)</span>
                    <div class="stat-val text-primary" id="stat-res">-</div>
                </div>
            </div>
            <div class="col-md-2 col-6">
                <div class="card p-3">
                    <span class="text-secondary small">候选代理总数</span>
                    <div class="stat-val text-light" id="stat-total">-</div>
                </div>
            </div>
            <div class="col-md-2 col-6">
                <div class="card p-3">
                    <span class="text-secondary small">平均延迟</span>
                    <div class="stat-val text-warning" id="stat-latency">- ms</div>
                </div>
            </div>
            <div class="col-md-2 col-6">
                <div class="card p-3">
                    <span class="text-secondary small">覆盖国家/地区</span>
                    <div class="stat-val text-primary" id="stat-countries">-</div>
                </div>
            </div>
        </div>

        <!-- Quick Extract Box -->
        <div class="card p-4 mb-4">
            <h5 class="fw-bold mb-3"><i class="bi bi-lightning-charge-fill text-warning me-1"></i>快捷提取代理</h5>
            <div class="row g-2 align-items-center">
                <div class="col-md-2">
                    <input type="text" class="form-control bg-dark text-light border-secondary" id="quick-country" placeholder="国家 (如 PH, US)">
                </div>
                <div class="col-md-2">
                    <select class="form-select bg-dark text-light border-secondary" id="quick-proto">
                        <option value="">所有协议</option>
                        <option value="socks5">SOCKS5</option>
                        <option value="http">HTTP</option>
                        <option value="https">HTTPS</option>
                    </select>
                </div>
                <div class="col-md-2">
                    <select class="form-select bg-dark text-light border-secondary" id="quick-type">
                        <option value="">全部类型</option>
                        <option value="residential">🏠 住宅宽带</option>
                        <option value="datacenter">🏢 机房IP</option>
                    </select>
                </div>
                <div class="col-md-2">
                    <div class="form-check form-switch pt-1">
                        <input class="form-check-input" type="checkbox" id="quick-clean">
                        <label class="form-check-label small text-info fw-bold" for="quick-clean">仅纯净节点</label>
                    </div>
                </div>
                <div class="col-md-4">
                    <button class="btn btn-warning w-100 fw-bold" onclick="quickFetchProxy()"><i class="bi bi-search"></i> 提取最优代理</button>
                </div>
                <div class="col-12 mt-2">
                    <div id="quick-result" class="p-2 bg-dark rounded border border-secondary text-truncate small">
                        点击按钮立即提取最优代理
                    </div>
                </div>
            </div>
        </div>

        <!-- Proxy List Section -->
        <div class="card p-3">
            <div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
                <h5 class="fw-bold m-0"><i class="bi bi-list-ul me-2"></i>可用代理列表</h5>
                <div class="d-flex gap-2 align-items-center">
                    <input type="text" class="form-control form-control-sm bg-dark text-light border-secondary" style="width: 110px;" id="filter-country" placeholder="国家 (如 PH)" onchange="loadProxies()">
                    <select class="form-select form-select-sm bg-dark text-light border-secondary" style="width: 100px;" id="filter-proto" onchange="loadProxies()">
                        <option value="">全协议</option>
                        <option value="socks5">SOCKS5</option>
                        <option value="http">HTTP</option>
                    </select>
                    <select class="form-select form-select-sm bg-dark text-light border-secondary" style="width: 100px;" id="filter-type" onchange="loadProxies()">
                        <option value="">全类型</option>
                        <option value="residential">住宅</option>
                        <option value="datacenter">机房</option>
                    </select>
                    <div class="form-check form-switch m-0">
                        <input class="form-check-input" type="checkbox" id="filter-clean" onchange="loadProxies()">
                        <label class="form-check-label small text-secondary" for="filter-clean">纯净</label>
                    </div>
                </div>
            </div>

            <div class="table-responsive">
                <table class="table table-dark table-hover align-middle mb-0">
                    <thead>
                        <tr class="text-secondary small">
                            <th>国家</th>
                            <th>协议</th>
                            <th>IP:端口</th>
                            <th>纯净度 / 类型</th>
                            <th>账号</th>
                            <th>密码</th>
                            <th>延迟</th>
                            <th>健康分</th>
                            <th>操作</th>
                        </tr>
                    </thead>
                    <tbody id="proxy-table-body">
                        <tr><td colspan="9" class="text-center text-secondary py-4">正在加载代理数据...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Import Modal -->
    <div class="modal fade" id="importModal" tabindex="-1">
        <div class="modal-dialog">
            <div class="modal-content bg-dark text-light border-secondary">
                <div class="modal-header border-secondary">
                    <h5 class="modal-title fw-bold">手动导入代理列表</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <p class="small text-secondary">支持格式：<code>ip:port:user:pass</code>, <code>socks5://user:pwd@ip:port</code>, <code>ss://...</code>, <code>ip:port</code> 等多种格式，每行一个。</p>
                    <textarea class="form-control bg-black text-light border-secondary mb-3" rows="8" id="import-text" placeholder="粘贴代理数据或从 TG 群复制的文本..."></textarea>
                    <label class="form-label small text-secondary">或上传 .txt / .json 文件：</label>
                    <input type="file" class="form-control bg-black text-light border-secondary" id="import-file">
                </div>
                <div class="modal-footer border-secondary">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                    <button type="button" class="btn btn-primary" onclick="submitImport()">确认导入</button>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        async function loadStats() {
            try {
                const res = await fetch('/api/v1/stats');
                const data = await res.json();
                document.getElementById('stat-active').innerText = data.active_proxies;
                document.getElementById('stat-clean').innerText = data.clean_proxies || 0;
                document.getElementById('stat-res').innerText = data.residential_proxies || 0;
                document.getElementById('stat-total').innerText = data.total_proxies;
                document.getElementById('stat-latency').innerText = (data.avg_latency_ms || '-') + ' ms';
                document.getElementById('stat-countries').innerText = Object.keys(data.by_country || {}).length;
            } catch (e) {
                console.error(e);
            }
        }

        function getPurityBadge(p) {
            let lvlBadge = '';
            if (p.clean_level === 'A') {
                lvlBadge = '<span class="badge bg-success" title="A级: 超纯净住宅节点">🛡️ A 住宅</span>';
            } else if (p.clean_level === 'B') {
                lvlBadge = '<span class="badge bg-info text-dark" title="B级: 良好纯净节点">🛡️ B 良好</span>';
            } else if (p.clean_level === 'C') {
                lvlBadge = '<span class="badge bg-secondary" title="C级: 机房常规节点">🏢 C 机房</span>';
            } else {
                lvlBadge = '<span class="badge bg-danger" title="D级: 风险/公开节点">⚠️ D 风险</span>';
            }

            const gIcon = p.google_clean
                ? '<span class="text-success ms-1" title="Google 搜索免验证"><i class="bi bi-google"></i></span>'
                : '<span class="text-secondary opacity-25 ms-1" title="未通过 Google 免验"><i class="bi bi-google"></i></span>';

            return lvlBadge + gIcon;
        }

        async function loadProxies() {
            const country = document.getElementById('filter-country').value.trim();
            const proto = document.getElementById('filter-proto').value;
            const ipType = document.getElementById('filter-type').value;
            const cleanOnly = document.getElementById('filter-clean').checked;

            let url = '/api/v1/proxies?limit=50&is_active=true';
            if (country) url += '&country=' + country.toUpperCase();
            if (proto) url += '&protocol=' + proto;
            if (ipType) url += '&ip_type=' + ipType;
            if (cleanOnly) url += '&clean_only=true';

            try {
                const res = await fetch(url);
                const list = await res.json();
                const tbody = document.getElementById('proxy-table-body');
                if (!list.length) {
                    tbody.innerHTML = '<tr><td colspan="9" class="text-center text-secondary py-4">暂无符合条件的可用代理</td></tr>';
                    return;
                }

                tbody.innerHTML = list.map(p => {
                    const fullUrl = (p.username && p.password)
                        ? `${p.protocol}://${p.username}:${p.password}@${p.ip}:${p.port}`
                        : `${p.protocol}://${p.ip}:${p.port}`;
                    const badgeProto = p.protocol === 'socks5' ? 'badge-socks5' : (p.protocol === 'https' ? 'badge-https' : 'badge-http');
                    const latBadge = p.latency ? (p.latency < 600 ? 'text-success' : (p.latency < 1500 ? 'text-warning' : 'text-danger')) : 'text-secondary';

                    return `
                        <tr>
                            <td><span class="badge-country">${p.country || '??'}</span></td>
                            <td><span class="badge badge-protocol ${badgeProto}">${p.protocol}</span></td>
                            <td><span class="code-box">${p.ip}:${p.port}</span></td>
                            <td>${getPurityBadge(p)}</td>
                            <td>${p.username ? `<span class="code-box">${p.username}</span>` : '<span class="text-secondary">-</span>'}</td>
                            <td>${p.password ? `<span class="code-box">${p.password}</span>` : '<span class="text-secondary">-</span>'}</td>
                            <td class="${latBadge} fw-bold">${p.latency ? p.latency + ' ms' : '-'}</td>
                            <td><span class="badge bg-secondary">${p.score}分</span></td>
                            <td>
                                <button class="btn btn-outline-info btn-copy" onclick="copyText('${fullUrl}')" title="复制完整代理链接">
                                    <i class="bi bi-clipboard"></i> 复制
                                </button>
                            </td>
                        </tr>
                    `;
                }).join('');
            } catch (e) {
                console.error(e);
            }
        }

        async function quickFetchProxy() {
            const country = document.getElementById('quick-country').value.trim();
            const proto = document.getElementById('quick-proto').value;
            const ipType = document.getElementById('quick-type').value;
            const cleanOnly = document.getElementById('quick-clean').checked;

            const resBox = document.getElementById('quick-result');
            resBox.innerHTML = '<span class="text-secondary">查询中...</span>';

            let url = '/api/v1/proxy?';
            if (country) url += 'country=' + country.toUpperCase() + '&';
            if (proto) url += 'protocol=' + proto + '&';
            if (ipType) url += 'ip_type=' + ipType + '&';
            if (cleanOnly) url += 'clean_only=true&';

            try {
                const res = await fetch(url);
                if (!res.ok) {
                    resBox.innerHTML = '<span class="text-danger">未找到符合条件的可用代理</span>';
                    return;
                }
                const p = await res.json();
                const fullUrl = (p.username && p.password)
                    ? `${p.protocol}://${p.username}:${p.password}@${p.ip}:${p.port}`
                    : `${p.protocol}://${p.ip}:${p.port}`;
                const purityText = `[${p.clean_level}级·${p.ip_type === 'residential' ? '住宅' : (p.ip_type === 'mobile' ? '移动' : '机房')}]`;
                resBox.innerHTML = `
                    <span class="text-success fw-bold">[${p.country}] ${p.protocol.toUpperCase()}</span>
                    <span class="badge bg-info text-dark mx-1">${purityText}</span>
                    <code class="text-info mx-1">${fullUrl}</code>
                    <button class="btn btn-sm btn-outline-light btn-copy" onclick="copyText('${fullUrl}')"><i class="bi bi-clipboard"></i></button>
                `;
            } catch (e) {
                resBox.innerHTML = '<span class="text-danger">请求失败</span>';
            }
        }

        async function submitImport() {
            const text = document.getElementById('import-text').value;
            const fileInput = document.getElementById('import-file');
            const formData = new FormData();

            if (fileInput.files.length > 0) {
                formData.append('file', fileInput.files[0]);
            } else if (text.trim()) {
                formData.append('text', text);
            } else {
                alert('请输入文本或选择文件！');
                return;
            }

            try {
                const res = await fetch('/api/v1/import', { method: 'POST', body: formData });
                const data = await res.json();
                alert(data.message || '导入成功！');
                bootstrap.Modal.getInstance(document.getElementById('importModal')).hide();
                document.getElementById('import-text').value = '';
                fileInput.value = '';
                refreshData();
            } catch (e) {
                alert('导入失败: ' + e);
            }
        }

        function copyText(text) {
            navigator.clipboard.writeText(text).then(() => {
                alert('已复制到剪贴板: ' + text);
            });
        }

        function refreshData() {
            loadStats();
            loadProxies();
        }

        window.onload = () => {
            refreshData();
            setInterval(refreshData, 30000);
        };
    </script>
</body>
</html>
    """
