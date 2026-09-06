#!/usr/bin/env python3
"""
Dynamic Proxy Pool Command Line Interface (CLI)
用法：
  python cli.py get [国家代码] [clean/residential] [协议]
    - 例如: python cli.py get PH              (普通提取菲律宾节点)
    - 例如: python cli.py get PH clean        (提取菲律宾高纯净节点)
    - 例如: python cli.py get US residential  (提取美国住宅宽带节点)
    - 例如: python cli.py get US clean socks5 (提取美国纯净SOCKS5)
  python cli.py list                  (查看存活国家与纯净分布)
  python cli.py stats                 (查看代理池指标与纯净度评级)
  python cli.py scrape                (立即触发一次全源采集)
  python cli.py check                 (立即触发一次待检队列测活与纯净度检测)
  python cli.py import <文件路径>      (导入代理文本文件)
"""
import sys
import asyncio
from storage import storage
from scheduler import scheduler
from extractor import extract_proxies_from_file, extract_proxies_from_text
from checker import checker


async def cmd_get(country: str = None, protocol: str = None, clean_only: bool = False, ip_type: str = None):
    await storage.init_db()
    proxy = await storage.get_proxy(
        country=country,
        protocol=protocol,
        clean_only=clean_only,
        ip_type=ip_type
    )
    if not proxy:
        filter_str = f"country='{country or 'ANY'}'"
        if protocol: filter_str += f", protocol='{protocol}'"
        if clean_only: filter_str += ", clean_only=True"
        if ip_type: filter_str += f", ip_type='{ip_type}'"
        print(f"❌ 未找到符合条件的可用代理: {filter_str}")
        return

    type_str = "🏠 住宅宽带 (Residential)" if proxy.ip_type == "residential" else ("📱 移动网络 (Mobile)" if proxy.ip_type == "mobile" else "🏢 机房节点 (DataCenter)")
    google_str = "🟢 Google通过 (免验证)" if proxy.google_clean else "⚪ Google未验/受限"

    print("\n" + "=" * 55)
    print(f"🌐 国家: {proxy.country} ({proxy.country_name or 'N/A'})")
    print(f"🛡️ 纯净评级: 【{proxy.clean_level} 级】 ({type_str})")
    print(f"🔍 风控指标: {google_str} | 欺诈分: {proxy.fraud_score}")
    print(f"🔌 协议: {proxy.protocol.upper()}")
    print(f"📍 地址: {proxy.ip}:{proxy.port}")
    if proxy.username:
        print(f"👤 账号: {proxy.username}")
    if proxy.password:
        print(f"🔑 密码: {proxy.password}")
    print(f"⚡ 延迟: {proxy.latency or 'N/A'} ms | 评分: {proxy.score}分")
    print(f"📋 连接URL: {proxy.to_url()}")
    print("=" * 55 + "\n")


async def cmd_list():
    await storage.init_db()
    stats = await storage.get_stats()
    print("\n🌍 存活代理国家分布：")
    print("-" * 40)
    print(f"  • 🛡️ 纯净节点 (A/B级) : {stats.clean_proxies:4d} 个")
    print(f"  • 🏠 住宅宽带 (ISP)    : {stats.residential_proxies:4d} 个\n")
    if not stats.by_country:
        print("  (暂无存活国家数据)")
    for code, count in stats.by_country.items():
        print(f"  • {code:5s} : {count:4d} 个可用")
    print("-" * 40 + "\n")


async def cmd_stats():
    await storage.init_db()
    stats = await storage.get_stats()
    print("\n📊 代理池运行与纯净度指标：")
    print("-" * 40)
    print(f"  • 收录总数: {stats.total_proxies} 个")
    print(f"  • 在线存活: {stats.active_proxies} 个")
    print(f"  • 🛡️ 纯净节点 (A/B级): {stats.clean_proxies} 个")
    print(f"  • 🏠 住宅宽带 (ISP):    {stats.residential_proxies} 个")
    print(f"  • 平均延迟: {stats.avg_latency_ms or '-'} ms")

    if stats.by_clean_level:
        print("  • 纯净度等级分布:")
        for lvl, count in sorted(stats.by_clean_level.items()):
            print(f"    - {lvl} 级: {count:4d} 个")

    print("  • 协议分布:")
    for proto, count in stats.by_protocol.items():
        print(f"    - {proto.upper():6s}: {count:4d} 个")
    print("-" * 40 + "\n")


async def cmd_scrape():
    await storage.init_db()
    print("[+] 正在执行全渠道采集任务 (MaskProxy + Geonode + Public Sources)...")
    await scheduler.scrape_job()
    stats = await storage.get_stats()
    print(f"[✅] 采集完成！当前候选库总数: {stats.total_proxies}")


async def cmd_check():
    await storage.init_db()
    print("[+] 正在执行待检节点批量测活与纯净度评测...")
    await scheduler.check_job()
    stats = await storage.get_stats()
    print(f"[✅] 测活完成！当前在线存活数: {stats.active_proxies}，纯净节点数: {stats.clean_proxies}")


async def cmd_import(file_path: str):
    await storage.init_db()
    try:
        proxies = extract_proxies_from_file(file_path, source=f"cli_import:{file_path}")
        if not proxies:
            print("[!] 未在文件中提取到有效代理。")
            return
        count = await storage.upsert_many(proxies)
        print(f"[✅] 成功导入 {count} 个代理候选！正在开始首轮并发测活与纯净度分析...")
        res = await checker.check_batch(proxies[:50])
        print(f"[✅] 首次测活完成: 存活 {res['alive']} 个, 失效 {res['dead']} 个。")
    except Exception as e:
        print(f"[!] 导入失败: {e}")


def main():
    args = [a.lower() for a in sys.argv[1:]]
    if not args:
        print(__doc__)
        return

    cmd = args[0]
    if cmd == "get":
        country = args[1].upper() if len(args) > 1 else None
        clean_only = "clean" in args[2:] or (len(args) > 2 and args[2] == "clean")
        ip_type = None
        if "residential" in args[2:] or "res" in args[2:]:
            ip_type = "residential"
        elif "datacenter" in args[2:] or "dc" in args[2:]:
            ip_type = "datacenter"

        protocol = None
        for a in args[2:]:
            if a in ["socks5", "socks4", "http", "https"]:
                protocol = a
                break

        asyncio.run(cmd_get(country, protocol, clean_only, ip_type))
    elif cmd == "list":
        asyncio.run(cmd_list())
    elif cmd == "stats":
        asyncio.run(cmd_stats())
    elif cmd == "scrape":
        asyncio.run(cmd_scrape())
    elif cmd == "check":
        asyncio.run(cmd_check())
    elif cmd == "import" and len(args) > 1:
        asyncio.run(cmd_import(sys.argv[2]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
