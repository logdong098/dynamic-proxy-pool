import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List
from pydantic import BaseModel
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
    clean_level: Optional[str] = Query(None, description="Filter by clean level: A, B, C, D"),
    ip_type: Optional[str] = Query(None, description="residential, datacenter, mobile")
):
    proxy = await storage.get_proxy(
        country=country,
        protocol=protocol,
        min_score=min_score,
        clean_only=clean_only,
        clean_level=clean_level,
        ip_type=ip_type
    )
    if not proxy:
        raise HTTPException(
            status_code=404,
            detail=f"No active proxy found for country='{country or 'ANY'}', protocol='{protocol or 'ANY'}', clean_only={clean_only}, clean_level={clean_level}"
        )
    return proxy


@app.get("/api/v1/proxies", response_model=List[ProxyItem], summary="Query list of proxies with filters")
async def list_proxies(
    country: Optional[str] = Query(None),
    protocol: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(True),
    clean_only: Optional[bool] = Query(None),
    clean_level: Optional[str] = Query(None),
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
        clean_level=clean_level,
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


class BatchCheckRequest(BaseModel):
    proxy_ids: List[int]


@app.post("/api/v1/proxy/{proxy_id}/check", summary="Real-time health check for a single proxy")
async def check_single_proxy(proxy_id: int):
    proxy = await storage.get_by_id(proxy_id)
    if not proxy:
        raise HTTPException(status_code=404, detail=f"Proxy with ID {proxy_id} not found")

    is_alive, latency, country, anonymity, ip_type, fraud_score, google_clean, clean_level = await checker.check_proxy(proxy)
    await storage.update_check_result(
        proxy_id=proxy.id,
        is_alive=is_alive,
        latency=latency,
        country=country,
        anonymity=anonymity,
        ip_type=ip_type,
        fraud_score=fraud_score,
        google_clean=google_clean,
        clean_level=clean_level,
        max_fail_count=settings.MAX_FAIL_COUNT
    )
    if not is_alive:
        await storage.delete_endpoint(proxy.ip, proxy.port, reason="realtime_check_failed")

    updated = await storage.get_by_id(proxy.id) if is_alive else None
    return {
        "id": proxy.id,
        "ip": proxy.ip,
        "port": proxy.port,
        "protocol": proxy.protocol,
        "is_alive": is_alive,
        "latency": latency,
        "country": country or proxy.country,
        "ip_type": ip_type,
        "clean_level": clean_level,
        "score": updated.score if updated else 0,
        "google_clean": google_clean,
        "message": f"测活成功: 延迟 {latency}ms, 等级 {clean_level}" if is_alive else "测活失败: 节点不可达或连接超时"
    }


@app.post("/api/v1/check-batch", summary="Real-time batch health check for proxies")
async def check_batch_proxies(req: BatchCheckRequest):
    if not req.proxy_ids:
        return {"total": 0, "alive": 0, "dead": 0, "results": []}

    proxies = []
    for pid in req.proxy_ids[:50]:
        p = await storage.get_by_id(pid)
        if p:
            proxies.append(p)

    async def _check_one(p: ProxyItem):
        is_alive, latency, country, anonymity, ip_type, fraud_score, google_clean, clean_level = await checker.check_proxy(p)
        await storage.update_check_result(
            proxy_id=p.id,
            is_alive=is_alive,
            latency=latency,
            country=country,
            anonymity=anonymity,
            ip_type=ip_type,
            fraud_score=fraud_score,
            google_clean=google_clean,
            clean_level=clean_level,
            max_fail_count=settings.MAX_FAIL_COUNT
        )
        if not is_alive:
            await storage.delete_endpoint(p.ip, p.port, reason="batch_realtime_check_failed")
        return {
            "id": p.id,
            "ip": p.ip,
            "port": p.port,
            "is_alive": is_alive,
            "latency": latency,
            "clean_level": clean_level
        }

    results = await asyncio.gather(*[_check_one(p) for p in proxies], return_exceptions=True)
    valid_results = [r for r in results if isinstance(r, dict)]
    alive_count = sum(1 for r in valid_results if r.get("is_alive"))
    dead_count = len(valid_results) - alive_count
    return {
        "total": len(valid_results),
        "alive": alive_count,
        "dead": dead_count,
        "results": valid_results
    }


@app.post("/api/v1/import", summary="Manually import raw text or text file containing proxies")
async def import_proxies(
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    default_protocol: str = Form("http")
):
    raw_content = ""
    source_name = "manual_import"

    if file and file.filename:
        content_bytes = await file.read()
        raw_content = content_bytes.decode("utf-8", errors="ignore")
        source_name = f"upload:{file.filename}"

    if text and text.strip():
        if raw_content:
            raw_content += "\n" + text.strip()
        else:
            raw_content = text.strip()
            if not file or not file.filename:
                source_name = "manual_text"

    if not raw_content.strip():
        raise HTTPException(status_code=400, detail="未提供有效的待导入代理内容或文件")

    extracted = extract_proxies_from_text(raw_content, default_protocol=default_protocol, source=source_name)
    if not extracted:
        return {"imported": 0, "message": "未能从输入内容中解析出有效代理，请检查格式"}

    for p in extracted:
        p.is_active = True

    count = await storage.upsert_many(extracted, check_blocked=False)
    # Trigger background check for newly imported proxies
    asyncio.create_task(checker.check_batch(extracted[:50]))

    return {
        "imported": count,
        "message": f"成功解析并入库 {count} 个代理节点，后台测活已加入队列！"
    }


@app.get("/", response_class=HTMLResponse, summary="Web Management Dashboard")
async def web_dashboard():
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dynamic Proxy IP Pool 控制台 · 多渠道动态 IP 代理池</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        :root {
            --bg-primary: #090d16;
            --bg-card: rgba(17, 24, 39, 0.78);
            --bg-card-hover: rgba(24, 34, 53, 0.9);
            --border-card: rgba(255, 255, 255, 0.08);
            --border-hover: rgba(99, 102, 241, 0.4);
            --primary: #3b82f6;
            --accent-purple: #8b5cf6;
            --accent-green: #10b981;
            --accent-cyan: #06b6d4;
            --accent-amber: #f59e0b;
            --accent-rose: #ef4444;
            --text-main: #f8fafc;
            --text-secondary: #94a3b8;
            --code-bg: rgba(10, 15, 28, 0.85);
        }

        body {
            background-color: var(--bg-primary);
            background-image:
                radial-gradient(at 15% 10%, rgba(59, 130, 246, 0.09) 0px, transparent 45%),
                radial-gradient(at 85% 15%, rgba(139, 92, 246, 0.09) 0px, transparent 45%),
                radial-gradient(at 50% 80%, rgba(16, 185, 129, 0.04) 0px, transparent 50%);
            background-attachment: fixed;
            color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            min-height: 100vh;
        }

        h1, h2, h3, h4, h5, h6 {
            color: var(--text-main);
        }

        .navbar-custom {
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border-bottom: 1px solid var(--border-card);
            position: sticky;
            top: 0;
            z-index: 1020;
        }

        .brand-icon {
            width: 42px;
            height: 42px;
            border-radius: 12px;
            background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.3rem;
            color: white;
            box-shadow: 0 4px 14px rgba(99, 102, 241, 0.35);
        }

        .live-dot {
            width: 8px;
            height: 8px;
            background-color: var(--accent-green);
            border-radius: 50%;
            display: inline-block;
            position: relative;
        }
        .live-dot::after {
            content: '';
            position: absolute;
            inset: -3px;
            border-radius: 50%;
            border: 2px solid var(--accent-green);
            animation: pulseRing 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }
        @keyframes pulseRing {
            0% { transform: scale(0.9); opacity: 0.9; }
            100% { transform: scale(2.6); opacity: 0; }
        }

        .glass-card {
            background: var(--bg-card);
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
            border: 1px solid var(--border-card);
            border-radius: 14px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.35);
            transition: all 0.25s ease;
        }
        .glass-card:hover {
            border-color: rgba(255, 255, 255, 0.14);
        }

        .stat-card {
            position: relative;
            overflow: hidden;
            padding: 1.2rem;
            transition: transform 0.2s ease, border-color 0.2s ease;
        }
        .stat-card:hover {
            transform: translateY(-2px);
            border-color: rgba(255, 255, 255, 0.18);
        }
        .stat-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: var(--card-accent, #3b82f6);
            border-radius: 14px 14px 0 0;
        }
        .stat-icon {
            width: 38px;
            height: 38px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.2rem;
            background: rgba(255, 255, 255, 0.05);
        }
        .stat-val {
            font-size: 1.85rem;
            font-weight: 700;
            letter-spacing: -0.5px;
            line-height: 1.2;
            margin-top: 0.3rem;
        }

        .code-box {
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            background: var(--code-bg);
            padding: 3px 8px;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            font-size: 0.84rem;
            color: #e2e8f0;
            display: inline-flex;
            align-items: center;
        }

        .badge-country {
            font-weight: 600;
            font-size: 0.82rem;
            padding: 4px 8px;
            background: rgba(59, 130, 246, 0.15);
            color: #93c5fd;
            border: 1px solid rgba(59, 130, 246, 0.3);
            border-radius: 6px;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }

        .badge-protocol {
            font-size: 0.76rem;
            font-weight: 700;
            text-transform: uppercase;
            border-radius: 6px;
            padding: 3px 8px;
            letter-spacing: 0.3px;
        }
        .badge-socks5 {
            background: rgba(139, 92, 246, 0.15);
            color: #c084fc;
            border: 1px solid rgba(139, 92, 246, 0.3);
        }
        .badge-http {
            background: rgba(6, 182, 212, 0.15);
            color: #22d3ee;
            border: 1px solid rgba(6, 182, 212, 0.3);
        }
        .badge-https {
            background: rgba(16, 185, 129, 0.15);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.3);
        }

        .badge-purity {
            font-size: 0.76rem;
            font-weight: 600;
            border-radius: 6px;
            padding: 3px 8px;
            display: inline-flex;
            align-items: center;
            gap: 3px;
        }
        .badge-purity-a {
            background: rgba(16, 185, 129, 0.15);
            color: #6ee7b7;
            border: 1px solid rgba(16, 185, 129, 0.35);
        }
        .badge-purity-b {
            background: rgba(6, 182, 212, 0.15);
            color: #67e8f9;
            border: 1px solid rgba(6, 182, 212, 0.35);
        }
        .badge-purity-c {
            background: rgba(148, 163, 184, 0.12);
            color: #cbd5e1;
            border: 1px solid rgba(148, 163, 184, 0.25);
        }
        .badge-purity-d {
            background: rgba(239, 68, 68, 0.15);
            color: #fca5a5;
            border: 1px solid rgba(239, 68, 68, 0.35);
        }

        .latency-indicator {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-weight: 600;
            font-size: 0.85rem;
        }
        .latency-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            display: inline-block;
        }
        .latency-fast { color: #34d399; }
        .latency-fast .latency-dot { background: #34d399; box-shadow: 0 0 8px rgba(52, 211, 153, 0.6); }
        .latency-med { color: #fbbf24; }
        .latency-med .latency-dot { background: #fbbf24; box-shadow: 0 0 8px rgba(251, 191, 36, 0.6); }
        .latency-slow { color: #f87171; }
        .latency-slow .latency-dot { background: #f87171; box-shadow: 0 0 8px rgba(248, 113, 113, 0.6); }
        .latency-none { color: #64748b; }
        .latency-none .latency-dot { background: #64748b; }

        .table-custom {
            color: var(--text-main);
            margin-bottom: 0;
            vertical-align: middle;
        }
        .table-custom th {
            background: rgba(15, 23, 42, 0.75) !important;
            color: var(--text-secondary);
            font-size: 0.78rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-bottom: 1px solid var(--border-card) !important;
            padding: 12px 14px;
        }
        .table-custom td {
            background: transparent !important;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04) !important;
            padding: 11px 14px;
            font-size: 0.86rem;
        }
        .table-custom tr:hover td {
            background: rgba(255, 255, 255, 0.025) !important;
        }

        .form-control-dark, .form-select-dark {
            background-color: rgba(13, 19, 33, 0.85);
            border: 1px solid rgba(255, 255, 255, 0.1);
            color: #f8fafc;
            border-radius: 8px;
            font-size: 0.85rem;
        }
        .form-control-dark:focus, .form-select-dark:focus {
            background-color: rgba(17, 24, 39, 0.95);
            border-color: #6366f1;
            color: #f8fafc;
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.2);
        }
        .form-control-dark::placeholder {
            color: #64748b;
        }

        .btn-action {
            padding: 3px 8px;
            font-size: 0.78rem;
            border-radius: 6px;
            transition: all 0.15s;
        }

        /* Toast Container */
        #toast-container {
            position: fixed;
            top: 24px;
            right: 24px;
            z-index: 1090;
            display: flex;
            flex-direction: column;
            gap: 10px;
            pointer-events: none;
        }
        .toast-item {
            min-width: 280px;
            max-width: 420px;
            padding: 12px 16px;
            border-radius: 10px;
            background: rgba(15, 23, 42, 0.95);
            backdrop-filter: blur(14px);
            border: 1px solid rgba(255, 255, 255, 0.12);
            color: #fff;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
            pointer-events: auto;
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 0.88rem;
            animation: toastIn 0.25s cubic-bezier(0.16, 1, 0.3, 1) forwards;
        }
        @keyframes toastIn {
            from { transform: translateX(50px); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        @keyframes toastOut {
            from { transform: translateX(0); opacity: 1; }
            to { transform: translateX(50px); opacity: 0; }
        }

        .spinning {
            animation: spin 0.8s linear infinite;
        }
        @keyframes spin {
            100% { transform: rotate(360deg); }
        }

        .progress-bar-custom {
            height: 6px;
            border-radius: 3px;
            background: rgba(255, 255, 255, 0.08);
            overflow: hidden;
        }
        .progress-bar-fill {
            height: 100%;
            border-radius: 3px;
            transition: width 0.4s ease;
        }

        .country-tag-btn {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            color: #cbd5e1;
            font-size: 0.75rem;
            padding: 2px 8px;
            border-radius: 20px;
            cursor: pointer;
            transition: all 0.15s;
        }
        .country-tag-btn:hover, .country-tag-btn.active {
            background: rgba(99, 102, 241, 0.25);
            border-color: #6366f1;
            color: #fff;
        }
    </style>
</head>
<body class="pb-5">
    <!-- Toast Notifications -->
    <div id="toast-container"></div>

    <!-- Header Navigation -->
    <nav class="navbar navbar-expand-lg navbar-custom py-3 mb-4">
        <div class="container-fluid px-lg-4">
            <div class="d-flex align-items-center gap-3">
                <div class="brand-icon">
                    <i class="bi bi-shield-shaded"></i>
                </div>
                <div>
                    <div class="d-flex align-items-center gap-2">
                        <h4 class="fw-bold mb-0 text-white tracking-tight">多渠道动态 IP 代理池</h4>
                        <span class="badge bg-primary-subtle text-primary border border-primary-subtle rounded-pill small">v1.1.0</span>
                    </div>
                    <div class="d-flex align-items-center gap-2 mt-1">
                        <span class="live-dot"></span>
                        <small class="text-secondary" style="font-size: 0.82rem;">实时抓取 · Telegram 监听 · 自动测活 · 纯净度检测 · 极速出池</small>
                    </div>
                </div>
            </div>

            <div class="d-flex align-items-center gap-2 mt-3 mt-lg-0 flex-wrap">
                <div class="d-flex align-items-center px-3 py-1 rounded-pill" style="background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);">
                    <div class="form-check form-switch m-0 d-flex align-items-center p-0">
                        <input class="form-check-input me-2 mt-0" type="checkbox" id="auto-refresh-toggle" checked onchange="toggleAutoRefresh(this.checked)">
                        <label class="form-check-label small text-secondary" for="auto-refresh-toggle">
                            自动刷新 (<span id="refresh-countdown">30</span>s)
                        </label>
                    </div>
                </div>
                <button class="btn btn-sm btn-outline-secondary text-light d-flex align-items-center gap-1" id="btn-refresh" onclick="manualRefresh(true)" title="手动刷新当前数据">
                    <i class="bi bi-arrow-clockwise" id="refresh-icon"></i>
                    <span>刷新</span>
                </button>
                <button class="btn btn-sm btn-primary d-flex align-items-center gap-1" data-bs-toggle="modal" data-bs-target="#importModal">
                    <i class="bi bi-plus-lg"></i>
                    <span>批量导入</span>
                </button>
                <a href="/docs" target="_blank" class="btn btn-sm btn-outline-secondary d-flex align-items-center gap-1" title="查看 OpenAPI / Swagger 文档">
                    <i class="bi bi-book"></i>
                    <span>API 文档</span>
                </a>
            </div>
        </div>
    </nav>

    <div class="container-fluid px-lg-4">
        <!-- 6 KPI Stat Cards -->
        <div class="row g-3 mb-4">
            <div class="col-xl-2 col-md-4 col-6">
                <div class="glass-card stat-card" style="--card-accent: #10b981;">
                    <div class="d-flex justify-content-between align-items-start">
                        <span class="text-secondary small fw-semibold">在线存活代理</span>
                        <div class="stat-icon text-success"><i class="bi bi-check-circle-fill"></i></div>
                    </div>
                    <div class="stat-val text-success" id="stat-active">-</div>
                    <div class="small text-secondary mt-1">池内健康活跃可用</div>
                </div>
            </div>
            <div class="col-xl-2 col-md-4 col-6">
                <div class="glass-card stat-card" style="--card-accent: #06b6d4;">
                    <div class="d-flex justify-content-between align-items-start">
                        <span class="text-secondary small fw-semibold">纯净节点 (A/B)</span>
                        <div class="stat-icon text-info"><i class="bi bi-shield-check"></i></div>
                    </div>
                    <div class="stat-val text-info" id="stat-clean">-</div>
                    <div class="small text-secondary mt-1">高信誉抗封防风控</div>
                </div>
            </div>
            <div class="col-xl-2 col-md-4 col-6">
                <div class="glass-card stat-card" style="--card-accent: #8b5cf6;">
                    <div class="d-flex justify-content-between align-items-start">
                        <span class="text-secondary small fw-semibold">住宅宽带 (ISP)</span>
                        <div class="stat-icon" style="color: #c084fc;"><i class="bi bi-house-check-fill"></i></div>
                    </div>
                    <div class="stat-val" style="color: #c084fc;" id="stat-res">-</div>
                    <div class="small text-secondary mt-1">真实家庭住宅宽带</div>
                </div>
            </div>
            <div class="col-xl-2 col-md-4 col-6">
                <div class="glass-card stat-card" style="--card-accent: #64748b;">
                    <div class="d-flex justify-content-between align-items-start">
                        <span class="text-secondary small fw-semibold">候选代理总数</span>
                        <div class="stat-icon text-secondary"><i class="bi bi-database-fill"></i></div>
                    </div>
                    <div class="stat-val text-light" id="stat-total">-</div>
                    <div class="small text-secondary mt-1">抓取与导入待测库</div>
                </div>
            </div>
            <div class="col-xl-2 col-md-4 col-6">
                <div class="glass-card stat-card" style="--card-accent: #f59e0b;">
                    <div class="d-flex justify-content-between align-items-start">
                        <span class="text-secondary small fw-semibold">平均响应延迟</span>
                        <div class="stat-icon text-warning"><i class="bi bi-lightning-charge-fill"></i></div>
                    </div>
                    <div class="stat-val text-warning" id="stat-latency">- ms</div>
                    <div class="small text-secondary mt-1">Cloudflare 握手延迟</div>
                </div>
            </div>
            <div class="col-xl-2 col-md-4 col-6">
                <div class="glass-card stat-card" style="--card-accent: #3b82f6;">
                    <div class="d-flex justify-content-between align-items-start">
                        <span class="text-secondary small fw-semibold">覆盖国家/地区</span>
                        <div class="stat-icon text-primary"><i class="bi bi-globe2"></i></div>
                    </div>
                    <div class="stat-val text-primary" id="stat-countries">-</div>
                    <div class="small text-secondary mt-1">全球多地域节点</div>
                </div>
            </div>
        </div>

        <!-- Visual Analytics Row (Distributions) -->
        <div class="row g-3 mb-4">
            <div class="col-lg-6">
                <div class="glass-card p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <span class="fw-bold"><i class="bi bi-bar-chart-fill text-primary me-2"></i>热门国家分布排行 (Top 5)</span>
                        <span class="text-secondary small" id="country-total-hint">按在线节点数排序</span>
                    </div>
                    <div id="top-countries-list" class="d-flex flex-column gap-2">
                        <div class="text-secondary small py-2 text-center">正在统计国家分布...</div>
                    </div>
                </div>
            </div>
            <div class="col-lg-6">
                <div class="glass-card p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <span class="fw-bold"><i class="bi bi-pie-chart-fill text-info me-2"></i>协议结构与纯净度构成</span>
                        <span class="text-secondary small">在线节点属性透视</span>
                    </div>
                    <!-- Protocol stacked progress -->
                    <div class="mb-3">
                        <div class="d-flex justify-content-between small text-secondary mb-1">
                            <span>协议类型分布</span>
                            <span id="protocol-summary-text">-</span>
                        </div>
                        <div class="progress-bar-custom d-flex" id="protocol-progress-bar">
                            <div class="progress-bar-fill" style="width: 100%; background: #3b82f6;"></div>
                        </div>
                        <div class="d-flex gap-3 mt-2 small flex-wrap" id="protocol-legend">
                            <!-- Populated dynamically -->
                        </div>
                    </div>
                    <!-- Purity level cards -->
                    <div class="row g-2" id="purity-summary-cards">
                        <div class="col-6 col-sm-3">
                            <div class="p-2 rounded text-center" style="background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.2);">
                                <div class="small text-success fw-bold">A级 纯净住宅</div>
                                <div class="fw-bold fs-5 text-success" id="purity-count-a">-</div>
                            </div>
                        </div>
                        <div class="col-6 col-sm-3">
                            <div class="p-2 rounded text-center" style="background: rgba(6, 182, 212, 0.08); border: 1px solid rgba(6, 182, 212, 0.2);">
                                <div class="small text-info fw-bold">B级 良好节点</div>
                                <div class="fw-bold fs-5 text-info" id="purity-count-b">-</div>
                            </div>
                        </div>
                        <div class="col-6 col-sm-3">
                            <div class="p-2 rounded text-center" style="background: rgba(148, 163, 184, 0.08); border: 1px solid rgba(148, 163, 184, 0.2);">
                                <div class="small text-secondary fw-bold">C级 机房常规</div>
                                <div class="fw-bold fs-5 text-light" id="purity-count-c">-</div>
                            </div>
                        </div>
                        <div class="col-6 col-sm-3">
                            <div class="p-2 rounded text-center" style="background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.2);">
                                <div class="small text-danger fw-bold">D级 风险标记</div>
                                <div class="fw-bold fs-5 text-danger" id="purity-count-d">-</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Quick Extract Box -->
        <div class="glass-card p-4 mb-4" style="border-left: 4px solid #f59e0b;">
            <div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
                <div>
                    <h5 class="fw-bold m-0 text-white"><i class="bi bi-lightning-charge-fill text-warning me-2"></i>快捷提取代理 · 快捷出池最优节点</h5>
                    <small class="text-secondary">实时算法综合纯净度等级、节点健康分与 Cloudflare 延迟即时调度出池</small>
                </div>
                <div class="d-flex align-items-center gap-1 flex-wrap">
                    <span class="small text-secondary me-1">热门地区:</span>
                    <button class="country-tag-btn" onclick="selectQuickCountry('')">全部</button>
                    <button class="country-tag-btn" onclick="selectQuickCountry('PH')">🇵🇭 菲律宾</button>
                    <button class="country-tag-btn" onclick="selectQuickCountry('US')">🇺🇸 美国</button>
                    <button class="country-tag-btn" onclick="selectQuickCountry('HK')">🇭🇰 香港</button>
                    <button class="country-tag-btn" onclick="selectQuickCountry('JP')">🇯🇵 日本</button>
                    <button class="country-tag-btn" onclick="selectQuickCountry('SG')">🇸🇬 新加坡</button>
                </div>
            </div>

            <div class="row g-2 align-items-center">
                <div class="col-md-2 col-6">
                    <input type="text" class="form-control form-control-dark" id="quick-country" placeholder="国家 (如 PH, US)">
                </div>
                <div class="col-md-2 col-6">
                    <select class="form-select form-select-dark" id="quick-proto">
                        <option value="">所有协议 (默认)</option>
                        <option value="socks5">SOCKS5</option>
                        <option value="http">HTTP</option>
                        <option value="https">HTTPS</option>
                    </select>
                </div>
                <div class="col-md-2 col-6">
                    <select class="form-select form-select-dark" id="quick-type">
                        <option value="">全部类型</option>
                        <option value="residential">🏠 住宅宽带</option>
                        <option value="datacenter">🏢 机房 IP</option>
                        <option value="mobile">📱 移动网络</option>
                    </select>
                </div>
                <div class="col-md-2 col-6">
                    <select class="form-select form-select-dark" id="quick-clean">
                        <option value="">全部纯净度</option>
                        <option value="clean" selected>纯净节点 (A/B)</option>
                        <option value="A">A级 超纯住宅</option>
                        <option value="B">B级 良好节点</option>
                    </select>
                </div>
                <div class="col-md-4 col-12">
                    <button class="btn btn-warning w-100 fw-bold d-flex align-items-center justify-content-center gap-2" id="btn-quick-fetch" onclick="quickFetchProxy()">
                        <i class="bi bi-lightning-charge-fill"></i>
                        <span>提取最优代理</span>
                    </button>
                </div>
                <div class="col-12 mt-3">
                    <div id="quick-result" class="p-3 rounded border border-secondary border-opacity-25" style="background: rgba(10, 15, 28, 0.7);">
                        <span class="text-secondary small"><i class="bi bi-info-circle me-1"></i>配置筛选条件后点击上方按钮，即刻获取当前最优可用节点并可实时测活验证或一键复制 cURL / URL。</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- Proxy List Section -->
        <div class="glass-card p-3">
            <div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
                <div class="d-flex align-items-center gap-2">
                    <h5 class="fw-bold m-0 text-white"><i class="bi bi-list-columns-reverse me-2 text-primary"></i>可用代理节点列表</h5>
                    <span class="badge bg-secondary-subtle text-secondary" id="table-count-badge">- 条可用</span>
                </div>
                <div class="d-flex gap-2 align-items-center flex-wrap">
                    <div class="input-group input-group-sm" style="width: 160px;">
                        <span class="input-group-text bg-dark border-secondary border-opacity-25 text-secondary"><i class="bi bi-search"></i></span>
                        <input type="text" class="form-control form-control-dark border-start-0" id="filter-search" placeholder="检索 IP / 端口..." oninput="handleSearchInput()">
                    </div>
                    <select class="form-select form-select-sm form-select-dark" style="width: 130px;" id="filter-country" onchange="loadProxies(1)">
                        <option value="">全部国家</option>
                    </select>
                    <select class="form-select form-select-sm form-select-dark" style="width: 100px;" id="filter-proto" onchange="loadProxies(1)">
                        <option value="">全部协议</option>
                        <option value="socks5">SOCKS5</option>
                        <option value="http">HTTP</option>
                        <option value="https">HTTPS</option>
                    </select>
                    <select class="form-select form-select-sm form-select-dark" style="width: 115px;" id="filter-type" onchange="loadProxies(1)">
                        <option value="">全部类型</option>
                        <option value="residential">🏠 住宅 ISP</option>
                        <option value="datacenter">🏢 机房 IDC</option>
                        <option value="mobile">📱 移动网络</option>
                    </select>
                    <select class="form-select form-select-sm form-select-dark" style="width: 130px;" id="filter-purity" onchange="loadProxies(1)">
                        <option value="">全部纯净度</option>
                        <option value="clean">纯净节点 (A/B)</option>
                        <option value="A">A级 · 超纯住宅</option>
                        <option value="B">B级 · 良好节点</option>
                        <option value="C">C级 · 机房常规</option>
                        <option value="D">D级 · 风险标记</option>
                    </select>
                    <button class="btn btn-sm btn-outline-success d-flex align-items-center gap-1" id="btn-batch-check" onclick="checkCurrentPageProxies()" title="对当前表格展示的节点进行并发实时测活验证">
                        <i class="bi bi-activity"></i>
                        <span>实时测活</span>
                    </button>
                    <div class="dropdown">
                        <button class="btn btn-sm btn-outline-secondary dropdown-toggle d-flex align-items-center gap-1" type="button" data-bs-toggle="dropdown">
                            <i class="bi bi-download"></i>
                            <span>导出</span>
                        </button>
                        <ul class="dropdown-menu dropdown-menu-dark dropdown-menu-end">
                            <li><a class="dropdown-item small" href="#" onclick="batchExport('url')"><i class="bi bi-link-45deg me-2"></i>复制为 Proxy URL (当前页)</a></li>
                            <li><a class="dropdown-item small" href="#" onclick="batchExport('pipe')"><i class="bi bi-terminal me-2"></i>复制为 IP:Port:User:Pass</a></li>
                        </ul>
                    </div>
                </div>
            </div>

            <div class="table-responsive">
                <table class="table table-custom align-middle">
                    <thead>
                        <tr>
                            <th>国家 / 地区</th>
                            <th>协议</th>
                            <th>IP : 端口</th>
                            <th>纯净度 / 类型</th>
                            <th>认证账号</th>
                            <th>认证密码</th>
                            <th>延迟</th>
                            <th>健康分</th>
                            <th class="text-end" style="min-width: 220px;">实时测活与操作</th>
                        </tr>
                    </thead>
                    <tbody id="proxy-table-body">
                        <tr><td colspan="9" class="text-center text-secondary py-4"><span class="spinner-border spinner-border-sm me-2"></span>正在加载代理节点...</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- Table Pagination Bar -->
            <div class="d-flex justify-content-between align-items-center mt-3 pt-3 border-top border-secondary border-opacity-25 flex-wrap gap-2">
                <div class="small text-secondary" id="pagination-info">
                    显示中...
                </div>
                <div class="d-flex align-items-center gap-2">
                    <select class="form-select form-select-sm form-select-dark" style="width: 105px;" id="page-size-select" onchange="changePageSize()">
                        <option value="20">20 条/页</option>
                        <option value="50" selected>50 条/页</option>
                        <option value="100">100 条/页</option>
                    </select>
                    <button class="btn btn-sm btn-outline-secondary" id="btn-prev-page" onclick="prevPage()" disabled>
                        <i class="bi bi-chevron-left"></i> 上一页
                    </button>
                    <span class="small px-2 text-light" id="page-display">第 1 页</span>
                    <button class="btn btn-sm btn-outline-secondary" id="btn-next-page" onclick="nextPage()">
                        下一页 <i class="bi bi-chevron-right"></i>
                    </button>
                </div>
            </div>
        </div>
    </div>

    <!-- Import Modal -->
    <div class="modal fade" id="importModal" tabindex="-1">
        <div class="modal-dialog modal-lg">
            <div class="modal-content glass-card text-light">
                <div class="modal-header border-secondary border-opacity-25">
                    <div class="d-flex align-items-center gap-2">
                        <i class="bi bi-plus-circle text-primary fs-5"></i>
                        <h5 class="modal-title fw-bold">手动批量导入代理节点</h5>
                    </div>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <div class="p-3 rounded mb-3" style="background: rgba(10, 15, 28, 0.7); border: 1px solid rgba(255,255,255,0.08);">
                        <span class="small fw-bold text-light d-block mb-1">💡 智能正则解析引擎支持以下格式（每行一个，混合文本自动提取）：</span>
                        <div class="d-flex flex-wrap gap-2 small font-monospace mt-2">
                            <span class="code-box">ip:port:user:pass</span>
                            <span class="code-box">socks5://user:pass@ip:port</span>
                            <span class="code-box">http://ip:port</span>
                            <span class="code-box">ss://BASE64...</span>
                        </div>
                    </div>

                    <div class="mb-3">
                        <label class="form-label small text-secondary">粘贴代理文本或 TG 群抓取日志：</label>
                        <textarea class="form-control form-control-dark font-monospace" rows="7" id="import-text" placeholder="粘贴代理数据，例如：
103.152.112.5:1080:user:pwd
socks5://proxyuser:proxypwd@123.45.67.89:1080
45.76.12.34:8080"></textarea>
                    </div>

                    <div class="row g-3 align-items-center">
                        <div class="col-md-6">
                            <label class="form-label small text-secondary">或直接上传 .txt / .json 文件：</label>
                            <input type="file" class="form-control form-control-dark" id="import-file" accept=".txt,.json,.csv">
                        </div>
                        <div class="col-md-6">
                            <label class="form-label small text-secondary">默认缺失协议填充：</label>
                            <select class="form-select form-select-dark" id="import-proto">
                                <option value="socks5">SOCKS5</option>
                                <option value="http" selected>HTTP</option>
                                <option value="https">HTTPS</option>
                            </select>
                        </div>
                    </div>
                </div>
                <div class="modal-footer border-secondary border-opacity-25">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                    <button type="button" class="btn btn-primary d-flex align-items-center gap-1" id="btn-import-submit" onclick="submitImport()">
                        <i class="bi bi-cloud-arrow-up"></i>
                        <span>确认导入并异步测活</span>
                    </button>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        const COUNTRY_FLAGS = {
            'PH': { flag: '🇵🇭', name: '菲律宾' },
            'US': { flag: '🇺🇸', name: '美国' },
            'HK': { flag: '🇭🇰', name: '香港' },
            'SG': { flag: '🇸🇬', name: '新加坡' },
            'JP': { flag: '🇯🇵', name: '日本' },
            'TW': { flag: '🇹🇼', name: '台湾' },
            'KR': { flag: '🇰🇷', name: '韩国' },
            'GB': { flag: '🇬🇧', name: '英国' },
            'DE': { flag: '🇩🇪', name: '德国' },
            'VN': { flag: '🇻🇳', name: '越南' },
            'TH': { flag: '🇹🇭', name: '泰国' },
            'ID': { flag: '🇮🇩', name: '印尼' },
            'MY': { flag: '🇲🇾', name: '马来西亚' },
            'IN': { flag: '🇮🇳', name: '印度' },
            'AU': { flag: '🇦🇺', name: '澳大利亚' },
            'CA': { flag: '🇨🇦', name: '加拿大' },
            'FR': { flag: '🇫🇷', name: '法国' },
            'NL': { flag: '🇳🇱', name: '荷兰' },
            'RU': { flag: '🇷🇺', name: '俄罗斯' },
            'BR': { flag: '🇧🇷', name: '巴西' },
            'CN': { flag: '🇨🇳', name: '中国' }
        };

        let currentPage = 1;
        let pageSize = 50;
        let currentLoadedList = [];
        let autoRefreshTimer = null;
        let countdownValue = 30;
        let isAutoRefresh = true;

        function getCountryMeta(code) {
            if (!code || code === 'UNKNOWN') return { flag: '🌐', name: '未知' };
            const upper = code.toUpperCase();
            if (COUNTRY_FLAGS[upper]) return COUNTRY_FLAGS[upper];
            return { flag: '🌐', name: upper };
        }

        function showToast(msg, type = 'info') {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = 'toast-item';
            const iconMap = {
                success: '<i class="bi bi-check-circle-fill text-success fs-5"></i>',
                warning: '<i class="bi bi-exclamation-triangle-fill text-warning fs-5"></i>',
                error: '<i class="bi bi-x-circle-fill text-danger fs-5"></i>',
                info: '<i class="bi bi-info-circle-fill text-info fs-5"></i>'
            };
            toast.innerHTML = `
                ${iconMap[type] || iconMap.info}
                <div class="flex-grow-1">${msg}</div>
                <button type="button" class="btn-close btn-close-white ms-2" style="font-size: 0.65rem;" onclick="this.parentElement.remove()"></button>
            `;
            container.appendChild(toast);
            setTimeout(() => {
                toast.style.animation = 'toastOut 0.25s forwards';
                setTimeout(() => toast.remove(), 250);
            }, 2800);
        }

        function fallbackCopy(text, btnElement) {
            let succeeded = false;
            try {
                const ta = document.createElement('textarea');
                ta.value = text;
                ta.style.position = 'fixed';
                ta.style.top = '0';
                ta.style.left = '-9999px';
                ta.style.opacity = '0';
                ta.setAttribute('readonly', '');
                document.body.appendChild(ta);

                ta.focus();
                ta.select();
                ta.setSelectionRange(0, text.length);

                succeeded = document.execCommand('copy');
                document.body.removeChild(ta);
            } catch (err) {
                console.error('fallbackCopy error', err);
                succeeded = false;
            }

            if (succeeded) {
                handleCopySuccess(btnElement);
            } else {
                window.prompt('请手动全选复制以下内容 (Ctrl+C / Cmd+C):', text);
            }
        }

        function copyText(text, btnElement = null) {
            if (!text) {
                showToast('复制内容为空', 'warning');
                return;
            }

            const isSecure = window.isSecureContext || window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
            if (isSecure && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
                navigator.clipboard.writeText(text).then(() => {
                    handleCopySuccess(btnElement);
                }).catch(() => {
                    fallbackCopy(text, btnElement);
                });
            } else {
                fallbackCopy(text, btnElement);
            }
        }

        function handleCopySuccess(btn) {
            showToast('已成功复制到剪贴板！', 'success');
            if (btn) {
                const origHtml = btn.innerHTML;
                btn.innerHTML = '<i class="bi bi-check-lg text-success"></i>';
                setTimeout(() => { btn.innerHTML = origHtml; }, 1200);
            }
        }

        async function loadStats() {
            try {
                const res = await fetch('/api/v1/stats');
                if (!res.ok) return;
                const data = await res.json();

                document.getElementById('stat-active').innerText = data.active_proxies || 0;
                document.getElementById('stat-clean').innerText = data.clean_proxies || 0;
                document.getElementById('stat-res').innerText = data.residential_proxies || 0;
                document.getElementById('stat-total').innerText = data.total_proxies || 0;
                document.getElementById('stat-latency').innerText = data.avg_latency_ms ? data.avg_latency_ms + ' ms' : '- ms';

                const countries = Object.keys(data.by_country || {});
                document.getElementById('stat-countries').innerText = countries.length;

                // Populate Top Countries List
                const topListContainer = document.getElementById('top-countries-list');
                if (countries.length === 0) {
                    topListContainer.innerHTML = '<div class="text-secondary small py-2 text-center">暂无国家分布数据</div>';
                } else {
                    const sortedCountries = countries.sort((a, b) => data.by_country[b] - data.by_country[a]).slice(0, 5);
                    const totalActive = data.active_proxies || 1;
                    topListContainer.innerHTML = sortedCountries.map(c => {
                        const count = data.by_country[c];
                        const pct = Math.round((count / totalActive) * 100);
                        const meta = getCountryMeta(c);
                        return `
                            <div>
                                <div class="d-flex justify-content-between small mb-1">
                                    <span>${meta.flag} <strong class="text-light">${meta.name}</strong> <span class="text-secondary">(${c})</span></span>
                                    <span><span class="text-info fw-bold">${count}</span> 个节点 <span class="text-secondary">(${pct}%)</span></span>
                                </div>
                                <div class="progress-bar-custom">
                                    <div class="progress-bar-fill" style="width: ${pct}%; background: linear-gradient(90deg, #3b82f6, #8b5cf6);"></div>
                                </div>
                            </div>
                        `;
                    }).join('');
                }

                // Update Protocol & Purity Distribution
                const protos = data.by_protocol || {};
                const totalActive = data.active_proxies || 1;
                const pSocks5 = protos['socks5'] || 0;
                const pHttp = (protos['http'] || 0) + (protos['https'] || 0);

                const socks5Pct = Math.round((pSocks5 / totalActive) * 100);
                const httpPct = Math.round((pHttp / totalActive) * 100);

                document.getElementById('protocol-summary-text').innerText = `SOCKS5: ${pSocks5} | HTTP(S): ${pHttp}`;
                document.getElementById('protocol-progress-bar').innerHTML = `
                    <div class="progress-bar-fill" style="width: ${socks5Pct}%; background: #8b5cf6;" title="SOCKS5: ${socks5Pct}%"></div>
                    <div class="progress-bar-fill" style="width: ${httpPct}%; background: #06b6d4;" title="HTTP/HTTPS: ${httpPct}%"></div>
                `;
                document.getElementById('protocol-legend').innerHTML = `
                    <span class="d-flex align-items-center gap-1"><span class="badge badge-socks5">SOCKS5</span> ${pSocks5} (${socks5Pct}%)</span>
                    <span class="d-flex align-items-center gap-1"><span class="badge badge-http">HTTP(S)</span> ${pHttp} (${httpPct}%)</span>
                `;

                // Update Purity Cards
                const cleans = data.by_clean_level || {};
                document.getElementById('purity-count-a').innerText = cleans['A'] || 0;
                document.getElementById('purity-count-b').innerText = cleans['B'] || 0;
                document.getElementById('purity-count-c').innerText = cleans['C'] || 0;
                document.getElementById('purity-count-d').innerText = cleans['D'] || 0;

                // Sync Country options in Table Filter
                const filterCountrySelect = document.getElementById('filter-country');
                const prevVal = filterCountrySelect.value;
                const countryOptions = ['<option value="">全部国家</option>'];
                countries.sort().forEach(c => {
                    const meta = getCountryMeta(c);
                    countryOptions.push(`<option value="${c}">${meta.flag} ${meta.name} (${c}) - ${data.by_country[c]}</option>`);
                });
                filterCountrySelect.innerHTML = countryOptions.join('');
                filterCountrySelect.value = prevVal;

            } catch (e) {
                console.error('loadStats error', e);
            }
        }

        function getPurityBadge(p) {
            let lvl = '';
            const cLvl = (p.clean_level || 'C').toUpperCase();
            if (cLvl === 'A') {
                lvl = '<span class="badge-purity badge-purity-a" title="A级: 超纯净住宅宽带"><i class="bi bi-shield-fill-check"></i> A 住宅</span>';
            } else if (cLvl === 'B') {
                lvl = '<span class="badge-purity badge-purity-b" title="B级: 良好节点"><i class="bi bi-shield-check"></i> B 良好</span>';
            } else if (cLvl === 'C') {
                lvl = '<span class="badge-purity badge-purity-c" title="C级: 机房常规节点"><i class="bi bi-building"></i> C 机房</span>';
            } else {
                lvl = '<span class="badge-purity badge-purity-d" title="D级: 风险标记节点"><i class="bi bi-shield-slash"></i> D 风险</span>';
            }

            const typeStr = p.ip_type === 'residential' ? '住宅' : (p.ip_type === 'mobile' ? '移动' : (p.ip_type === 'datacenter' ? '机房' : ''));
            const typeBadge = typeStr ? `<span class="badge bg-dark border border-secondary text-secondary ms-1" style="font-size: 0.72rem;">${typeStr}</span>` : '';

            const gIcon = p.google_clean
                ? '<span class="text-success ms-1" title="Google 搜索免验证"><i class="bi bi-google"></i></span>'
                : '<span class="text-secondary opacity-25 ms-1" title="未经验证"><i class="bi bi-google"></i></span>';

            return lvl + typeBadge + gIcon;
        }

        function getLatencyBadge(lat) {
            if (lat === null || lat === undefined) {
                return '<span class="latency-indicator latency-none"><span class="latency-dot"></span>- ms</span>';
            }
            if (lat < 600) {
                return `<span class="latency-indicator latency-fast" title="极速网络"><span class="latency-dot"></span>${lat} ms</span>`;
            }
            if (lat < 1400) {
                return `<span class="latency-indicator latency-med" title="延迟良好"><span class="latency-dot"></span>${lat} ms</span>`;
            }
            return `<span class="latency-indicator latency-slow" title="延迟较高"><span class="latency-dot"></span>${lat} ms</span>`;
        }

        async function loadProxies(page = 1) {
            currentPage = page;
            const country = document.getElementById('filter-country').value.trim();
            const proto = document.getElementById('filter-proto').value;
            const ipType = document.getElementById('filter-type').value;
            const purity = document.getElementById('filter-purity').value;
            const searchKw = document.getElementById('filter-search').value.trim().toLowerCase();

            const offset = (currentPage - 1) * pageSize;
            let url = `/api/v1/proxies?limit=${pageSize}&offset=${offset}&is_active=true`;
            if (country) url += '&country=' + encodeURIComponent(country.toUpperCase());
            if (proto) url += '&protocol=' + encodeURIComponent(proto);
            if (ipType) url += '&ip_type=' + encodeURIComponent(ipType);
            if (purity === 'clean') {
                url += '&clean_only=true';
            } else if (purity && ['A', 'B', 'C', 'D'].includes(purity)) {
                url += '&clean_level=' + encodeURIComponent(purity);
            }

            const tbody = document.getElementById('proxy-table-body');
            tbody.innerHTML = '<tr><td colspan="9" class="text-center text-secondary py-4"><span class="spinner-border spinner-border-sm me-2"></span>加载数据中...</td></tr>';

            try {
                const res = await fetch(url);
                if (!res.ok) throw new Error('网络请求异常 ' + res.status);
                let list = await res.json();

                if (searchKw) {
                    list = list.filter(p => {
                        const target = `${p.ip}:${p.port} ${p.username || ''} ${p.country || ''}`.toLowerCase();
                        return target.includes(searchKw);
                    });
                }
                currentLoadedList = list;

                document.getElementById('table-count-badge').innerText = `${list.length} 条已加载`;

                if (!list.length) {
                    tbody.innerHTML = '<tr><td colspan="9" class="text-center text-secondary py-4"><i class="bi bi-inbox fs-4 d-block mb-1"></i>暂无符合条件的可用代理节点</td></tr>';
                    document.getElementById('pagination-info').innerText = '第 0 页 · 共 0 条';
                    document.getElementById('btn-prev-page').disabled = (currentPage <= 1);
                    document.getElementById('btn-next-page').disabled = true;
                    return;
                }

                tbody.innerHTML = list.map((p, idx) => {
                    const meta = getCountryMeta(p.country);
                    const fullUrl = (p.username && p.password)
                        ? `${p.protocol}://${p.username}:${p.password}@${p.ip}:${p.port}`
                        : `${p.protocol}://${p.ip}:${p.port}`;
                    const pipeFormat = (p.username && p.password)
                        ? `${p.ip}:${p.port}:${p.username}:${p.password}`
                        : `${p.ip}:${p.port}`;
                    const curlCmd = `curl -x ${fullUrl} https://api.ipify.org`;

                    const badgeProto = p.protocol === 'socks5' ? 'badge-socks5' : (p.protocol === 'https' ? 'badge-https' : 'badge-http');
                    const pwId = `pw-cell-${idx}`;

                    return `
                        <tr id="proxy-row-${p.id}">
                            <td>
                                <span class="badge-country" title="${meta.name}">
                                    ${meta.flag} <span>${p.country || '??'}</span>
                                </span>
                            </td>
                            <td><span class="badge badge-protocol ${badgeProto}">${p.protocol}</span></td>
                            <td>
                                <span class="code-box cursor-pointer" onclick="copyText('${p.ip}:${p.port}', this)" title="点击复制 IP:Port">
                                    ${p.ip}:${p.port}
                                </span>
                            </td>
                            <td id="purity-cell-${p.id}">${getPurityBadge(p)}</td>
                            <td>
                                ${p.username ? `<span class="code-box" onclick="copyText('${p.username}', this)" title="点击复制账号">${p.username}</span>` : '<span class="text-secondary opacity-50">-</span>'}
                            </td>
                            <td>
                                ${p.password ? `
                                    <div class="d-inline-flex align-items-center gap-1">
                                        <span class="code-box font-monospace" id="${pwId}">••••••</span>
                                        <button class="btn btn-sm btn-link p-0 text-secondary" onclick="togglePasswordVisibility('${pwId}', '${p.password}')" title="显示/隐藏密码">
                                            <i class="bi bi-eye"></i>
                                        </button>
                                        <button class="btn btn-sm btn-link p-0 text-secondary" onclick="copyText('${p.password}', this)" title="复制密码">
                                            <i class="bi bi-clipboard"></i>
                                        </button>
                                    </div>
                                ` : '<span class="text-secondary opacity-50">-</span>'}
                            </td>
                            <td id="latency-cell-${p.id}">${getLatencyBadge(p.latency)}</td>
                            <td id="score-cell-${p.id}">
                                <span class="badge ${p.score >= 80 ? 'bg-success-subtle text-success border border-success-subtle' : 'bg-secondary-subtle text-light'}">${p.score}分</span>
                            </td>
                            <td class="text-end">
                                <div class="btn-group">
                                    <button class="btn btn-sm btn-outline-success btn-action" onclick="checkSingleProxy(${p.id}, this)" title="实时测活验证节点是否有效">
                                        <i class="bi bi-activity"></i> 测活
                                    </button>
                                    <button class="btn btn-sm btn-outline-info btn-action" onclick="copyText('${fullUrl}', this)" title="复制标准代理链接 (${fullUrl})">
                                        <i class="bi bi-link-45deg"></i> URL
                                    </button>
                                    <button class="btn btn-sm btn-outline-secondary btn-action text-light" onclick="copyText('${curlCmd}', this)" title="复制 cURL 测试命令">
                                        <i class="bi bi-terminal"></i> cURL
                                    </button>
                                    <button class="btn btn-sm btn-outline-secondary btn-action text-light" onclick="copyText('${pipeFormat}', this)" title="复制 ip:port:user:pass">
                                        <i class="bi bi-clipboard"></i>
                                    </button>
                                </div>
                            </td>
                        </tr>
                    `;
                }).join('');

                // Update Pagination Controls
                document.getElementById('pagination-info').innerText = `当前第 ${offset + 1} - ${offset + list.length} 条`;
                document.getElementById('page-display').innerText = `第 ${currentPage} 页`;
                document.getElementById('btn-prev-page').disabled = (currentPage <= 1);
                document.getElementById('btn-next-page').disabled = (list.length < pageSize);

            } catch (e) {
                console.error('loadProxies error', e);
                tbody.innerHTML = `<tr><td colspan="9" class="text-center text-danger py-4">加载代理数据失败: ${e.message}</td></tr>`;
            }
        }

        function togglePasswordVisibility(elementId, realPassword) {
            const el = document.getElementById(elementId);
            if (!el) return;
            if (el.innerText === '••••••') {
                el.innerText = realPassword;
            } else {
                el.innerText = '••••••';
            }
        }

        let searchDebounceTimer = null;
        function handleSearchInput() {
            clearTimeout(searchDebounceTimer);
            searchDebounceTimer = setTimeout(() => {
                loadProxies(1);
            }, 300);
        }

        function changePageSize() {
            pageSize = parseInt(document.getElementById('page-size-select').value, 10) || 50;
            loadProxies(1);
        }

        function prevPage() {
            if (currentPage > 1) {
                loadProxies(currentPage - 1);
            }
        }

        function nextPage() {
            loadProxies(currentPage + 1);
        }

        function selectQuickCountry(code) {
            document.getElementById('quick-country').value = code;
            document.querySelectorAll('.country-tag-btn').forEach(b => b.classList.remove('active'));
            if (event && event.target) event.target.classList.add('active');
            quickFetchProxy();
        }

        async function quickFetchProxy() {
            const country = document.getElementById('quick-country').value.trim();
            const proto = document.getElementById('quick-proto').value;
            const ipType = document.getElementById('quick-type').value;
            const purity = document.getElementById('quick-clean').value;

            const btn = document.getElementById('btn-quick-fetch');
            const resBox = document.getElementById('quick-result');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> 正在匹配最优节点...';

            let url = '/api/v1/proxy?';
            if (country) url += 'country=' + encodeURIComponent(country.toUpperCase()) + '&';
            if (proto) url += 'protocol=' + encodeURIComponent(proto) + '&';
            if (ipType) url += 'ip_type=' + encodeURIComponent(ipType) + '&';
            if (purity === 'clean') {
                url += 'clean_only=true&';
            } else if (purity && ['A', 'B', 'C', 'D'].includes(purity)) {
                url += 'clean_level=' + encodeURIComponent(purity) + '&';
            }

            try {
                const res = await fetch(url);
                if (!res.ok) {
                    resBox.innerHTML = `
                        <div class="d-flex align-items-center gap-2 text-danger">
                            <i class="bi bi-exclamation-octagon-fill fs-5"></i>
                            <span>未找到匹配的可用节点 (国家='${country || "ANY"}', 纯净/等级=${purity || "ANY"})，请调整条件或尝试导入新节点。</span>
                        </div>
                    `;
                    return;
                }

                const p = await res.json();
                const meta = getCountryMeta(p.country);
                const fullUrl = (p.username && p.password)
                    ? `${p.protocol}://${p.username}:${p.password}@${p.ip}:${p.port}`
                    : `${p.protocol}://${p.ip}:${p.port}`;
                const pipeFormat = (p.username && p.password)
                    ? `${p.ip}:${p.port}:${p.username}:${p.password}`
                    : `${p.ip}:${p.port}`;
                const curlCmd = `curl -x ${fullUrl} https://api.ipify.org`;

                resBox.innerHTML = `
                    <div class="d-flex justify-content-between align-items-center flex-wrap gap-2 mb-2 pb-2 border-bottom border-secondary border-opacity-25">
                        <div class="d-flex align-items-center gap-2">
                            <span class="badge-country">${meta.flag} ${meta.name} (${p.country})</span>
                            <span class="badge badge-protocol ${p.protocol === 'socks5' ? 'badge-socks5' : 'badge-http'}">${p.protocol}</span>
                            ${getPurityBadge(p)}
                            ${getLatencyBadge(p.latency)}
                            <span class="badge bg-success-subtle text-success">评分 ${p.score}</span>
                        </div>
                        <div class="text-secondary small">出池响应时间: 实时</div>
                    </div>
                    <div class="d-flex align-items-center justify-content-between flex-wrap gap-2">
                        <div class="d-flex align-items-center gap-2 flex-grow-1">
                            <span class="text-secondary small">URL:</span>
                            <code class="code-box text-info flex-grow-1 text-truncate" style="font-size: 0.9rem;">${fullUrl}</code>
                        </div>
                        <div class="d-flex gap-2">
                            <button class="btn btn-sm btn-outline-success d-flex align-items-center gap-1" onclick="checkSingleProxy(${p.id}, this)" title="实时测活验证当前节点">
                                <i class="bi bi-activity"></i> 实时测活
                            </button>
                            <button class="btn btn-sm btn-primary d-flex align-items-center gap-1" onclick="copyText('${fullUrl}', this)">
                                <i class="bi bi-clipboard"></i> 复制 URL
                            </button>
                            <button class="btn btn-sm btn-outline-light d-flex align-items-center gap-1" onclick="copyText('${pipeFormat}', this)">
                                复制 Pipe
                            </button>
                            <button class="btn btn-sm btn-outline-info d-flex align-items-center gap-1" onclick="copyText('${curlCmd}', this)">
                                <i class="bi bi-terminal"></i> 复制 cURL
                            </button>
                        </div>
                    </div>
                `;
                showToast(`已提取最优节点: [${p.country}] ${p.ip}:${p.port}`, 'success');
            } catch (e) {
                resBox.innerHTML = `<span class="text-danger">请求出池失败: ${e.message}</span>`;
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="bi bi-lightning-charge-fill"></i> <span>提取最优代理</span>';
            }
        }

        function batchExport(format) {
            if (!currentLoadedList || currentLoadedList.length === 0) {
                showToast('当前列表暂无代理节点可导出', 'warning');
                return;
            }

            let text = '';
            if (format === 'url') {
                text = currentLoadedList.map(p => {
                    return (p.username && p.password)
                        ? `${p.protocol}://${p.username}:${p.password}@${p.ip}:${p.port}`
                        : `${p.protocol}://${p.ip}:${p.port}`;
                }).join('\n');
            } else {
                text = currentLoadedList.map(p => {
                    return (p.username && p.password)
                        ? `${p.ip}:${p.port}:${p.username}:${p.password}`
                        : `${p.ip}:${p.port}`;
                }).join('\n');
            }

            copyText(text);
            showToast(`已导出 ${currentLoadedList.length} 个节点至剪贴板！`, 'success');
        }

        async function checkSingleProxy(proxyId, btn) {
            if (!proxyId) return;
            const origHtml = btn ? btn.innerHTML : '';
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 测活中...';
            }

            try {
                const res = await fetch(`/api/v1/proxy/${proxyId}/check`, { method: 'POST' });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '测活接口返回异常');

                if (data.is_alive) {
                    showToast(`[${data.ip}:${data.port}] 测活成功！延迟: ${data.latency}ms | 纯净度: ${data.clean_level}级`, 'success');
                    if (btn) {
                        btn.innerHTML = '<i class="bi bi-check-lg text-success"></i> 有效';
                        btn.className = 'btn btn-sm btn-outline-success btn-action';
                    }
                    const latCell = document.getElementById(`latency-cell-${proxyId}`);
                    if (latCell) latCell.innerHTML = getLatencyBadge(data.latency);
                    const scoreCell = document.getElementById(`score-cell-${proxyId}`);
                    if (scoreCell) scoreCell.innerHTML = `<span class="badge ${data.score >= 80 ? 'bg-success-subtle text-success border border-success-subtle' : 'bg-secondary-subtle text-light'}">${data.score}分</span>`;
                    const purityCell = document.getElementById(`purity-cell-${proxyId}`);
                    if (purityCell) purityCell.innerHTML = getPurityBadge(data);
                } else {
                    showToast(`[${data.ip}:${data.port}] 测活失败，已标记失效或移除`, 'warning');
                    if (btn) {
                        btn.innerHTML = '<i class="bi bi-x-circle text-danger"></i> 失效';
                        btn.className = 'btn btn-sm btn-outline-danger btn-action';
                    }
                    const row = document.getElementById(`proxy-row-${proxyId}`);
                    if (row) {
                        row.style.opacity = '0.4';
                        row.style.textDecoration = 'line-through';
                    }
                }
            } catch (e) {
                showToast('测活请求失败: ' + e.message, 'error');
                if (btn) {
                    btn.innerHTML = origHtml;
                    btn.disabled = false;
                }
            } finally {
                setTimeout(() => {
                    if (btn && btn.disabled) {
                        btn.disabled = false;
                        btn.innerHTML = origHtml;
                        btn.className = 'btn btn-sm btn-outline-success btn-action';
                    }
                }, 4000);
            }
        }

        async function checkCurrentPageProxies() {
            if (!currentLoadedList || currentLoadedList.length === 0) {
                showToast('当前列表暂无代理节点可测活', 'warning');
                return;
            }

            const btn = document.getElementById('btn-batch-check');
            const origHtml = btn ? btn.innerHTML : '';
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> 测活中...';
            }

            showToast(`开始对当前页 ${currentLoadedList.length} 个节点并发测活...`, 'info');
            const ids = currentLoadedList.map(p => p.id).filter(Boolean);

            try {
                const res = await fetch('/api/v1/check-batch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ proxy_ids: ids })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '批量测活失败');

                showToast(`批量测活完成: 存活 ${data.alive} / 异常 ${data.dead} (共 ${data.total} 个)`, data.alive > 0 ? 'success' : 'warning');
                await Promise.all([loadStats(), loadProxies(currentPage)]);
            } catch (e) {
                showToast('批量测活异常: ' + e.message, 'error');
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = origHtml;
                }
            }
        }

        async function submitImport() {
            const text = document.getElementById('import-text').value;
            const fileInput = document.getElementById('import-file');
            const defaultProto = document.getElementById('import-proto').value || 'http';
            const btn = document.getElementById('btn-import-submit');

            const hasFile = fileInput.files && fileInput.files.length > 0;
            const hasText = Boolean(text && text.trim());

            if (!hasFile && !hasText) {
                showToast('请粘贴代理文本或选择待导入的文件！', 'warning');
                return;
            }

            const formData = new FormData();
            formData.append('default_protocol', defaultProto);

            if (hasFile) {
                formData.append('file', fileInput.files[0]);
            }
            if (hasText) {
                formData.append('text', text.trim());
            }

            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> 正在解析入库...';

            try {
                const res = await fetch('/api/v1/import', { method: 'POST', body: formData });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '导入接口异常');

                if (data.imported === 0) {
                    showToast(data.message || '未能成功导入任何有效代理，请检查格式', 'warning');
                    return;
                }

                showToast(data.message || `成功入库 ${data.imported} 个候选代理！`, 'success');
                const modalEl = document.getElementById('importModal');
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();

                document.getElementById('import-text').value = '';
                fileInput.value = '';
                await manualRefresh(true);
            } catch (e) {
                showToast('导入失败: ' + e.message, 'error');
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="bi bi-cloud-arrow-up"></i> <span>确认导入并异步测活</span>';
            }
        }

        function toggleAutoRefresh(enabled) {
            isAutoRefresh = enabled;
            countdownValue = 30;
            document.getElementById('refresh-countdown').innerText = countdownValue;
            if (!enabled) {
                showToast('已暂停自动刷新', 'info');
            } else {
                showToast('已开启自动定时刷新 (30s)', 'info');
            }
        }

        async function manualRefresh(showNotification = false) {
            const btn = document.getElementById('btn-refresh');
            const icon = document.getElementById('refresh-icon');
            if (icon) icon.classList.add('spinning');
            if (btn) btn.disabled = true;

            try {
                await Promise.all([loadStats(), loadProxies(currentPage)]);
                countdownValue = 30;
                document.getElementById('refresh-countdown').innerText = countdownValue;
                if (showNotification) {
                    showToast('代理池数据已刷新！', 'success');
                }
            } catch (err) {
                if (showNotification) {
                    showToast('刷新数据失败: ' + err.message, 'error');
                }
            } finally {
                if (btn) btn.disabled = false;
                setTimeout(() => {
                    if (icon) icon.classList.remove('spinning');
                }, 600);
            }
        }

        function startCountdown() {
            if (autoRefreshTimer) clearInterval(autoRefreshTimer);
            autoRefreshTimer = setInterval(() => {
                if (!isAutoRefresh) return;
                countdownValue--;
                if (countdownValue <= 0) {
                    countdownValue = 30;
                    manualRefresh(false);
                }
                const el = document.getElementById('refresh-countdown');
                if (el) el.innerText = countdownValue;
            }, 1000);
        }

        window.addEventListener('DOMContentLoaded', () => {
            manualRefresh();
            startCountdown();
        });
    </script>
</body>
</html>"""
