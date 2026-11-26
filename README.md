# MCX Trade Pro v4.4

Android calendar spread monitoring and trading application built with Python, Kivy, Buildozer and Zerodha Kite Connect REST APIs. Designed primarily for MCX Natural Gas futures.

> **Experimental trading software:** Manual trading is the default. Automatic live trading is opt-in and can result in real financial losses. Android runtime and live execution validation are required before production use. This project is not affiliated with Zerodha or MCX.

## Features

- Live futures quotes via REST polling (approximately every two seconds, subject to network/API latency).
- Current–Next, Current–Third, Next–Third and custom expiry pairs.
- Calendar spread and daily price-change difference, signal chart and entry/exit alerts.
- Signal-only, paper, manual and automatic live trading modes.
- Independent Market or Protected Limit order selection for automated entry, exit and emergency square-off.
- Broker fill checks, partial-fill recovery, managed live P&L and recovery journal.
- Paper trade statistics and CSV export, broker positions and order history.
- Voice alerts, in-app popups and platform-dependent notifications.

## Repository layout

```text
MCX_Auto_Trade_Android/
├── main.py          # Rename MCX_Trade_Pro_v44_main.py to main.py
├── buildozer.spec   # Working Android build configuration
├── README.md
└── .gitignore
```

The application implements its own lightweight Kite REST client; the external `kiteconnect` Python package is not required.

## 1. Build prerequisites

Use Ubuntu 22.04 (native Linux or WSL2), preferably with the project under the Linux home directory rather than `/mnt/c`. Allow sufficient disk space for Android SDK, NDK and compilation caches. Internet access is needed for initial dependency downloads.

On Windows, install WSL2 if necessary from PowerShell:

```powershell
wsl --install -d Ubuntu-22.04
```

Inside Ubuntu:

```bash
sudo apt update
sudo apt install -y git zip unzip build-essential openjdk-17-jdk \
  autoconf automake libtool pkg-config cmake ccache patch \
  zlib1g-dev libffi-dev libssl-dev libncurses-dev \
  python3 python3-venv python3-pip adb
```

Use the existing working Python 3.14.2 / Buildozer environment if available:

```bash
source ~/buildozer-venv314/bin/activate
python --version
buildozer --version
```

For a new environment, first install CPython 3.14.2 using your preferred trusted Python installation method, then:

```bash
python3.14 -m venv ~/buildozer-venv314
source ~/buildozer-venv314/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install buildozer cython
```

Buildozer, Cython and python-for-android compatibility varies by revision. The fresh-install commands are a starting point, not a verified lockfile. Preserve the versions and python-for-android revision from a successful build; do not upgrade a working toolchain unnecessarily.

## 2. Get the source

```bash
# Replace the placeholder with your repository URL.
git clone <YOUR_REPOSITORY_URL> ~/MCX_Auto_Trade_Android
cd ~/MCX_Auto_Trade_Android
```

If setting up manually, copy `MCX_Trade_Pro_v44_main.py` into this directory as `main.py`. Keep your working `buildozer.spec`. For a new project without a spec, run `buildozer init` and edit the generated file.

## 3. Android build configuration

The following is the intended configuration for the Python 3.14.2 setup. Merge these keys into the existing `[app]` section rather than creating duplicate sections or keys. Preserve any other settings required by your successful build.

```ini
[app]
title = MCX Trade Pro
package.name = mcxsignals
package.domain = org.jainy
version = 4.4
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,json
source.exclude_dirs = .git,venv,.venv,bin,.buildozer,__pycache__,kiteconnect,vendor,p4a-recipes
source.exclude_patterns = zerodha_credentials.json*,live_spread_v43.json*,*.bak,*.log,*.db*,paper_trades.csv
requirements = python3==3.14.2,hostpython3==3.14.2,kivy,pyjnius,plyer,requests,six,certifi,urllib3,python-dateutil
orientation = portrait
fullscreen = 0
android.api = 36
android.minapi = 26
android.ndk = 28c
android.archs = arm64-v8a
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,POST_NOTIFICATIONS
android.accept_sdk_license = True
android.allow_backup = False

[buildozer]
log_level = 2
warn_on_root = 1
```

The older supplied spec uses Python 3.12.10 and version 3.3; the example above intentionally updates these to the intended 3.14.2/v4.4 configuration. The final successful spec and exact toolchain revisions were not supplied for verification. Python 3.14.2 requires a compatible python-for-android revision; retain the working revision (previous setup used the develop branch). Keep the same package identity and signing key when upgrading an installed APK. The example package ID is `org.jainy.mcxsignals`.

## 4. Build APK

```bash
cd ~/MCX_Auto_Trade_Android
source ~/buildozer-venv314/bin/activate
python -m py_compile main.py
set -o pipefail
buildozer -v android debug 2>&1 | tee build_v44.log
ls -lh bin/*.apk
```

The first build downloads and compiles dependencies. A debug APK is produced in `bin/`. Do not run Buildozer with sudo. For ordinary Python changes, rebuild without cleaning the entire SDK/NDK cache.

## 5. Install and run

Enable Developer options and USB debugging on the Android device. Connect it to the machine hosting ADB and authorize the connection.

```bash
adb devices
adb install -r "$(ls -t bin/*.apk | head -1)"
```

Under WSL2, USB may require USB/IP forwarding; alternatively copy the APK to Windows and use Windows platform-tools (`adb.exe`) or transfer the APK to the phone and install it manually. Allow installation from the chosen source only if you trust the APK. A debug APK is for testing, not a signed production release.

## 6. Zerodha login

1. Create/configure a Kite Connect application in the [developer portal](https://developers.kite.trade/), including a valid redirect URL and access to the required market-data and trading APIs.
2. Open MCX Trade Pro and enter the API key and API secret.
3. Tap **Open Zerodha Login** and authenticate in the browser.
4. Copy the final redirect URL containing `request_token`, or the token itself.
5. Tap **Paste Redirect URL**, then **Exchange Token / Connect**.
6. Verify the connection and profile. Use **Validate Saved Session** when reopening the app; repeat login when the access token expires.

The API secret is used for the token exchange. The API key and access token are stored in `zerodha_credentials.json` in app-private storage on Android (plain JSON, not encrypted). Never share credentials, tokens or redirect URLs. See [Kite Connect documentation](https://kite.trade/docs/connect/v3/) for broker requirements.

## 7. Use the application

### Select contracts and monitor

1. On Dashboard, select `NATURALGAS` and tap **Load contracts**.
2. Select Current - Next, Current - Third, Next - Third or Custom expiry.
3. For custom expiry, select two distinct contracts in chronological order.
4. Tap **Apply futures pair**. Configure thresholds, lot quantity and risk settings.
5. Select the trading mode and tap **Start monitoring**.
6. Use **Stop** before switching pairs. Resolve any open paper or managed live exposure first; verify unrelated manual positions directly with the broker.

### Signal calculation

```text
Absolute spread = LTP(A) - LTP(B)
Daily change difference = (LTP(A) - PreviousClose(A))
                        - (LTP(B) - PreviousClose(B))
Entry signal: daily change difference < entry threshold
Exit signal:  daily change difference > exit threshold
```

The entry spread direction is Buy A / Sell B; exit reverses those legs. The thresholds are applied to the daily change difference, not the absolute spread. For example, if A moves +2 and B moves +13, the signal value is -11. This is an illustrative calculation, not a trading recommendation.

### Defaults

| Setting | Default |
| --- | --- |
| Mode | Manual trading |
| Pair | Current - Next |
| Entry / exit thresholds | -10 / +10 INR |
| Combined profit target | INR 2,500 |
| Combined stop-loss | INR 1,500 |
| Daily loss limit | INR 1,500 |
| Lots per leg | 1 |
| Entry / exit / emergency order type | Protected Limit |
| Limit protection | 2 ticks (configurable 0–10) |
| Voice / popup alerts | Enabled |

Natural Gas entry signals are blocked before 12:37 PM IST, on weekends and on Thursdays (manual inventory review required). Absolute daily change difference must be at least INR 7. These are signal/automation filters, not restrictions on every manual order. The app does not automatically fetch the inventory release or implement a Thursday exception. Exchange holidays and session schedules must be checked separately.

### Trading modes

- **Signal only:** Monitor and receive alerts without automated order submission.
- **Paper trading:** Simulate spread trades and combined P&L; close manually or through configured thresholds. Export using **Export paper trades CSV**. Paper history is session-based.
- **Manual trading:** Use Orders and select the appropriate contract or individual leg BUY/SELL buttons; review confirmation before submission. Manual orders remain Market orders.
- **Automatic live:** Real-money trading. Select this mode, set entry/exit/emergency execution policies and protection ticks, then explicitly confirm **ARM automatic live trading** and start monitoring. Policy is captured on arming and recorded with the managed trade. Only enable after testing and verifying broker permissions, margins and positions.

### Automated execution and recovery

Automated orders use MCX NRML and IOC validity. Protected Limit derives the limit from executable market depth and instrument tick size, rejecting stale depth; Market has no explicit limit price. Broker order history is checked for terminal status and actual filled quantity. Legs are separate orders, not an atomic exchange spread.

Managed live positions are monitored for exit signal, combined profit target, stop-loss and daily loss limit. P&L is gross, excluding charges. Ambiguous submissions or mismatches can stop automation and require reconciliation. **Emergency square-off (managed pair)** and **Reconcile / recover managed pair** attempt to close/recover the managed exposure; verify orders and net quantities directly in Kite afterwards. **Emergency stop** blocks new orders; it is not a substitute for confirming positions are flat. Do not delete the recovery journal while exposure or unresolved orders exist.

## 8. Testing checklist

- [ ] Python syntax check and successful APK build.
- [ ] Fresh install and upgrade of existing installation.
- [ ] Login, token expiry and network failure handling.
- [ ] Contract selection for all four pair modes; reject invalid custom expiries.
- [ ] Verify quote values, previous closes, signal calculations and lot sizes against Kite.
- [ ] Verify time/gap filters, alerts, paper target/stop-loss and CSV export.
- [ ] Mock broker tests for rejection, partial fills, timeouts, stale quotes and duplicate prevention.
- [ ] Verify entry, exit and emergency execution policies separately for both order types.
- [ ] Verify recovery after app restart and reconciliation of unresolved orders.
- [ ] Verify broker positions and orders independently before any real-money use.

The checklist is a test plan, not a claim that these tests have passed.

## 9. Troubleshooting

| Problem | Action |
| --- | --- |
| `Failed to resolve api.kite.trade` | Confirm INTERNET permission in installed APK; test network/DNS on phone and try Wi-Fi/mobile data. |
| Token exchange fails | Check API key/secret and redirect configuration; generate a fresh request token. |
| API 36 missing | Install `platforms;android-36` using the SDK manager in the SDK selected by Buildozer. |
| `unable to find library -lpython3.14` | Check compatible p4a revision, matching host/target Python versions and NDK; rebuild stale distribution only after checking logs. |
| App crashes | Use Settings → View / Copy Crash Log, or `adb logcat`; redact sensitive data before sharing. |
| APK update fails | Check package identity, signing key, architecture and version; do not uninstall while recovery information is needed. |
| Notifications absent | Allow notifications in Android settings, verify TTS engine and runtime permission support. |
| Orders rejected / recovery required | Stop new entries, inspect actual Kite orders/positions and resolve exposure before rearming. |

```bash
adb shell dumpsys package org.jainy.mcxsignals | grep -A 20 'requested permissions'
adb logcat -d > android_log.txt
```

## 10. Git and credential hygiene

Create `.gitignore` with the following entries before staging files:

```gitignore
.buildozer/
bin/
venv/
.venv/
__pycache__/
*.py[cod]
*.apk
*.aab
*.log
*.bak
*.backup
*.db
*.db-*
zerodha_credentials.json*
live_spread_v43.json*
paper_trades.csv
android_log.txt
.env
.env.*
*.jks
*.keystore
```

Do not commit local data, API secrets, access tokens, recovery journals or signing keys. Git ignore rules do not remove previously committed secrets; revoke exposed credentials and clean history where necessary. Also check Buildozer source exclusions independently of Git.

```bash
git init
git add main.py buildozer.spec README.md .gitignore
git diff --cached
git commit -m "Add MCX Trade Pro v4.4 and build documentation"
git branch -M main
git remote add origin <YOUR_REPOSITORY_URL>
git push -u origin main
```

Use the existing remote instead of adding it again if the repository was cloned. Replace placeholders with your actual repository URL. Review the staged diff for credentials before pushing.

## Known limitations

- No WebSocket streaming or reliable Android background execution service.
- No guaranteed fills, atomic spread execution or guaranteed maximum loss. Recovery orders can fail; network loss, app suspension and broker restrictions can prevent exits.
- No dedicated margin precheck; confirm available margin and current broker/exchange/API eligibility, including applicable automated trading requirements.
- Paper fills and P&L do not model all execution costs; live P&L excludes charges. Persistent weekly/monthly performance reports are not implemented.
- Automatic execution settings do not change the manual order type.
- The source was inspected for this README; the APK and live trading paths were not tested for this documentation task. Keep manual mode as default and use paper/mocked execution tests before live use.

## License

No project license was supplied. Add a license only after confirming ownership and the intended distribution terms. Third-party dependencies retain their respective licenses.
