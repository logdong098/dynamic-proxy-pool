#!/usr/bin/env python3
"""
Telegram UserBot 登录认证授权助手
运行此脚本，根据终端提示输入电话号码和验证码，生成 session 文件到 data/userbot.session。
此后在 Docker 中直接挂载 ./data 即可免密自动运行！
"""
import os
import asyncio
from telethon import TelegramClient

from config import settings


async def main():
    print("=" * 60)
    print("      Telegram UserBot 首次授权生成工具")
    print("=" * 60)

    api_id = settings.TG_API_ID
    api_hash = settings.TG_API_HASH
    session_path = settings.TG_SESSION_PATH

    if not api_id or not api_hash:
        print("\n[!] 错误: 未检测到 TG_API_ID 或 TG_API_HASH。")
        print("请在 .env 文件中配置你的 TG_API_ID 和 TG_API_HASH，或者在下方手动输入：")
        try:
            api_id_input = input("请输入 TG_API_ID: ").strip()
            api_hash_input = input("请输入 TG_API_HASH: ").strip()
            if not api_id_input or not api_hash_input:
                print("输入无效，退出。")
                return
            api_id = int(api_id_input)
            api_hash = api_hash_input
        except Exception as e:
            print(f"输入错误: {e}")
            return

    os.makedirs(os.path.dirname(os.path.abspath(session_path)), exist_ok=True)
    print(f"\n[+] 正在连接 Telegram 并验证 Session ({session_path})...")

    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        phone = input("请输入你的 Telegram 手机号 (例如 +8613800000000 / +63...): ").strip()
        await client.send_code_request(phone)
        code = input("请输入收到的验证码: ").strip()
        try:
            await client.sign_in(phone, code)
        except Exception as e:
            # Handle 2FA password if enabled
            if "password" in str(e).lower():
                pwd = input("检测到两步验证密码，请输入 2FA 密码: ").strip()
                await client.sign_in(password=pwd)
            else:
                raise e

    me = await client.get_me()
    print(f"\n✅ 登录成功！当前登录账号: {me.first_name} (@{me.username or '无用户名'}, ID: {me.id})")
    print(f"✅ Session 凭证已保存至: {session_path}")
    print("✅ 现在你可以直接启动服务或使用 Docker 容器，无需再次输入验证码。")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
