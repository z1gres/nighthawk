import asyncio
import hashlib
import json
import os
from pathlib import Path
import random
import re
import socket
import sys
import time
import uuid

import aiohttp
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image as PlatypusImage, Paragraph, SimpleDocTemplate, Spacer, Table as PlatypusTable, TableStyle
from rich.console import Console
from rich.panel import Panel
from rich.table import Table as RichTable

# CLI Flags & Mode
SIMPLE_MODE = "--simple" in sys.argv or "-s" in sys.argv
VERIFY_SSL_FLAG = "--verify-ssl" in sys.argv
console = Console(no_color=SIMPLE_MODE)

# Resource paths
BASE_DIR = Path(__file__).resolve().parent
IMG_DIR = BASE_DIR / "assets" / "img"
FONTS_DIR = BASE_DIR / "assets" / "fonts"
LOCAL_DB_FILE = BASE_DIR / "nighthawk_db.json"
PROXIES_FILE = BASE_DIR / "proxies.txt"

CORRECT_PNG = str(IMG_DIR / "correct.png")
LINK_PNG = str(IMG_DIR / "link.png")
WARNING_PNG = str(IMG_DIR / "warning.png")

FONT_BOLD_PATH = FONTS_DIR / "Montserrat-Bold.ttf"
FONT_REGULAR_PATH = FONTS_DIR / "Montserrat-Regular.ttf"

# Settings state
config = {
    "export_pdf": True,
    "concurrency_limit": 80,
    "timeout": 6,
    "max_retries": 3,
    "use_proxies": True,
    "verify_ssl": VERIFY_SSL_FLAG,
    "proxy_cooldown": 60,
}

BANNER = r"""
 ███▄    █  ██▓  ▄████  ██░ ██ ▄▄▄█████▓ ██░ ██  ▄▄▄       █     █░██ ▄█▀
 ██ ▀█   █ ▓██▒ ██▒ ▀█▒▓██░ ██▒▓  ██▒ ▓▒▓██░ ██▒▒████▄    ▓█░ █ ░█░██▄█▒ 
▓██  ▀█ ██▒▒██▒▒██░▄▄▄░▒██▀▀██░▒ ▓██░ ▒░▒██▀▀██░▒██  ▀█▄  ▒█░ █ ░█▓███▄░ 
▓██▒  ▐▌██▒░██░░▓█  ██▓░▓█ ░██ ░ ▓██▓ ░ ░▓█ ░██ ░██▄▄▄▄██ ░█░ █ ░█▓██ █▄ 
▒██░   ▓██░░██░░▒▓███▀▒░▓█▒░██▓  ▒██▒ ░ ░▓█▒░██▓ ▓█   ▓██▒░░██▒██▓▒██▒ █▄
░ ▒░   ▒ ▒ ░▓   ░▒   ▒  ▒ ░░▒░▒  ▒ ░░    ▒ ░░▒░▒ ▒▒   ▓▒█░░ ▓░▒ ▒ ▒ ▒▒ ▓▒
░ ░░   ░ ▒░ ▒ ░  ░   ░  ▒ ░▒░ ░    ░     ▒ ░▒░ ░  ▒   ▒▒ ░  ▒ ░ ░ ░ ░▒ ▒░
   ░   ░ ░  ▒ ░░ ░   ░  ░  ░░ ░  ░       ░  ░░ ░  ░   ▒     ░   ░ ░ ░░ ░ 
         ░  ░        ░  ░  ░  ░          ░  ░  ░      ░  ░    ░   ░  ░   
"""

# ==============================================================================
# 0. УПРАВЛІННЯ ДИНАМІЧНИМИ ЗАГОЛОВКАМИ ТА РОЗУМНИЙ PROXY MANAGER
# ==============================================================================

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        '"Windows"',
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        '"macOS"',
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        '"Google Chrome";v="124", "Chromium";v="124", "Not.A/Brand";v="24"',
        '"Linux"',
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
        None,
        None,
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        None,
        None,
    ),
]


class DynamicHeaders:
    @staticmethod
    def get(extra_headers=None):
        ua, sec_ua, sec_platform = random.choice(USER_AGENTS)
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": random.choice(["en-US,en;q=0.9", "en-GB,en;q=0.8,uk;q=0.6", "en-US,en;q=0.5"]),
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
        if sec_ua and sec_platform:
            headers["sec-ch-ua"] = sec_ua
            headers["sec-ch-ua-mobile"] = "?0"
            headers["sec-ch-ua-platform"] = sec_platform

        if extra_headers:
            headers.update(extra_headers)
        return headers


class SmartProxyManager:
    """Керує пулом проксі з підтримкою чорного списку та карантину (cooldown)"""

    def __init__(self, proxy_file=PROXIES_FILE, cooldown_seconds=60):
        self.raw_proxies = []
        self.quarantine = {}  # proxy_url: release_timestamp
        self.cooldown_seconds = cooldown_seconds

        if proxy_file.exists():
            try:
                with open(proxy_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            if not line.startswith(("http://", "https://", "socks5://")):
                                line = f"http://{line}"
                            self.raw_proxies.append(line)
            except Exception:
                pass

    def get_proxy(self):
        if not self.raw_proxies or not config["use_proxies"]:
            return None

        now = time.time()
        # Фільтруємо проксі, термін карантину яких минув
        active_proxies = [p for p in self.raw_proxies if self.quarantine.get(p, 0) <= now]

        if not active_proxies:
            # Якщо всі проксі в карантині, беремо той, у якого найменший залишок часу
            return min(self.raw_proxies, key=lambda p: self.quarantine.get(p, 0))

        return random.choice(active_proxies)

    def mark_failed(self, proxy, is_rate_limit=False):
        if not proxy:
            return
        # Для 429 блокування збільшуємо час карантину вдвічі
        multiplier = 2 if is_rate_limit else 1
        self.quarantine[proxy] = time.time() + (self.cooldown_seconds * multiplier)

    def mark_success(self, proxy):
        if proxy in self.quarantine:
            del self.quarantine[proxy]

    def total_active(self):
        now = time.time()
        return len([p for p in self.raw_proxies if self.quarantine.get(p, 0) <= now])

    def total(self):
        return len(self.raw_proxies)


proxy_manager = SmartProxyManager(cooldown_seconds=config["proxy_cooldown"])

# ==============================================================================
# 1. ЕВРИСТИКА SOFT-404 ТА WAF-ФІЛЬТРАЦІЯ
# ==============================================================================

SOFT_404_REGEX = [
    re.compile(r"<title>.*?(404|not found|page not found|doesn't exist|user not found).*?</title>", re.IGNORECASE),
    re.compile(r"(this user does not exist|account has been suspended|user unavailable|profile not found)", re.IGNORECASE),
    re.compile(r"(we couldn't find the page|the specified profile could not be found|page not available)", re.IGNORECASE),
]

WAF_CHALLENGE_REGEX = [
    re.compile(r"(cf-browser-verification|cloudflare ray id|just a moment\.\.\.|attention required! \| cloudflare)", re.IGNORECASE),
    re.compile(r"(challenge-platform|datadome|recaptcha|ddos-guard)", re.IGNORECASE),
]


def is_soft_404(html_text, status_code, current_url):
    parsed_path = str(current_url).lower()
    if any(k in parsed_path for k in ["/404", "/login", "/error", "/search", "/notfound"]):
        return True

    for rgx in SOFT_404_REGEX:
        if rgx.search(html_text):
            return True

    return False


def is_waf_interception(html_text, status_code):
    if status_code in (403, 503, 429):
        for rgx in WAF_CHALLENGE_REGEX:
            if rgx.search(html_text):
                return True
    return False


# ==============================================================================
# 2. АСИНХРОННА ІНІЦІАЛІЗАЦІЯ БАЗИ ДАНИХ
# ==============================================================================


def sanitize_filename(name, max_len=40):
    clean = re.sub(r'[\\/*?:"<>|;\s]+', "_", str(name)).strip("._")
    if not clean:
        clean = "report"
    if len(clean) > max_len:
        name_hash = hashlib.md5(clean.encode()).hexdigest()[:8]
        clean = f"{clean[:max_len]}_{name_hash}"
    return clean


def register_custom_fonts():
    font_bold = "Helvetica-Bold"
    font_reg = "Helvetica"
    if FONT_BOLD_PATH.exists():
        try:
            pdfmetrics.registerFont(TTFont("Montserrat-Bold", str(FONT_BOLD_PATH)))
            font_bold = "Montserrat-Bold"
        except Exception:
            pass
    if FONT_REGULAR_PATH.exists():
        try:
            pdfmetrics.registerFont(TTFont("Montserrat-Regular", str(FONT_REGULAR_PATH)))
            font_reg = "Montserrat-Regular"
        except Exception:
            pass
    return font_bold, font_reg


def show_banner():
    if SIMPLE_MODE:
        print("\n=== NIGHT HAWK (Async OSINT Engine) ===\n")
        return
    console.clear()
    console.print(f"[bold #ff0033]{BANNER}[/bold #ff0033]\n", highlight=False, soft_wrap=True)


async def auto_compile_database_async():
    """Асинхронний збір та збереження бази сигнатур 700+ платформ через aiohttp"""
    source_url = "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json"
    connector = aiohttp.TCPConnector(ssl=config["verify_ssl"])

    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            headers = DynamicHeaders.get()
            timeout = aiohttp.ClientTimeout(total=15)
            async with session.get(source_url, headers=headers, timeout=timeout) as resp:
                resp.raise_for_status()
                payload = await resp.json()
                raw_data = payload.get("sites", [])

                normalized = []
                for site in raw_data:
                    check_url = site.get("uri_check", "")
                    if not check_url:
                        continue
                    normalized.append({
                        "name": site.get("name", "Unknown"),
                        "url": site.get("uri_pretty", check_url),
                        "check_url": check_url,
                        "e_code": site.get("e_code"),
                        "e_string": site.get("e_string"),
                        "m_code": site.get("m_code"),
                        "m_string": site.get("m_string"),
                        "category": site.get("cat", "General"),
                        "headers": site.get("headers", {}),
                    })

                output_data = {
                    "version": "4.1.0-async",
                    "engine": "NightHawk-Native-Async",
                    "total_targets": len(normalized),
                    "platforms": normalized,
                }
                with open(LOCAL_DB_FILE, "w", encoding="utf-8") as f:
                    json.dump(output_data, f, indent=2, ensure_ascii=False)
                return normalized
        except Exception as e:
            if SIMPLE_MODE:
                print(f"[-] Database build error: {e}")
            else:
                console.print(f"[bold red][-] Database build error:[/bold red] {e}")
            return []


async def load_database_async():
    if not LOCAL_DB_FILE.exists():
        if SIMPLE_MODE:
            print("[*] Local database missing. Compiling 700+ targets asynchronously...")
        else:
            console.print("[bold yellow][!] Local DB missing. Compiling 700+ platforms asynchronously...[/bold yellow]")
        sites = await auto_compile_database_async()
        if sites:
            return sites

    try:
        with open(LOCAL_DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            sites = data.get("platforms", [])

        if SIMPLE_MODE:
            print(f"[+] Loaded {len(sites)} targets. Active Proxies: {proxy_manager.total_active()}/{proxy_manager.total()}\n")
        else:
            ssl_status = "[green]ON[/green]" if config["verify_ssl"] else "[yellow]OFF (Permissive)[/yellow]"
            console.print(
                f"[bold #ff1a1a]:heavy_check_mark:[/bold #ff1a1a] [white]Async OSINT Engine ready! Targets:[/white]"
                f" [bold #ff0033]{len(sites)}[/bold #ff0033] | [white]Proxies:[/white] [bold cyan]{proxy_manager.total_active()}/{proxy_manager.total()}[/bold cyan] | [white]SSL Verify:[/white] {ssl_status}\n"
            )
        return sites
    except Exception as e:
        print(f"[-] Critical error loading DB: {e}")
        sys.exit(1)


# ==============================================================================
# 3. ВЕРСТКА ЗВІТУ PDF ЧЕРЕЗ REPORTLAB PLATYPUS
# ==============================================================================


def export_to_pdf(target_name, found_accounts, title_prefix="Username"):
    """Створює PDF-звіт через Platypus з автоматичним перенесенням рядків та розбиттям на сторінки"""
    safe_name = sanitize_filename(target_name)
    pdf_filename = f"report_{safe_name}.pdf"
    font_bold, font_reg = register_custom_fonts()

    doc = SimpleDocTemplate(
        pdf_filename,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        fontName=font_bold,
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#ff0033"),
        spaceAfter=4,
    )

    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        fontName=font_reg,
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#555555"),
        spaceAfter=14,
    )

    cell_bold = ParagraphStyle(
        "CellBold",
        fontName=font_bold,
        fontSize=10,
        leading=12,
        textColor=colors.HexColor("#111111"),
    )

    cell_url = ParagraphStyle(
        "CellUrl",
        fontName=font_reg,
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#0066cc"),
        wordWrap="CJK",
    )

    story = []

    # Заголовок
    display_target = str(target_name)
    if len(display_target) > 50:
        display_target = display_target[:47] + "..."

    story.append(Paragraph(f"NIGHT HAWK Intelligence Report: {display_target}", title_style))
    story.append(Paragraph(f"Mode: {title_prefix} | Identified Accounts: {len(found_accounts)} | Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}", subtitle_style))
    story.append(Spacer(1, 8))

    # Таблиця знайдених акаунтів
    table_data = [[Paragraph("Platform", cell_bold), Paragraph("Identified Profile URL", cell_bold)]]

    for item in found_accounts:
        p_name = Paragraph(item.get("name", "Unknown"), cell_bold)
        url_val = item.get("url", "")
        p_url = Paragraph(f'<a href="{url_val}" color="#0066cc">{url_val}</a>', cell_url)
        table_data.append([p_name, p_url])

    # Ширина стовпців: 160pt під назву платформи, 380pt під URL
    results_table = PlatypusTable(table_data, colWidths=[160, 380])
    results_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f4f4")),
            ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.HexColor("#ff0033")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
    )

    story.append(results_table)

    try:
        doc.build(story)
        if SIMPLE_MODE:
            print(f"[+] Platypus PDF report saved: {pdf_filename}")
        else:
            console.print(f"[bold #ff3344]:page_facing_up: Platypus PDF report saved:[/bold #ff3344] [underline #ff6666]{pdf_filename}[/underline #ff6666]")
    except Exception as e:
        print(f"[-] Error compiling PDF: {e}")


# ==============================================================================
# 4. АСИНХРОННИЙ СКАНЕР НІКНЕЙМІВ
# ==============================================================================


async def async_fetch_target(session, site, username, semaphore):
    site_name = site.get("name", "Unknown")
    check_url = site["check_url"].replace("{account}", username)
    profile_url = site.get("url", site["check_url"]).replace("{account}", username)

    e_code = site.get("e_code")
    e_string = site.get("e_string")
    m_code = site.get("m_code")
    m_string = site.get("m_string")

    retries = config["max_retries"]
    backoff = 0.5

    async with semaphore:
        for attempt in range(retries):
            proxy = proxy_manager.get_proxy()
            headers = DynamicHeaders.get(site.get("headers"))

            try:
                timeout = aiohttp.ClientTimeout(total=config["timeout"])
                async with session.get(
                    check_url,
                    headers=headers,
                    proxy=proxy,
                    timeout=timeout,
                    allow_redirects=True,
                    ssl=config["verify_ssl"],
                ) as resp:
                    # Обробка Rate Limit (429)
                    if resp.status == 429:
                        proxy_manager.mark_failed(proxy, is_rate_limit=True)
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        continue

                    text = await resp.text(errors="ignore")

                    # Перевірка на перехоплення WAF
                    if is_waf_interception(text, resp.status):
                        proxy_manager.mark_failed(proxy)
                        return {"found": False, "available": False, "waf": True}

                    proxy_manager.mark_success(proxy)

                    code_match = (resp.status == e_code) if e_code is not None else True
                    string_match = (e_string in text) if e_string else True

                    avail_code = (resp.status == m_code) if m_code is not None else False
                    avail_string = (m_string in text) if m_string else False
                    is_available = (avail_code or avail_string) or (resp.status in (404, 410) and not (code_match and string_match))

                    if code_match and string_match:
                        if not e_string and is_soft_404(text, resp.status, resp.url):
                            return {"found": False, "available": True, "name": site_name, "url": profile_url}
                        return {"found": True, "available": False, "name": site_name, "url": profile_url}

                    return {"found": False, "available": is_available, "name": site_name, "url": profile_url}

            except (aiohttp.ClientError, asyncio.TimeoutError):
                proxy_manager.mark_failed(proxy)
                if attempt == retries - 1:
                    return {"found": False, "available": False, "name": site_name, "url": profile_url}
                await asyncio.sleep(backoff)

        return {"found": False, "available": False, "name": site_name, "url": profile_url}


async def scan_target_async(username, sites, check_mode="search"):
    start_t = time.time()
    mode_text = "Scanning profiles for" if check_mode == "search" else "Checking availability for"
    if SIMPLE_MODE:
        print(f"\n[*] {mode_text}: {username}\n")
    else:
        console.print(f"\n[bold #ff3344]:mag: {mode_text}:[bold #ff3344] [bold white]{username}[/bold white]\n")

    semaphore = asyncio.Semaphore(config["concurrency_limit"])
    connector = aiohttp.TCPConnector(limit=300, ssl=config["verify_ssl"], ttl_dns_cache=300)

    matched_results = []
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [async_fetch_target(session, site, username, semaphore) for site in sites]

        for future in asyncio.as_completed(tasks):
            res = await future
            if check_mode == "search" and res["found"]:
                matched_results.append(res)
                if SIMPLE_MODE:
                    print(f"[+] {res['name']}: {res['url']}")
                else:
                    console.print(
                        f"[bold #ff0033]:heavy_check_mark:[/bold #ff0033] [bold white]{res['name']}[/bold white] "
                        f" [#ff4d4d]:link:[/#ff4d4d]  [#ff6666 underline]{res['url']}[/#ff6666 underline]"
                    )
            elif check_mode == "avail" and res["available"]:
                matched_results.append(res)
                if SIMPLE_MODE:
                    print(f"[+] [FREE] {res['name']}: {res['url']}")
                else:
                    console.print(
                        f"[bold #00ff66]:white_check_mark: [FREE][/bold #00ff66] [bold white]{res['name']}[/bold white] "
                        f" [#ff4d4d]:link:[/#ff4d4d]  [#4ec9b0 underline]{res['url']}[/#4ec9b0 underline]"
                    )

    elapsed = round(time.time() - start_t, 2)
    if SIMPLE_MODE:
        print("\n" + "=" * 50)
        print(f"[+] Completed for {username}! Result: {len(matched_results)} ({elapsed}s)")
        print("=" * 50 + "\n")
    else:
        color = "#ff1a1a" if check_mode == "search" else "#00ff66"
        console.print("\n" + "[bold #ff0033]" + "=" * 60 + "[/bold #ff0033]")
        console.print(
            f"[bold {color}]:heavy_check_mark: Completed for {username}![/bold {color}] "
            f"Result: [bold white]{len(matched_results)}[/bold white] [dim](took {elapsed}s)[/dim]"
        )
        console.print("[bold #ff0033]" + "=" * 60 + "[/bold #ff0033]\n")

    if matched_results and config["export_pdf"]:
        prefix = "Username" if check_mode == "search" else "Availability"
        export_to_pdf(f"{prefix.lower()}_{username}", matched_results, prefix)

    return matched_results


# ==============================================================================
# 5. АСИНХРОННИЙ EMAIL-РУШІЙ З ОБРОБКОЮ ЛІМІТІВ GITHUB ТА CSRF
# ==============================================================================


async def check_email_spotify_async(session, email):
    url = f"https://spclient.wg.spotify.com/signup/public/v1/account?validate=1&email={email}"
    headers = DynamicHeaders.get({
        "Accept": "application/json",
        "Referer": "https://www.spotify.com/",
        "Origin": "https://www.spotify.com",
    })
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                data = await r.json()
                if data.get("status") == 20 or ("errors" in data and "email" in data.get("errors", {})):
                    return {"name": "Spotify", "url": "https://www.spotify.com", "avatar": None}
    except Exception:
        pass
    return None


async def check_email_adobe_async(session, email):
    url = "https://auth.services.adobe.com/signin/v2/users/accounts"
    headers = DynamicHeaders.get({
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "X-Request-Id": str(uuid.uuid4()),
        "Origin": "https://auth.services.adobe.com",
        "Referer": "https://auth.services.adobe.com/en_US/index.html",
    })
    try:
        async with session.post(url, headers=headers, json={"username": email}, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                data = await r.json()
                if isinstance(data, list) and len(data) > 0:
                    return {"name": "Adobe", "url": "https://account.adobe.com", "avatar": data[0].get("avatarUrl")}
    except Exception:
        pass
    return None


async def check_email_duolingo_async(session, email):
    url = f"https://www.duolingo.com/2017-06-30/users?email={email}"
    headers = DynamicHeaders.get({"Referer": "https://www.duolingo.com/"})
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                data = await r.json()
                users = data.get("users", [])
                if users:
                    u = users[0]
                    avatar = u.get("picture")
                    if avatar and not avatar.startswith("http"):
                        avatar = f"https:{avatar}"
                    username = u.get("username", "")
                    profile_url = f"https://www.duolingo.com/profile/{username}" if username else "https://www.duolingo.com"
                    return {"name": "Duolingo", "url": profile_url, "avatar": avatar}
    except Exception:
        pass
    return None


async def check_email_gravatar_async(session, email):
    md5_hash = hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()
    url = f"https://en.gravatar.com/{md5_hash}.json"
    avatar_url = f"https://www.gravatar.com/avatar/{md5_hash}?d=404"
    try:
        async with session.get(url, headers=DynamicHeaders.get(), timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                data = await r.json()
                entry = data.get("entry", [{}])[0]
                return {
                    "name": "Gravatar",
                    "url": entry.get("profileUrl", f"https://gravatar.com/{md5_hash}"),
                    "avatar": entry.get("photos", [{}])[0].get("value", avatar_url),
                }
    except Exception:
        pass
    return None


async def check_email_github_async(session, email):
    """GitHub Search з підтримкою GITHUB_TOKEN та захистом від 403 Rate Limit"""
    url = f"https://api.github.com/search/users?q={email}+in:email"
    extra_headers = {"Accept": "application/vnd.github.v3+json"}

    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    if github_token:
        extra_headers["Authorization"] = f"Bearer {github_token}"

    headers = DynamicHeaders.get(extra_headers)

    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                data = await r.json()
                if data.get("total_count", 0) > 0:
                    user = data["items"][0]
                    return {
                        "name": "GitHub",
                        "url": user.get("html_url", "https://github.com"),
                        "avatar": user.get("avatar_url"),
                    }
            elif r.status in (403, 429):
                # Rate limit досягнуто - не крашимо пошук
                return None
    except Exception:
        pass
    return None


async def check_email_proton_async(session, email):
    url = f"https://api.protonmail.ch/pks/lookup?op=get&search={email}"
    try:
        async with session.get(url, headers=DynamicHeaders.get(), timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                text = await r.text()
                if "BEGIN PGP PUBLIC KEY BLOCK" in text:
                    return {"name": "ProtonMail", "url": "https://mail.proton.me", "avatar": None}
    except Exception:
        pass
    return None


async def check_email_archive_async(session, email):
    url = f"https://archive.org/services/users/v1/exists/{email}"
    try:
        async with session.get(url, headers=DynamicHeaders.get(), timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                data = await r.json()
                if data.get("exists") is True:
                    return {"name": "Archive.org", "url": "https://archive.org", "avatar": None}
    except Exception:
        pass
    return None


ASYNC_EMAIL_CHECKERS = [
    check_email_spotify_async,
    check_email_adobe_async,
    check_email_duolingo_async,
    check_email_gravatar_async,
    check_email_github_async,
    check_email_proton_async,
    check_email_archive_async,
]


async def scan_email_async(email):
    if SIMPLE_MODE:
        print(f'\n[*] Enumerating accounts for: "{email}"\n')
    else:
        console.print(f'\n[bold #ff3344]:mag: Enumerating accounts for:[bold #ff3344] [bold white]"{email}"[/bold white]\n')

    start_t = time.time()
    found_accounts = []

    connector = aiohttp.TCPConnector(ssl=config["verify_ssl"])
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [fn(session, email) for fn in ASYNC_EMAIL_CHECKERS]
        results = await asyncio.gather(*tasks)

        for res in results:
            if res:
                found_accounts.append(res)
                if SIMPLE_MODE:
                    print(f"[+] [{res['name']}] {res['url']}")
                    if res.get("avatar"):
                        print(f"    -> Avatar: {res['avatar']}")
                else:
                    console.print(f"[bold #ff0033]:heavy_check_mark:[/bold #ff0033] [bold white][{res['name']}][/bold white] [blue underline]{res['url']}[/blue underline]")
                    if res.get("avatar"):
                        console.print(f"   [bold cyan]➡ Avatar:[/bold cyan] [underline cyan]{res['avatar']}[/underline cyan]")

    elapsed = round(time.time() - start_t, 2)
    if SIMPLE_MODE:
        print("\n" + "=" * 50)
        print(f"[+] Completed for {email}! Found: {len(found_accounts)} ({elapsed}s)\n")
    else:
        console.print("\n" + "[bold #ff0033]" + "=" * 60 + "[/bold #ff0033]")
        console.print(f"[bold #ff1a1a]:heavy_check_mark: Completed for {email}! Found: {len(found_accounts)} ({elapsed}s)[/bold #ff1a1a]\n")

    if found_accounts and config["export_pdf"]:
        export_to_pdf(email, found_accounts, "Email")


# ==============================================================================
# 6. ПЕРМУТАТОР НІКНЕЙМІВ
# ==============================================================================


def generate_permutations(base_name, extra_keyword=""):
    base = base_name.strip().lower()
    if not base:
        return []
    variations, seen = [], set()

    def add(v):
        clean = v.strip()
        if clean and clean not in seen:
            seen.add(clean)
            variations.append(clean)

    add(base)
    add(f"{base}{base[-1]}")
    add(f"{base}{base[-1]}{base[-1]}")
    add(f"{base[0]}{base}")

    for sep in ["_", ".", "-", "__"]:
        add(f"{base}{sep}")
        add(f"{sep}{base}")
        add(f"{sep}{base}{sep}")

    prefixes = ["real", "the", "iam", "its", "itsme", "not", "just", "only", "mr", "dr", "official", "pro", "master", "god", "dark", "cyber", "hacker", "alpha"]
    suffixes = ["official", "real", "orig", "verified", "dev", "tech", "pro", "main", "alt", "live", "yt", "gaming", "hub", "world", "team", "app"]

    for p in prefixes:
        add(f"{p}_{base}")
        add(f"{p}{base}")
        add(f"{p}.{base}")
    for s in suffixes:
        add(f"{base}_{s}")
        add(f"{base}{s}")
        add(f"{base}.{s}")

    nums = ["1", "2", "3", "7", "8", "9", "0", "00", "07", "10", "11", "12", "13", "69", "77", "99", "101", "404", "420", "777", "888", "999", "007", "123", "1337"]
    years = ["88", "90", "95", "96", "97", "98", "99", "00", "01", "02", "03", "04", "05", "10", "1995", "1998", "1999", "2000", "2001", "2002", "2003", "2004", "2005"]

    for n in nums:
        add(f"{base}{n}")
        add(f"{base}_{n}")
    for y in years:
        add(f"{base}{y}")
        add(f"{base}_{y}")

    leet_map = {"o": "0", "e": "3", "i": "1", "l": "1", "a": "4", "s": "5", "t": "7"}
    leet_chars = [leet_map.get(ch, ch) for ch in base]
    leet_str = "".join(leet_chars)
    if leet_str != base:
        add(leet_str)
        add(f"{leet_str}_")
        add(f"{leet_str}123")
        add(f"real_{leet_str}")

    if extra_keyword:
        kw = extra_keyword.strip().lower()
        for sep in ["", "_", ".", "-"]:
            add(f"{base}{sep}{kw}")
            add(f"{kw}{sep}{base}")
            add(f"{base}{sep}{kw}_official")

    return variations


# ==============================================================================
# 7. IP LOOKUP ENGINE
# ==============================================================================


async def run_ip_lookup_async():
    prompt_text = "» Enter target IP / Domain: " if SIMPLE_MODE else "[bold #ff1a1a]» Enter target IP / Domain: [/bold #ff1a1a]"
    target = input(prompt_text).strip() if SIMPLE_MODE else console.input(prompt_text).strip()
    if not target:
        return False

    url = f"http://ip-api.com/json/{target}?fields=status,message,country,countryCode,regionName,city,zip,lat,lon,timezone,isp,org,as,query,reverse,proxy,hosting"
    connector = aiohttp.TCPConnector(ssl=config["verify_ssl"])

    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get(url, headers=DynamicHeaders.get(), timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data = await resp.json()
                if data.get("status") != "success":
                    print(f"[-] Lookup failed: {data.get('message', 'Invalid target')}")
                    return True

                if SIMPLE_MODE:
                    print(f"\n--- IP INTEL: {data.get('query')} ---")
                    print(f"Country:       {data.get('country')} ({data.get('countryCode')})")
                    print(f"City / Region: {data.get('city')}, {data.get('regionName')} ({data.get('zip', 'N/A')})")
                    print(f"ISP / Org:     {data.get('isp')} / {data.get('org')}")
                    print(f"ASN:           {data.get('as')}")
                    print(f"Proxy / VPN:   {'YES' if data.get('proxy') else 'No'}\n")
                else:
                    table = RichTable(title=f"IP INTEL: {data.get('query')}", style="#ff0033", border_style="#ff3344")
                    table.add_column("Property", style="bold white")
                    table.add_column("Details", style="bold #ff6666")
                    table.add_row("Country", f"{data.get('country')} ({data.get('countryCode')})")
                    table.add_row("City / Region", f"{data.get('city')}, {data.get('regionName')} ({data.get('zip', 'N/A')})")
                    table.add_row("ISP / Org", f"{data.get('isp')} / {data.get('org')}")
                    table.add_row("ASN", f"{data.get('as')}")
                    table.add_row("Proxy / VPN", "YES" if data.get("proxy") else "No / Unknown")
                    console.print("\n")
                    console.print(table)
                    console.print("\n")
        except Exception as e:
            print(f"[-] Error: {e}")
    return True


# ==============================================================================
# ГОЛОВНЕ МЕНЮ ТА АСИНХРОННИЙ ЦИКЛ
# ==============================================================================


async def username_search_menu_async(sites):
    while True:
        show_banner()
        prompt = "night-hawk/username > " if SIMPLE_MODE else "[bold #ff1a1a]night-hawk/username > [/bold #ff1a1a]"
        if SIMPLE_MODE:
            pdf_icon = "+" if config["export_pdf"] else "-"
            print(f"USERNAME SEARCH:\n [1] Single Target\n [2] Batch Scan\n [3] PDF Export [{pdf_icon}]\n [0] Back\n")
            choice = input(prompt).strip()
        else:
            pdf_icon = "[bold #00ff66]✔[/bold #00ff66] " if config["export_pdf"] else "[bold #ff3344]✖[/bold #ff3344] "
            console.print("[bold #ff1a1a]USERNAME SEARCH MODE:[/bold #ff1a1a]")
            console.print(" [bold #ff0033][1][/bold #ff0033] Single Target")
            console.print(" [bold #ff0033][2][/bold #ff0033] Batch Scan")
            console.print(f" [bold #ff0033][3][/bold #ff0033] PDF Export [{pdf_icon}]")
            console.print(" [bold #ff0033][0][/bold #ff0033] Back\n")
            choice = console.input(prompt).strip()

        if choice == "1":
            target = (input("» Username: ") if SIMPLE_MODE else console.input("[bold #ff1a1a]» Username: [/bold #ff1a1a]")).strip()
            if target:
                await scan_target_async(target, sites, "search")
                input("\nPress Enter...")
        elif choice == "2":
            raw = (input("» Targets (comma separated): ") if SIMPLE_MODE else console.input("[bold #ff1a1a]» Targets: [/bold #ff1a1a]")).strip()
            targets = [t.strip() for t in raw.split(",") if t.strip()]
            for t in targets:
                await scan_target_async(t, sites, "search")
            if targets:
                input("\nPress Enter...")
        elif choice == "3":
            config["export_pdf"] = not config["export_pdf"]
        elif choice in ("0", "q", "back"):
            break


async def email_search_menu_async():
    while True:
        show_banner()
        prompt = "night-hawk/email > " if SIMPLE_MODE else "[bold #ff1a1a]night-hawk/email > [/bold #ff1a1a]"
        if SIMPLE_MODE:
            print("EMAIL SEARCH:\n [1] Single Target\n [0] Back\n")
            choice = input(prompt).strip()
        else:
            console.print("[bold #ff1a1a]EMAIL SEARCH MODE:[/bold #ff1a1a]\n [bold #ff0033][1][/bold #ff0033] Single Target\n [bold #ff0033][0][/bold #ff0033] Back\n")
            choice = console.input(prompt).strip()

        if choice == "1":
            email = (input("» Target Email: ") if SIMPLE_MODE else console.input("[bold #ff1a1a]» Target Email: [/bold #ff1a1a]")).strip()
            if re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email):
                await scan_email_async(email)
                input("\nPress Enter...")
            else:
                print("[-] Invalid email format!")
                time.sleep(1)
        elif choice in ("0", "q", "back"):
            break


async def permutator_menu_async(sites):
    prompt_base = "» Base username: " if SIMPLE_MODE else "[bold #ff1a1a]» Base username: [/bold #ff1a1a]"
    base = (input(prompt_base) if SIMPLE_MODE else console.input(prompt_base)).strip()
    if not base:
        return

    prompt_extra = "» Optional keyword / year: " if SIMPLE_MODE else "[bold #ff1a1a]» Optional keyword / year: [/bold #ff1a1a]"
    extra = (input(prompt_extra) if SIMPLE_MODE else console.input(prompt_extra)).strip()

    perms = generate_permutations(base, extra)
    comma_str = ", ".join(perms)

    if SIMPLE_MODE:
        print(f"\n[+] Generated {len(perms)} permutations:\n{comma_str}\n")
        launch = input("» Turbo Scan all variations now? (y/N): ").strip().lower()
    else:
        console.print(f"\n[bold #ff1a1a]:sparkles: Generated [bold white]{len(perms)}[/bold white] permutations:[/bold #ff1a1a]\n")
        console.print(Panel(comma_str, title="Permutations", border_style="#ff3344"))
        launch = console.input("[bold #ff1a1a]» Turbo Scan all variations now? (y/N): [/bold #ff1a1a]").strip().lower()

    if launch in ("y", "yes"):
        for t in perms:
            await scan_target_async(t, sites, "search")
        input("\nPress Enter...")


async def main_async():
    show_banner()
    sites = await load_database_async()

    while True:
        if SIMPLE_MODE:
            print("SELECT OPTION:\n [1] Username Search\n [2] IP Lookup\n [3] Email Search\n [4] Permutator Engine\n [5] Nickname Availability (Free)\n [0] Exit\n")
            choice = input("night-hawk > ").strip()
        else:
            console.print("[bold #ff1a1a]SELECT OPTION:[/bold #ff1a1a]")
            console.print(" [bold #ff0033][1][/bold #ff0033] Username Search")
            console.print(" [bold #ff0033][2][/bold #ff0033] IP Lookup")
            console.print(" [bold #ff0033][3][/bold #ff0033] Email Search")
            console.print(" [bold #ff0033][4][/bold #ff0033] Permutator Engine")
            console.print(" [bold #ff0033][5][/bold #ff0033] Nickname Availability (Free)")
            console.print(" [bold #ff0033][0][/bold #ff0033] Exit\n")
            choice = console.input("[bold #ff1a1a]night-hawk > [/bold #ff1a1a]").strip()

        if choice == "1":
            await username_search_menu_async(sites)
            show_banner()
        elif choice == "2":
            await run_ip_lookup_async()
            input("\nPress Enter...")
            show_banner()
        elif choice == "3":
            await email_search_menu_async()
            show_banner()
        elif choice == "4":
            await permutator_menu_async(sites)
            show_banner()
        elif choice == "5":
            target = (input("» Check availability for: ") if SIMPLE_MODE else console.input("[bold #ff1a1a]» Check availability for: [/bold #ff1a1a]")).strip()
            if target:
                await scan_target_async(target, sites, "avail")
                input("\nPress Enter...")
            show_banner()
        elif choice in ("0", "q", "exit"):
            print("\nExiting NIGHT HAWK...")
            break


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n[!] Execution interrupted by user. Exiting...")


if __name__ == "__main__":
    main()