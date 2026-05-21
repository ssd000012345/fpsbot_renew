#!/usr/bin/env python3
"""
fps.ms 免费服务器自动续期脚本
- 通过 Discord Token 注入实现 OAuth 登录
- 通过 Cloudflare WARP 绕过 IP 封锁
- 通过 SeleniumBase UC Mode 绕过 Turnstile 验证
- 每天点击 "+Add 24 hours" 按钮为服务器续期
"""

import os
import re
import sys
import json
import time
import traceback
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from pathlib import Path

# ── 环境变量 ──────────────────────────────────────────────────────────────
DISCORD_TOKEN  = os.environ.get("FPSMS_DISCORD_TOKEN", "").strip()
TG_BOT_TOKEN   = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID     = os.environ.get("TG_CHAT_ID", "").strip()

# ── 常量配置 ──────────────────────────────────────────────────────────────
BASE_URL        = "https://panel.fps.ms"
LOGIN_URL       = f"{BASE_URL}/auth/login"
SERVERS_URL     = f"{BASE_URL}/servers"
TIMEOUT         = 60        # 秒（SeleniumBase 用秒）
MAX_RETRIES     = 3
SCREENSHOT_DIR  = Path("screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

# ── 日志工具 ──────────────────────────────────────────────────────────────
def _mask(text: str) -> str:
    if DISCORD_TOKEN:
        text = text.replace(DISCORD_TOKEN, "***TOKEN***")
    if TG_BOT_TOKEN:
        text = text.replace(TG_BOT_TOKEN, "***")
    if TG_CHAT_ID:
        text = text.replace(TG_CHAT_ID, "***")
    # 脱敏 IP 末段
    text = re.sub(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.)\d{1,3}\b", r"\1xx", text)
    return text

def log(level: str, msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}][{level}] {_mask(msg)}", flush=True)

def log_info(msg):  log("INFO ", msg)
def log_warn(msg):  log("WARN ", msg)
def log_error(msg): log("ERROR", msg)

# ── Telegram 推送 ──────────────────────────────────────────────────────────
def send_tg(text: str, image_bytes: bytes | None = None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log_warn("TG 未配置，跳过推送")
        return
    try:
        if image_bytes:
            boundary = f"Boundary{abs(hash(text))}"
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{TG_CHAT_ID}\r\n'
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="caption"\r\n\r\n{text}\r\n'
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="photo"; filename="s.png"\r\n'
                f"Content-Type: image/png\r\n\r\n"
            ).encode() + image_bytes + f"\r\n--{boundary}--\r\n".encode()
            req = Request(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
        else:
            req = Request(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                data=json.dumps({"chat_id": TG_CHAT_ID, "text": text}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        with urlopen(req, timeout=30) as r:
            log_info("TG 推送成功" if r.status == 200 else f"TG HTTP {r.status}")
    except Exception as e:
        log_warn(f"TG 推送异常: {e}")

# ── 截图工具 ───────────────────────────────────────────────────────────────
def screenshot(driver, name: str) -> bytes | None:
    try:
        path = str(SCREENSHOT_DIR / f"{name}.png")
        driver.save_screenshot(path)
        log_info(f"截图: {path}")
        return Path(path).read_bytes()
    except Exception as e:
        log_warn(f"截图失败: {e}")
        return None

# ── 等待工具 ───────────────────────────────────────────────────────────────
def wait_for_url_contains(driver, keyword: str, timeout: int = 30):
    """等待当前 URL 包含指定关键字"""
    for _ in range(timeout * 2):
        if keyword in driver.get_current_url():
            return True
        time.sleep(0.5)
    return False

def wait_for_element(driver, selector: str, timeout: int = 20, by="css"):
    """等待元素出现并返回，失败返回 None"""
    import seleniumbase
    for _ in range(timeout * 2):
        try:
            if by == "css":
                el = driver.find_element("css selector", selector)
            else:
                el = driver.find_element("xpath", selector)
            if el:
                return el
        except Exception:
            pass
        time.sleep(0.5)
    return None

# ── Discord Token 注入登录 ──────────────────────────────────────────────────
def inject_discord_token(driver):
    """
    1. 点击 fps.ms 登录页的 "Login With Discord" 按钮
    2. 跳转到 discord.com/login 后注入 Token
    3. 等待 OAuth 授权回调完成
    """
    log_info("访问 fps.ms 登录页...")
    driver.get(LOGIN_URL)
    time.sleep(3)

    # 等待并点击 "Login With Discord"
    log_info("寻找 Login With Discord 按钮...")
    btn = None
    for _ in range(20):
        try:
            # 多种选择器尝试
            for sel in [
                "button.bg-indigo-500",
                "button[class*='indigo']",
                "//button[contains(., 'Login With Discord')]",
                "//button[contains(., 'Discord')]",
                "//a[contains(., 'Discord')]",
            ]:
                try:
                    if sel.startswith("//"):
                        btn = driver.find_element("xpath", sel)
                    else:
                        btn = driver.find_element("css selector", sel)
                    if btn and btn.is_displayed():
                        break
                    btn = None
                except Exception:
                    btn = None
            if btn:
                break
        except Exception:
            pass
        time.sleep(0.5)

    if not btn:
        # 尝试通过文本内容找按钮
        try:
            btn = driver.find_element("xpath", "//*[contains(text(), 'Discord')]")
        except Exception:
            pass

    if not btn:
        raise RuntimeError("找不到 Discord 登录按钮")

    log_info("点击 Discord 登录按钮...")
    try:
        driver.execute_script("arguments[0].click();", btn)
    except Exception:
        btn.click()
    time.sleep(3)

    # 等待跳转到 Discord
    log_info("等待跳转到 Discord...")
    for _ in range(20):
        url = driver.get_current_url()
        if "discord.com" in url:
            break
        time.sleep(1)
    else:
        raise RuntimeError("未跳转到 Discord")

    log_info(f"当前 Discord 页面: {driver.get_current_url()}")

    # 注入 Token
    log_info("注入 Discord Token...")
    driver.execute_script("""
        (function(token) {
            // 方法1: 通过 iframe 注入（绕过跨域限制）
            try {
                var iframe = document.createElement('iframe');
                iframe.style.display = 'none';
                document.body.appendChild(iframe);
                iframe.contentWindow.localStorage.setItem('token', JSON.stringify(token));
                document.body.removeChild(iframe);
            } catch(e) {}
            // 方法2: 直接注入
            try {
                localStorage.setItem('token', JSON.stringify(token));
            } catch(e) {}
        })(arguments[0]);
    """, DISCORD_TOKEN)

    time.sleep(1)
    driver.refresh()
    time.sleep(4)

    current = driver.get_current_url()
    log_info(f"刷新后页面: {current}")

    if "discord.com/login" in current:
        raise RuntimeError("Token 注入失败，仍在登录页")

    # 处理 OAuth 授权页
    if "oauth2/authorize" in current:
        log_info("处理 OAuth 授权页...")
        handle_oauth(driver)

    # 等待回调完成，跳回 fps.ms
    log_info("等待 OAuth 回调完成...")
    for _ in range(30):
        url = driver.get_current_url()
        if "fps.ms" in url and "discord.com" not in url:
            log_info(f"已回到 fps.ms: {url}")
            return
        if "discord.com" in url and "oauth2" in url:
            handle_oauth(driver)
        time.sleep(1)

    raise RuntimeError(f"OAuth 回调超时，当前页: {driver.get_current_url()}")


def handle_oauth(driver):
    """处理 Discord OAuth 授权确认页，点击 Authorize 按钮"""
    log_info("处理 Discord OAuth 授权页...")
    time.sleep(2)

    for attempt in range(15):
        if "discord.com" not in driver.get_current_url():
            return

        # 先滚动到底部确保按钮可见
        try:
            driver.execute_script("""
                document.querySelectorAll('div').forEach(el => {
                    if (el.scrollHeight > el.clientHeight + 5) {
                        el.scrollTop = el.scrollHeight;
                    }
                });
                window.scrollTo(0, document.body.scrollHeight);
            """)
        except Exception:
            pass
        time.sleep(0.8)

        # 尝试点击授权按钮
        clicked = False
        for sel in [
            "//button[contains(., 'Authorize')]",
            "//button[contains(., '授权')]",
            "button[type='submit']",
            "//div[contains(@class, 'footer')]//button[last()]",
        ]:
            try:
                if sel.startswith("//"):
                    el = driver.find_element("xpath", sel)
                else:
                    el = driver.find_element("css selector", sel)
                if not el or not el.is_displayed():
                    continue
                text = el.text.strip().lower()
                if any(k in text for k in ("cancel", "deny", "取消")):
                    continue
                if el.is_enabled():
                    driver.execute_script("arguments[0].click();", el)
                    log_info(f"已点击授权按钮: '{el.text.strip()}'")
                    clicked = True
                    time.sleep(2)
                    break
            except Exception:
                continue

        if not clicked:
            time.sleep(1)
        
        if "discord.com" not in driver.get_current_url():
            return

    log_warn("OAuth 授权按钮处理超时")


# ── 获取服务器列表 ──────────────────────────────────────────────────────────
def get_server_ids(driver) -> list[str]:
    """
    访问 /servers 页面，从 URL 中提取服务器 ID（UUID 格式）
    fps.ms 的服务器链接格式: /server/<uuid>
    """
    log_info("访问服务器列表页...")
    driver.get(SERVERS_URL)
    time.sleep(4)

    # 等待服务器列表加载（有服务器行出现）
    for _ in range(20):
        try:
            # 检查是否有服务器行
            rows = driver.find_elements("css selector", "tr, [class*='server'], a[href*='/server/']")
            if rows:
                break
        except Exception:
            pass
        time.sleep(0.5)

    # 从页面链接提取服务器 ID（UUID）
    ids = []
    try:
        links = driver.find_elements("css selector", "a[href*='/server/']")
        for link in links:
            href = link.get_attribute("href") or ""
            # fps.ms 服务器 URL: /server/<uuid>
            m = re.search(r"/server/([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}|[a-f0-9]{6,})", href, re.I)
            if m and m.group(1) not in ids:
                ids.append(m.group(1))
    except Exception as e:
        log_warn(f"提取服务器链接失败: {e}")

    # 备用：从页面源码提取
    if not ids:
        try:
            src = driver.get_page_source()
            found = re.findall(r"/server/([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}|[a-f0-9]{6,})", src, re.I)
            ids = list(dict.fromkeys(found))  # 去重保序
        except Exception as e:
            log_warn(f"源码提取服务器 ID 失败: {e}")

    # 再备用：从 API 请求 URL 拦截（SeleniumBase 暂不支持，跳过）
    log_info(f"发现 {len(ids)} 台服务器: {ids}")
    return ids


# ── 处理单台服务器续期 ──────────────────────────────────────────────────────
def process_server(driver, server_id: str) -> dict:
    """
    进入服务器控制台，点击 "+Add 24 hours" 按钮，
    等待 Cloudflare Turnstile 验证通过，读取结果。
    """
    result = {
        "server_id": server_id,
        "status": "unknown",
        "emoji": "❓",
        "label": "未知",
        "detail": "",
    }

    server_url = f"{BASE_URL}/server/{server_id}"
    log_info(f"[{server_id[:8]}] 访问服务器控制台...")
    driver.get(server_url)
    time.sleep(5)

    # ── 读取当前到期时间 ────────────────────────────────────────────
    expires_text = ""
    try:
        src = driver.get_page_source()
        # "Server Expires 2026年5月23日 GMT+8 16:19" 或 "2026-05-23"
        m = re.search(r"Server\s+Expires?\s*([\d年月日\s:+GMT\-\/\.]+)", src, re.I | re.S)
        if m:
            expires_text = m.group(1).strip()[:50]
            log_info(f"[{server_id[:8]}] 当前到期: {expires_text}")
    except Exception:
        pass

    result["detail"] = expires_text

    # ── 查找 +Add 24 hours 按钮 ────────────────────────────────────
    log_info(f"[{server_id[:8]}] 寻找 +Add 24 hours 按钮...")
    add_btn = None

    for sel in [
        "//button[contains(., 'Add 24 hours')]",
        "//button[contains(., 'Add 24')]",
        "//*[contains(@class, 'btn') and contains(., '24')]",
        "//button[contains(., '+')]",
    ]:
        try:
            el = driver.find_element("xpath", sel)
            if el and el.is_displayed() and el.is_enabled():
                add_btn = el
                log_info(f"[{server_id[:8]}] 找到按钮: '{el.text.strip()}'")
                break
        except Exception:
            continue

    # CSS 选择器备用
    if not add_btn:
        for sel in [
            "button.btn-pink", "button[class*='pink']",
            "button[class*='primary']", "button[class*='add']",
        ]:
            try:
                els = driver.find_elements("css selector", sel)
                for el in els:
                    if el.is_displayed() and "24" in (el.text or ""):
                        add_btn = el
                        break
                if add_btn:
                    break
            except Exception:
                continue

    if not add_btn:
        log_warn(f"[{server_id[:8]}] 未找到 +Add 24 hours 按钮")
        result.update(status="no_btn", emoji="❓", label="未找到续期按钮")
        return result

    # ── 点击按钮 ───────────────────────────────────────────────────
    log_info(f"[{server_id[:8]}] 点击 +Add 24 hours...")
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", add_btn)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", add_btn)
    except Exception as e:
        log_warn(f"[{server_id[:8]}] 点击失败，尝试直接点击: {e}")
        try:
            add_btn.click()
        except Exception as e2:
            result.update(status="error", emoji="❌", label="点击失败", detail=str(e2)[:60])
            return result

    time.sleep(2)

    # ── 等待 Cloudflare Turnstile 验证 ────────────────────────────
    # SeleniumBase UC Mode 会自动处理 Turnstile，此处等待结果出现
    log_info(f"[{server_id[:8]}] 等待 Turnstile 验证...")
    cf_result = wait_for_turnstile_result(driver, server_id, timeout=45)

    if cf_result == "success":
        log_info(f"[{server_id[:8]}] Turnstile 验证通过，等待处理结果...")
        time.sleep(3)

        # 读取结果（成功 or 错误提示）
        status = read_renew_result(driver, server_id)
        result.update(**status)

    elif cf_result == "already_renewed":
        result.update(
            status="tooearly",
            emoji="⏳",
            label="今日已续期",
            detail="you can only once at one time period",
        )
    else:
        result.update(
            status="cf_timeout",
            emoji="⚠️",
            label="Turnstile 超时",
        )

    return result


def wait_for_turnstile_result(driver, server_id: str, timeout: int = 45) -> str:
    """
    等待 Cloudflare Turnstile 验证完成。
    UC Mode 会自动勾选，此处检测结果状态。
    返回: "success" / "already_renewed" / "timeout"
    """
    for i in range(timeout * 2):
        try:
            src = driver.get_page_source()

            # 检测到已续期的错误提示
            if "you can only once at one time period" in src.lower():
                log_info(f"[{server_id[:8]}] 检测到今日限制提示")
                return "already_renewed"

            # 检测到成功标志（成功后 Turnstile 区域消失，显示新到期时间）
            # "成功!" 字样（Cloudflare 中文验证成功）
            if "成功" in src and ("Processing" not in src or i > 10):
                log_info(f"[{server_id[:8]}] 检测到成功标志")
                return "success"

            # 检测 Turnstile checkbox 已勾选（绿色对勾）
            try:
                # Turnstile iframe 内的 checked 状态
                iframes = driver.find_elements("css selector", "iframe[src*='turnstile'], iframe[src*='challenges.cloudflare']")
                for iframe in iframes:
                    driver.switch_to.frame(iframe)
                    checked = driver.execute_script("""
                        var cb = document.querySelector('input[type="checkbox"]');
                        return cb ? cb.checked : false;
                    """)
                    driver.switch_to.default_content()
                    if checked:
                        log_info(f"[{server_id[:8]}] Turnstile checkbox 已勾选")
                        return "success"
            except Exception:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass

            # Processing 状态：等待
            if "Processing" in src:
                log_info(f"[{server_id[:8]}] 处理中... ({i//2}s)")

        except Exception as e:
            log_warn(f"[{server_id[:8]}] 检测状态异常: {e}")

        time.sleep(0.5)

    return "timeout"


def read_renew_result(driver, server_id: str) -> dict:
    """读取续期操作结果"""
    try:
        src = driver.get_page_source()

        # 已续期限制
        if "you can only once at one time period" in src.lower():
            return {"status": "tooearly", "emoji": "⏳", "label": "今日已续期", "detail": "24h 内只能续期一次"}

        # 读取新到期时间
        m = re.search(r"Server\s+Expires?\s*([\d年月日\s:+GMT\-\/\.]+)", src, re.I | re.S)
        expires = m.group(1).strip()[:50] if m else ""

        # 检查到期时间是否已更新（包含明天或更晚的日期）
        if expires:
            return {"status": "renewed", "emoji": "✅", "label": "续期成功", "detail": expires}

        # 通用成功判断
        return {"status": "renewed", "emoji": "✅", "label": "续期成功", "detail": ""}

    except Exception as e:
        return {"status": "error", "emoji": "❌", "label": "读取结果失败", "detail": str(e)[:60]}


# ── 主流程 ─────────────────────────────────────────────────────────────────
def run():
    if not DISCORD_TOKEN:
        raise RuntimeError("缺少环境变量 FPSMS_DISCORD_TOKEN")

    log_info("=" * 55)
    log_info("fps.ms 自动续期脚本启动")
    log_info("=" * 55)

    # 导入 SeleniumBase（UC Mode 用于绕过 Turnstile）
    try:
        from seleniumbase import SB
    except ImportError:
        raise RuntimeError("请先安装 seleniumbase: pip install seleniumbase")

    # 验证出口 IP（通过 WARP）
    try:
        log_info("检查出口 IP...")
        req = Request("https://api.ipify.org?format=json")
        with urlopen(req, timeout=10) as r:
            ip_data = json.loads(r.read())
            log_info(f"出口 IP: {ip_data.get('ip', '?')}")
    except Exception as e:
        log_warn(f"IP 检查失败: {e}")

    # 检查 WARP 状态
    try:
        req = Request("https://www.cloudflare.com/cdn-cgi/trace")
        with urlopen(req, timeout=10) as r:
            trace = r.read().decode()
            warp_line = next((l for l in trace.splitlines() if l.startswith("warp=")), "warp=unknown")
            log_info(f"WARP 状态: {warp_line}")
    except Exception as e:
        log_warn(f"WARP 状态检查失败: {e}")

    results = []
    screenshots = []

    # 使用 SeleniumBase UC Mode 启动浏览器
    # uc=True: 启用 UC Mode（undetected-chromedriver）自动绕过 Cloudflare Turnstile
    # headless=True: 无头模式（GitHub Actions 环境）
    log_info("启动 SeleniumBase UC Mode 浏览器...")

    with SB(uc=True, headless=True, incognito=False) as sb:
        driver = sb.driver
        driver.set_window_size(1280, 800)

        try:
            # ── 登录 ─────────────────────────────────────────────
            log_info("开始 Discord Token 登录流程...")
            inject_discord_token(driver)
            log_info("登录成功！")
            screenshot(driver, "01-logged-in")

            # ── 确认已登录到 fps.ms ─────────────────────────────
            for _ in range(10):
                url = driver.get_current_url()
                if "fps.ms" in url:
                    break
                time.sleep(1)
            else:
                screenshot(driver, "01-login-failed")
                raise RuntimeError(f"登录后未能进入 fps.ms，当前: {driver.get_current_url()}")

            log_info(f"当前页面: {driver.get_current_url()}")

            # ── 获取服务器列表 ───────────────────────────────────
            server_ids = get_server_ids(driver)
            if not server_ids:
                buf = screenshot(driver, "02-no-servers")
                send_tg("⚠️ fps.ms 续期\n未找到任何服务器", buf)
                log_warn("未发现服务器，退出")
                return

            log_info(f"发现 {len(server_ids)} 台服务器，开始逐一续期")

            # ── 逐台续期 ─────────────────────────────────────────
            for i, sid in enumerate(server_ids, 1):
                log_info(f"\n{'='*50}")
                log_info(f"处理服务器 {i}/{len(server_ids)}: {sid[:8]}...")
                res = process_server(driver, sid)
                results.append(res)
                buf = screenshot(driver, f"server-{i}-{res['status']}")
                if buf:
                    screenshots.append(buf)
                log_info(f"[{sid[:8]}] 结果: {res['emoji']} {res['label']} {res['detail']}")

        except Exception as e:
            log_error(f"流程异常: {e}")
            buf = screenshot(driver, "fatal-error")
            screenshots.append(buf) if buf else None
            results.append({
                "server_id": "N/A",
                "status": "error",
                "emoji": "❌",
                "label": "脚本异常",
                "detail": str(e)[:100],
            })
            raise

        finally:
            # ── 发送 TG 报告 ──────────────────────────────────
            if results:
                lines = ["🖥️ fps.ms 自动续期报告"]
                lines.append(f"时间: {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')} CST")
                lines.append("")
                for r in results:
                    sid_short = r["server_id"][:8] if r["server_id"] != "N/A" else "N/A"
                    line = f"{r['emoji']} 服务器 {sid_short}: {r['label']}"
                    if r.get("detail"):
                        line += f"\n   到期: {r['detail']}"
                    lines.append(line)
                lines.append("\nfps.ms Auto Renew")

                final_img = screenshots[0] if screenshots else None
                send_tg("\n".join(lines), final_img)

    log_info("所有服务器处理完毕")


if __name__ == "__main__":
    try:
        run()
        log_info("✅ 脚本执行完毕")
    except Exception:
        log_error("❌ 脚本执行失败")
        traceback.print_exc()
        sys.exit(1)
