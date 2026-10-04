#!/usr/bin/env python3
# ══════════════════════════════════════════════════════════════
#   🔥 BRONX ULTRA v23.0 — PROXY ENFORCED + 24/7 AUTO
# ══════════════════════════════════════════════════════════════
import json, random, string, time, re, urllib3, os, threading
from datetime import datetime, timezone, timedelta
from flask import Flask, request, Response
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as cf

urllib3.disable_warnings()
app = Flask(__name__)
BASE = "https://freefollower.net"

# ... baaki pura code ...

# ─────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))
DATA_DIR = os.environ.get("BRONX_DATA_DIR", os.path.expanduser("~/bronx_data"))
VAULT_FILE = f"{DATA_DIR}/vault.json"
PROXY_VAULT_FILE = f"{DATA_DIR}/proxy_vault.json"
SETTINGS_FILE = f"{DATA_DIR}/settings.json"
SESSIONS_DIR = f"{DATA_DIR}/sessions"
POOL_FILE = f"{DATA_DIR}/session_pool.json"
LOG_DIR = f"{DATA_DIR}/logs"
for d in [DATA_DIR, SESSIONS_DIR, LOG_DIR]:
    os.makedirs(d, exist_ok=True)

# ─────────────────────────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────────────────────────
PROXY_VAULT = []
PROXY_VAULT_LOCK = threading.Lock()
VAULT = {"accounts": [], "created_at": None, "last_updated": None}
VAULT_LOCK = threading.Lock()
SESSION_POOL = []
SESSION_POOL_LOCK = threading.Lock()
IP_COUNT = {}
IP_COUNT_LOCK = threading.Lock()

SETTINGS = {
    "proxy_mode": "vault",
    "create_workers": 3,
    "order_workers": 20,
    "test_workers": 200,
    "request_timeout": 15,
    "retry_count": 3,
    "delay_between": 0.1,
    "fast_proxy_only": True,
    "auto_fallback_direct": False,   # ⚠️ DEFAULT OFF — proxy mandatory
    "use_session_pool": True,
    "pool_size": 5000,
    "local_proxy": "127.0.0.1:7890",
    "max_per_ip": 5,
    "cooldown_mode": "smart",
    "auto_midnight_reset": True,
    "proxy_max_ms": 8000,
    "auto_cleanup_dead": True,
    "strict_proxy": True,             # ⚠️ NEW: never fallback to direct
}

STATS = {
    "created": 0, "orders": 0, "views": 0, "reactions": 0,
    "failed": 0, "reused": 0, "captcha": 0,
    "sessions_built": 0, "session_errors": 0, "total_ms": 0,
    "json_errors": 0, "proxies_scanned": 0,
    "proxies_added": 0, "proxy_test_pass": 0, "proxy_test_fail": 0,
    "partial_orders": 0, "midnight_resets": 0,
    "orders_placed": 0, "orders_failed": 0,
    "token_success": 0, "token_fail": 0,
    "direct_blocked": 0,              # ⚠️ NEW: tracks blocked direct attempts
}
STATS_LOCK = threading.Lock()

STOP_CREATE = threading.Event()
STOP_ORDER = threading.Event()
STOP_POOL = threading.Event()
STOP_AUTO = threading.Event()
STOP_SCAN = threading.Event()

AUTOMATION_RUNNING = False
AUTOMATION_THREAD = None
AUTOMATION_STATE = {
    "cycle": 0, "phase": "idle", "created": 0,
    "fetched": 0, "attempts": 0, "last": None,
    "started": None, "next": None, "target": 100,
    "workers": 3, "ghost": True, "proxy": "vault",
    "prefix": "bronx",
}
AUTOMATION_LOCK = threading.Lock()

BUILDER_RUNNING = False
MIDNIGHT_RUNNING = False

# ─────────────────────────────────────────────────────────────
#  TIME
# ─────────────────────────────────────────────────────────────
def now_ist():
    return datetime.now(IST)

def today_ist():
    return now_ist().strftime("%Y-%m-%d")

def secs_midnight():
    n = now_ist()
    t = (n + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (t - n).total_seconds()

def log_to_file(msg):
    try:
        with open(f"{LOG_DIR}/{today_ist()}.log", "a") as f:
            f.write(f"[{now_ist().strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────
#  FILE IO
# ─────────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return default

def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def load_all():
    global PROXY_VAULT, VAULT, SETTINGS, SESSION_POOL
    PROXY_VAULT = load_json(PROXY_VAULT_FILE, [])
    VAULT = load_json(VAULT_FILE, {"accounts": [], "created_at": time.time(), "last_updated": time.time()})
    SESSION_POOL = load_json(POOL_FILE, [])
    s = load_json(SETTINGS_FILE, {})
    if s:
        SETTINGS.update(s)

def save_pv():
    with PROXY_VAULT_LOCK:
        save_json(PROXY_VAULT_FILE, PROXY_VAULT)

def save_av():
    with VAULT_LOCK:
        VAULT["last_updated"] = time.time()
        save_json(VAULT_FILE, VAULT)

def save_set():
    save_json(SETTINGS_FILE, SETTINGS)

def save_pool():
    with SESSION_POOL_LOCK:
        save_json(POOL_FILE, SESSION_POOL)

# ─────────────────────────────────────────────────────────────
#  PROXY HELPERS
# ─────────────────────────────────────────────────────────────
def detect_proto(proxy):
    p = proxy.lower().strip()
    if p.startswith("socks5"): return "socks5"
    if p.startswith("socks4"): return "socks4"
    if p.startswith("https"): return "https"
    return "http"

def proxy_url(proxy):
    proto = detect_proto(proxy)
    core = proxy.split("://", 1)[1] if "://" in proxy else proxy
    if proto == "socks5": return f"socks5://{core}"
    if proto == "socks4": return f"socks4://{core}"
    return f"http://{core}"

IP_PORT_RE = re.compile(r'^[a-zA-Z0-9\.\-_]+:\d{2,5}$')
IP_EXTRACT = re.compile(r'((?:\d{1,3}\.){3}\d{1,3}:\d{2,5})')

def clean_proxy_line(raw):
    if not raw:
        return None
    line = str(raw).strip()
    if not line or line.startswith("#"):
        return None
    core = line.split("://", 1)[1] if "://" in line else line
    if IP_PORT_RE.match(core):
        return line
    m = IP_EXTRACT.search(line)
    if m:
        return m.group(1)
    return None

# ─────────────────────────────────────────────────────────────
#  PROXY VAULT
# ─────────────────────────────────────────────────────────────
def proxy_exists(proxy):
    with PROXY_VAULT_LOCK:
        for p in PROXY_VAULT:
            if p["proxy"] == proxy:
                return True
    return False

def add_proxy(proxy, ms=None):
    proxy = proxy.strip()
    if not proxy or proxy_exists(proxy):
        return False
    entry = {
        "proxy": proxy,
        "protocol": detect_proto(proxy),
        "added_at": time.time(),
        "response_ms": ms,
        "success_count": 0,
        "fail_count": 0,
        "total_used": 0,
        "last_used": None,
        "status": "working",
        "speed": "unknown",
    }
    if ms:
        if ms < 2000: entry["speed"] = "fast"
        elif ms < 5000: entry["speed"] = "medium"
        else: entry["speed"] = "slow"
    with PROXY_VAULT_LOCK:
        PROXY_VAULT.append(entry)
    with STATS_LOCK:
        STATS["proxies_added"] += 1
    return True

def remove_proxy(proxy):
    global PROXY_VAULT
    with PROXY_VAULT_LOCK:
        PROXY_VAULT = [p for p in PROXY_VAULT if p["proxy"] != proxy]
    save_pv()

def update_proxy_result(proxy, success, ms=None):
    if not proxy or proxy == "direct":
        return
    with PROXY_VAULT_LOCK:
        for p in PROXY_VAULT:
            if p["proxy"] == proxy:
                p["last_used"] = time.time()
                p["total_used"] = p.get("total_used", 0) + 1
                if ms:
                    p["response_ms"] = ms
                if success:
                    p["success_count"] = p.get("success_count", 0) + 1
                    if p.get("status") == "dead":
                        p["status"] = "working"
                else:
                    p["fail_count"] = p.get("fail_count", 0) + 1
                    if SETTINGS.get("auto_cleanup_dead") and p["fail_count"] >= 5:
                        p["status"] = "dead"
                break
    save_pv()

def get_working_proxies():
    with PROXY_VAULT_LOCK:
        working = [p for p in PROXY_VAULT if p.get("status") == "working"]
        if SETTINGS.get("fast_proxy_only"):
            mx = SETTINGS.get("proxy_max_ms", 8000)
            fast = [p for p in working if (p.get("response_ms") or 9999) < mx]
            return fast if fast else working
        return list(working)

def pick_proxy():
    working = get_working_proxies()
    if not working:
        return None
    max_per_ip = SETTINGS.get("max_per_ip", 5)
    with IP_COUNT_LOCK:
        avail = [p for p in working if IP_COUNT.get(p["proxy"], 0) < max_per_ip]
    pool = avail if avail else working
    weighted = []
    for p in pool:
        w = max(1, (p.get("success_count", 0) + 1) * 3 - p.get("fail_count", 0) * 2)
        weighted.extend([p["proxy"]] * min(w, 10))
    if weighted:
        return random.choice(weighted)
    return random.choice([p["proxy"] for p in pool])

def get_proxy_for(mode):
    if mode == "vpn":
        return None
    if mode == "local":
        return SETTINGS.get("local_proxy", "127.0.0.1:7890") or None
    return pick_proxy()

# ─────────────────────────────────────────────────────────────
#  PROXY TEST / SCAN
# ─────────────────────────────────────────────────────────────
def test_one_proxy(proxy):
    if STOP_SCAN.is_set():
        return {"proxy": proxy, "ms": 9999, "ok": False}
    try:
        t0 = time.time()
        px = proxy_url(proxy)
        r = cf.get(f"{BASE}/", proxies={"http": px, "https": px},
                   timeout=8, verify=False, impersonate="chrome120")
        ms = int((time.time() - t0) * 1000)
        if r.status_code < 400:
            with STATS_LOCK:
                STATS["proxy_test_pass"] += 1
            return {"proxy": proxy, "ms": ms, "ok": True}
        with STATS_LOCK:
            STATS["proxy_test_fail"] += 1
        return {"proxy": proxy, "ms": ms, "ok": False}
    except Exception:
        with STATS_LOCK:
            STATS["proxy_test_fail"] += 1
        return {"proxy": proxy, "ms": 9999, "ok": False}

def test_proxies_batch(proxies, workers=200, progress_cb=None):
    results = []
    done = 0
    total = len(proxies)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(test_one_proxy, p): p for p in proxies}
        for fut in as_completed(futs):
            done += 1
            results.append(fut.result())
            if progress_cb and (done % 25 == 0 or done == total):
                try:
                    progress_cb(done, total)
                except Exception:
                    pass
    return results

def scan_bulk_proxies(raw_list, max_test=2000):
    clean = []
    seen = set()
    for raw in raw_list:
        p = clean_proxy_line(raw)
        if p and p not in seen:
            seen.add(p)
            clean.append(p)
    log_to_file(f"Bulk: {len(clean)} clean from {len(raw_list)} raw")
    to_test = clean[:max_test]
    with STATS_LOCK:
        STATS["proxies_scanned"] += len(clean)
    results = test_proxies_batch(to_test, workers=SETTINGS.get("test_workers", 200))
    mx = SETTINGS.get("proxy_max_ms", 8000)
    working = [r for r in results if r["ok"] and r["ms"] < mx]
    added = 0
    for r in working:
        if add_proxy(r["proxy"], ms=r["ms"]):
            added += 1
    save_pv()
    return {
        "raw": len(raw_list), "clean": len(clean), "tested": len(to_test),
        "working": len(working), "added": added,
    }

# ─────────────────────────────────────────────────────────────
#  AUTO FETCH SOURCES
# ─────────────────────────────────────────────────────────────
PROXY_SOURCES = [
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all",
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&protocol=http&timeout=5000",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&timeout=5000",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=5000",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks4.txt",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/mertguvencli/http-proxy-list/main/proxy-list/data.txt",
]
IP_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b')

def fetch_sources():
    all_p = set()
    def fetch(u):
        try:
            r = cf.get(u, timeout=10, verify=False, impersonate="chrome120")
            if r.status_code == 200:
                return IP_RE.findall(r.text)
        except Exception:
            pass
        return []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for res in ex.map(fetch, PROXY_SOURCES):
            all_p.update(res)
    return list(all_p)

# ─────────────────────────────────────────────────────────────
#  DEVICE MANAGER
# ─────────────────────────────────────────────────────────────
DEVICES = [
    ("Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36", "Galaxy S24"),
    ("Mozilla/5.0 (Linux; Android 13; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36", "Pixel 8"),
    ("Mozilla/5.0 (Linux; Android 12; Redmi Note 11) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36", "Redmi Note 11"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1", "iPhone 15"),
    ("Mozilla/5.0 (Linux; Android 11; Vivo Y21) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36", "Vivo Y21"),
    ("Mozilla/5.0 (Linux; Android 14; OnePlus 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36", "OnePlus 12"),
    ("Mozilla/5.0 (Linux; Android 13; OPPO Reno8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36", "OPPO Reno8"),
    ("Mozilla/5.0 (Linux; Android 12; Infinix X6819) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36", "Infinix"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36", "Windows PC"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15", "Mac"),
]

def random_device():
    ua, name = random.choice(DEVICES)
    ua = re.sub(r'Chrome/\d+\.0\.\d+\.\d+', f'Chrome/{random.randint(115,124)}.0.{random.randint(0,9)}.{random.randint(0,99)}', ua)
    ua = re.sub(r'Version/\d+\.\d+', f'Version/{random.randint(15,18)}.{random.randint(0,5)}', ua)
    ua = re.sub(r'OS \d+_\d+', f'OS {random.randint(14,18)}_{random.randint(0,6)}', ua)
    ua = re.sub(r'Android \d+', f'Android {random.randint(10,14)}', ua)
    return {"ua": ua, "name": name}

# ─────────────────────────────────────────────────────────────
#  HTTP HELPERS
# ─────────────────────────────────────────────────────────────
def hdr(referer=None, xsrf=None, ct=None, ua=None):
    h = {
        'Accept': "application/json, text/plain, */*",
        'Accept-Language': "en-US,en;q=0.7",
        'x-site-host': "freefollower.net",
        'x-tablet': "true",
        'X-Requested-With': "XMLHttpRequest",
        'Origin': BASE,
        'Referer': referer or f"{BASE}/",
    }
    if ua:
        h['User-Agent'] = ua
    if xsrf and xsrf != 'no-xsrf':
        h['x-xsrf-token'] = xsrf
    if ct:
        h['Content-Type'] = ct
    return h

def new_session(proxy=None, device=None):
    if device is None:
        device = random_device()
    s = cf.Session(impersonate="chrome120")
    s.verify = False
    s.headers.update({'User-Agent': device["ua"], 'Accept-Language': "en-US,en;q=0.7"})
    s.device = device
    if proxy:
        px = proxy_url(proxy)
        s.proxies = {"http": px, "https": px}
    return s

def get_ck(s):
    try:
        return dict(s.cookies)
    except Exception:
        return {}

def set_ck(s, d):
    if not d:
        return
    for k, v in d.items():
        try:
            s.cookies.set(k, v)
        except Exception:
            pass

def safe_json(txt):
    try:
        return json.loads(txt)
    except Exception:
        with STATS_LOCK:
            STATS["json_errors"] += 1
        return None

def is_html(t):
    if not t:
        return False
    s = t.strip()[:20].lower()
    return s.startswith("<!") or s.startswith("<html")

def save_sess_file(email, cookies, ua):
    try:
        with open(f"{SESSIONS_DIR}/{email.replace('@','_at_')}.json", "w") as f:
            json.dump({"cookies": cookies, "ua": ua, "saved": time.time()}, f)
    except Exception:
        pass

def get_token_multi(s):
    endpoints = [
        ("/", None),
        ("/reg", None),
        ("/api/services/page?v=3&full=false", f"{BASE}/order"),
    ]
    for path, ref in endpoints:
        for attempt in range(2):
            try:
                r = s.get(f"{BASE}{path}",
                         headers=hdr(referer=ref or f"{BASE}/"),
                         timeout=SETTINGS["request_timeout"] + 5,
                         verify=False)
                if r.status_code >= 500:
                    continue
                if r.status_code in (403, 429):
                    time.sleep(1)
                    continue
                if is_html(r.text):
                    continue
                cks = get_ck(s)
                tok = cks.get('XSRF-TOKEN') or cks.get('socpanel_session')
                if tok:
                    with STATS_LOCK:
                        STATS["token_success"] += 1
                    return cks.get('XSRF-TOKEN', 'no-xsrf'), cks
                if cks:
                    time.sleep(0.5)
                    continue
            except Exception:
                time.sleep(0.5)
                continue
    with STATS_LOCK:
        STATS["token_fail"] += 1
    return None, None

def verify_proxy_live(proxy):
    """Quick check proxy is actually working (used to enforce proxy)."""
    try:
        px = proxy_url(proxy)
        r = cf.get(f"{BASE}/", proxies={"http": px, "https": px},
                   timeout=8, verify=False, impersonate="chrome120")
        return r.status_code < 400
    except Exception:
        return False

def get_fresh_session_proxy_only(proxy, retries=3, device=None):
    """
    STRICT: only works through given proxy. Never falls back to direct.
    """
    for attempt in range(retries):
        if STOP_CREATE.is_set():
            return None, None
        try:
            s = new_session(proxy=proxy, device=device)
            tok, cks = get_token_multi(s)
            if tok:
                return s, tok
            time.sleep(0.8 + attempt * 0.4)
        except Exception:
            time.sleep(0.8)
    return None, None

# ─────────────────────────────────────────────────────────────
#  ACCOUNT CREATION
# ─────────────────────────────────────────────────────────────
def rnd_str(n=10):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=n))

def rnd_pass():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=12))

def rnd_email(prefix="bronx"):
    return f"{prefix}{rnd_str(12)}@gmail.com"

def create_account(prefix="bronx", ghost=True, proxy_mode="vault"):
    """
    ⚠️ STRICT PROXY MODE:
      - vault / auto  → MUST use proxy from vault (no direct fallback)
      - vpn           → uses VPN (no proxy)
      - local         → uses local proxy
    """
    if STOP_CREATE.is_set():
        return {"ok": False, "error": "stopped"}

    # VPN mode — no proxy needed (user has VPN)
    if proxy_mode == "vpn":
        return _create_via_direct_or_local(prefix, ghost, mode="vpn")

    # Local proxy mode — must use the local proxy
    if proxy_mode == "local":
        local = SETTINGS.get("local_proxy", "").strip()
        if not local:
            return {"ok": False, "error": "no local proxy"}
        return _create_via_strict_proxy(prefix, ghost, local, source="local")

    # ⚠️ VAULT / AUTO — proxy MANDATORY
    # 1) If session pool has proxy sessions, use them first
    if SETTINGS.get("use_session_pool"):
        pool_entry = pop_pool_session()
        if pool_entry and pool_entry.get("proxy") and pool_entry["proxy"] != "direct":
            return do_register(pool_entry["cookies"], pool_entry["device"],
                             pool_entry["proxy"], prefix, time.time(),
                             "pool", pool_entry["proxy"])

    # 2) Try each attempt with a FRESH working proxy from vault
    attempts = SETTINGS.get("retry_count", 3)
    tried = set()
    last_err = "no proxy"
    for attempt in range(attempts):
        if STOP_CREATE.is_set():
            return {"ok": False, "error": "stopped"}
        proxy = pick_proxy()
        if not proxy:
            with STATS_LOCK:
                STATS["direct_blocked"] += 1
            return {"ok": False, "error": "Vault empty — no working proxy"}
        if proxy in tried and attempt < attempts - 1:
            # try a different one
            proxy = pick_proxy()
        tried.add(proxy)

        device = random_device()
        if ghost and attempt > 0:
            time.sleep(random.uniform(1.0, 2.5))

        # STRICT: only through proxy
        s, tok = get_fresh_session_proxy_only(proxy, retries=2, device=device)
        if s and tok:
            res = do_register(get_ck(s), device, proxy, prefix, time.time(),
                            "vault", proxy)
            if res.get("ok"):
                return res
            last_err = res.get("error", "register failed")
            # proxy used, but register failed — mark proxy bad and try next
            update_proxy_result(proxy, False)
            if last_err == "captcha":
                # captcha means proxy is actually working, just flagged
                return res
        else:
            # session via proxy failed → mark proxy bad
            update_proxy_result(proxy, False)
            last_err = "proxy session failed"

    with STATS_LOCK:
        STATS["session_errors"] += 1
    return {"ok": False, "error": last_err}


def _create_via_strict_proxy(prefix, ghost, proxy, source="local"):
    """Used for local proxy mode — mandatory proxy."""
    for attempt in range(SETTINGS.get("retry_count", 3)):
        if STOP_CREATE.is_set():
            return {"ok": False, "error": "stopped"}
        device = random_device()
        if ghost and attempt > 0:
            time.sleep(random.uniform(1.0, 2.5))
        s, tok = get_fresh_session_proxy_only(proxy, retries=2, device=device)
        if s and tok:
            res = do_register(get_ck(s), device, proxy, prefix, time.time(),
                            source, proxy)
            if res.get("ok"):
                return res
            if res.get("error") == "captcha":
                return res
        time.sleep(0.5)
    with STATS_LOCK:
        STATS["session_errors"] += 1
    return {"ok": False, "error": "local proxy failed"}


def _create_via_direct_or_local(prefix, ghost, mode="vpn"):
    """
    ONLY for VPN mode (user explicitly wants their own IP via VPN).
    """
    for attempt in range(SETTINGS.get("retry_count", 3)):
        if STOP_CREATE.is_set():
            return {"ok": False, "error": "stopped"}
        device = random_device()
        if ghost and attempt > 0:
            time.sleep(random.uniform(1.0, 2.5))
        try:
            s = new_session(proxy=None, device=device)
            tok, cks = get_token_multi(s)
            if s and tok:
                return do_register(get_ck(s), device, "direct", prefix,
                                 time.time(), mode, "direct-vpn")
        except Exception:
            pass
        time.sleep(0.6)
    with STATS_LOCK:
        STATS["session_errors"] += 1
    return {"ok": False, "error": "vpn session failed"}


def do_register(cookies, device, proxy, prefix, t0, source, key=None):
    try:
        s = cf.Session(impersonate="chrome120")
        s.verify = False
        s.headers.update({'User-Agent': device["ua"]})
        if proxy and proxy != "direct":
            px = proxy_url(proxy)
            s.proxies = {"http": px, "https": px}
        set_ck(s, cookies)
        try:
            s.get(f"{BASE}/", timeout=SETTINGS["request_timeout"], verify=False)
            tok, _ = get_token_multi(s)
        except Exception:
            tok = None
        ck = get_ck(s)
        tok = ck.get('XSRF-TOKEN', 'no-xsrf')
        login = rnd_str(9)
        email = rnd_email(prefix)
        password = rnd_pass()
        r = s.post(
            f"{BASE}/api/register",
            data=json.dumps({"login": login, "email": email, "password": password}),
            headers=hdr(referer=f"{BASE}/reg", xsrf=tok, ct="application/json", ua=device["ua"]),
            timeout=SETTINGS["request_timeout"]
        )
        elapsed_ms = int((time.time() - t0) * 1000)

        if r.status_code in (200, 201):
            d = safe_json(r.text)
            if not d:
                if proxy:
                    update_proxy_result(proxy, False)
                return {"ok": False, "error": "invalid JSON"}
            final_cookies = get_ck(s)
            if key:
                with IP_COUNT_LOCK:
                    IP_COUNT[key] = IP_COUNT.get(key, 0) + 1
            acc = {
                "ok": True, "login": login, "email": email, "password": password,
                "api_token": d.get("api_token"), "user_id": d.get("id"),
                "proxy": proxy or "direct", "cookies": final_cookies,
                "device_ua": device["ua"], "device_name": device["name"],
                "created_at": time.time(), "create_ms": elapsed_ms, "source": source,
                "viewed_today": False, "reacted_today": False,
                "view_count_today": 0, "reaction_count_today": 0,
                "last_view_at": None, "last_reaction_at": None,
                "last_reset_date": today_ist(), "cooldown_until": None,
            }
            vault_add(acc)
            save_sess_file(email, final_cookies, device["ua"])
            with STATS_LOCK:
                STATS["created"] += 1
                STATS["total_ms"] += elapsed_ms
            if proxy and proxy != "direct":
                update_proxy_result(proxy, True, elapsed_ms)
            return acc

        if "need_captcha" in r.text or r.status_code == 429:
            if key:
                with IP_COUNT_LOCK:
                    IP_COUNT[key] = SETTINGS.get("max_per_ip", 5)
            if proxy:
                update_proxy_result(proxy, False)
            with STATS_LOCK:
                STATS["captcha"] += 1
            return {"ok": False, "error": "captcha"}
        if proxy:
            update_proxy_result(proxy, False)
        return {"ok": False, "error": f"{r.status_code}"}
    except Exception as e:
        if proxy:
            update_proxy_result(proxy, False)
        return {"ok": False, "error": str(e)[:80]}

# ─────────────────────────────────────────────────────────────
#  ORDER PLACER
# ─────────────────────────────────────────────────────────────
def place_order(acc, service_id, link, qty, has_qty):
    if STOP_ORDER.is_set():
        return {"ok": False, "error": "stopped"}
    proxy = pick_proxy()
    t0 = time.time()
    try:
        ua = acc.get("device_ua")
        s = cf.Session(impersonate="chrome120")
        s.verify = False
        if ua:
            s.headers.update({'User-Agent': ua})
        if proxy:
            px = proxy_url(proxy)
            s.proxies = {"http": px, "https": px}
        set_ck(s, acc["cookies"])
        ck = get_ck(s)
        tok = ck.get('XSRF-TOKEN', 'no-xsrf')
        r = s.post(
            f"{BASE}/api/orders",
            data=json.dumps({"quantity": qty, "has_quantity_field": has_qty,
                             "link": link, "panel_service_id": service_id}),
            headers=hdr(referer=f"{BASE}/order/free-follower/{service_id}",
                        xsrf=tok, ct="application/json", ua=ua),
            timeout=SETTINGS["request_timeout"]
        )
        elapsed_ms = int((time.time() - t0) * 1000)
        if proxy:
            update_proxy_result(proxy, r.status_code < 500, elapsed_ms)
        if r.status_code in (200, 201):
            d = safe_json(r.text)
            if not d:
                return {"ok": False, "error": "invalid JSON"}
            with STATS_LOCK:
                STATS["orders"] += 1
                STATS["orders_placed"] += 1
            return {"ok": True, "order_id": d.get("id"), "qty": qty}
        with STATS_LOCK:
            STATS["orders_failed"] += 1
        return {"ok": False, "error": f"{r.status_code}"}
    except Exception as e:
        if proxy:
            update_proxy_result(proxy, False)
        return {"ok": False, "error": str(e)[:80]}

# ─────────────────────────────────────────────────────────────
#  ACCOUNT VAULT
# ─────────────────────────────────────────────────────────────
def vault_add(acc):
    with VAULT_LOCK:
        for a in VAULT["accounts"]:
            if a.get("email") == acc.get("email"):
                return False
        acc["added_at"] = time.time()
        VAULT["accounts"].append(acc)
    return True

def mark_used(email, is_view=False, is_react=False, qty=0):
    with VAULT_LOCK:
        for a in VAULT["accounts"]:
            if a.get("email") == email:
                now = time.time()
                if is_view:
                    a["viewed_today"] = True
                    a["view_count_today"] = a.get("view_count_today", 0) + qty
                    a["last_view_at"] = now
                if is_react:
                    a["reacted_today"] = True
                    a["reaction_count_today"] = a.get("reaction_count_today", 0) + qty
                    a["last_reaction_at"] = now
                mode = SETTINGS.get("cooldown_mode", "smart")
                if mode == "smart":
                    if a.get("viewed_today") and a.get("reacted_today"):
                        a["cooldown_until"] = now + 86400
                elif mode == "strict":
                    a["cooldown_until"] = now + 86400
                break

def is_ready(acc):
    if SETTINGS.get("auto_midnight_reset") or SETTINGS.get("cooldown_mode") == "midnight":
        today = today_ist()
        if acc.get("last_reset_date") != today:
            acc["viewed_today"] = False
            acc["reacted_today"] = False
            acc["view_count_today"] = 0
            acc["reaction_count_today"] = 0
            acc["cooldown_until"] = None
            acc["last_reset_date"] = today
    cd = acc.get("cooldown_until")
    if not cd:
        return True
    return time.time() >= cd

def get_ready_accounts():
    with VAULT_LOCK:
        return [a for a in VAULT["accounts"] if is_ready(a)]

def reset_daily():
    today = today_ist()
    count = 0
    with VAULT_LOCK:
        for a in VAULT["accounts"]:
            if a.get("last_reset_date") != today:
                a["viewed_today"] = False
                a["reacted_today"] = False
                a["view_count_today"] = 0
                a["reaction_count_today"] = 0
                a["cooldown_until"] = None
                a["last_reset_date"] = today
                count += 1
    save_av()
    return count

# ─────────────────────────────────────────────────────────────
#  SESSION POOL
# ─────────────────────────────────────────────────────────────
def build_pool_session():
    if STOP_POOL.is_set():
        return None
    proxy = pick_proxy()
    # ⚠️ strict: no direct fallback in pool either
    if not proxy:
        return None
    try:
        dev = random_device()
        s = new_session(proxy=proxy, device=dev)
        tok, ck = get_token_multi(s)
        if not tok:
            update_proxy_result(proxy, False)
            return None
        update_proxy_result(proxy, True)
        return {"cookies": ck, "device": dev, "proxy": proxy,
                "built_at": time.time(), "used": False}
    except Exception:
        update_proxy_result(proxy, False)
        return None

def pool_loop(target=5000, workers=30):
    global BUILDER_RUNNING
    BUILDER_RUNNING = True
    while not STOP_POOL.is_set() and len(SESSION_POOL) < target:
        need = target - len(SESSION_POOL)
        batch = min(workers * 3, need)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(build_pool_session) for _ in range(batch)]
            for fut in as_completed(futs):
                if STOP_POOL.is_set():
                    break
                res = fut.result()
                if res:
                    with SESSION_POOL_LOCK:
                        SESSION_POOL.append(res)
                        STATS["sessions_built"] += 1
        if len(SESSION_POOL) % 50 == 0:
            save_pool()
        time.sleep(0.3)
    save_pool()
    BUILDER_RUNNING = False

def start_pool(target=5000, workers=30):
    global BUILDER_RUNNING
    if BUILDER_RUNNING:
        return
    STOP_POOL.clear()
    t = threading.Thread(target=pool_loop, args=(target, workers), daemon=True)
    t.start()

def pop_pool_session():
    with SESSION_POOL_LOCK:
        for s in SESSION_POOL:
            if not s.get("used"):
                s["used"] = True
                return s
    return None

def pool_stats():
    with SESSION_POOL_LOCK:
        t = len(SESSION_POOL)
        u = sum(1 for s in SESSION_POOL if s.get("used"))
        return {"total": t, "used": u, "avail": t - u}

# ─────────────────────────────────────────────────────────────
#  MIDNIGHT
# ─────────────────────────────────────────────────────────────
def midnight_loop():
    global MIDNIGHT_RUNNING
    MIDNIGHT_RUNNING = True
    while MIDNIGHT_RUNNING:
        try:
            secs = secs_midnight()
            time.sleep(max(60, secs - 5))
            reset_daily()
            with STATS_LOCK:
                STATS["midnight_resets"] += 1
            time.sleep(60)
        except Exception:
            time.sleep(60)

def start_midnight():
    global MIDNIGHT_RUNNING
    if MIDNIGHT_RUNNING:
        return
    t = threading.Thread(target=midnight_loop, daemon=True)
    t.start()

# ─────────────────────────────────────────────────────────────
#  AUTOMATION — 24/7 INFINITE
# ─────────────────────────────────────────────────────────────
def auto_upd(**kw):
    with AUTOMATION_LOCK:
        AUTOMATION_STATE.update(kw)
        AUTOMATION_STATE["last"] = now_ist().strftime("%H:%M:%S")

def automation_loop():
    """
    24/7 INFINITE loop:
      1) If vault has no working proxies → auto-fetch from sources
      2) If still empty → retry fetch again after short wait
      3) Create accounts using ONLY vault proxies
      4) Repeat forever until STOP
    """
    global AUTOMATION_RUNNING
    AUTOMATION_RUNNING = True
    auto_upd(started=now_ist().strftime("%Y-%m-%d %H:%M:%S"))
    log_to_file("AUTOMATION v23 started (24/7 infinite)")

    while not STOP_AUTO.is_set():
        try:
            with AUTOMATION_LOCK:
                cycle = AUTOMATION_STATE["cycle"] + 1
                target = AUTOMATION_STATE["target"]
                workers = AUTOMATION_STATE["workers"]
                ghost = AUTOMATION_STATE["ghost"]
                proxy_mode = AUTOMATION_STATE["proxy"]
                prefix = AUTOMATION_STATE["prefix"]
            auto_upd(cycle=cycle, phase="checking")

            # ── STEP 1: Ensure vault has enough working proxies ──
            if proxy_mode in ("vault", "auto"):
                wp = get_working_proxies()
                need = max(workers * 2, 5)
                if len(wp) < need:
                    auto_upd(phase="fetching", attempts=0)
                    log_to_file(f"Cycle {cycle}: vault has {len(wp)} proxies, need {need}. Auto-fetching...")
                    att = 0
                    while not STOP_AUTO.is_set() and len(get_working_proxies()) < need and att < 8:
                        att += 1
                        auto_upd(attempts=att)
                        raw = fetch_sources()
                        if raw:
                            auto_upd(phase="testing")
                            r = scan_bulk_proxies(raw, max_test=800)
                            with AUTOMATION_LOCK:
                                AUTOMATION_STATE["fetched"] += r["added"]
                            log_to_file(f"Cycle {cycle} att{att}: fetched={r['added']}, vault now={len(get_working_proxies())}")
                        if len(get_working_proxies()) >= need:
                            break
                        time.sleep(2)
                    if not get_working_proxies():
                        # no proxies at all → wait and retry cycle (24/7 keeps trying)
                        auto_upd(phase="waiting-proxy", next="30s")
                        log_to_file(f"Cycle {cycle}: no proxies, retry in 30s")
                        # sleep in small chunks so STOP is responsive
                        for _ in range(30):
                            if STOP_AUTO.is_set():
                                break
                            time.sleep(1)
                        continue

            if STOP_AUTO.is_set():
                break

            # ── STEP 2: Create accounts via strict proxy ──
            auto_upd(phase="creating")
            log_to_file(f"Cycle {cycle}: creating {target} accounts via {proxy_mode}")

            def task(i):
                if STOP_AUTO.is_set():
                    return None
                return create_account(prefix=prefix, ghost=ghost, proxy_mode=proxy_mode)

            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(task, i): i for i in range(1, target + 1)}
                for fut in as_completed(futs):
                    if STOP_AUTO.is_set():
                        for f in futs:
                            f.cancel()
                        break
                    try:
                        r = fut.result()
                    except Exception:
                        r = None
                    if r and r.get("ok"):
                        with AUTOMATION_LOCK:
                            AUTOMATION_STATE["created"] += 1
                    time.sleep(SETTINGS.get("delay_between", 0.1))

            save_av()

            # ── STEP 3: Short cooldown, then next cycle (24/7) ──
            auto_upd(phase="idle", next="5s")
            for _ in range(5):
                if STOP_AUTO.is_set():
                    break
                time.sleep(1)

        except Exception as e:
            log_to_file(f"Auto err: {e}")
            for _ in range(10):
                if STOP_AUTO.is_set():
                    break
                time.sleep(1)

    AUTOMATION_RUNNING = False
    auto_upd(phase="stopped")
    log_to_file("AUTOMATION stopped")

def start_auto(config):
    global AUTOMATION_THREAD, AUTOMATION_RUNNING
    if AUTOMATION_RUNNING:
        return False
    STOP_AUTO.clear()
    with AUTOMATION_LOCK:
        AUTOMATION_STATE.update(config)
        AUTOMATION_STATE["cycle"] = 0
        AUTOMATION_STATE["created"] = 0
        AUTOMATION_STATE["fetched"] = 0
    AUTOMATION_THREAD = threading.Thread(target=automation_loop, daemon=True)
    AUTOMATION_THREAD.start()
    return True

def stop_auto():
    STOP_AUTO.set()

# ═════════════════════════════════════════════════════════════
#  HTML UI (same as v22 + small display for direct_blocked)
# ═════════════════════════════════════════════════════════════
HTML = r'''<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BRONX ULTRA v23.0</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#04060d;color:#e2e8f0;padding:14px;min-height:100vh;position:relative;overflow-x:hidden}
body::before{content:'';position:fixed;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle at 20% 30%,#f9731620,transparent 40%),radial-gradient(circle at 80% 70%,#a855f720,transparent 40%),radial-gradient(circle at 50% 50%,#3b82f620,transparent 50%);animation:bgRotate 25s linear infinite;z-index:-1;pointer-events:none}
@keyframes bgRotate{to{transform:rotate(360deg)}}
.wrap{max-width:1000px;margin:0 auto;position:relative;z-index:1}
.brand{text-align:center;margin-bottom:16px}
.brand h1{font-size:30px;font-weight:900;letter-spacing:3px;background:linear-gradient(90deg,#f97316,#ef4444,#a855f7,#3b82f6,#06b6d4,#f97316);background-size:400% 100%;-webkit-background-clip:text;color:transparent;animation:shine 3s linear infinite}
@keyframes shine{to{background-position:400% 0}}
.brand p{color:#64748b;font-size:11px;letter-spacing:1px;margin-top:4px}
.tabs{display:flex;gap:5px;margin-bottom:14px;overflow-x:auto;padding-bottom:4px;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab{padding:9px 15px;background:rgba(17,24,39,0.7);border:1px solid #1f2937;border-radius:9px;color:#94a3b8;font-size:12.5px;font-weight:600;cursor:pointer;white-space:nowrap;transition:.2s}
.tab.active{background:linear-gradient(135deg,#f97316,#ef4444);color:#fff;border-color:#ef4444;box-shadow:0 0 20px #ef444466}
.card{background:rgba(17,24,39,0.75);border:1px solid #1f2937;border-radius:14px;padding:16px;margin-bottom:12px}
.card h3{font-size:13.5px;color:#38bdf8;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center}
label{display:block;font-size:12px;color:#94a3b8;margin-bottom:4px;font-weight:600}
input,textarea,select{width:100%;padding:10px 12px;border-radius:9px;border:1px solid #1f2937;background:rgba(10,14,39,0.9);color:#e2e8f0;font-size:13.5px;margin-bottom:10px;outline:none}
input:focus,textarea:focus,select:focus{border-color:#38bdf8}
textarea{font-family:monospace;font-size:11px}
.row{display:flex;gap:8px}.row>div{flex:1}
button{width:100%;padding:11px;border:none;border-radius:9px;background:linear-gradient(135deg,#f97316,#ef4444);color:#fff;font-weight:700;font-size:13px;cursor:pointer;margin-bottom:6px;transition:.15s}
button:hover:not(:disabled){transform:translateY(-1px);box-shadow:0 8px 24px #ef444466}
button:disabled{opacity:.45;cursor:not-allowed}
button.alt{background:linear-gradient(135deg,#10b981,#059669)}
button.blue{background:linear-gradient(135deg,#3b82f6,#6366f1)}
button.purple{background:linear-gradient(135deg,#a855f7,#7c3aed)}
button.gray{background:linear-gradient(135deg,#475569,#334155)}
button.red{background:linear-gradient(135deg,#ef4444,#dc2626)}
button.speed{background:linear-gradient(135deg,#f59e0b,#f97316)}
button.cyan{background:linear-gradient(135deg,#06b6d4,#0891b2)}
button.sm{padding:7px;font-size:11.5px;margin-bottom:0}
button.auto{background:linear-gradient(135deg,#7c3aed,#a855f7);animation:autoPulse 2s ease-in-out infinite}
@keyframes autoPulse{0%,100%{box-shadow:0 0 25px #a855f788}50%{box-shadow:0 0 40px #a855f7cc}}
#log{background:rgba(5,8,16,0.95);border:1px solid #1e293b;border-radius:11px;padding:12px;font-family:monospace;font-size:11px;white-space:pre-wrap;max-height:400px;overflow-y:auto;margin-top:10px;display:none;line-height:1.6}
.stats-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.stat-box{background:rgba(10,14,39,0.8);border:1px solid #1f2937;border-radius:9px;padding:9px;text-align:center}
.stat-box .n{font-size:18px;font-weight:900;color:#38bdf8;display:block}
.stat-box .l{font-size:9.5px;color:#64748b;text-transform:uppercase}
.stat-box.green .n{color:#10b981}
.stat-box.orange .n{color:#f97316}
.stat-box.purple .n{color:#a855f7}
.stat-box.red .n{color:#ef4444}
.stat-box.blue .n{color:#3b82f6}
.stat-box.yellow .n{color:#f59e0b}
.stat-box.cyan .n{color:#06b6d4}
.stat{display:flex;justify-content:space-between;padding:5px 0;font-size:12px;color:#cbd5e1;border-bottom:1px dashed #1f2937}
.stat:last-child{border:0}
.stat b{color:#38bdf8}
.tip{font-size:10.5px;color:#64748b;margin-top:5px;line-height:1.55}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700}
.badge.on{background:#10b98133;color:#6ee7b7;border:1px solid #10b981}
.badge.off{background:#ef444433;color:#fca5a5;border:1px solid #ef4444}
.badge.cyan{background:#06b6d433;color:#67e8f9;border:1px solid #06b6d4}
.tab-content{display:none}.tab-content.active{display:block}
.px-list{max-height:400px;overflow-y:auto;font-family:monospace;font-size:11px}
.px-item{display:flex;justify-content:space-between;align-items:center;padding:6px 8px;border-bottom:1px solid #1f2937}
.px-ok{color:#6ee7b7}.px-dead{color:#fca5a5}.px-fast{color:#67e8f9}
.dot{width:7px;height:7px;border-radius:50%;display:inline-block}
.dot.g{background:#10b981}.dot.r{background:#ef4444}.dot.c{background:#06b6d4}
.bar{height:5px;background:#0f172a;border-radius:3px;overflow:hidden;margin-top:8px;display:none}
.bar>div{height:100%;background:linear-gradient(90deg,#38bdf8,#a855f7,#f97316);width:0%;transition:.3s}
.mode-switch{display:flex;background:#0a0e27;border:1px solid #1f2937;border-radius:9px;padding:3px;margin-bottom:10px}
.mode-switch button{flex:1;margin:0;padding:8px;font-size:11.5px;background:transparent;color:#94a3b8;border-radius:6px;font-weight:600}
.mode-switch button.active{background:linear-gradient(135deg,#3b82f6,#6366f1);color:#fff}
.acc-item{padding:6px 8px;border-bottom:1px dashed #1f2937;font-family:monospace;font-size:11px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px}
.acc-ok{color:#6ee7b7}.acc-cool{color:#fcd34d}.acc-partial{color:#38bdf8}
.upload{border:2px dashed #06b6d4;border-radius:12px;padding:18px;text-align:center;background:#06b6d411;cursor:pointer;margin-bottom:10px}
.upload:hover{border-color:#67e8f9;background:#06b6d422}
.upload input{display:none}
textarea.bulk{min-height:150px;font-family:monospace;font-size:11px;line-height:1.5}
.warn{background:#f59e0b22;border:1px solid #f59e0b;color:#fcd34d;padding:8px 10px;border-radius:8px;font-size:11px;margin-bottom:10px}
</style></head><body>
<div class="wrap">

<div class="brand">
<h1>🔥 BRONX ULTRA v23.0 🔥</h1>
<p>PROXY ENFORCED · 24/7 AUTO · NO LOCAL IP LEAK</p>
</div>

<div class="tabs">
<div class="tab active" onclick="tab('dash',this)">📊 Dash</div>
<div class="tab" onclick="tab('vault',this)">🏦 Proxy Vault</div>
<div class="tab" onclick="tab('auto',this)">🤖 Automation</div>
<div class="tab" onclick="tab('create',this)">👻 Create</div>
<div class="tab" onclick="tab('order',this)">🚀 Orders</div>
<div class="tab" onclick="tab('pool',this)">🎯 Pool</div>
<div class="tab" onclick="tab('acc',this)">💾 Accounts</div>
<div class="tab" onclick="tab('set',this)">⚙️ Settings</div>
</div>

<!-- DASH -->
<div id="tab-dash" class="tab-content active">
<div class="card">
<h3>📊 Live Stats</h3>
<div class="stats-grid">
<div class="stat-box green"><span class="n" id="s_created">0</span><span class="l">Created</span></div>
<div class="stat-box blue"><span class="n" id="s_orders">0</span><span class="l">Orders</span></div>
<div class="stat-box orange"><span class="n" id="s_views">0</span><span class="l">Views</span></div>
<div class="stat-box purple"><span class="n" id="s_reacts">0</span><span class="l">Reacts</span></div>
<div class="stat-box yellow"><span class="n" id="s_partial">0</span><span class="l">Partial</span></div>
<div class="stat-box red"><span class="n" id="s_failed">0</span><span class="l">Failed</span></div>
</div>
<div class="stat" style="margin-top:8px"><span>🏦 Vault working:</span><b id="d_pw" style="color:#67e8f9">0</b></div>
<div class="stat"><span>🖼️ Vault total:</span><b id="d_pt">0</b></div>
<div class="stat"><span>🚫 Direct blocked:</span><b id="d_db" style="color:#fca5a5">0</b></div>
<div class="stat"><span>🤖 Automation:</span><b id="d_auto">Stopped</b></div>
<div class="stat"><span>⏰ Next reset:</span><b id="d_nr" style="color:#c4b5fd">—</b></div>
<div class="stat"><span>🚫 Captcha:</span><b id="d_cap" style="color:#ef4444">0</b></div>
<div class="stat"><span>⚠️ JSON errors:</span><b id="d_json" style="color:#f59e0b">0</b></div>
<div class="stat"><span>🧪 Scanned:</span><b id="d_scan" style="color:#06b6d4">0</b></div>
<div class="stat"><span>🔑 Tokens OK:</span><b id="d_tok" style="color:#10b981">0</b></div>
<div class="stat"><span>❌ Token fails:</span><b id="d_tokf" style="color:#ef4444">0</b></div>
<div class="stat"><span>🎯 Session Pool:</span><b id="d_pool">0</b></div>
<div class="stat"><span>💾 Accounts:</span><b id="d_acc">0</b></div>
<div class="stat"><span>✅ Ready:</span><b id="d_rd" style="color:#6ee7b7">0</b></div>
<div class="stat"><span>⏳ Cooldown:</span><b id="d_cd" style="color:#fcd34d">0</b></div>
<div class="stat"><span>⏰ IST:</span><b id="d_time">—</b></div>
</div>
</div>

<!-- VAULT -->
<div id="tab-vault" class="tab-content">
<div class="card" style="border-color:#06b6d4">
<h3>🏦 Proxy Vault <span class="badge cyan" id="pv_badge">0 working</span></h3>
<div class="stats-grid" style="margin-bottom:12px">
<div class="stat-box cyan"><span class="n" id="pv_w">0</span><span class="l">Working</span></div>
<div class="stat-box red"><span class="n" id="pv_d">0</span><span class="l">Dead</span></div>
<div class="stat-box green"><span class="n" id="pv_f">0</span><span class="l">Fast</span></div>
<div class="stat-box yellow"><span class="n" id="pv_m">0</span><span class="l">Medium</span></div>
</div>

<div class="card" style="background:#06b6d411;border-color:#06b6d4">
<h3 style="color:#67e8f9">📁 Method 1: File Upload</h3>
<div class="upload" id="upz" onclick="document.getElementById('fi').click()">
<div style="font-size:30px">📁</div>
<div style="font-size:13px;color:#67e8f9;font-weight:600">Click to upload</div>
<div style="font-size:11px;color:#64748b">.txt · .csv · .json</div>
<input type="file" id="fi" accept=".txt,.csv,.json" onchange="onFile(this)">
</div>
<div id="upstat" style="font-size:11.5px;color:#67e8f9"></div>
</div>

<div class="card">
<h3>📝 Method 2: Manual Paste</h3>
<textarea id="bulk_in" class="bulk" placeholder="45.80.151.33:3129&#10;socks5://103.29.151.146:1080&#10;socks4://47.91.104.88:9050"></textarea>
<div class="row">
<div><button class="cyan" onclick="scanPaste()">🔍 Scan & Add</button></div>
<div><button class="gray" onclick="document.getElementById('bulk_in').value=''">🗑️ Clear</button></div>
</div>
</div>

<div class="card">
<h3>📡 Method 3: Auto Fetch</h3>
<button class="cyan" onclick="autoFetch()" id="afbtn">📡 Auto Fetch from Sources</button>
</div>

<div class="card">
<h3>💾 Export & Manage</h3>
<div class="row" style="margin-bottom:8px">
<div><button class="blue sm" onclick="expPV('txt')">📄 TXT</button></div>
<div><button class="blue sm" onclick="expPV('json')">📥 JSON</button></div>
</div>
<div class="row">
<div><button class="speed sm" onclick="revalPV()">🔬 Revalidate</button></div>
<div><button class="red sm" onclick="clearDeadPV()">🗑️ Remove Dead</button></div>
</div>
<div class="row">
<div><button class="purple sm" onclick="viewPV()">👁️ View</button></div>
<div><button class="red sm" onclick="clearAllPV()">⚠️ Clear ALL</button></div>
</div>
</div>

<div class="bar" id="pv_bar"><div></div></div>
<div id="pv_list" class="px-list" style="display:none;margin-top:10px;background:#050810;border-radius:10px;padding:8px;border:1px solid #06b6d4"></div>
</div>
</div>

<!-- AUTO -->
<div id="tab-auto" class="tab-content">
<div class="card" style="border:2px solid #a855f7">
<h3>🤖 Automation 24/7 <span class="badge on" id="ab_badge">STOPPED</span></h3>
<div class="warn">⚡ Infinite cycle: proxy fetch → create → repeat. Never stops until you press STOP.</div>
<div class="row">
<div><label>Target/cycle</label><input type="number" id="a_target" value="100"></div>
<div><label>Workers</label>
<select id="a_workers">
<option value="2">2</option><option value="3" selected>3</option><option value="5">5</option><option value="10">10</option>
</select></div>
</div>
<div class="row">
<div><label>Ghost</label>
<select id="a_ghost"><option value="yes" selected>👻 ON</option><option value="no">OFF</option></select></div>
<div><label>Proxy</label>
<select id="a_proxy"><option value="vault" selected>🏦 Vault</option><option value="vpn">🔒 VPN</option><option value="local">🏠 Local</option></select></div>
</div>
<label>Prefix</label>
<input type="text" id="a_prefix" value="bronx">
<div class="row" style="margin-top:8px">
<div><button class="auto" onclick="startAuto()" id="a_start">🚀 START 24/7</button></div>
<div><button class="red" onclick="stopAuto()" id="a_stop" style="display:none">⛔ STOP</button></div>
</div>
<div class="bar" id="a_bar" style="display:block"><div id="a_fill" style="width:0%"></div></div>
<div class="stats-grid" style="margin-top:12px">
<div class="stat-box purple"><span class="n" id="ac_cycle">0</span><span class="l">Cycle</span></div>
<div class="stat-box green"><span class="n" id="ac_created">0</span><span class="l">Created</span></div>
<div class="stat-box cyan"><span class="n" id="ac_fetched">0</span><span class="l">Added</span></div>
<div class="stat-box yellow"><span class="n" id="ac_phase">idle</span><span class="l">Phase</span></div>
</div>
<div id="a_log" style="background:#050810;border:1px solid #7c3aed;border-radius:10px;padding:10px;font-family:monospace;font-size:11px;white-space:pre-wrap;max-height:200px;overflow-y:auto;margin-top:10px;color:#c4b5fd">Ready...</div>
</div>
</div>

<!-- CREATE -->
<div id="tab-create" class="tab-content">
<div class="card">
<h3>👻 Create Accounts</h3>
<div class="warn">⚠️ Vault/Local mode me proxy MANDATORY — local IP use nahi hoga.</div>
<label>Ghost</label>
<div class="mode-switch">
<button onclick="setG(true,this)" id="g_on" class="active">👻 ON</button>
<button onclick="setG(false,this)" id="g_off">💤 OFF</button>
</div>
<label>Proxy</label>
<div class="mode-switch">
<button onclick="setPM('vault',this)" id="pm_v" class="active">🏦 Vault</button>
<button onclick="setPM('vpn',this)" id="pm_p">🔒 VPN</button>
<button onclick="setPM('local',this)" id="pm_l">🏠 Local</button>
</div>
<div id="lp_box" style="display:none">
<label>Local Proxy</label>
<input type="text" id="lp_in" value="127.0.0.1:7890">
</div>
<label>Accounts</label>
<input type="number" id="c_count" value="20">
<label>Prefix</label>
<input type="text" id="c_prefix" value="bronx">
<label>Workers</label>
<select id="c_workers">
<option value="2">2</option><option value="3" selected>3</option><option value="5">5</option><option value="10">10</option>
</select>
<div class="row">
<div><button class="speed" onclick="cStart()" id="c_btn">⚡ CREATE</button></div>
<div><button class="red" onclick="cStop()" id="c_stop" style="display:none">⛔ STOP</button></div>
</div>
<div class="bar" id="c_bar"><div></div></div>
<div class="stat"><span>✅ Success:</span><b id="c_suc" style="color:#10b981">0</b></div>
<div class="stat"><span>❌ Failed:</span><b id="c_fail" style="color:#ef4444">0</b></div>
</div>
</div>

<!-- ORDER -->
<div id="tab-order" class="tab-content">
<div class="card">
<h3>🚀 Orders</h3>
<label>Post URL</label>
<input type="text" id="o_link" placeholder="https://t.me/bronx_ultra_osint/331">
<div class="row">
<div><label>🎯 Accounts</label><input type="number" id="o_acc" value="1"></div>
<div><label>📺 Views/acc</label><input type="number" id="o_vpa" value="100"></div>
</div>
<div class="row">
<div><label>👍 Reacts/acc</label><input type="number" id="o_rpa" value="10"></div>
<div><label>Mode</label>
<select id="o_mode">
<option value="auto">N accounts</option>
<option value="all">ALL ready</option>
<option value="single">Single</option>
</select>
</div>
</div>
<div id="o_single" style="display:none"><label>Email</label><input type="text" id="o_email"></div>
<label>Workers (higher = faster)</label>
<select id="o_workers">
<option value="10">10</option><option value="20" selected>20</option><option value="30">30</option><option value="50">50</option>
</select>
<div id="o_calc" style="font-size:11px;color:#a5f3fc;font-family:monospace;padding:6px;background:#00000033;border-radius:6px;margin-top:6px">Preview...</div>
<div class="row" style="margin-top:8px">
<div><button class="alt" onclick="oRun('views')">📺 VIEWS</button></div>
<div><button class="blue" onclick="oRun('reactions')">👍 REACTS</button></div>
</div>
<div class="row">
<div><button class="speed" onclick="oRun('both')" id="o_btn">⚡ BOTH (FAST)</button></div>
<div><button class="red" onclick="oStop()" id="o_stop" style="display:none">⛔ STOP</button></div>
</div>
<div class="bar" id="o_bar"><div></div></div>
</div>
</div>

<!-- POOL -->
<div id="tab-pool" class="tab-content">
<div class="card">
<h3>🎯 Session Pool</h3>
<div style="display:flex;justify-content:space-between;background:#0c0a2e;border:1px solid #4c1d95;border-radius:10px;padding:12px;margin:10px 0">
<div><div style="font-size:11px;color:#94a3b8">Total</div><div style="font-size:24px;font-weight:900;color:#a855f7" id="p_total">0</div></div>
<div style="text-align:right"><div style="font-size:11px;color:#94a3b8">Available</div><div style="font-size:24px;font-weight:900;color:#10b981" id="p_avail">0</div></div>
<div style="text-align:right"><div style="font-size:11px;color:#94a3b8">Used</div><div style="font-size:24px;font-weight:900;color:#f59e0b" id="p_used">0</div></div>
</div>
<label>Target</label><input type="number" id="p_target" value="5000">
<label>Workers</label>
<select id="p_workers"><option value="10">10</option><option value="30" selected>30</option><option value="50">50</option></select>
<div class="row" style="margin-top:8px">
<div><button class="purple" onclick="pStart()" id="p_btn">🎯 BUILD</button></div>
<div><button class="red" onclick="pStop()" id="p_stop" style="display:none">⛔ STOP</button></div>
</div>
<div class="row"><div><button class="red sm" onclick="pClear()">🗑️ Clear Pool</button></div></div>
</div>
</div>

<!-- ACCOUNTS -->
<div id="tab-acc" class="tab-content">
<div class="card">
<h3>💾 Accounts Vault</h3>
<div class="row">
<div><button class="blue sm" onclick="expAcc('json')">📥 JSON</button></div>
<div><button class="blue sm" onclick="expAcc('txt')">📄 TXT</button></div>
<div><button class="blue sm" onclick="expAcc('csv')">📊 CSV</button></div>
</div>
<button class="purple sm" onclick="showImp()">📤 Import JSON</button>
<div id="imp_box" style="display:none;margin-top:8px">
<textarea id="imp_json" rows="4"></textarea>
<button class="alt sm" onclick="doImp()">✅ Import</button>
</div>
<div class="row" style="margin-top:8px">
<div><button class="gray sm" onclick="viewAcc()">👁️ View</button></div>
<div><button class="red sm" onclick="clearAcc()">🗑️ Clear</button></div>
</div>
<div class="row"><div><button class="purple sm" onclick="resetMan()">🌙 Manual Reset</button></div></div>
<div id="acc_view" style="display:none;margin-top:10px;background:#050810;border-radius:10px;padding:10px;max-height:400px;overflow-y:auto;font-family:monospace;font-size:11px"></div>
</div>
</div>

<!-- SETTINGS -->
<div id="tab-set" class="tab-content">
<div class="card">
<h3>⚙️ Settings</h3>
<label>Default Proxy Mode</label>
<select id="s_pm" onchange="saveSet('proxy_mode',this.value)">
<option value="vault">🏦 Vault</option><option value="vpn">🔒 VPN</option><option value="local">🏠 Local</option>
</select>
<label>Local Proxy</label>
<input type="text" id="s_lp" value="127.0.0.1:7890" onchange="saveSet('local_proxy',this.value)">
<label>Create Workers</label>
<input type="number" id="s_cw" value="3" onchange="saveSet('create_workers',+this.value)">
<label>Order Workers</label>
<input type="number" id="s_ow" value="20" onchange="saveSet('order_workers',+this.value)">
<label>Max per IP</label>
<input type="number" id="s_mx" value="5" onchange="saveSet('max_per_ip',+this.value)">
<label>Proxy max ms</label>
<input type="number" id="s_pms" value="8000" onchange="saveSet('proxy_max_ms',+this.value)">
<label>Cooldown Mode</label>
<select id="s_cm" onchange="saveSet('cooldown_mode',this.value)">
<option value="smart">🧠 Smart</option><option value="strict">🔒 Strict</option><option value="midnight">🌙 Midnight</option>
</select>
<label>Auto Midnight Reset</label>
<select id="s_mid" onchange="saveSet('auto_midnight_reset',this.value==='yes')">
<option value="yes">✅ Yes</option><option value="no">❌ No</option>
</select>
<label>Auto cleanup dead</label>
<select id="s_cl" onchange="saveSet('auto_cleanup_dead',this.value==='yes')">
<option value="yes">✅ Yes</option><option value="no">❌ No</option>
</select>
<label>Timeout (sec)</label>
<input type="number" id="s_to" value="15" onchange="saveSet('request_timeout',+this.value)">
<label>Allow direct fallback (NOT recommended)</label>
<select id="s_af" onchange="saveSet('auto_fallback_direct',this.value==='yes')">
<option value="no">❌ No (proxy mandatory)</option><option value="yes">⚠️ Yes</option>
</select>
<button class="alt" onclick="saveAll()" style="margin-top:8px">💾 Save All</button>
</div>
</div>

<div id="log"></div>
</div>

<script>
let GHOST = true;
let PM = 'vault';
let AUTO_ON = false;

function tab(name, el){
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  el.classList.add('active');
}

document.getElementById('o_mode').addEventListener('change', e => {
  document.getElementById('o_single').style.display = e.target.value === 'single' ? 'block' : 'none';
});

function showLog(m){const l=document.getElementById('log');l.style.display='block';l.textContent+=m+'\n';l.scrollTop=l.scrollHeight;}
function clearLog(){document.getElementById('log').textContent='';}
function setBar(id, p){const b=document.getElementById(id);if(!b)return;b.style.display='block';b.firstElementChild.style.width=p+'%';}
function setG(on, el){GHOST = on;document.querySelectorAll('#g_on,#g_off').forEach(b=>b.classList.remove('active'));el.classList.add('active');}
function setPM(m, el){PM = m;document.querySelectorAll('#pm_v,#pm_p,#pm_l').forEach(b=>b.classList.remove('active'));el.classList.add('active');document.getElementById('lp_box').style.display = m === 'local' ? 'block' : 'none';}

function calc(){
  const a = +document.getElementById('o_acc').value || 1;
  const v = +document.getElementById('o_vpa').value || 100;
  const r = +document.getElementById('o_rpa').value || 10;
  document.getElementById('o_calc').textContent = a + ' acc × ' + v + ' = ' + (a*v) + ' views | ' + a + ' × ' + r + ' = ' + (a*r) + ' reacts';
}
['o_acc','o_vpa','o_rpa'].forEach(id => document.getElementById(id).addEventListener('input', calc));

const upz = document.getElementById('upz');
['dragenter','dragover'].forEach(e => upz.addEventListener(e, ev => {ev.preventDefault(); upz.style.borderColor = '#10b981';}));
['dragleave','drop'].forEach(e => upz.addEventListener(e, ev => {ev.preventDefault(); upz.style.borderColor = '#06b6d4';}));
upz.addEventListener('drop', ev => {if(ev.dataTransfer.files.length) onFile({files: ev.dataTransfer.files});});

function onFile(inp){
  if(!inp.files.length) return;
  const f = inp.files[0];
  document.getElementById('upstat').textContent = '⏳ Reading ' + f.name + '...';
  const r = new FileReader();
  r.onload = async e => {
    const txt = e.target.result;
    let lines = [];
    if(f.name.endsWith('.json')){
      try{
        const j = JSON.parse(txt);
        if(Array.isArray(j)){
          j.forEach(it => {
            if(typeof it === 'string') lines.push(it);
            else if(it && it.proxy) lines.push(it.proxy);
          });
        }
      }catch(err){
        document.getElementById('upstat').textContent = '❌ Invalid JSON';
        return;
      }
    } else {
      lines = txt.split(/[\n\r,;]+/).map(s => s.trim()).filter(s => s);
    }
    document.getElementById('upstat').textContent = '📦 ' + lines.length + ' lines, scanning...';
    await scanList(lines);
    document.getElementById('upstat').textContent = '✅ Done!';
    refresh();
  };
  r.readAsText(f);
}

async function scanList(list){
  clearLog();
  showLog('🔍 Scanning ' + list.length + ' proxies...');
  setBar('pv_bar', 0);
  try{
    const res = await fetch('/api/pv/scan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({proxies: list})
    });
    const rd = res.body.getReader();
    const dc = new TextDecoder();
    let buf = '';
    while(true){
      const {value, done} = await rd.read();
      if(done) break;
      buf += dc.decode(value, {stream: true});
      let i;
      while((i = buf.indexOf('\n')) >= 0){
        const line = buf.slice(0, i);
        buf = buf.slice(i+1);
        if(!line.trim()) continue;
        try{
          const m = JSON.parse(line);
          if(m.text) showLog(m.text);
          if(m.pct !== undefined) setBar('pv_bar', m.pct);
        }catch(e){}
      }
    }
  }catch(e){showLog('❌ ' + e.message);}
}

async function scanPaste(){
  const t = document.getElementById('bulk_in').value;
  const l = t.split(/[\n\r,;]+/).map(s => s.trim()).filter(s => s.length > 3);
  if(!l.length){alert('Empty'); return;}
  if(!confirm('Scan ' + l.length + ' proxies?')) return;
  await scanList(l);
  refresh();
}

async function autoFetch(){
  const b = document.getElementById('afbtn');
  b.disabled = true;
  b.textContent = '⏳ Fetching...';
  clearLog();
  showLog('📡 Fetching...');
  setBar('pv_bar', 0);
  try{
    const res = await fetch('/api/pv/fetch', {method: 'POST'});
    const rd = res.body.getReader();
    const dc = new TextDecoder();
    let buf = '';
    while(true){
      const {value, done} = await rd.read();
      if(done) break;
      buf += dc.decode(value, {stream: true});
      let i;
      while((i = buf.indexOf('\n')) >= 0){
        const line = buf.slice(0, i);
        buf = buf.slice(i+1);
        if(!line.trim()) continue;
        try{
          const m = JSON.parse(line);
          if(m.text) showLog(m.text);
          if(m.pct !== undefined) setBar('pv_bar', m.pct);
        }catch(e){}
      }
    }
  }catch(e){showLog('❌ ' + e.message);}
  b.disabled = false;
  b.textContent = '📡 Auto Fetch from Sources';
  refresh();
}

async function revalPV(){
  clearLog();
  showLog('🔬 Revalidating...');
  setBar('pv_bar', 0);
  const r = await fetch('/api/pv/revalidate', {method: 'POST'});
  const rd = r.body.getReader();
  const dc = new TextDecoder();
  let buf = '';
  while(true){
    const {value, done} = await rd.read();
    if(done) break;
    buf += dc.decode(value, {stream: true});
    let i;
    while((i = buf.indexOf('\n')) >= 0){
      const line = buf.slice(0, i);
      buf = buf.slice(i+1);
      if(!line.trim()) continue;
      try{
        const m = JSON.parse(line);
        if(m.text) showLog(m.text);
        if(m.pct !== undefined) setBar('pv_bar', m.pct);
      }catch(e){}
    }
  }
  refresh();
}

async function clearDeadPV(){
  if(!confirm('Remove dead?')) return;
  const r = await fetch('/api/pv/clear-dead', {method: 'POST'});
  const d = await r.json();
  alert('✅ Removed ' + d.removed);
  refresh();
}

async function clearAllPV(){
  if(!confirm('Clear ALL?')) return;
  await fetch('/api/pv/clear-all', {method: 'POST'});
  refresh();
}

function expPV(f){window.location.href = '/api/pv/export?fmt=' + f;}

async function viewPV(){
  const b = document.getElementById('pv_list');
  if(b.style.display === 'none'){
    const r = await fetch('/api/pv/list');
    const d = await r.json();
    let h = '';
    d.proxies.slice(0, 500).forEach(p => {
      const cls = p.status === 'working' ? ((p.response_ms || 9999) < 2000 ? 'px-fast' : 'px-ok') : 'px-dead';
      const dot = p.status === 'working' ? ((p.response_ms || 9999) < 2000 ? 'c' : 'g') : 'r';
      const spd = p.response_ms ? p.response_ms + 'ms' : '—';
      h += '<div class="px-item"><div style="display:flex;gap:6px;align-items:center"><span class="dot ' + dot + '"></span><span class="' + cls + '">[' + p.protocol + '] ' + p.proxy + '</span><span style="color:#64748b;font-size:10px">' + spd + '</span></div><button onclick="delPV(\'' + p.proxy + '\')" style="width:auto;padding:2px 6px;font-size:9px;margin:0;background:#ef4444">🗑️</button></div>';
    });
    b.innerHTML = h || '<div style="text-align:center;padding:10px;color:#64748b">Empty</div>';
    b.style.display = 'block';
  } else {
    b.style.display = 'none';
  }
}

async function delPV(p){
  await fetch('/api/pv/remove', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({proxy: p})});
  document.getElementById('pv_list').style.display = 'none';
  viewPV();
  refresh();
}

async function startAuto(){
  if(!confirm('Start 24/7 Automation?')) return;
  const b = document.getElementById('a_start');
  const s = document.getElementById('a_stop');
  b.disabled = true;
  b.textContent = '⏳...';
  const cfg = {
    target: +document.getElementById('a_target').value || 100,
    workers: +document.getElementById('a_workers').value || 3,
    ghost: document.getElementById('a_ghost').value === 'yes',
    proxy: document.getElementById('a_proxy').value,
    prefix: document.getElementById('a_prefix').value || 'bronx'
  };
  const r = await fetch('/api/auto/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(cfg)});
  const d = await r.json();
  if(d.ok){
    AUTO_ON = true;
    b.style.display = 'none';
    s.style.display = 'block';
    pollAuto();
  } else {
    alert('❌ Already running');
    b.disabled = false;
    b.textContent = '🚀 START 24/7';
  }
}

async function stopAuto(){
  if(!confirm('Stop 24/7 Automation?')) return;
  await fetch('/api/auto/stop', {method: 'POST'});
  AUTO_ON = false;
  document.getElementById('a_start').style.display = 'block';
  document.getElementById('a_start').disabled = false;
  document.getElementById('a_start').textContent = '🚀 START 24/7';
  document.getElementById('a_stop').style.display = 'none';
}

async function pollAuto(){
  if(!AUTO_ON) return;
  try{
    const r = await fetch('/api/auto/status');
    const d = await r.json();
    document.getElementById('ac_cycle').textContent = d.state.cycle || 0;
    document.getElementById('ac_created').textContent = d.state.created || 0;
    document.getElementById('ac_fetched').textContent = d.state.fetched || 0;
    document.getElementById('ac_phase').textContent = d.state.phase || 'idle';
    const bd = document.getElementById('ab_badge');
    if(d.running){
      bd.textContent = 'RUNNING 24/7';
      bd.className = 'badge on';
    } else {
      bd.textContent = 'STOPPED';
      bd.className = 'badge off';
      AUTO_ON = false;
      document.getElementById('a_start').style.display = 'block';
      document.getElementById('a_start').disabled = false;
      document.getElementById('a_start').textContent = '🚀 START 24/7';
      document.getElementById('a_stop').style.display = 'none';
    }
    const pm = {checking:15, fetching:30, testing:50, creating:80, 'waiting-proxy':60, idle:95, stopped:0};
    document.getElementById('a_fill').style.width = (pm[d.state.phase] || 0) + '%';
  }catch(e){}
  if(AUTO_ON) setTimeout(pollAuto, 2000);
}

async function cStart(){
  const b = document.getElementById('c_btn');
  const s = document.getElementById('c_stop');
  b.disabled = true;
  s.style.display = 'block';
  clearLog();
  document.getElementById('c_suc').textContent = '0';
  document.getElementById('c_fail').textContent = '0';
  if(PM === 'local'){
    const lp = document.getElementById('lp_in').value.trim();
    await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({local_proxy: lp})});
  }
  const p = {
    count: +document.getElementById('c_count').value || 1,
    prefix: document.getElementById('c_prefix').value || 'bronx',
    workers: +document.getElementById('c_workers').value || 3,
    ghost_mode: GHOST,
    proxy_choice: PM
  };
  const res = await fetch('/api/create', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(p)});
  const rd = res.body.getReader();
  const dc = new TextDecoder();
  let buf = '';
  let suc = 0;
  let fai = 0;
  while(true){
    const {value, done} = await rd.read();
    if(done) break;
    buf += dc.decode(value, {stream: true});
    let i;
    while((i = buf.indexOf('\n')) >= 0){
      const line = buf.slice(0, i);
      buf = buf.slice(i+1);
      if(!line.trim()) continue;
      try{
        const m = JSON.parse(line);
        if(m.text) showLog(m.text);
        if(m.pct !== undefined) setBar('c_bar', m.pct);
        if(m.ok === true){suc++; document.getElementById('c_suc').textContent = suc;}
        if(m.ok === false){fai++; document.getElementById('c_fail').textContent = fai;}
      }catch(e){}
    }
  }
  b.disabled = false;
  s.style.display = 'none';
  refresh();
}

async function cStop(){await fetch('/api/create/stop', {method: 'POST'});}

function orderPayload(type){
  const link = document.getElementById('o_link').value.trim();
  if(!link.startsWith('https://t.me/')){alert('Invalid'); return null;}
  const p = {
    post_link: link,
    account_count: +document.getElementById('o_acc').value || 1,
    views_per_acc: +document.getElementById('o_vpa').value || 100,
    reacts_per_acc: +document.getElementById('o_rpa').value || 10,
    mode: document.getElementById('o_mode').value,
    order_type: type,
    workers: +document.getElementById('o_workers').value || 20
  };
  if(p.mode === 'single') p.email = document.getElementById('o_email').value.trim();
  return p;
}

async function oRun(type){
  const p = orderPayload(type);
  if(!p) return;
  const b = document.getElementById('o_btn');
  const s = document.getElementById('o_stop');
  b.disabled = true;
  s.style.display = 'block';
  clearLog();
  const res = await fetch('/api/orders', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(p)});
  const rd = res.body.getReader();
  const dc = new TextDecoder();
  let buf = '';
  while(true){
    const {value, done} = await rd.read();
    if(done) break;
    buf += dc.decode(value, {stream: true});
    let i;
    while((i = buf.indexOf('\n')) >= 0){
      const line = buf.slice(0, i);
      buf = buf.slice(i+1);
      if(!line.trim()) continue;
      try{
        const m = JSON.parse(line);
        if(m.text) showLog(m.text);
        if(m.pct !== undefined) setBar('o_bar', m.pct);
      }catch(e){}
    }
  }
  b.disabled = false;
  s.style.display = 'none';
  refresh();
}

async function oStop(){await fetch('/api/orders/stop', {method: 'POST'});}

async function pStart(){
  const b = document.getElementById('p_btn');
  const s = document.getElementById('p_stop');
  b.disabled = true;
  s.style.display = 'block';
  const p = {
    target: +document.getElementById('p_target').value || 5000,
    workers: +document.getElementById('p_workers').value || 30
  };
  await fetch('/api/pool/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(p)});
  refresh();
}

async function pStop(){await fetch('/api/pool/stop', {method: 'POST'}); refresh();}
async function pClear(){if(!confirm('Clear pool?')) return; await fetch('/api/pool/clear', {method: 'POST'}); refresh();}

function expAcc(f){window.location.href = '/api/acc/export?fmt=' + f;}
function showImp(){const b = document.getElementById('imp_box'); b.style.display = b.style.display === 'none' ? 'block' : 'none';}

async function doImp(){
  try{
    const data = JSON.parse(document.getElementById('imp_json').value);
    const r = await fetch('/api/acc/import', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({data: data})});
    const d = await r.json();
    alert('✅ Added ' + d.added);
    refresh();
  }catch(e){alert('❌ Invalid JSON');}
}

async function viewAcc(){
  const b = document.getElementById('acc_view');
  if(b.style.display === 'none'){
    const r = await fetch('/api/acc/list');
    const d = await r.json();
    let h = '';
    d.accounts.slice(0, 500).forEach(a => {
      let st = '✅';
      let cls = 'acc-ok';
      let info = 'ready';
      if(!a.available){st = '⏳'; cls = 'acc-cool'; info = a.hours_left.toFixed(1) + 'h';}
      else if(a.viewed_today || a.reacted_today){st = '⚡'; cls = 'acc-partial'; info = (a.viewed_today ? '👁️' : '') + (a.reacted_today ? '👍' : '');}
      h += '<div class="acc-item"><span class="' + cls + '">' + st + ' ' + a.email + '</span><span style="color:#64748b">' + info + '</span></div>';
    });
    b.innerHTML = h || '<div style="text-align:center;color:#64748b;padding:10px">Empty</div>';
    b.style.display = 'block';
  } else {
    b.style.display = 'none';
  }
}

async function clearAcc(){if(!confirm('Clear?')) return; await fetch('/api/acc/clear', {method: 'POST'}); refresh();}
async function resetMan(){if(!confirm('Reset?')) return; const r = await fetch('/api/acc/reset', {method: 'POST'}); const d = await r.json(); alert('Reset ' + d.count); refresh();}

async function saveSet(k, v){await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({[k]: v})});}

async function saveAll(){
  const s = {
    proxy_mode: document.getElementById('s_pm').value,
    local_proxy: document.getElementById('s_lp').value,
    create_workers: +document.getElementById('s_cw').value,
    order_workers: +document.getElementById('s_ow').value,
    max_per_ip: +document.getElementById('s_mx').value,
    proxy_max_ms: +document.getElementById('s_pms').value,
    cooldown_mode: document.getElementById('s_cm').value,
    auto_midnight_reset: document.getElementById('s_mid').value === 'yes',
    auto_cleanup_dead: document.getElementById('s_cl').value === 'yes',
    request_timeout: +document.getElementById('s_to').value,
    auto_fallback_direct: document.getElementById('s_af').value === 'yes'
  };
  await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(s)});
  alert('✅ Saved');
  refresh();
}

async function refresh(){
  try{
    const r = await fetch('/api/stats');
    const d = await r.json();
    document.getElementById('s_created').textContent = d.created;
    document.getElementById('s_orders').textContent = d.orders;
    document.getElementById('s_views').textContent = d.views;
    document.getElementById('s_reacts').textContent = d.reactions;
    document.getElementById('s_partial').textContent = d.partial;
    document.getElementById('s_failed').textContent = d.failed;
    document.getElementById('d_pw').textContent = d.pv_working;
    document.getElementById('d_pt').textContent = d.pv_total;
    document.getElementById('d_db').textContent = d.direct_blocked || 0;
    document.getElementById('d_auto').textContent = d.auto ? '▶️ Running 24/7' : '⏹️ Stopped';
    document.getElementById('d_nr').textContent = d.next_reset;
    document.getElementById('d_cap').textContent = d.captcha;
    document.getElementById('d_json').textContent = d.json_err;
    document.getElementById('d_scan').textContent = d.scanned;
    document.getElementById('d_tok').textContent = d.token_success;
    document.getElementById('d_tokf').textContent = d.token_fail;
    document.getElementById('d_pool').textContent = d.pool_total;
    document.getElementById('d_acc').textContent = d.acc_total;
    document.getElementById('d_rd').textContent = d.acc_ready;
    document.getElementById('d_cd').textContent = d.acc_cool;
    document.getElementById('d_time').textContent = d.ist;
    document.getElementById('pv_badge').textContent = d.pv_working + ' working';
    document.getElementById('pv_w').textContent = d.pv_working;
    document.getElementById('pv_d').textContent = d.pv_dead;
    document.getElementById('pv_f').textContent = d.pv_fast;
    document.getElementById('pv_m').textContent = d.pv_medium;
    document.getElementById('p_total').textContent = d.pool_total;
    document.getElementById('p_avail').textContent = d.pool_avail;
    document.getElementById('p_used').textContent = d.pool_used;
  }catch(e){}
}

setInterval(refresh, 2000);
refresh();
calc();
</script></body></html>'''

# ═════════════════════════════════════════════════════════════
#  ROUTES
# ═════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")

@app.route("/api/stats")
def api_stats():
    with VAULT_LOCK:
        total = len(VAULT["accounts"])
        ready = sum(1 for a in VAULT["accounts"] if is_ready(a))
        cool = total - ready
        partial = sum(1 for a in VAULT["accounts"]
                     if (a.get("viewed_today") and not a.get("reacted_today"))
                     or (a.get("reacted_today") and not a.get("viewed_today")))
    with PROXY_VAULT_LOCK:
        pw = sum(1 for p in PROXY_VAULT if p.get("status") == "working")
        pd = sum(1 for p in PROXY_VAULT if p.get("status") == "dead")
        pf = sum(1 for p in PROXY_VAULT if p.get("status") == "working" and (p.get("response_ms") or 9999) < 2000)
        pmm = sum(1 for p in PROXY_VAULT if p.get("status") == "working" and 2000 <= (p.get("response_ms") or 9999) < 5000)
        pt = len(PROXY_VAULT)
    ps = pool_stats()
    s = secs_midnight()
    with STATS_LOCK:
        snap = dict(STATS)
    return {
        **snap,
        "partial": partial,
        "pv_working": pw,
        "pv_dead": pd,
        "pv_fast": pf,
        "pv_medium": pmm,
        "pv_total": pt,
        "acc_total": total,
        "acc_ready": ready,
        "acc_cool": cool,
        "pool_total": ps["total"],
        "pool_used": ps["used"],
        "pool_avail": ps["avail"],
        "auto": AUTOMATION_RUNNING,
        "ist": now_ist().strftime("%I:%M:%S %p"),
        "next_reset": f"{int(s//3600)}h {int((s%3600)//60)}m",
    }

@app.route("/api/settings", methods=["POST"])
def api_settings():
    data = request.json or {}
    for k, v in data.items():
        if k in SETTINGS:
            SETTINGS[k] = v
    save_set()
    return {"ok": True}

# ── PROXY VAULT ──
@app.route("/api/pv/list")
def pv_list():
    with PROXY_VAULT_LOCK:
        return {"proxies": list(PROXY_VAULT)}

@app.route("/api/pv/remove", methods=["POST"])
def pv_rm():
    data = request.json or {}
    remove_proxy(data.get("proxy"))
    return {"ok": True}

@app.route("/api/pv/export")
def pv_export():
    fmt = request.args.get("fmt", "txt")
    with PROXY_VAULT_LOCK:
        allp = list(PROXY_VAULT)
        working = [p for p in allp if p.get("status") == "working"]
    if fmt == "json":
        return Response(json.dumps(allp, indent=2), mimetype="application/json",
                        headers={"Content-Disposition": "attachment;filename=proxy_vault.json"})
    body = "\n".join([p["proxy"] for p in working])
    return Response(body, mimetype="text/plain",
                    headers={"Content-Disposition": "attachment;filename=proxy_vault.txt"})

@app.route("/api/pv/scan", methods=["POST"])
def pv_scan():
    data = request.json or {}
    raw = data.get("proxies", [])
    def stream():
        def send(o):
            return (json.dumps(o) + "\n").encode()
        yield send({"text": f"📦 {len(raw)} raw", "pct": 0})
        yield send({"text": "🔍 Testing...", "pct": 5})
        res = scan_bulk_proxies(raw, max_test=2000)
        yield send({"text": f"✅ Clean {res['clean']}, Tested {res['tested']}", "pct": 80})
        yield send({"text": f"🎯 Working {res['working']}, Added {res['added']}", "pct": 95})
        yield send({"text": f"🏦 Vault: {len(PROXY_VAULT)}", "pct": 100})
    return Response(stream(), mimetype="application/x-ndjson")

@app.route("/api/pv/fetch", methods=["POST"])
def pv_fetch():
    def stream():
        def send(o):
            return (json.dumps(o) + "\n").encode()
        yield send({"text": "📡 Fetching...", "pct": 0})
        raw = fetch_sources()
        yield send({"text": f"📦 Raw: {len(raw)}", "pct": 20})
        yield send({"text": "🧪 Testing...", "pct": 30})
        res = scan_bulk_proxies(raw, max_test=500)
        yield send({"text": f"✅ Working {res['working']}, Added {res['added']}", "pct": 100})
    return Response(stream(), mimetype="application/x-ndjson")

@app.route("/api/pv/revalidate", methods=["POST"])
def pv_reval():
    def stream():
        def send(o):
            return (json.dumps(o) + "\n").encode()
        with PROXY_VAULT_LOCK:
            px = [p["proxy"] for p in PROXY_VAULT]
        if not px:
            yield send({"text": "❌ Empty", "pct": 100})
            return
        yield send({"text": f"🔬 Testing {len(px)}...", "pct": 0})
        res = test_proxies_batch(px, workers=SETTINGS.get("test_workers", 200))
        live = {r["proxy"]: r for r in res}
        with PROXY_VAULT_LOCK:
            for p in PROXY_VAULT:
                r = live.get(p["proxy"])
                if not r:
                    continue
                p["response_ms"] = r["ms"]
                if r["ok"]:
                    p["status"] = "working"
                    if r["ms"] < 2000: p["speed"] = "fast"
                    elif r["ms"] < 5000: p["speed"] = "medium"
                    else: p["speed"] = "slow"
                else:
                    p["status"] = "dead"
        save_pv()
        yield send({"text": "✅ Done", "pct": 100})
    return Response(stream(), mimetype="application/x-ndjson")

@app.route("/api/pv/clear-dead", methods=["POST"])
def pv_clear_dead():
    global PROXY_VAULT
    with PROXY_VAULT_LOCK:
        b = len(PROXY_VAULT)
        PROXY_VAULT = [p for p in PROXY_VAULT if p.get("status") == "working"]
        a = len(PROXY_VAULT)
    save_pv()
    return {"ok": True, "removed": b - a, "total": a}

@app.route("/api/pv/clear-all", methods=["POST"])
def pv_clear_all():
    global PROXY_VAULT
    with PROXY_VAULT_LOCK:
        PROXY_VAULT = []
    save_pv()
    return {"ok": True}

# ── AUTOMATION ──
@app.route("/api/auto/start", methods=["POST"])
def auto_start_api():
    data = request.json or {}
    cfg = {
        "target": max(1, min(5000, int(data.get("target", 100)))),
        "workers": max(1, min(10, int(data.get("workers", 3)))),
        "ghost": bool(data.get("ghost", True)),
        "proxy": data.get("proxy", "vault"),
        "prefix": (data.get("prefix") or "bronx")[:10],
    }
    ok = start_auto(cfg)
    return {"ok": ok}

@app.route("/api/auto/stop", methods=["POST"])
def auto_stop_api():
    stop_auto()
    return {"ok": True}

@app.route("/api/auto/status")
def auto_status_api():
    with AUTOMATION_LOCK:
        st = dict(AUTOMATION_STATE)
    return {"running": AUTOMATION_RUNNING, "state": st}

# ── CREATE ──
@app.route("/api/create", methods=["POST"])
def create_api():
    data = request.json or {}
    count = max(1, min(5000, int(data.get("count", 1))))
    prefix = (data.get("prefix") or "bronx")[:10]
    workers = max(1, min(50, int(data.get("workers", 3))))
    ghost = bool(data.get("ghost_mode", True))
    pmod = data.get("proxy_choice", "vault")
    STOP_CREATE.clear()

    def stream():
        def send(o):
            return (json.dumps(o) + "\n").encode()
        yield send({"text": f"⚡ {count} acc, {workers}W, Ghost:{ghost}, Proxy:{pmod}", "pct": 0})
        wp = len(get_working_proxies())
        yield send({"text": f"🏦 Vault working: {wp}"})
        if pmod in ("vault", "auto") and wp == 0:
            yield send({"text": "❌ Vault empty — proxy mandatory, please add proxies first!"})
            yield send({"text": "", "pct": 100})
            return
        ok = 0
        ng = 0
        done = 0
        lock = threading.Lock()
        def task(i):
            if STOP_CREATE.is_set():
                return None
            return create_account(prefix=prefix, ghost=ghost, proxy_mode=pmod)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(task, i): i for i in range(1, count + 1)}
            for f in as_completed(futs):
                done += 1
                if STOP_CREATE.is_set():
                    for x in futs:
                        x.cancel()
                    break
                try:
                    r = f.result()
                except Exception:
                    r = {"ok": False, "error": "ex"}
                if r is None:
                    continue
                with lock:
                    if r.get("ok"):
                        ok += 1
                        msg = f"✅ [{done}/{count}] {r['email']} via {r.get('proxy','?')}"
                    else:
                        ng += 1
                        msg = f"❌ [{done}/{count}] {r.get('error','?')}"
                yield send({"text": msg, "pct": int((done / count) * 100), "ok": r.get("ok", False)})
        yield send({"text": f"\n🎉 {ok}✅ {ng}❌", "pct": 100})
        save_av()
    return Response(stream(), mimetype="application/x-ndjson")

@app.route("/api/create/stop", methods=["POST"])
def create_stop_api():
    STOP_CREATE.set()
    return {"ok": True}

# ── ORDERS ──
@app.route("/api/orders", methods=["POST"])
def orders_api():
    data = request.json or {}
    link = (data.get("post_link") or "").strip()
    acc_count = int(data.get("account_count", 1))
    vpa = int(data.get("views_per_acc", 100))
    rpa = int(data.get("reacts_per_acc", 10))
    mode = data.get("mode", "auto")
    otype = data.get("order_type", "both")
    workers = max(1, min(50, int(data.get("workers", 20))))
    STOP_ORDER.clear()
    if not link.startswith("https://t.me/"):
        def err():
            yield (json.dumps({"text": "❌ Invalid"}) + "\n").encode()
        return Response(err(), mimetype="application/x-ndjson")

    def stream():
        def send(o):
            return (json.dumps(o) + "\n").encode()
        if mode == "single":
            em = data.get("email", "").strip()
            with VAULT_LOCK:
                ready = [a for a in VAULT["accounts"] if a.get("email") == em and is_ready(a)]
        elif mode == "all":
            ready = get_ready_accounts()
        else:
            ready = get_ready_accounts()[:acc_count]
        if not ready:
            yield send({"text": "❌ No ready"})
            return
        yield send({"text": f"⚡ {len(ready)} acc · {workers}W · {otype}", "pct": 0})
        tv = 0
        tr = 0
        ok = 0
        done = 0
        lock = threading.Lock()
        def task(acc):
            if STOP_ORDER.is_set():
                return None
            r = {"email": acc["email"], "view": None, "reaction": None}
            if otype in ("views", "both") and not acc.get("viewed_today"):
                r["view"] = place_order(acc, 192, link, vpa, False)
            if otype in ("reactions", "both") and not acc.get("reacted_today"):
                r["reaction"] = place_order(acc, 193, link, rpa, True)
            if r["view"] and r["view"].get("ok"):
                mark_used(acc["email"], is_view=True, qty=vpa)
            if r["reaction"] and r["reaction"].get("ok"):
                mark_used(acc["email"], is_react=True, qty=rpa)
            return r
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(task, acc): acc for acc in ready}
            for f in as_completed(futs):
                done += 1
                if STOP_ORDER.is_set():
                    for x in futs:
                        x.cancel()
                    break
                try:
                    r = f.result()
                except Exception:
                    r = None
                if not r:
                    continue
                v = r.get("view")
                rc = r.get("reaction")
                msg = f"[{done}/{len(ready)}] {r['email']} "
                with lock:
                    if v and v.get("ok"):
                        tv += vpa
                        msg += f"✅V#{v['order_id']} "
                    if rc and rc.get("ok"):
                        tr += rpa
                        msg += f"✅R#{rc['order_id']}"
                    if (v and v.get("ok")) or (rc and rc.get("ok")):
                        ok += 1
                yield send({"text": msg, "pct": int((done / len(ready)) * 100)})
        with STATS_LOCK:
            STATS["views"] += tv
            STATS["reactions"] += tr
            STATS["reused"] += ok
        yield send({"text": f"\n🎉 {ok}/{len(ready)} · {tv}V · {tr}R", "pct": 100})
        save_av()
    return Response(stream(), mimetype="application/x-ndjson")

@app.route("/api/orders/stop", methods=["POST"])
def orders_stop_api():
    STOP_ORDER.set()
    return {"ok": True}

# ── POOL ──
@app.route("/api/pool/start", methods=["POST"])
def pool_start():
    data = request.json or {}
    start_pool(target=int(data.get("target", 5000)), workers=int(data.get("workers", 30)))
    return {"ok": True}

@app.route("/api/pool/stop", methods=["POST"])
def pool_stop():
    STOP_POOL.set()
    return {"ok": True}

@app.route("/api/pool/clear", methods=["POST"])
def pool_clear():
    global SESSION_POOL
    with SESSION_POOL_LOCK:
        SESSION_POOL = []
    save_pool()
    return {"ok": True}

# ── ACCOUNTS ──
@app.route("/api/acc/export")
def acc_export():
    fmt = request.args.get("fmt", "json")
    with VAULT_LOCK:
        accs = list(VAULT["accounts"])
    if fmt == "txt":
        body = "\n".join(f"{a['email']} | {a['password']} | {a.get('api_token','')} | {a['proxy']}" for a in accs)
        return Response(body, mimetype="text/plain", headers={"Content-Disposition": "attachment;filename=accounts.txt"})
    if fmt == "csv":
        lines = ["email,password,api_token,proxy"]
        for a in accs:
            lines.append(f"{a['email']},{a['password']},{a.get('api_token','')},{a['proxy']}")
        return Response("\n".join(lines), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=accounts.csv"})
    return Response(json.dumps(VAULT, indent=2), mimetype="application/json",
                    headers={"Content-Disposition": "attachment;filename=accounts.json"})

@app.route("/api/acc/import", methods=["POST"])
def acc_import():
    data = request.json or {}
    inc = data.get("data") or {}
    added = 0
    with VAULT_LOCK:
        existing = {a["email"] for a in VAULT["accounts"]}
        for a in inc.get("accounts", []):
            if a.get("email") and a["email"] not in existing:
                a.setdefault("viewed_today", False)
                a.setdefault("reacted_today", False)
                a.setdefault("view_count_today", 0)
                a.setdefault("reaction_count_today", 0)
                a.setdefault("last_reset_date", today_ist())
                a.setdefault("cooldown_until", None)
                a.setdefault("added_at", time.time())
                VAULT["accounts"].append(a)
                added += 1
    save_av()
    return {"ok": True, "added": added}

@app.route("/api/acc/list")
def acc_list():
    with VAULT_LOCK:
        accs = []
        for a in VAULT["accounts"]:
            cd = a.get("cooldown_until")
            avail = is_ready(a)
            hrs = 0 if avail else max(0, (cd - time.time()) / 3600)
            accs.append({
                "email": a["email"], "available": avail, "hours_left": hrs,
                "viewed_today": a.get("viewed_today", False),
                "reacted_today": a.get("reacted_today", False),
            })
    return {"count": len(accs), "accounts": accs}

@app.route("/api/acc/clear", methods=["POST"])
def acc_clear():
    global VAULT
    with VAULT_LOCK:
        VAULT = {"accounts": [], "created_at": time.time(), "last_updated": time.time()}
    save_av()
    return {"ok": True}

@app.route("/api/acc/reset", methods=["POST"])
def acc_reset_api():
    c = reset_daily()
    return {"ok": True, "count": c}

# ═════════════════════════════════════════════════════════════
if __name__ == "__main__":
    load_all()
    start_midnight()
    print("\n" + "═" * 62)
    print("  🔥 BRONX ULTRA v23.0 — PROXY ENFORCED + 24/7 AUTO")
    print("  ✅ Accounts ONLY via Vault Working Proxies")
    print("  ✅ Automation runs 24/7 infinite")
    print("  🔗 http://localhost:5000")
    print("  🇮🇳 " + now_ist().strftime("%Y-%m-%d %I:%M %p IST"))
    print("═" * 62 + "\n")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
