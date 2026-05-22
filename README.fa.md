# استریکس (Astrix)

**عبور از سانسور با تونل TCP روی Google Apps Script**

[![Python](https://img.shields.io/badge/python-3.14%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-orange)](VERSION)

استریکس یک پورت Python پرسرعت از [GooseRelayVPN](https://github.com/kianmhz/GooseRelayVPN) است — ابزاری که ترافیک TCP را از طریق Google Apps Script تونل می‌کند تا از سانسور اینترنت عبور کند. از تکنیک domain fronting، رمزنگاری AES-256-GCM و فشرده‌سازی Zstandard برای ایجاد یک تونل مبهم استفاده می‌کند که در نگاه ترافیک عادی HTTPS به Google به نظر می‌رسد.

```
مرورگر ──► SOCKS5 :1080 ──► astrix-client
  ──► Zstd + AES-256-GCM batch
  ──► HTTPS به Google edge (SNI=www.google.com, Host=script.google.com)
  ──► Apps Script doPost() — لوله ناشنوا، کلید را نمی‌بیند
  ──► astrix-server :8443 — رمزگشایی، demux, dials target
  ◄── مسیر برگشت بصورت long-poll
```

---

## قابلیت‌ها

- **Domain fronting** — ترافیک بصورت HTTPS عادی Google نمایش داده می‌شود
- **AES-256-GCM AEAD** — رمزنگاری نظامی، احراز هویت با تگ (بدون پسورد)
- **Zstandard فشرده‌سازی** — تطبیقی، داده‌های غیرقابل فشرده را خودکار رد می‌کند
- **پروکسی SOCKS5** — پروتکل استاندارد، پشتیبانی از احراز هویت RFC 1929
- **Failover چند-اندارد** — پخش بار بین چند حساب Google
- **اتصال مجدد خودکار** — backoff نمایی (۳ ثانیه → ۱ ساعت)، مانیتورینگ سلامت
- **اندازه تطبیقی batch** — ۱ تا ۶۴ فریم در هر درخواست HTTP بر اساس RTT زنده
- **I/O رویداد-محور** — صفر مصرف CPU در حالت بیکاری
- **Rich TUI** — شل تعاملی با ویزارد نصب، دیاگنوستیک، آمار زنده
- **یکپارچگی با systemd** — فایل PID، خاموشی ملایم، گردش لاگ
- **پیکربندی با متغیر محیط** — تمام تنظیمات قابل override با env var

---

## شروع سریع

### نصب

```bash
# نصب یک-دستوری روی VPS (root):
sudo bash deploy/install.sh client     # کلاینت پروکسی SOCKS5
sudo bash deploy/install.sh server     # سرور خروجی VPS
sudo bash deploy/install.sh all        # هر دو

# یا نصب با pip:
pip install -e astrix-client
pip install -e astrix-server
```

### پیکربندی

```bash
# ساخت کانفیگ تعاملی (بدون آرگومان = TUI):
astrix-client

# یا ویرایش مستقیم JSON:
nano client_config.json
nano server_config.json
```

**حداقل `client_config.json`:**
```json
{
    "socks_host": "127.0.0.1",
    "socks_port": 1080,
    "google_host": "216.239.38.120",
    "sni": ["www.google.com"],
    "script_keys": ["AKfycb..."],
    "tunnel_key": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

**حداقل `server_config.json`:**
```json
{
    "server_host": "0.0.0.0",
    "server_port": 8443,
    "tunnel_key": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

> **`tunnel_key` باید در کلاینت و سرور یکسان باشد.** تولید با:
> ```bash
> openssl rand -hex 32
> ```

### اجرا

```bash
# شل تعاملی:
astrix-client
astrix-server

# بدون TUI (headless):
astrix-client --config /etc/astrix/client.json --start
astrix-server --config /etc/astrix/server.json --start

# حالت daemon (سیستمد):
astrix-client --config /etc/astrix/client.json --daemon --log-file /var/log/astrix/client.log
astrix-server --config /etc/astrix/server.json --daemon --log-file /var/log/astrix/server.log

# حالت بنچمارک:
astrix-client --config /etc/astrix/client.json --benchmark
astrix-server --config /etc/astrix/server.json --benchmark

# با systemd:
systemctl start astrix-client
systemctl start astrix-server
journalctl -u astrix-client -f
```

---

## معماری

### اجزا

| کامپوننت | نقش |
|---|---|
| **astrix-client** | روی دستگاه شما اجرا می‌شود. پروکسی SOCKS5 + carrier با domain fronting |
| **astrix-server** | روی VPS اجرا می‌شود. هندلر تونل HTTP + dialer بالادست |
| **Apps Script** | وب‌اپ Google Apps Script. لوله ناشنوا — کلید رمز را نمی‌بیند |

### پروتکل وایر

```
Frame:   session_id(16) || seq(u64 BE) || flags(u8) || target_len(u8)
         || target || payload_len(u32 BE) || payload
Flags:   SYN=1, FIN=2, ACK=4, RST=8

Batch:   base64( nonce(12) || AES-256-GCM( flags_byte || client_id(16)
         || u16_frame_count || [u32_len || frame_bytes]… ) )

فشرده‌سازی: Zstd level 1، رد شدن اگر <512 بایت یا غیرقابل فشرده
```

سازگار بایت-به-بایت با پیاده‌سازی Go اصلی.

### ویژگی‌های عملکرد

| بهینه‌سازی | توضیح |
|---|---|
| marshal بدون کپی | `marshal_into()` مستقیم در بافر batch می‌نویسد — بدون بایت میانی |
| unmarshal درجا | `decode_batch()` فریم‌ها را مستقیماً از plaintext می‌خواند — بدون تابع‌کال اضافه |
| استخر بافر | استخرهای thread-safe برای marshal buffers، Zstd compressor/decompressor |
| استخر اتصال | `aiohttp.ClientSession` پایدار به ازای هر SNI با keep-alive |
| قفل ر stained | ۶۴ قفل async برای کمترین contention |
| بیدارباش رویداد-محور | `asyncio.Event` جایگزین sleep — صفر CPU در بیکاری |
| فشرده‌سازی تطبیقی | رد Zstd بعد ۳ batch فشرده نشده، بررسی مجدد هر ۱۰ |
| اندازه تطبیقی batch | ۱–۶۴ فریم در هر درخواست بر اساس RTT مشاهده شده |
| امتیازدهی کیفیت اندپوینت | انتخاب خودکار بهترین اندپوینت با median RTT |
| TCP_NODELAY/QUICKACK | اعمال روی همه سوکت‌ها |
| حالت پایپ‌لاین | drain بعدی همزمان با انتظار HTTP response شروع می‌شود |
| تایمر watchdog | اتصال مجدد اجباری بعد ۱۲۰ ثانیه بیکاری با session فعال |

---

## پیکربندی

### کانفیگ فایلی

**فیلدهای کلاینت** (`client_config.json`):

| فیلد | پیش‌فرض | توضیح |
|---|---|---|
| `socks_host` | `127.0.0.1` | آدرس گوش دادن SOCKS5 |
| `socks_port` | `1080` | پورت گوش دادن SOCKS5 |
| `socks_user` | `""` | نام کاربری احراز هویت SOCKS5 |
| `socks_pass` | `""` | رمز عبور احراز هویت SOCKS5 |
| `google_host` | `216.239.38.120` | IP اج Google برای domain fronting |
| `sni` | `["www.google.com"]` | نام‌های SNI (یکی به ازای هر bucket محدودیت) |
| `script_keys` | `[]` | IDهای استقرار Apps Script |
| `tunnel_key` | — | کلید ۶۴ کاراکتری hex (اشتراکی با سرور) |
| `coalesce_step_ms` | `25` | پنجره coalescing فریم |
| `idle_slots_per_bucket` | `2` | poll همزمان بیکار (۰–۳) |
| `debug_timing` | `false` | فعال‌سازی لاگ زمان‌بندی |

**فیلدهای سرور** (`server_config.json`):

| فیلد | پیش‌فرض | توضیح |
|---|---|---|
| `server_host` | `0.0.0.0` | آدرس گوش دادن HTTP |
| `server_port` | `8443` | پورت گوش دادن HTTP |
| `tunnel_key` | — | کلید ۶۴ کاراکتری hex (اشتراکی با کلاینت) |
| `upstream_proxy` | `""` | پروکسی SOCKS5 بالادست (اختیاری) |
| `debug_timing` | `false` | فعال‌سازی لاگ زمان‌بندی |

### متغیرهای محیطی

همه فیلدها با متغیرهای `ASTRIX_*` قابل override هستند (اولویت با env var):

```bash
# کلاینت:
ASTRIX_TUNNEL_KEY=abc... ASTRIX_SOCKS_PORT=9090 astrix-client --start

# سرور:
ASTRIX_TUNNEL_KEY=abc... ASTRIX_SERVER_PORT=443 astrix-server --start
```

لیست کامل: `astrix-client --help`

---

## استقرار روی VPS

### systemd (توصیه شده)

```bash
sudo bash deploy/install.sh client --auto-config
sudo bash deploy/install.sh server --auto-config
```

این دستور وابستگی‌های Python را نصب می‌کند، کانفیگ با کلید تصادفی می‌سازد، و سرویس systemd را فعال می‌کند.

**دستی:**
```bash
sudo cp deploy/astrix-client.service /etc/systemd/system/
sudo cp deploy/astrix-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now astrix-client
sudo systemctl enable --now astrix-server
```

### Docker

```bash
# کلاینت
docker build -t astrix-client -f deploy/Dockerfile.client .
docker run -v /etc/astrix:/etc/astrix astrix-client

# سرور
docker build -t astrix-server -f deploy/Dockerfile.server .
docker run -p 8443:8443 -v /etc/astrix:/etc/astrix astrix-server
```

---

## توسعه

### پیش‌نیازها

- Python **3.14+**
- `pip install -e astrix-client`
- `pip install -e astrix-server`

### تست

```bash
pip install -e astrix-client
python -m astrix_client  # شل تعاملی
```

### افزایش نسخه

```bash
# نسخه فعلی: 0.1.0
./scripts/bump_version.sh patch    # → 0.1.1
./scripts/bump_version.sh minor    # → 0.2.0
./scripts/bump_version.sh major    # → 1.0.0
./scripts/bump_version.sh 0.5.0    # → 0.5.0 (مستقیم)
```

---

## ساختار پروژه

```
.
├── astrix-client/           → کلاینت Python SOCKS5
│   ├── astrix_client/
│   │   ├── carrier/         → Long-poll + domain fronting
│   │   ├── cli/             → شل تعاملی Rich TUI
│   │   ├── config/          → بارگذاری JSON + env var
│   │   ├── crypto/          → AES-256-GCM + Zstd
│   │   ├── frame/           → مارشال/آنمارشال فرمت وایر
│   │   ├── session/         → مدیریت وضعیت session
│   │   ├── socks/           → سرور پروکسی SOCKS5
│   │   ├── daemon.py        → wrapper systemd daemon
│   │   └── __main__.py      → نقطه ورود
│   └── pyproject.toml
├── astrix-server/           → سرور خروجی Python VPS
│   ├── astrix_server/
│   │   ├── cli/             → شل تعاملی Rich TUI
│   │   ├── config/          → بارگذاری JSON + env var
│   │   ├── crypto/          → AES-256-GCM + Zstd (کپی مشترک)
│   │   ├── exit/            → هندلر HTTP + بالادست
│   │   ├── frame/           → فرمت وایر (کپی مشترک)
│   │   ├── session/         → وضعیت session (کپی مشترک)
│   │   ├── daemon.py        → wrapper systemd daemon
│   │   └── __main__.py      → نقطه ورود
│   └── pyproject.toml
├── apps-script/Code.gs      → فورواردر Google Apps Script
├── deploy/
│   ├── astrix-client.service → یونیت systemd برای کلاینت
│   ├── astrix-server.service → یونیت systemd برای سرور
│   └── install.sh           → نصب‌کننده یک-دستوری VPS
├── scripts/
│   └── bump_version.sh      → افزایش دهنده نسخه
├── VERSION                  → منبع واحد نسخه
├── AGENTS.md                → دستورالعمل‌های AI agent
├── README.md                → مستندات انگلیسی
└── README.fa.md             → مستندات فارسی
```

---

## مجوز

این پروژه یک fork از [GooseRelayVPN](https://github.com/kianmhz/GooseRelayVPN) توسط Kianmhz است.

**نویسنده:** UNDEAD ([github.com/itsund3ad](https://github.com/itsund3ad))

مجوز MIT — برای جزئیات [LICENSE](LICENSE) را ببینید.
