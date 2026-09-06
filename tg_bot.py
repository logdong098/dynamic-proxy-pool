import logging
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from config import settings
from storage import storage

logger = logging.getLogger("tg_bot")

COUNTRY_FLAGS = {
    "PH": "🇵🇭 菲律宾",
    "US": "🇺🇸 美国",
    "HK": "🇭🇰 中国香港",
    "SG": "🇸🇬 新加坡",
    "JP": "🇯🇵 日本",
    "TW": "🇹🇼 中国台湾",
    "KR": "🇰🇷 韩国",
    "GB": "🇬🇧 英国",
    "DE": "🇩🇪 德国",
    "VN": "🇻🇳 越南",
    "TH": "🇹🇭 泰国",
    "ID": "🇮🇩 印度尼西亚",
    "MY": "🇲🇾 马来西亚",
}


def get_country_display(code: str) -> str:
    return COUNTRY_FLAGS.get(code.upper(), f"🌐 {code.upper()}")


def format_proxy_message(proxy) -> str:
    """Format proxy details with HTML for Telegram with monospace copyable blocks and purity badges."""
    country_name = get_country_display(proxy.country)
    url = proxy.to_url()

    type_display = "🏠 住宅宽带" if proxy.ip_type == "residential" else ("📱 移动网络" if proxy.ip_type == "mobile" else "🏢 机房IP")
    google_text = "🟢 Google通过" if proxy.google_clean else "⚪ Google未验"

    msg = (
        f"<b>{country_name} 可用节点</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>🛡️ 纯净评级:</b> <b>{proxy.clean_level} 级</b> ({type_display})\n"
        f"<b>🔍 风控指标:</b> {google_text} · 欺诈分: <code>{proxy.fraud_score}</code>\n"
        f"<b>🔌 协议类型:</b> <code>{proxy.protocol.upper()}</code>\n"
        f"<b>📍 节点地址:</b> <code>{proxy.ip}:{proxy.port}</code>\n"
    )

    if proxy.username:
        msg += f"<b>👤 认证账号:</b> <code>{proxy.username}</code>\n"
    if proxy.password:
        msg += f"<b>🔑 认证密码:</b> <code>{proxy.password}</code>\n"

    latency_str = f"{proxy.latency} ms" if proxy.latency else "未测"
    msg += (
        f"<b>⚡ 延迟与分:</b> <code>{latency_str}</code> (健康分: {proxy.score})\n"
        f"<b>🏷️ 节点来源:</b> <code>{proxy.source}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>📋 快捷链接 (点击复制):</b>\n"
        f"<code>{url}</code>"
    )
    return msg


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start."""
    keyboard = [
        [
            InlineKeyboardButton("🇵🇭 菲律宾 (极速)", callback_data="get:PH"),
            InlineKeyboardButton("🇵🇭 菲律宾 (🛡️纯净)", callback_data="get:PH:clean"),
        ],
        [
            InlineKeyboardButton("🇺🇸 美国 (普通)", callback_data="get:US"),
            InlineKeyboardButton("🇺🇸 美国 (🛡️住宅)", callback_data="get:US:clean"),
        ],
        [
            InlineKeyboardButton("🇭🇰 香港", callback_data="get:HK"),
            InlineKeyboardButton("🇸🇬 新加坡", callback_data="get:SG"),
        ],
        [
            InlineKeyboardButton("📊 查看统计", callback_data="stats"),
            InlineKeyboardButton("🌍 国家列表", callback_data="list"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "🤖 <b>欢迎使用多渠道动态 IP 代理助手 (支持纯净度与住宅IP识别)</b>\n\n"
        "你可以使用以下方式提取代理：\n"
        "• 点击下方快捷按钮直接获取对应国家节点\n"
        "• 发送 <code>/get &lt;国家&gt; [clean/residential] [协议]</code>，例如：\n"
        "  - <code>/get PH</code> (快速提取)\n"
        "  - <code>/get PH clean</code> (<b>仅提取纯净节点</b>)\n"
        "  - <code>/get US residential socks5</code> (<b>提取美国住宅SOCKS5</b>)\n"
        "• <code>/list</code> 查看各国家可用节点及纯净度分布\n"
        "• <code>/stats</code> 查看代理池健康状态与纯净度统计\n"
    )
    await update.message.reply_html(text, reply_markup=reply_markup)


async def get_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /get [country] [clean/residential] [protocol]."""
    args = [a.lower() for a in (context.args or [])]
    if not args:
        await update.message.reply_html(
            "⚠️ 请输入国家代码，例如：\n"
            "• <code>/get PH</code> (普通提取)\n"
            "• <code>/get PH clean</code> (仅提取纯净节点)\n"
            "• <code>/get US residential socks5</code> (提取美国住宅 SOCKS5)"
        )
        return

    country = args[0].upper()
    clean_only = "clean" in args
    ip_type = None
    if "residential" in args or "res" in args:
        ip_type = "residential"
    elif "datacenter" in args or "dc" in args:
        ip_type = "datacenter"

    protocol = None
    for a in args[1:]:
        if a in ["socks5", "socks4", "http", "https"]:
            protocol = a
            break

    proxy = await storage.get_proxy(
        country=country,
        protocol=protocol,
        clean_only=clean_only,
        ip_type=ip_type
    )
    if not proxy:
        filter_str = f"{country}"
        if protocol: filter_str += f" + {protocol.upper()}"
        if clean_only: filter_str += " + 仅纯净(Level A/B)"
        if ip_type: filter_str += f" + {ip_type}"
        await update.message.reply_html(f"❌ 暂未找到符合条件的可用代理: <b>{filter_str}</b>")
        return

    await update.message.reply_html(format_proxy_message(proxy))


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /list."""
    stats = await storage.get_stats()
    if not stats.by_country:
        await update.message.reply_html("ℹ️ 当前代理池暂无已测活的可用国家。")
        return

    lines = [
        "<b>🌍 可用代理国家与数量分布:</b>\n",
        f"• <b>纯净度 A/B 级节点:</b> <code>{stats.clean_proxies}</code> 个",
        f"• <b>家庭住宅宽带节点:</b> <code>{stats.residential_proxies}</code> 个\n"
    ]
    for code, count in sorted(stats.by_country.items(), key=lambda x: x[1], reverse=True):
        display = get_country_display(code)
        lines.append(f"• {display}: <b>{count}</b> 个可用")

    lines.append("\n👉 发送 <code>/get &lt;代码&gt; clean</code> 提取纯净节点。")
    await update.message.reply_html("\n".join(lines))


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /stats."""
    stats = await storage.get_stats()
    lines = [
        "<b>📊 代理池实时运行指标</b>\n",
        f"• <b>在线存活:</b> <code>{stats.active_proxies}</code> 个",
        f"• <b>🛡️ 纯净节点 (A/B级):</b> <code>{stats.clean_proxies}</code> 个",
        f"• <b>🏠 住宅宽带 (ISP):</b> <code>{stats.residential_proxies}</code> 个",
        f"• <b>收录总数:</b> <code>{stats.total_proxies}</code> 个",
        f"• <b>平均延迟:</b> <code>{stats.avg_latency_ms or '-'} ms</code>",
        f"• <b>覆盖国家:</b> <code>{len(stats.by_country)}</code> 个",
        "\n<b>纯净度评级分布:</b>"
    ]
    for lvl, count in sorted(stats.by_clean_level.items()):
        lines.append(f"• <code>{lvl} 级</code>: {count} 个")

    lines.append("\n<b>协议分布:</b>")
    for proto, count in stats.by_protocol.items():
        lines.append(f"• <code>{proto.upper()}</code>: {count} 个")

    await update.message.reply_html("\n".join(lines))


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles inline button clicks."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("get:"):
        parts = data.split(":")
        country = parts[1]
        clean_only = (len(parts) > 2 and parts[2] == "clean")
        proxy = await storage.get_proxy(country=country, clean_only=clean_only)
        if not proxy:
            suffix = " (纯净)" if clean_only else ""
            await query.message.reply_html(f"❌ 暂无可用的 <b>{get_country_display(country)}{suffix}</b> 代理。")
        else:
            await query.message.reply_html(format_proxy_message(proxy))
    elif data == "stats":
        await stats_command(update, context)
    elif data == "list":
        await list_command(update, context)


class TelegramQueryBot:
    def __init__(self):
        self.token = settings.TG_BOT_TOKEN
        self.app: Optional[Application] = None

    def is_configured(self) -> bool:
        return bool(self.token and self.token.strip())

    async def start(self):
        """Starts the telegram polling bot asynchronously."""
        if not self.is_configured():
            logger.info("Telegram Query Bot: TG_BOT_TOKEN not provided. Telegram Bot interface disabled.")
            return

        try:
            self.app = Application.builder().token(self.token).build()
            self.app.add_handler(CommandHandler("start", start_command))
            self.app.add_handler(CommandHandler("get", get_command))
            self.app.add_handler(CommandHandler("list", list_command))
            self.app.add_handler(CommandHandler("stats", stats_command))
            self.app.add_handler(CallbackQueryHandler(button_handler))

            logger.info("Telegram Query Bot: Initialized. Starting polling...")
            await self.app.initialize()
            await self.app.start()
            await self.app.updater.start_polling()
        except Exception as e:
            logger.error(f"Telegram Query Bot error: {e}")

    async def stop(self):
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            logger.info("Telegram Query Bot stopped.")


tg_query_bot = TelegramQueryBot()
