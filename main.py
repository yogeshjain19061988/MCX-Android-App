# ============================================================
# MCX Trading App - Kivy / Android version
# Run on desktop: python main.py
# Build APK:      buildozer -v android debug
# ============================================================
import os
import json
import time
import threading
import sqlite3
import webbrowser
import re
import traceback
import sys
import importlib.util
import logging
from datetime import datetime as dt, timedelta, date, timezone
from urllib.parse import urlparse, parse_qs

from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.spinner import Spinner
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.uix.floatlayout import FloatLayout
from kivy.animation import Animation
from kivy.metrics import dp
from kivy.utils import platform
from kivy.core.clipboard import Clipboard
from kivy.properties import StringProperty, NumericProperty, BooleanProperty
from kivy.graphics import Color, RoundedRectangle, Line
from kivy.uix.widget import Widget
from kivy.uix.switch import Switch
import math
from kivy.core.window import Window
from kivy.base import ExceptionHandler, ExceptionManager


# Lightweight Kite Connect REST client: avoids Twisted/Autobahn on Android.
# Uses the official Kite Connect HTTP endpoints; live quotes are polled, not streamed.
import csv
import hashlib
import io
from urllib.parse import urlencode
import requests

KITE_IMPORT_ERROR = ""
KITE_IMPORT_TRACEBACK = ""
KITE_IMPORT_LOCATION = "Built-in REST client (no kiteconnect SDK)"

class KiteConnect:
    BASE_URL = "https://api.kite.trade"

    def __init__(self, api_key):
        self.api_key = api_key
        self.access_token = ""
        self.http = requests.Session()
        self.http.headers.update({"X-Kite-Version": "3", "User-Agent": "MCXSignalsAndroid/2.8"})

    def login_url(self):
        return "https://kite.zerodha.com/connect/login?" + urlencode({"v": "3", "api_key": self.api_key})

    def set_access_token(self, token):
        self.access_token = token

    def _request(self, method, path, *, params=None, data=None, auth=True, csv_result=False):
        headers = {}
        if auth:
            if not self.access_token:
                raise RuntimeError("No Kite access token. Please log in.")
            headers["Authorization"] = f"token {self.api_key}:{self.access_token}"
        try:
            response = self.http.request(method, self.BASE_URL + path,
                                         params=params, data=data, headers=headers, timeout=25)
            response.raise_for_status()
            if csv_result:
                return list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
            payload = response.json()
        except requests.HTTPError as exc:
            try:
                message = response.json().get("message", str(exc))
            except (ValueError, AttributeError):
                message = str(exc)
            raise RuntimeError(f"Kite API HTTP {response.status_code}: {message}") from exc
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(f"Kite API request failed: {exc}") from exc
        if payload.get("status") != "success":
            raise RuntimeError(payload.get("message", "Kite API returned an error"))
        return payload.get("data")

    def generate_session(self, request_token, api_secret):
        checksum = hashlib.sha256((self.api_key + request_token + api_secret).encode("utf-8")).hexdigest()
        return self._request("POST", "/session/token", auth=False,
                             data={"api_key": self.api_key,
                                   "request_token": request_token,
                                   "checksum": checksum})

    def profile(self):
        return self._request("GET", "/user/profile")

    def instruments(self, exchange="MCX"):
        return self._request("GET", "/instruments/" + exchange, csv_result=True)

    def quote(self, instruments):
        return self._request("GET", "/quote", params=[("i", i) for i in instruments])

    def positions(self):
        return self._request("GET", "/portfolio/positions")

    def orders(self):
        return self._request("GET", "/orders")

    def place_order(self, *, variety, exchange, tradingsymbol, transaction_type,
                    quantity, order_type, product, validity):
        data = {"exchange": exchange, "tradingsymbol": tradingsymbol,
                "transaction_type": transaction_type, "quantity": quantity,
                "order_type": order_type, "product": product, "validity": validity}
        result = self._request("POST", "/orders/" + variety, data=data)
        return result["order_id"]

# ---- Professional dark UI and persistent crash reporting ----
BG = (0.045, 0.065, 0.11, 1)
CARD = (0.085, 0.12, 0.19, 1)
CARD2 = (0.12, 0.17, 0.25, 1)
FG = (0.94, 0.97, 1, 1)
MUTED = (0.61, 0.69, 0.78, 1)
ACCENT = (0.20, 0.65, 0.98, 1)
GREEN = (0.15, 0.78, 0.54, 1)
RED = (0.98, 0.36, 0.42, 1)

class Surface(BoxLayout):
    """Rounded card with a stable, canvas-before background."""
    def __init__(self, fill=CARD, radius=16, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            self._bg_color = Color(*fill)
            self._bg_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radius)])
        self.bind(pos=self._sync_bg, size=self._sync_bg)

    def _sync_bg(self, *_):
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size


class NavigationShell(FloatLayout):
    """Overlay drawer with edge swipe navigation; scroll gestures remain usable."""
    def __init__(self, app, manager, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.manager = manager
        self.drawer_open = False
        self.drawer_width = dp(278)
        self._touch_origin = None
        self.add_widget(manager)
        self.shade = Button(background_normal='', background_color=(0, 0, 0, .55),
                            opacity=0, disabled=True, size_hint=(1, 1))
        self.shade.bind(on_release=lambda *_: self.close_drawer())
        self.drawer = Surface(orientation='vertical', padding=[dp(18), dp(35), dp(18), dp(20)],
                              spacing=dp(10), fill=(.075, .105, .17, 1),
                              size_hint=(None, 1), width=self.drawer_width)
        self.drawer.bind(width=self._position_drawer)
        self.bind(size=self._position_drawer)
        self._build_drawer()
        Clock.schedule_once(lambda *_: self._position_drawer(), 0)

    def _position_drawer(self, *_):
        if not self.drawer_open:
            self.drawer.x = -self.drawer_width
        self.drawer.y = self.y

    def _build_drawer(self):
        self.drawer.add_widget(txt('MCX  /  TRADE PRO', 21, FG, True, 56))
        self.drawer.add_widget(txt('TRADING WORKSPACE', 11, MUTED, True, 34))
        for label, screen in [('⌂   Dashboard', 'dashboard'),
                              ('↗   Trade ticket', 'orders'),
                              ('⚙   Account & settings', 'settings')]:
            self.drawer.add_widget(action(label, lambda target=screen: self.navigate(target), CARD2, 54))
        self.drawer.add_widget(BoxLayout())
        self.drawer.add_widget(txt('Zerodha Kite Connect  •  MCX', 11, MUTED, height=32))
        self.drawer.add_widget(action('Sign out', self.app._logout, RED, 48))

    def open_drawer(self):
        if not self.app.is_logged_in or self.manager.current == "login" or self.drawer_open:
            return
        self.drawer_open = True
        self.drawer.x = -self.drawer_width
        self.add_widget(self.shade)
        self.add_widget(self.drawer)
        self.shade.disabled = False
        self.shade.opacity = 1
        Animation.cancel_all(self.drawer, 'x')
        Animation(x=0, duration=.22, t='out_cubic').start(self.drawer)

    def close_drawer(self):
        self.drawer_open = False
        self.shade.disabled = True
        self.shade.opacity = 0
        Animation.cancel_all(self.drawer, 'x')
        self.drawer.x = -self.drawer_width
        if self.drawer.parent is self:
            self.remove_widget(self.drawer)
        if self.shade.parent is self:
            self.remove_widget(self.shade)

    def toggle_drawer(self):
        self.close_drawer() if self.drawer_open else self.open_drawer()

    def navigate(self, screen):
        names = ['dashboard', 'orders', 'settings']
        if screen in names and self.manager.current in names:
            direction = 'left' if names.index(screen) > names.index(self.manager.current) else 'right'
        else:
            direction = 'left'
        self.manager.transition = SlideTransition(direction=direction, duration=.24)
        self.manager.current = screen
        self.close_drawer()

    def on_touch_down(self, touch):
        if self.manager.current == "login":
            self._touch_origin = None
            return super().on_touch_down(touch)
        self._touch_origin = (touch.x, touch.y)
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        if self.manager.current == "login":
            self._touch_origin = None
            return super().on_touch_up(touch)
        origin = self._touch_origin
        self._touch_origin = None
        if origin and abs(touch.y - origin[1]) < dp(85):
            delta = touch.x - origin[0]
            if not self.drawer_open and origin[0] < dp(28) and delta > dp(75):
                self.open_drawer()
                return True
            if self.drawer_open and delta < -dp(75):
                self.close_drawer()
                return True
        return super().on_touch_up(touch)


def txt(value, size=14, color=FG, bold=False, height=32):
    w = Label(text=value, font_size=dp(size), color=color, bold=bold,
              halign='left', valign='middle', size_hint_y=None, height=dp(height))
    w.bind(size=lambda obj, *_: setattr(obj, 'text_size', (obj.width, None)))
    return w

def action(value, callback, color=CARD2, height=48):
    return Button(text=value, size_hint_y=None, height=dp(height), bold=True,
                  font_size=dp(13), background_normal='', background_color=color,
                  color=FG, on_release=lambda *_: callback())

def field(hint, value='', password=False):
    return TextInput(text=value, hint_text=hint, multiline=False, password=password,
                     size_hint_y=None, height=dp(48), font_size=dp(15),
                     foreground_color=FG, hint_text_color=MUTED,
                     background_normal='', background_active='',
                     background_color=CARD2, padding=[dp(12), dp(12)])

def stack_scroll():
    scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
    root = BoxLayout(orientation='vertical', spacing=dp(12), padding=dp(14), size_hint_y=None)
    root.bind(minimum_height=root.setter('height'))
    scroll.add_widget(root)
    return scroll, root

def crash_log_path():
    try:
        app = App.get_running_app()
        base = app.user_data_dir if app else os.path.dirname(os.path.abspath(__file__))
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, 'mcx_crash.log')
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mcx_crash.log')

def record_crash(exc_type, exc_value, exc_tb):
    detail = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        with open(crash_log_path(), 'a', encoding='utf-8') as out:
            out.write('\n' + dt.now().isoformat() + '\n' + detail)
    except Exception:
        pass
    print('MCX_CRASH:', detail, flush=True)

class MCXExceptionHandler(ExceptionHandler):
    def handle_exception(self, inst):
        try:
            record_crash(type(inst), inst, inst.__traceback__)
        except Exception:
            pass
        return ExceptionManager.RAISE

ExceptionManager.add_handler(MCXExceptionHandler())
sys.excepthook = record_crash
if hasattr(threading, 'excepthook'):
    def _thread_crash(args):
        record_crash(args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = _thread_crash

# ---------- Android-safe TTS + notifications ----------
try:
    from plyer import tts, notification
    HAS_PLYER = True
except ImportError:
    HAS_PLYER = False

# ---------- Paths ----------
def app_data_dir():
    if platform == "android":
        from kivy.app import App
        app = App.get_running_app()
        if app:
            return app.user_data_dir
        return os.path.join(os.path.expanduser("~"), ".mcxsignals")
    return os.path.dirname(os.path.abspath(__file__))

# Resolved in build(), after Kivy has initialized the Android Activity.
DATA_DIR = None
DB_PATH = None
CRED_PATH = None

# ---------- Speech queue (Android-safe) ----------
_speech_queue = []
_speech_lock = threading.Lock()
_last_spoken = {}
SPEECH_COOLDOWN = 30


def speak(text):
    """Queue a TTS message with cooldown to avoid spamming."""
    now = time.time()
    last = _last_spoken.get(text, 0)
    if now - last < SPEECH_COOLDOWN:
        return
    _last_spoken[text] = now
    if not HAS_PLYER:
        return
    try:
        tts.speak(text)
    except Exception as e:
        print(f"TTS error: {e}")


def notify(title, message):
    if HAS_PLYER:
        try:
            notification.notify(title=title, message=message, timeout=10)
        except Exception as e:
            print(f"Notification error: {e}")


# ---------- Database ----------
def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS price_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, price_diff REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS order_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, contract TEXT, txn TEXT, qty INTEGER,
        price TEXT, status TEXT, order_id TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS prev_close (
        date TEXT, contract TEXT, close REAL,
        PRIMARY KEY(date, contract))""")
    conn.commit()
    conn.close()


def log_price(value):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO price_log(ts, price_diff) VALUES(?,?)",
                 (dt.now().isoformat(), float(value)))
    conn.commit()
    conn.close()


def log_order(rec):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""INSERT INTO order_history
        (ts,contract,txn,qty,price,status,order_id) VALUES(?,?,?,?,?,?,?)""",
        (rec["time"], rec["contract"], rec["type"], rec["quantity"],
         str(rec["price"]), rec["status"], rec.get("order_id", "")))
    conn.commit()
    conn.close()


def save_prev_close(contract, price):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO prev_close VALUES(?,?,?)",
                 (date.today().isoformat(), contract, float(price)))
    conn.commit()
    conn.close()


def load_prev_closes():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT contract, close FROM prev_close WHERE date=?",
                        (date.today().isoformat(),)).fetchall()
    conn.close()
    return {c: p for c, p in rows}


# ---------- Credentials ----------
def load_creds():
    try:
        with open(CRED_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_creds(api_key, token):
    """Save the API key and current access token in app-private storage."""
    os.makedirs(DATA_DIR, exist_ok=True)
    temp_path = CRED_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump({
            "api_key": api_key,
            "access_token": token,
            "saved_at": dt.now().isoformat(timespec="seconds"),
        }, f)
    os.replace(temp_path, CRED_PATH)
    try:
        os.chmod(CRED_PATH, 0o600)
    except OSError:
        pass


def clear_saved_token(api_key=""):
    """Keep the API key but discard an expired/invalid access token."""
    save_creds(api_key, "")


def extract_request_token(value):
    """Extract a real Kite request_token; never treat a login URL as a token."""
    value = (value or "").strip()
    if not value:
        return ""
    if "://" in value or "?" in value or "&" in value or "=" in value:
        parsed = urlparse(value)
        params = parse_qs(parsed.query, keep_blank_values=True)
        if not params.get("request_token") and parsed.fragment:
            params = parse_qs(parsed.fragment.lstrip("?"), keep_blank_values=True)
        tokens = params.get("request_token", [])
        return tokens[0].strip() if tokens else ""
    # Kite tokens are alphanumeric; reject arbitrary pasted text.
    return value if re.fullmatch(r"[A-Za-z0-9]{10,256}", value) else ""


# ============================================================
#  MAIN APP
# ============================================================
class TradingApp(App):
    status_text = StringProperty("Disconnected")
    price_diff_text = StringProperty("--")
    signal_text = StringProperty("WAIT")
    login_status_text = StringProperty("Not Logged In")
    pnl_text = StringProperty("P&L: Rs 0.00")

    def build(self):
        try:
            return self._build_application()
        except Exception as exc:
            record_crash(type(exc), exc, exc.__traceback__)
            detail = traceback.format_exc()
            Window.clearcolor = (0.06, 0.08, 0.13, 1)
            root = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(12))
            root.add_widget(Label(text='MCX TRADE PRO - STARTUP ERROR', color=(1, 0.35, 0.35, 1),
                                  size_hint_y=None, height=dp(55), font_size=dp(18), bold=True))
            root.add_widget(Label(text='Copy the error below and send it for repair.',
                                  color=(1, 1, 1, 1), size_hint_y=None, height=dp(40)))
            root.add_widget(TextInput(text=detail, readonly=True, multiline=True,
                                      foreground_color=(1, 1, 1, 1), background_color=(0.13, 0.16, 0.23, 1)))
            root.add_widget(Button(text='COPY ERROR', size_hint_y=None, height=dp(54),
                                   on_release=lambda *_: Clipboard.copy(detail)))
            return root

    def _build_application(self):
        global DATA_DIR, DB_PATH, CRED_PATH
        DATA_DIR = self.user_data_dir
        os.makedirs(DATA_DIR, exist_ok=True)
        DB_PATH = os.path.join(DATA_DIR, "trading.db")
        CRED_PATH = os.path.join(DATA_DIR, "zerodha_credentials.json")
        try:
            init_db()
        except Exception as exc:
            print("MCX database initialization error:", repr(exc))
        self.kite = None
        self.is_logged_in = False
        self.api_key = ""
        self.access_token = ""
        self.instruments_df = None
        self.available_futures = []
        self.current_month = None
        self.next_month = None
        self.next_next_month = None
        try:
            self.prev_closes = load_prev_closes()
        except Exception as exc:
            record_crash(type(exc), exc, exc.__traceback__)
            self.prev_closes = {}
        self.last_quotes = {}

        self.monitoring = False
        self._auth_busy = False
        self.entry_threshold = -10.0
        self.exit_threshold = 10.0
        self.trigger_cooldown = 600
        self.last_trigger = None
        self.order_history = []

        creds = load_creds()
        self.api_key = creds.get("api_key", "")
        self.access_token = creds.get("access_token", "")

        sm = ScreenManager()
        # Initialize navigation before constructing screens: page headers
        # register callbacks that refer to self.shell.
        self.sm = sm
        self.shell = NavigationShell(self, sm)
        sm.add_widget(self._login_screen())
        sm.add_widget(self._dashboard_screen())
        sm.add_widget(self._settings_screen())
        sm.add_widget(self._orders_screen())
        sm.current = 'login'

        if self.api_key and self.access_token:
            Clock.schedule_once(lambda *_: self.auto_login(), 2)
        return self.shell

    def _on_api_key_edited(self, widget, value):
        """Debounced persistence; changing key invalidates the prior token."""
        if hasattr(self, "_key_save_event") and self._key_save_event is not None:
            self._key_save_event.cancel()
        self._key_save_event = Clock.schedule_once(lambda dt: self._persist_api_key(value.strip()), 0.6)

    def _persist_api_key(self, key):
        try:
            if self._auth_busy:
                return
            if key != self.api_key:
                self.api_key = key
                self.access_token = ""
                self.is_logged_in = False
                save_creds(key, "")
        except Exception as exc:
            self.log("Could not save API key: " + str(exc))

    def _run_login_action(self, name, callback):
        """Visible proof of touch dispatch and errors."""
        self._set_login_status(name + " tapped", True)
        self.log(name + " tapped")
        try:
            Clock.schedule_once(lambda *_: self._safe_action(name, callback), 0)
        except Exception as exc:
            self._set_login_status(str(exc)[:100], False)

    def _safe_action(self, name, callback):
        try:
            callback()
        except Exception as exc:
            self.log(f"{name} error: {exc}")
            self._set_login_status(f"{name} failed: {str(exc)[:80]}", False)

    def _nav(self, current):
        nav = BoxLayout(size_hint_y=None, height=dp(62), spacing=dp(6))
        for title, screen in [('⌂  Home', 'dashboard'), ('↗  Trade', 'orders'), ('⚙  Account', 'settings')]:
            nav.add_widget(action(title, lambda target=screen: self.shell.navigate(target),
                                  ACCENT if current == screen else CARD2, 58))
        return nav

    def _page_header(self, title, subtitle):
        header = BoxLayout(size_hint_y=None, height=dp(62), spacing=dp(10))
        header.add_widget(action('☰', self.shell.toggle_drawer, CARD2, 50))
        names = BoxLayout(orientation='vertical')
        names.add_widget(txt(title, 22, FG, True, 35))
        names.add_widget(txt(subtitle, 11, MUTED, height=23))
        header.add_widget(names)
        return header

    def _login_screen(self):
        scr = Screen(name='login')
#        scr.canvas.before.clear()
        Window.clearcolor = BG
        scroll, root = stack_scroll()
        root.add_widget(txt('MCX  |  TRADE PRO', 25, FG, True, 48))
        root.add_widget(txt('Secure Zerodha connection', 14, MUTED, height=28))
        panel = Surface(orientation='vertical', padding=dp(16), spacing=dp(12), size_hint_y=None)
        panel.bind(minimum_height=panel.setter('height'))
        panel.add_widget(txt('CONNECT YOUR ACCOUNT', 15, FG, True))
        self.api_key_input = field('Kite API key', self.api_key)
        self.api_key_input.bind(text=self._on_api_key_edited)
        self.api_secret_input = field('API secret (not saved)', password=True)
        self.request_token_input = field('Request token or redirect URL')
        for item in (self.api_key_input, self.api_secret_input, self.request_token_input):
            panel.add_widget(item)
        panel.add_widget(action('1  Open Zerodha Login', lambda: self._run_login_action('Open Zerodha Login', self.generate_login_url), ACCENT))
        panel.add_widget(action('2  Paste Redirect URL', lambda: self._run_login_action('Paste Redirect URL', self.paste_redirect_url)))
        panel.add_widget(action('3  Exchange Token / Connect', lambda: self._run_login_action('Exchange Token / Login', self.manual_login), GREEN))
        panel.add_widget(action('Validate Saved Session', lambda: self._run_login_action('Validate Saved Token', self.auto_login)))
        self.login_status_lbl = txt('Not logged in', 14, RED, True, 36)
        panel.add_widget(self.login_status_lbl)
        self.login_help_lbl = txt('Complete login in the browser, then paste the final redirect URL containing request_token.', 12, MUTED, height=74)
        panel.add_widget(self.login_help_lbl)
        root.add_widget(panel)
        root.add_widget(action('View / Copy Crash Diagnostics', self.show_crash_log))
        scr.add_widget(scroll)
        return scr

    def _dashboard_screen(self):
        scr = Screen(name='dashboard')
        outer = BoxLayout(orientation='vertical', padding=dp(8), spacing=dp(5))
        scroll, root = stack_scroll()
        self.dashboard_root = root
        root.add_widget(self._page_header('Market overview', 'Live futures • calendar spread'))
        self.top_status = txt('Disconnected', 13, MUTED, height=28)
        root.add_widget(self.top_status)
        market = Surface(orientation='vertical', padding=dp(14), spacing=dp(10), size_hint_y=None)
        market.bind(minimum_height=market.setter('height'))
        market.add_widget(txt('MCX FUTURES', 13, MUTED, True))
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.commodity_spin = Spinner(text='NATURALGAS', values=('NATURALGAS', 'GOLD', 'SILVER', 'CRUDEOIL', 'COPPER', 'LEAD', 'ZINC'),
                                      background_normal='', background_color=CARD2, color=FG, font_size=dp(14))
        row.add_widget(self.commodity_spin)
        row.add_widget(action('Load contracts', self.load_contracts, ACCENT))
        market.add_widget(row)
        self.contract_spin = Spinner(text='--', values=(), size_hint_y=None, height=dp(48), background_normal='', background_color=CARD2, color=FG)
        market.add_widget(self.contract_spin)
        self.month_prices_label = txt('Current: --  |  Next: --', 13, FG, height=52)
        self.month_changes_label = txt('Daily changes: --', 12, MUTED, height=44)
        market.add_widget(self.month_prices_label)
        market.add_widget(self.month_changes_label)
        self.third_price_label = txt('Third month: --', 13, FG, height=44)
        self.spread_label = txt('Near / next spread: --', 13, FG, height=64)
        self.rule_label = txt('NG entry rules: waiting for quotes', 12, MUTED, height=70)
        for widget in (self.third_price_label, self.spread_label, self.rule_label):
            market.add_widget(widget)
        root.add_widget(market)
        signal = Surface(orientation='vertical', padding=dp(16), spacing=dp(4), size_hint_y=None, height=dp(176))
        signal.add_widget(txt('DAILY CHANGE DIFFERENCE', 12, MUTED, True))
        self.diff_label = txt('--', 42, FG, True, 74)
        self.signal_label = txt('WAIT', 18, MUTED, True, 38)
        signal.add_widget(self.diff_label)
        signal.add_widget(self.signal_label)
        root.add_widget(signal)
        monitor = Surface(orientation='vertical', padding=dp(14), spacing=dp(10), size_hint_y=None)
        monitor.bind(minimum_height=monitor.setter('height'))
        monitor.add_widget(txt('SIGNAL MONITOR', 14, FG, True))
        thresholds = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.entry_input = field('Entry below', '-10.0')
        self.exit_input = field('Exit above', '10.0')
        thresholds.add_widget(self.entry_input)
        thresholds.add_widget(self.exit_input)
        monitor.add_widget(thresholds)
        buttons = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        buttons.add_widget(action('Start monitoring', self.start_monitoring, GREEN))
        buttons.add_widget(action('Stop', self.stop_monitoring, RED))
        monitor.add_widget(buttons)
        root.add_widget(monitor)
        self.pnl_label = txt('P&L: Rs 0.00', 17, FG, True, 48)
        root.add_widget(self.pnl_label)
        root.add_widget(action('Refresh positions / P&L', lambda: threading.Thread(target=self._refresh_positions, daemon=True).start()))
        root.add_widget(txt('ACTIVITY', 13, MUTED, True, 32))
        self.log_scroll = ScrollView(size_hint_y=None, height=dp(180))
        self.log_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(4))
        self.log_box.bind(minimum_height=self.log_box.setter('height'))
        self.log_scroll.add_widget(self.log_box)
        root.add_widget(self.log_scroll)
        outer.add_widget(scroll)
        outer.add_widget(self._nav('dashboard'))
        scr.add_widget(outer)
        return scr

    def _orders_screen(self):
        scr = Screen(name='orders')
        outer = BoxLayout(orientation='vertical', padding=dp(8), spacing=dp(5))
        scroll, root = stack_scroll()
        root.add_widget(self._page_header('Trade ticket', 'Review before submitting real orders'))
        root.add_widget(txt('REAL orders: review contract, lot size and margin', 12, MUTED, height=42))
        panel = Surface(orientation='vertical', padding=dp(14), spacing=dp(12), size_hint_y=None)
        panel.bind(minimum_height=panel.setter('height'))
        panel.add_widget(txt('QUANTITY IN LOTS', 13, MUTED, True))
        self.quantity_input = field('Lots', '1')
        self.quantity_input.input_filter = 'int'
        panel.add_widget(self.quantity_input)
        panel.add_widget(txt('Selected contract comes from Market overview', 12, MUTED, height=48))
        buy_sell = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        buy_sell.add_widget(action('BUY FUTURE', lambda: self.place_order('BUY'), GREEN, 52))
        buy_sell.add_widget(action('SELL FUTURE', lambda: self.place_order('SELL'), RED, 52))
        panel.add_widget(buy_sell)
        panel.add_widget(txt('CALENDAR SPREAD', 13, MUTED, True))
        panel.add_widget(action('Entry: Buy near / Sell next', lambda: self.confirm_spread('ENTRY'), ACCENT))
        panel.add_widget(action('Exit: Sell near / Buy next', lambda: self.confirm_spread('EXIT')))
        root.add_widget(panel)
        root.add_widget(action('View recent orders', lambda: threading.Thread(target=self._show_orders, daemon=True).start()))
        root.add_widget(txt('Spread legs execute separately. Check fills and positions.', 12, MUTED, height=55))
        outer.add_widget(scroll)
        outer.add_widget(self._nav('orders'))
        scr.add_widget(outer)
        return scr

    # ---------- ACCOUNT ----------
    def _settings_screen(self):
        scr = Screen(name='settings')
        outer = BoxLayout(orientation='vertical', padding=dp(8), spacing=dp(5))
        scroll, root = stack_scroll()
        root.add_widget(self._page_header('Account & settings', 'Connection and diagnostics'))
        root.add_widget(action('Test Zerodha connection', self.test_connection, ACCENT))
        root.add_widget(action('Refresh positions / P&L', lambda: threading.Thread(target=self._refresh_positions, daemon=True).start()))
        root.add_widget(action('Show order history', lambda: threading.Thread(target=self._show_orders, daemon=True).start()))
        root.add_widget(action('View / Copy Crash Log', self.show_crash_log))
        root.add_widget(action('Logout', self._logout, RED))
        root.add_widget(txt('Crash logs are stored privately on the device.', 12, MUTED, height=55))
        outer.add_widget(scroll)
        outer.add_widget(self._nav('settings'))
        scr.add_widget(outer)
        return scr

    def show_crash_log(self):
        path = crash_log_path()
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                detail = handle.read()[-20000:]
        except FileNotFoundError:
            detail = 'No Python crash has been recorded. For native Android crashes, collect adb logcat.'
        except Exception as exc:
            detail = 'Cannot read log: ' + str(exc)
        panel = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(8))
        panel.add_widget(TextInput(text=detail, readonly=True, multiline=True))
        buttons = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        popup = Popup(title='Crash diagnostics', content=panel, size_hint=(0.96, 0.85))
        buttons.add_widget(action('Copy log', lambda: Clipboard.copy(detail)))
        buttons.add_widget(action('Close', popup.dismiss))
        panel.add_widget(buttons)
        popup.open()

    def _refresh_positions(self):
        if not self.is_logged_in:
            self.log("Login required")
            return
        try:
            rows = self.kite.positions().get("net", [])
            pnl = sum(float(r.get("pnl", 0) or 0) for r in rows)
            self.log(f"Net positions: {len(rows)} | P&L Rs {pnl:.2f}")
            Clock.schedule_once(lambda *_: setattr(self.pnl_label, "text", f"P&L: Rs {pnl:.2f}"), 0)
            for r in rows:
                if int(r.get("quantity") or 0):
                    self.log(f"{r.get('tradingsymbol')} qty {r.get('quantity')} pnl {r.get('pnl')}")
        except Exception as e:
            self.log(f"Positions failed: {e}")

    def _show_orders(self):
        if not self.is_logged_in:
            self.log("Login required")
            return
        try:
            orders = self.kite.orders()
            self.log(f"Orders today: {len(orders)}")
            for r in orders[-12:]:
                self.log(f"{r.get('tradingsymbol')} {r.get('transaction_type')} {r.get('status')}")
        except Exception as e:
            self.log(f"Orders failed: {e}")

    def _logout(self):
        self.kite = None
        self.monitoring = False
        self.is_logged_in = False
        self.access_token = ""
        clear_saved_token(self.api_key)
        self.shell.close_drawer()
        self.sm.current = "login"
        self._set_login_status("Logged out", False)

    # ---------- LOGGING ----------
    @mainthread
    def log(self, msg):
        ts = dt.now().strftime("%H:%M:%S")
        line = Label(text=f"[{ts}] {msg}", halign="left", valign="top",
                     size_hint_y=None, height=dp(20), font_size=dp(11))
        line.bind(width=lambda l, w: setattr(l, "text_size", (w, None)))
        if hasattr(self, "log_box"):
            self.log_box.add_widget(line)
        if hasattr(self, "log_scroll"):
            self.log_scroll.scroll_y = 0

    # ---------- AUTH ----------
    def show_import_error(self):
        """Show complete import traceback, copy it, and save a local diagnostic."""
        detail = ("Kite client: " + KITE_IMPORT_LOCATION + "\n\n" + (KITE_IMPORT_TRACEBACK or KITE_IMPORT_ERROR or "No import error recorded."))
        try:
            path = os.path.join(self.user_data_dir, "kite_import_error.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(detail)
            self.log("Kite import diagnostic saved to " + path)
        except Exception as exc:
            self.log("Unable to save diagnostic: " + str(exc))
        body = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        content = TextInput(text=detail, readonly=True, multiline=True,
                            size_hint=(1, 0.85), font_size=dp(13))
        body.add_widget(content)
        actions = BoxLayout(size_hint=(1, 0.15), spacing=dp(8))
        copy_btn = Button(text="Copy Full Error")
        close_btn = Button(text="Close")
        actions.add_widget(copy_btn)
        actions.add_widget(close_btn)
        body.add_widget(actions)
        popup = Popup(title="Kite Client Diagnostic", content=body,
                      size_hint=(0.95, 0.82), auto_dismiss=True)
        copy_btn.bind(on_release=lambda *_: Clipboard.copy(detail))
        close_btn.bind(on_release=lambda *_: popup.dismiss())
        popup.open()

    def generate_login_url(self, *_):
        """Launch Kite login in a browser, with Android fallback and visible errors."""
        if self._auth_busy:
            return
        key = self.api_key_input.text.strip()
        if not key:
            self._set_login_status("Enter API Key first", False)
            return
        if KiteConnect is None:
            self._set_login_status("KiteConnect unavailable: " + KITE_IMPORT_ERROR[:90], False)
            self.log("KiteConnect import failed: " + KITE_IMPORT_ERROR)
            self.show_import_error()
            return
        try:
            self._persist_api_key(key)
            url = KiteConnect(api_key=key).login_url()
        except Exception as exc:
            self._set_login_status("Unable to create login URL", False)
            self.login_help_lbl.text = f"Login URL error: {str(exc)[:200]}"
            self.log(f"Login URL error: {exc}")
            return

        self.last_login_url = url
        opened = False
        errors = []
        if platform == "android":
            try:
                from jnius import autoclass
                PythonActivity = autoclass("org.kivy.android.PythonActivity")
                Intent = autoclass("android.content.Intent")
                Uri = autoclass("android.net.Uri")
                activity = PythonActivity.mActivity
                intent = Intent(Intent.ACTION_VIEW)
                intent.setData(Uri.parse(url))
                intent.addCategory(Intent.CATEGORY_BROWSABLE)
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                activity.startActivity(intent)
                opened = True
            except Exception as exc:
                errors.append(f"Android intent: {exc}")
            if not opened:
                try:
                    from plyer import browser
                    browser.open(url)
                    opened = True
                except Exception as exc:
                    errors.append(f"Plyer: {exc}")
        else:
            try:
                opened = bool(webbrowser.open(url))
            except Exception as exc:
                errors.append(f"Desktop browser: {exc}")

        if opened:
            self._set_login_status("Browser launch requested", True)
            self.login_help_lbl.text = (
                "Complete Zerodha login in the browser. Then paste the FINAL "
                "redirect URL containing request_token=... into the app."
            )
            self.log("Browser launch requested for Kite login")
        else:
            self._set_login_status("Browser could not open", False)
            self.login_help_lbl.text = (
                "No browser available in this emulator/device. Install Chrome "
                "or another browser, then retry. See log for technical details."
            )
            self.log("Browser launch failed: " + "; ".join(errors))
            self.log("Login URL (public, no secret): " + url)

    def paste_redirect_url(self, *_):
        """Read Android clipboard on the Android UI thread, update Kivy on its thread."""
        if platform == "android":
            try:
                from android.runnable import run_on_ui_thread
                from jnius import autoclass
                @run_on_ui_thread
                def read_clipboard():
                    try:
                        activity = autoclass("org.kivy.android.PythonActivity").mActivity
                        clipboard = activity.getSystemService("clipboard")
                        clip = clipboard.getPrimaryClip()
                        value = ""
                        if clip is not None and clip.getItemCount() > 0:
                            text = clip.getItemAt(0).coerceToText(activity)
                            if text is not None:
                                value = str(text.toString())
                        Clock.schedule_once(lambda _, value=value: self._apply_pasted_token(value), 0)
                    except Exception:
                        Clock.schedule_once(lambda _: self._clipboard_failed(), 0)
                read_clipboard()
            except Exception:
                self._clipboard_failed()
        else:
            try:
                self._apply_pasted_token(Clipboard.get())
            except Exception:
                self._clipboard_failed()

    def _clipboard_failed(self):
        self._set_login_status("Clipboard unavailable", False)
        self.login_help_lbl.text = "Long-press the request token field and select Paste, or type the token manually."

    def _apply_pasted_token(self, value):
        if self._auth_busy:
            return
        token = extract_request_token(value or "")
        if token:
            self.request_token_input.text = token
            self._set_login_status("Request token ready", True)
        else:
            self._set_login_status("No valid request token in clipboard", False)
            self.login_help_lbl.text = "Copy the final redirect URL containing request_token, or paste it directly into the token field."

    def manual_login(self, *_):
        if self._auth_busy:
            return
        key = self.api_key_input.text.strip()
        secret = self.api_secret_input.text.strip()
        token = extract_request_token(self.request_token_input.text)
        if not key or not secret or not token:
            self._login_error("API key, API secret and fresh request token are required")
            return
        self._start_auth(key, token, secret, False)

    def auto_login(self, *_):
        if self._auth_busy:
            return
        key = self.api_key_input.text.strip()
        if key != self.api_key or not self.access_token:
            self._login_error("No saved session for this API key")
            return
        self._start_auth(key, self.access_token, None, True)

    def _start_auth(self, key, token, secret, saved):
        event = getattr(self, "_key_save_event", None)
        if event is not None:
            event.cancel()
        self._auth_busy = True
        self.is_logged_in = False
        for widget in (self.api_key_input, self.api_secret_input, self.request_token_input):
            widget.disabled = True
        self._set_login_status("Authenticating...", False)
        try:
            threading.Thread(target=self._auth_worker, args=(key, token, secret, saved), daemon=True).start()
        except Exception as exc:
            self._finish_auth(key, None, "", str(exc), saved)

    def _auth_worker(self, key, token, secret, saved):
        client = None
        access = ""
        error = ""
        try:
            client = KiteConnect(api_key=key)
            access = token if saved else client.generate_session(token, api_secret=secret)["access_token"]
            client.set_access_token(access)
            client.profile()
        except Exception as exc:
            error = str(exc)
        Clock.schedule_once(lambda dt: self._finish_auth(key, client, access, error, saved), 0)

    def _finish_auth(self, key, client, access, error, saved):
        self._auth_busy = False
        for widget in (self.api_key_input, self.api_secret_input, self.request_token_input):
            widget.disabled = False
        if error:
            self.is_logged_in = False
            self.kite = None
            self._login_error(error)
            return
        self.api_key, self.access_token, self.kite = key, access, client
        self.is_logged_in = True
        try:
            save_creds(key, access)
        except Exception as exc:
            self.log("Session storage failed: " + str(exc))
        self.api_secret_input.text = ""
        self.request_token_input.text = ""
        self._set_login_status("Logged In", True)
        self.top_status.text = "Connected"
        self.shell.navigate("dashboard")
        threading.Thread(target=self.load_instruments, daemon=True).start()

    def _login_error(self, error):
        self._set_login_status("Login failed", False)
        self.login_help_lbl.text = str(error)[:400]
        Popup(title="Zerodha login error", content=Label(text=str(error)[:400], text_size=(dp(260), None)), size_hint=(.92, .55)).open()

    def _set_login_status(self, message, success):
        if hasattr(self, "login_status_lbl"):
            self.login_status_lbl.text = message
            self.login_status_lbl.color = GREEN if success else RED

    def load_instruments(self):
        try:
            data = self.kite.instruments("MCX")
            self.instruments_df = data
            self.log(f"Loaded {len(data)} MCX instruments")
        except Exception as e:
            self.log(f"Instrument load failed: {e}")

    # ---------- CONTRACTS ----------
    def _contracts_list(self):
        return [c for c in (self.current_month, self.next_month,
                            self.next_next_month) if c]

    def load_contracts(self):
        if not self.is_logged_in or not self.instruments_df:
            self.log("Login first")
            return
        try:
            base = self.commodity_spin.text
            today = date.today()
            rows = [r for r in self.instruments_df
                    if r.get("name") == base
                    and r["instrument_type"] == "FUT"]
            def _exp(r):
                e = r["expiry"]
                return e if isinstance(e, date) else dt.strptime(str(e)[:10], "%Y-%m-%d").date()
            rows = [r for r in rows if _exp(r) >= today]
            rows.sort(key=_exp)
            syms = [r["tradingsymbol"] for r in rows]
            if len(syms) < 2:
                self.log(f"Only {len(syms)} contracts for {base}")
                return
            self.current_month = syms[0]
            self.next_month = syms[1]
            self.next_next_month = syms[2] if len(syms) > 2 else None
            self.available_futures = syms
            self.contract_spin.values = tuple(syms)
            self.contract_spin.text = syms[0]
            self.log(f"Loaded: {', '.join(syms)}")
        except Exception as e:
            self.log(f"Load contracts error: {e}")

    # ---------- MONITORING ----------
    def start_monitoring(self):
        if not self.is_logged_in:
            self.log("Login first"); return
        if self.monitoring:
            return
        try:
            self.entry_threshold = float(self.entry_input.text)
            self.exit_threshold = float(self.exit_input.text)
        except ValueError:
            self.log("Invalid thresholds"); return
        self.monitoring = True
        self.log("Monitoring started")
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def stop_monitoring(self):
        self.monitoring = False
        self.log("Monitoring stopped")

    def on_pause(self):
        # Keep the Android process eligible to resume; do not stop the session.
        return True

    def on_resume(self):
        # Access tokens are validated on startup or when the user taps Validate.
        return

    def _monitor_loop(self):
        while self.monitoring and self.is_logged_in:
            try:
                diff = self._compute_diff()
                if diff is None:
                    time.sleep(2); continue
                Clock.schedule_once(lambda *_, d=diff: self._update_ui(d), 0)
                log_price(diff)
                self._check_signal(diff)
            except Exception as e:
                self.log(f"Monitor error: {e}")
                time.sleep(5)
                continue
            time.sleep(2)

    def _compute_diff(self):
        try:
            first, second = self.current_month, self.next_month
            if not first or not second:
                return None
            symbols = [first, second]
            if self.next_next_month:
                symbols.append(self.next_next_month)
            q = self.kite.quote([f"MCX:{symbol}" for symbol in symbols])
            quotes = {}
            for symbol in symbols:
                row = q.get(f"MCX:{symbol}", {})
                price = float(row.get("last_price") or 0)
                close = float(row.get("ohlc", {}).get("close") or 0)
                if price > 0 and close > 0:
                    quotes[symbol] = (price, close)
            if first not in quotes or second not in quotes:
                return None
            self.last_quotes = quotes
            p1, c1 = quotes[first]
            p2, c2 = quotes[second]
            return (p1 - c1) - (p2 - c2)
        except Exception as e:
            self.log(f"Diff error: {e}")
            return None

    def _ng_entry_rule(self, diff):
        # Advisory filter only; inventory timing is not inferred.
        if not (self.current_month or '').startswith('NATURALGAS'):
            return True, 'NG rules not applied to this commodity'
        now = dt.now(timezone(timedelta(hours=5, minutes=30)))
        if now.weekday() >= 5:
            return False, 'Weekend: entry alerts blocked'
        if now.weekday() == 3:
            return False, 'Thursday: inventory rule requires manual review'
        if (now.hour, now.minute) < (12, 37):
            return False, 'Entry alerts blocked until 12:37 PM IST'
        if abs(diff) < 7:
            return False, 'Daily change gap below Rs 7'
        return True, 'Time and daily change gap filters passed'

    def _check_signal(self, diff):
        now = time.time()
        if self.last_trigger and now - self.last_trigger < self.trigger_cooldown:
            return
        if diff < self.entry_threshold:
            allowed, _ = self._ng_entry_rule(diff)
            if not allowed:
                return
            self.last_trigger = now
            self.log(f"ENTRY signal: {diff:+.2f}")
            speak(f"Entry signal. Price difference {diff:.2f}")
            notify("ENTRY SIGNAL", f"Price diff {diff:+.2f}")
        elif diff > self.exit_threshold:
            self.last_trigger = now
            self.log(f"EXIT signal: {diff:+.2f}")
            speak(f"Exit signal. Price difference {diff:.2f}")
            notify("EXIT SIGNAL", f"Price diff {diff:+.2f}")

    @mainthread
    def _update_ui(self, diff):
        self.diff_label.text = f"{diff:+.2f}"
        first, second = self.current_month, self.next_month
        if first in self.last_quotes and second in self.last_quotes:
            p1, c1 = self.last_quotes[first]
            p2, c2 = self.last_quotes[second]
            self.month_prices_label.text = f"{first}: {p1:.2f} | {second}: {p2:.2f}"
            pct1 = (p1 - c1) / c1 * 100 if c1 else 0
            pct2 = (p2 - c2) / c2 * 100 if c2 else 0
            self.month_changes_label.text = f"Day change: {pct1:+.2f}% | {pct2:+.2f}% | Sum {pct1+pct2:+.2f}%"
            self.spread_label.text = f'Near - next: Rs {p1-p2:+.2f} | Previous: Rs {c1-c2:+.2f}'
            third = self.next_next_month
            if third in self.last_quotes:
                p3, c3 = self.last_quotes[third]
                gap = (p2-c2)-(p3-c3)
                self.third_price_label.text = f'{third}: {p3:.2f} | Change: {p3-c3:+.2f}'
                self.spread_label.text += f'\nNext - third: {p2-p3:+.2f} | Change gap: {gap:+.2f}'
                if abs(gap) >= 8:
                    self.spread_label.text += ' (review)'
            else:
                self.third_price_label.text = 'Third month: unavailable'
        allowed, reason = self._ng_entry_rule(diff)
        self.rule_label.text = reason
        color = (1, 0.3, 0.3, 1) if diff < 0 else (0.3, 0.8, 0.3, 1)
        self.diff_label.color = color
        if diff < self.entry_threshold:
            self.signal_label.text = "ENTRY SIGNAL" if allowed else "ENTRY BLOCKED"
            self.signal_label.color = (0.2, 0.8, 0.3, 1)
        elif diff > self.exit_threshold:
            self.signal_label.text = "EXIT SIGNAL"
            self.signal_label.color = (0.9, 0.2, 0.2, 1)
        else:
            self.signal_label.text = "WAIT"
            self.signal_label.color = (0.5, 0.5, 0.5, 1)
        self.top_status.text = f"Connected | {dt.now().strftime('%H:%M:%S')}"

    # ---------- ORDERS ----------
    def _lots_to_units(self, symbol, lots):
        for item in (self.instruments_df or []):
            if item.get("tradingsymbol") == symbol:
                lot_size = int(item.get("lot_size") or 0)
                if lot_size <= 0:
                    raise ValueError("Invalid lot size for " + symbol)
                return lots * lot_size
        raise ValueError("Contract lot size unavailable: " + symbol)

    def _order_quantity(self):
        qty = int(self.quantity_input.text)
        if qty <= 0:
            raise ValueError("Quantity must be positive")
        return qty

    def _confirm_orders(self, title, legs):
        """Explicit confirmation: never submit live orders on signal alone."""
        description = "\n".join(f"{side} {qty} {symbol}" for symbol, side, qty in legs)
        content = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        content.add_widget(Label(text=description, font_size=dp(15)))
        actions = BoxLayout(size_hint_y=0.32, spacing=dp(8))
        popup = Popup(title=title + " - REAL MCX ORDERS", content=content, size_hint=(0.94, 0.45))
        actions.add_widget(Button(text="Cancel", on_press=lambda *_: popup.dismiss()))
        def submit(*_):
            popup.dismiss()
            threading.Thread(target=self._submit_legs, args=(legs,), daemon=True).start()
        actions.add_widget(Button(text="Confirm orders", on_press=submit))
        content.add_widget(actions)
        popup.open()

    def _submit_legs(self, legs):
        """Submit legs sequentially and stop on first failure."""
        for symbol, side, qty in legs:
            if not self._do_order(symbol, side, qty):
                self.log("ORDER WARNING: leg failed. Check positions before retrying.")
                break

    def place_order(self, side):
        if not self.is_logged_in:
            self.log("Login first")
            return
        contract = self.contract_spin.text
        if not contract or contract == "--":
            self.log("Select contract first")
            return
        try:
            qty = self._order_quantity()
        except ValueError as e:
            self.log(f"Invalid quantity: {e}")
            return
        try:
            self._confirm_orders(side, [(contract, side, self._lots_to_units(contract, qty))])
        except ValueError as e:
            self.log(str(e))

    def confirm_spread(self, kind):
        if not self.is_logged_in or not self.current_month or not self.next_month:
            self.log("Login and load two contracts first")
            return
        try:
            qty = self._order_quantity()
        except ValueError as e:
            self.log(f"Invalid quantity: {e}")
            return
        # Matches the source script's leg direction for each signal type.
        if kind == "ENTRY":
            legs = [(self.next_month, "SELL", qty), (self.current_month, "BUY", qty)]
        else:
            legs = [(self.current_month, "SELL", qty), (self.next_month, "BUY", qty)]
        try:
            legs = [(symbol, side, self._lots_to_units(symbol, qty)) for symbol, side, qty in legs]
            self._confirm_orders(kind + " calendar spread", legs)
        except ValueError as e:
            self.log(str(e))

    def _do_order(self, contract, side, qty=1):
        try:
            oid = self.kite.place_order(
                variety="regular",
                exchange="MCX",
                tradingsymbol=contract,
                transaction_type=side,
                quantity=qty,
                order_type="MARKET",
                product="NRML",
                validity="DAY",
            )
            rec = {"time": dt.now().strftime("%H:%M:%S"),
                   "contract": contract, "type": side, "quantity": qty,
                   "price": "MARKET", "status": "PLACED", "order_id": oid}
            self.order_history.append(rec)
            log_order(rec)
            self.log(f"{side} {qty} {contract} placed (id {oid})")
            return True
        except Exception as e:
            self.log(f"Order failed: {e}")
            return False

    def test_connection(self, *_):
        threading.Thread(target=self._test_connection_worker, daemon=True).start()

    def _test_connection_worker(self):
        if not self.is_logged_in:
            self.log("Login first"); return
        try:
            p = self.kite.profile()
            self.log(f"Connected as {p['user_name']}")
        except Exception as e:
            self.log(f"Connection test failed: {e}")


class TradingApp41(TradingApp):
    def _build_application(self):
        self.mode = 'Manual trading'
        self.halted = False
        self.paper = None
        self.paper_history = []
        self.samples = []
        self._worker = None
        self._order_busy = False
        self._quote_at = 0
        self._alert_popup = None
        self.target, self.stop_loss, self.daily_limit = 2500., 1500., 1500.
        return super()._build_application()

    def _dashboard_screen(self):
        scr = super()._dashboard_screen()
        root = self.dashboard_root
        panel = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(6))
        panel.bind(minimum_height=panel.setter('height'))
        panel.add_widget(txt('V4.4  |  TRADE CONTROLS', 16, FG, True))
        self.mode_spin = Spinner(text=self.mode, values=('Manual trading','Signal only','Paper trading'), size_hint_y=None, height=dp(48))
        self.mode_spin.bind(text=self._change_mode)
        panel.add_widget(self.mode_spin)
        self.target_field = field('Combined target', '2500')
        self.loss_field = field('Combined stop loss', '1500')
        self.limit_field = field('Daily paper loss limit', '1500')
        for label, widget in [('Profit target (Rs)',self.target_field),('Stop loss (Rs)',self.loss_field),('Daily paper loss limit (Rs)',self.limit_field)]:
            panel.add_widget(txt(label)); panel.add_widget(widget)
        self.voice_switch = Switch(active=True); self.popup_switch = Switch(active=True)
        for label, widget in [('Voice alerts',self.voice_switch),('Popup alerts',self.popup_switch)]:
            row = BoxLayout(size_hint_y=None,height=dp(44)); row.add_widget(txt(label)); row.add_widget(widget); panel.add_widget(row)
        panel.add_widget(action('EMERGENCY STOP (blocks new orders)',self._halt,RED))
        panel.add_widget(action('Reset emergency stop',self._reset_halt))
        panel.add_widget(action('Close paper spread',lambda: self._paper_close('Manual exit')))
        self.performance = txt('Paper P&L: Rs 0',13,FG,height=100); panel.add_widget(self.performance)
        self.chart = Widget(size_hint_y=None,height=dp(120)); panel.add_widget(self.chart)
        panel.add_widget(action('Export paper trades CSV',self._export_paper))
        root.add_widget(panel,index=len(root.children)-1)
        return scr

    def _change_mode(self, widget, value):
        if self.paper or self._order_busy:
            if value != self.mode: widget.text = self.mode
            return
        self.mode = value

    def _halt(self):
        self.halted = True
        self.stop_monitoring()
        self.log('Emergency stop: new orders blocked; existing positions NOT closed')

    def _reset_halt(self):
        if not self._order_busy:
            self.halted = False
            self.log('Emergency stop reset; restart monitoring manually')

    def load_contracts(self):
        if self.monitoring or self.paper or self._order_busy:
            self.log('Stop monitoring and close paper spread before changing contracts'); return
        self.last_quotes = {}; self._quote_at = 0; self.samples = []
        super().load_contracts()

    def start_monitoring(self):
        if self.halted or (self._worker and self._worker.is_alive()):
            self.log('Reset stop or wait for previous monitor to finish'); return
        if not self.is_logged_in or not self.current_month or not self.next_month:
            self.log('Login and load contracts first'); return
        try:
            values = [float(w.text) for w in (self.entry_input,self.exit_input,self.target_field,self.loss_field,self.limit_field)]
            if not all(math.isfinite(v) for v in values) or not values[0] < values[1] or min(values[2:]) <= 0: raise ValueError()
            self.entry_threshold,self.exit_threshold,self.target,self.stop_loss,self.daily_limit = values
        except ValueError:
            self.log('Invalid risk settings'); return
        self.monitoring = True
        self._worker = threading.Thread(target=self._monitor_loop,daemon=True); self._worker.start()

    def _monitor_loop(self):
        while self.monitoring and self.is_logged_in:
            diff = self._compute_diff()
            if diff is not None:
                snapshot = dict(self.last_quotes)
                Clock.schedule_once(lambda _,d=diff,q=snapshot: self._tick(d,q),0)
                try: log_price(diff)
                except Exception as exc: self.log(str(exc))
            else:
                Clock.schedule_once(lambda _: self._stale(),0)
            time.sleep(2)

    def _stale(self):
        self._quote_at = 0
        self.top_status.text = 'Quotes unavailable / stale'

    def _tick(self,diff,quotes):
        if not self.monitoring or not self.is_logged_in or self.halted: return
        self.last_quotes = quotes; self._quote_at = time.monotonic()
        self._update_ui(diff)
        self.samples = (self.samples+[diff])[-120:]
        self._draw_chart()
        self._check_signal(diff)
        if self.paper:
            pnl = self._paper_pnl()
            if pnl is not None and (pnl >= self.target or pnl <= -self.stop_loss or self._daily_pnl()+pnl <= -self.daily_limit):
                self._paper_close('Risk threshold')
        self._show_performance()

    def _draw_chart(self):
        c=self.chart; c.canvas.clear()
        if len(self.samples)<2: return
        lo=min(self.samples); span=max(max(self.samples)-lo,0.01)
        pts=[]
        for i,v in enumerate(self.samples): pts.extend((c.x+c.width*i/(len(self.samples)-1),c.y+c.height*(v-lo)/span))
        with c.canvas:
            Color(*ACCENT); Line(points=pts,width=1.4)

    def _alert(self,title,message):
        self.log(title+': '+message)
        if self.voice_switch.active: speak(title+' '+message)
        notify(title,message)
        if self.popup_switch.active and not self._alert_popup:
            popup=Popup(title=title,content=Label(text=message),size_hint=(.9,.35))
            self._alert_popup=popup
            popup.bind(on_dismiss=lambda *_: setattr(self,'_alert_popup',None)); popup.open()

    def _check_signal(self,diff):
        if self.mode == 'Paper trading' and self.paper and diff > self.exit_threshold:
            self._paper_close('Exit signal'); return
        if self.last_trigger and time.time()-self.last_trigger < self.trigger_cooldown: return
        if diff < self.entry_threshold and self._ng_entry_rule(diff)[0]:
            self.last_trigger=time.time(); self._alert('ENTRY: BUY near / SELL next',f'Gap {diff:+.2f}')
            if self.mode=='Paper trading' and not self.paper: self._paper_open()
        elif diff > self.exit_threshold:
            self.last_trigger=time.time(); self._alert('EXIT: SELL near / BUY next',f'Gap {diff:+.2f}')

    def _daily_pnl(self):
        today=dt.now(timezone(timedelta(hours=5,minutes=30))).date().isoformat()
        return sum(r['pnl'] for r in self.paper_history if r['date']==today)

    def _paper_open(self):
        if self.halted or self.paper or self._daily_pnl() <= -self.daily_limit or time.monotonic()-self._quote_at>8: return
        try:
            a,b=self.current_month,self.next_month
            lots=self._order_quantity()
            self.paper=(a,b,self.last_quotes[a][0],self.last_quotes[b][0],self._lots_to_units(a,lots),self._lots_to_units(b,lots))
            self.log('Paper spread opened (simulated LTP fills)')
        except (ValueError,KeyError) as exc: self.log(str(exc))

    def _paper_pnl(self):
        if not self.paper or time.monotonic()-self._quote_at>8: return None
        a,b,p,q,u,v=self.paper
        if a not in self.last_quotes or b not in self.last_quotes: return None
        return (self.last_quotes[a][0]-p)*u+(q-self.last_quotes[b][0])*v

    def _paper_close(self,reason):
        pnl=self._paper_pnl()
        if pnl is None: self.log('No paper position or fresh quote'); return
        a,b,*_=self.paper
        self.paper_history.append(dict(date=dt.now(timezone(timedelta(hours=5,minutes=30))).date().isoformat(),near=a,next=b,pnl=pnl,reason=reason))
        self.paper=None; self._alert('Paper exit',f'{reason}: Rs {pnl:.2f}'); self._show_performance()

    def _show_performance(self):
        h=self.paper_history; wins=sum(r['pnl']>0 for r in h); losses=sum(r['pnl']<0 for r in h)
        pnl=self._paper_pnl()
        self.performance.text=f'Paper trades {len(h)} | Wins {wins} | Losses {losses} | Win rate {100*wins/max(1,len(h)):.1f}%\nRealized Rs {sum(r["pnl"] for r in h):.2f} | Today Rs {self._daily_pnl():.2f}\nOpen P&L: {pnl if pnl is not None else "--"} (gross)'

    def _export_paper(self):
        path=os.path.join(self.user_data_dir,'paper_trades.csv')
        try:
            with open(path,'w',newline='',encoding='utf-8') as f:
                w=csv.DictWriter(f,fieldnames=['date','near','next','pnl','reason']); w.writeheader(); w.writerows(self.paper_history)
            self.log('Exported '+path)
        except OSError as exc: self.log(str(exc))

    def _confirm_orders(self,title,legs):
        if self.mode!='Manual trading' or self.halted or self._order_busy:
            self.log('Live orders blocked by mode or safety stop'); return
        if len(legs)>1:
            self.log('Live spread batch disabled: submit and verify each leg manually'); return
        super()._confirm_orders(title,legs)

    def _submit_legs(self,legs):
        if self.mode!='Manual trading' or self.halted or self._order_busy or not self.is_logged_in: return
        self._order_busy=True
        try:
            for symbol,side,qty in legs:
                if self.halted or not self.is_logged_in or not self._do_order(symbol,side,qty):
                    self.log('Order sequence stopped: reconcile broker positions'); break
        finally: self._order_busy=False

class TradingApp42(TradingApp41):
    PAIRS = {'Current - Next': (0, 1), 'Current - Third': (0, 2), 'Next - Third': (1, 2)}

    def _build_application(self):
        self.pair_mode = 'Current - Next'
        self._pair_generation = 0
        return super()._build_application()

    def _dashboard_screen(self):
        screen = super()._dashboard_screen()
        panel = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(6))
        panel.bind(minimum_height=panel.setter('height'))
        panel.add_widget(txt('CALENDAR SPREAD PAIR', 15, FG, True))
        self.pair_spin = Spinner(text=self.pair_mode, values=tuple(self.PAIRS)+('Custom expiry',), size_hint_y=None, height=dp(48))
        panel.add_widget(self.pair_spin)
        self.leg_a = Spinner(text='--', values=(), size_hint_y=None, height=dp(48))
        self.leg_b = Spinner(text='--', values=(), size_hint_y=None, height=dp(48))
        for label, widget in [('First leg (custom)', self.leg_a), ('Second leg (custom)', self.leg_b)]:
            panel.add_widget(txt(label)); panel.add_widget(widget)
        panel.add_widget(action('Apply futures pair', self._apply_pair, ACCENT))
        self.active_pair_label = txt('Active pair: load contracts', 13, FG, height=56)
        panel.add_widget(self.active_pair_label)
        self.dashboard_root.add_widget(panel, index=len(self.dashboard_root.children)-2)
        return screen

    def _orders_screen(self):
        screen = super()._orders_screen()
        # Separate leg buttons retain individual broker confirmation.
        outer = screen.children[0]
        scroll = next(w for w in outer.children if isinstance(w, ScrollView))
        root = scroll.children[0]
        self.ticket_pair = txt('Active pair: --', 13, FG, height=56)
        root.add_widget(self.ticket_pair)
        for leg in (0, 1):
            row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
            for side in ('BUY', 'SELL'):
                row.add_widget(action(f'{side} leg {leg+1}', lambda l=leg, s=side: self._pair_order(l,s), GREEN if side=='BUY' else RED))
            root.add_widget(row)
        return screen

    def _pair_order(self, leg, side):
        symbol = (self.current_month, self.next_month)[leg]
        if not self.is_logged_in or not symbol:
            self.log('Login and apply a pair first'); return
        try:
            self._confirm_orders(f'{side} {symbol}', [(symbol, side, self._lots_to_units(symbol,self._order_quantity()))])
        except ValueError as exc: self.log(str(exc))

    def _pair_locked(self):
        return self.monitoring or self.paper is not None or self._order_busy or bool(self._worker and self._worker.is_alive())

    def load_contracts(self):
        if self._pair_locked():
            self.log('Stop monitoring, wait for worker and close paper spread first'); return
        if not self.is_logged_in or not self.instruments_df:
            self.log('Login first'); return
        self.available_futures = []
        self.current_month = self.next_month = self.next_next_month = None
        self.contract_spin.values = (); self.contract_spin.text = '--'
        super().load_contracts()
        syms = tuple(self.available_futures)
        self.leg_a.values = self.leg_b.values = syms
        self.leg_a.text = syms[0] if syms else '--'
        self.leg_b.text = syms[1] if len(syms)>1 else '--'
        self.pair_spin.text = 'Current - Next'
        self._apply_pair()

    def _apply_pair(self):
        if self._pair_locked():
            self.log('Pair locked: stop monitoring and close paper spread first'); return
        syms = self.available_futures
        mode = self.pair_spin.text
        if mode == 'Custom expiry':
            a,b = self.leg_a.text,self.leg_b.text
            if a not in syms or b not in syms or syms.index(a)>=syms.index(b):
                self.log('Select two distinct expiries in chronological order'); return
        else:
            i,j = self.PAIRS[mode]
            if len(syms)<=j:
                self.log('Selected pair unavailable'); return
            a,b = syms[i],syms[j]
        self.current_month,self.next_month = a,b
        self.next_next_month = syms[2] if len(syms)>2 else None
        self.pair_mode = mode
        self._pair_generation += 1
        self.last_quotes = {}; self._quote_at = 0; self.samples = []; self.last_trigger = None
        self.contract_spin.values = (a,b); self.contract_spin.text = a
        self.active_pair_label.text = f'Active: {a} / {b}'
        self.ticket_pair.text = self.active_pair_label.text
        self.diff_label.text = '--'; self.signal_label.text = 'WAIT'
        self.month_prices_label.text = 'Waiting for selected pair quotes'
        self.month_changes_label.text = 'Daily changes: --'; self.spread_label.text = 'Spread: --'
        self.third_price_label.text = 'Third month: --'; self.rule_label.text = 'Waiting for quotes'
        self._draw_chart()
        self.log(f'Applied {mode}: {a} / {b}')

    def _compute_diff(self):
        try:
            a,b = self.current_month,self.next_month
            if not a or not b: return None
            symbols = list(dict.fromkeys([a,b]+self.available_futures[:3]))
            data = self.kite.quote([f'MCX:{x}' for x in symbols])
            quotes = {}
            for x in symbols:
                row = data.get(f'MCX:{x}', {})
                p,c = float(row.get('last_price') or 0),float(row.get('ohlc',{}).get('close') or 0)
                if p>0 and c>0 and math.isfinite(p) and math.isfinite(c): quotes[x]=(p,c)
            if a not in quotes or b not in quotes: return None
            self.last_quotes = quotes
            p,c=quotes[a]; q,d=quotes[b]
            return (p-c)-(q-d)
        except Exception as exc:
            self.log(f'Quote error: {exc}'); return None

    def _monitor_loop(self):
        generation = self._pair_generation
        while self.monitoring and self.is_logged_in:
            diff = self._compute_diff()
            if diff is not None:
                snapshot = dict(self.last_quotes)
                Clock.schedule_once(lambda _,d=diff,q=snapshot,g=generation: self._tick(d,q) if g==self._pair_generation else None,0)
                try: log_price(diff)
                except Exception as exc: self.log(str(exc))
            else:
                Clock.schedule_once(lambda _,g=generation: self._stale() if g==self._pair_generation else None,0)
            time.sleep(2)

    @mainthread
    def _update_ui(self, diff):
        # Use canonical third-month display independently of active pair.
        super()._update_ui(diff)
        a,b=self.current_month,self.next_month
        if a in self.last_quotes and b in self.last_quotes:
            p,c=self.last_quotes[a]; q,d=self.last_quotes[b]
            self.spread_label.text=f'Selected spread: Rs {p-q:+.2f} | Previous: Rs {c-d:+.2f}'
        if len(self.available_futures)>=3:
            n,t=self.available_futures[1:3]
            if n in self.last_quotes and t in self.last_quotes:
                p,c=self.last_quotes[n]; q,d=self.last_quotes[t]; gap=(p-c)-(q-d)
                self.third_price_label.text=f'{t}: {q:.2f} | Change: {q-d:+.2f}'
                self.spread_label.text+=f'\nNext - third gap: {gap:+.2f}'+(' (review)' if abs(gap)>=8 else '')

    def _alert(self,title,message):
        a,b=self.current_month,self.next_month
        super()._alert(title, f'{a} / {b}\n{message}')

class TradingApp44(TradingApp42):
    """Opt-in live execution. Never retry an ambiguous submission automatically."""
    def _build_application(self):
        self.live_armed = False
        self.live_busy = False
        self.live_state = None
        self.live_fault = False
        self.live_realized = 0.0
        self.live_day = ''
        self.live_pnl = None
        self.live_rearm = True
        self.live_policy = dict(entry='Protected Limit', exit='Protected Limit', emergency='Protected Limit', ticks=2)
        self.live_path = os.path.join(self.user_data_dir, 'live_spread_v43.json')
        if os.path.exists(self.live_path):
            try:
                with open(self.live_path) as f: journal = json.load(f)
                self.live_state = journal.get('state')
                self.live_day = journal.get('day','')
                self.live_realized = float(journal.get('realized',0))
                self.live_fault = bool(self.live_state or journal.get('fault'))
            except Exception:
                self.live_fault = True
        return super()._build_application()

    def _dashboard_screen(self):
        screen = super()._dashboard_screen()
        self.mode_spin.values += ('Automatic live',)
        panel = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(6))
        panel.bind(minimum_height=panel.setter('height'))
        panel.add_widget(txt('V4.4 | LIVE EXECUTION',16,FG,True))
        self.protection = field('Maximum ticks beyond best price','2')
        panel.add_widget(txt('Limit price protection (ticks)')); panel.add_widget(self.protection)
        self.execution_spins = {}
        for purpose in ('entry','exit','emergency'):
            panel.add_widget(txt(purpose.title()+' order type'))
            spin=Spinner(text='Protected Limit', values=('Protected Limit','Market'), size_hint_y=None,height=dp(48))
            self.execution_spins[purpose]=spin
            panel.add_widget(spin)
        panel.add_widget(txt('Auto settings apply on ARM; recovery uses saved policy. Manual orders remain market orders.',12,MUTED,height=48))
        self.live_label = txt('Live execution disarmed',13,FG,height=100)
        panel.add_widget(self.live_label)
        panel.add_widget(action('ARM automatic live trading',self._arm_live,RED))
        panel.add_widget(action('Emergency square-off (managed pair)',self._square_live,RED))
        panel.add_widget(action('Reconcile / recover managed pair',self._square_live))
        self.dashboard_root.add_widget(panel,index=len(self.dashboard_root.children)-1)
        return screen

    def _save_live(self):
        tmp=self.live_path+'.tmp'
        with open(tmp,'w') as f:
            json.dump(dict(state=self.live_state,day=self.live_day,realized=self.live_realized,fault=self.live_fault),f)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp,self.live_path)

    def _live_message(self, message):
        Clock.schedule_once(lambda _,m=message: self._alert('Live execution',m),0)

    def _arm_live(self):
        if self.mode!='Automatic live' or not self.is_logged_in or self.halted or self.live_fault or self.live_state or self.live_busy or self.paper:
            self.log('Select Automatic live, login and resolve any recovery first'); return
        try:
            ticks=int(self.protection.text)
            if not 0<=ticks<=10: raise ValueError()
        except ValueError:
            self.log('Protection must be 0 to 10 ticks'); return
        policy={k:w.text for k,w in self.execution_spins.items()}
        policy['ticks']=ticks
        content=BoxLayout(orientation='vertical')
        content.add_widget(Label(text='Real money orders. Legs may fill separately.\nLosses can exceed stop loss. Arm for this session?'))
        pop=Popup(title='Confirm live automation',content=content,size_hint=(.95,.45))
        def arm():
            pop.dismiss()
            if self.mode=='Automatic live' and not self.halted and not self.live_fault and not self.live_state:
                self.live_policy=policy.copy(); self.live_ticks=ticks; self.live_armed=True; self.live_rearm=True
                self.log('Live automation armed; start monitoring')
        content.add_widget(action('ARM REAL MONEY',arm,RED)); content.add_widget(action('Cancel',pop.dismiss)); pop.open()

    def _pair_locked(self):
        return super()._pair_locked() or self.live_busy or bool(self.live_state) or self.live_fault or self.live_armed

    def _change_mode(self,widget,value):
        if self.live_state or self.live_busy or self.live_fault:
            if value!=self.mode: widget.text=self.mode
            return
        self.live_armed=False
        super()._change_mode(widget,value)

    def _logout(self):
        if self.live_state or self.live_busy or self.live_fault:
            self.log('Recover managed live positions before logout'); return
        self.live_armed=False; super()._logout()

    def stop_monitoring(self):
        self.live_armed=False
        if self.live_state or self.live_busy:
            self.log('Live exposure may remain; use emergency square-off')
        super().stop_monitoring()

    def _halt(self):
        self.live_armed=False
        super()._halt()
        if self.live_state: self._square_live()

    def _confirm_orders(self,title,legs):
        if self.live_state or self.live_busy or self.live_fault or self.live_armed:
            self.log('Manual orders blocked during managed live execution'); return
        super()._confirm_orders(title,legs)

    def _submit_legs(self,legs):
        if self.live_state or self.live_busy or self.live_fault or self.live_armed: return
        super()._submit_legs(legs)

    def _live_positions(self,client,symbols):
        rows=client.positions()['net']
        out={s:(0,0.) for s in symbols}
        for r in rows:
            s=r.get('tradingsymbol')
            if r.get('exchange')=='MCX' and s in out:
                if r.get('product')!='NRML':
                    if int(r['quantity']): raise RuntimeError('Other product exposure on '+s)
                    continue
                out[s]=(int(r['quantity']),float(r['pnl']))
        return out

    def _live_pending(self,client,symbols):
        return [r for r in client.orders() if r.get('exchange')=='MCX' and r.get('tradingsymbol') in symbols and r.get('status') not in ('COMPLETE','CANCELLED','REJECTED')]

    def _protected_order(self,client,symbol,side,qty,purpose="entry"):
        policy=self.live_state.get("policy", self.live_policy)
        order_type=policy.get(purpose,"Protected Limit")
        if order_type not in ("Market","Protected Limit"): raise RuntimeError("Invalid execution policy")
        data=dict(exchange='MCX',tradingsymbol=symbol,transaction_type=side,quantity=qty,product='NRML',validity='IOC',order_type='MARKET')
        if order_type=='Protected Limit':
            from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
            meta=next(r for r in self.instruments_df if r.get('tradingsymbol')==symbol)
            tick=Decimal(str(meta['tick_size']))
            if tick<=0: raise RuntimeError('Invalid tick size')
            row=client.quote(['MCX:'+symbol])['MCX:'+symbol]
            depth=row['depth']['sell' if side=='BUY' else 'buy']
            best=Decimal(str(depth[0]['price']))
            if best<=0 or int(depth[0]['quantity'])<=0: raise RuntimeError('No executable depth')
            # Reject stale exchange timestamps; Kite timestamps are exchange-local.
            stamp=dt.fromisoformat(str(row['timestamp']))
            now=dt.now(timezone(timedelta(hours=5,minutes=30)))
            if stamp.tzinfo is None: stamp=stamp.replace(tzinfo=now.tzinfo)
            if abs((now-stamp).total_seconds())>15: raise RuntimeError('Stale exchange quote')
            ticks=int(policy.get('ticks',2))
            if not 0<=ticks<=10: raise RuntimeError('Invalid protection ticks')
            raw=best+tick*ticks if side=='BUY' else best-tick*ticks
            price=(raw/tick).to_integral_value(rounding=ROUND_FLOOR if side=='BUY' else ROUND_CEILING)*tick
            if price<=0: raise RuntimeError('Invalid limit price')
            data.update(order_type='LIMIT',price=str(price))
        import uuid
        tag='mcx'+uuid.uuid4().hex[:16]
        self.live_state['pending_tag']=tag; self._save_live()
        # IOC avoids leaving a working limit order after the immediate attempt.
        result=client._request('POST','/orders/regular',data=dict(data,tag=tag))
        oid=str(result['order_id'])
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            history=client._request('GET','/orders/'+oid)
            r=history[-1]
            if r['status'] in ('COMPLETE','CANCELLED','REJECTED'):
                filled=int(r['filled_quantity'])
                if not 0<=filled<=qty: raise RuntimeError('Unexpected fill quantity')
                self.live_state['pending_tag']=None; self._save_live()
                return filled
            time.sleep(.5)
        raise RuntimeError('Order status unresolved; no automatic retry: '+oid)

    def _recover_live(self,client,purpose="emergency"):
        state=self.live_state
        symbols=state['symbols']
        if state.get('pending_tag'):
            matches=[r for r in client.orders() if r.get('tag')==state['pending_tag']]
            if len(matches)!=1 or matches[0]['status'] not in ('COMPLETE','CANCELLED','REJECTED'):
                raise RuntimeError('Ambiguous order: verify in Kite before recovery')
            state['pending_tag']=None; self._save_live()
        if self._live_pending(client,symbols): raise RuntimeError('Pending broker orders; cancel/reconcile in Kite')
        pos=self._live_positions(client,symbols)
        for s,expected in zip(symbols,state['quantities']):
            q=pos[s][0]
            if abs(q)>abs(expected) or q*expected<0: raise RuntimeError('Unexpected external position; manual reconciliation required')
        for s in symbols:
            q=self._live_positions(client,symbols)[s][0]
            if q: self._protected_order(client,s,'SELL' if q>0 else 'BUY',abs(q),purpose)
        pos=self._live_positions(client,symbols)
        if any(q for q,p in pos.values()) or self._live_pending(client,symbols): raise RuntimeError('Residual exposure: retry recovery or close in Kite')
        pnl=sum(pos[s][1]-state['baseline'][s] for s in symbols)
        self.live_realized+=pnl; self.live_state=None; self.live_fault=False; self.live_pnl=None; self._save_live()
        self._live_message(f'Broker confirms flat; gross P&L Rs {pnl:.2f}')

    def _square_live(self):
        self.live_armed=False
        if self.live_busy: self.log('Execution in progress; recovery requested'); self.live_recovery_requested=True; return
        if not self.is_logged_in or not self.live_state:
            self.log('No recoverable managed pair or login required; check Kite'); return
        self._launch_live('emergency')

    def _launch_live(self,operation):
        if self.live_busy or not self.is_logged_in: return
        self.live_busy=True; self.live_recovery_requested=False
        client=KiteConnect(self.kite.api_key); client.set_access_token(self.kite.access_token)
        symbols=[self.current_month,self.next_month]
        try:
            quantities=[self._lots_to_units(symbols[0],self._order_quantity()),-self._lots_to_units(symbols[1],self._order_quantity())] if operation=='open' else []
        except Exception as e:
            self.live_busy=False; self.log(str(e)); return
        threading.Thread(target=self._live_worker,args=(client,operation,symbols,quantities),daemon=True).start()

    def _live_worker(self,client,operation,symbols,quantities):
        try:
            if operation=='open':
                today=dt.now(timezone(timedelta(hours=5,minutes=30))).date().isoformat()
                if today!=self.live_day: self.live_day=today; self.live_realized=0.
                if self.live_realized<=-self.daily_limit: raise RuntimeError('Daily managed loss limit reached')
                if not self.live_armed or self.halted: return
                pos=self._live_positions(client,symbols)
                if any(q for q,p in pos.values()) or self._live_pending(client,symbols): raise RuntimeError('Existing broker exposure or orders; entry blocked')
                self.live_state=dict(symbols=symbols,quantities=quantities,baseline={s:pos[s][1] for s in symbols},pending_tag=None,opened=False,policy=self.live_policy.copy())
                self._save_live()
                for s,q in zip(symbols,quantities):
                    if not self.live_armed or self.halted or self.live_recovery_requested: break
                    if self._protected_order(client,s,'BUY' if q>0 else 'SELL',abs(q))!=abs(q): break
                pos=self._live_positions(client,symbols)
                if [pos[s][0] for s in symbols]!=quantities or self.halted or not self.live_armed or self.live_recovery_requested:
                    self.live_armed=False; self._recover_live(client); return
                self.live_state['opened']=True; self._save_live(); self._live_message('Both live legs verified at broker')
            elif operation in ('close','emergency'): self._recover_live(client,'exit' if operation=='close' else 'emergency')
            elif operation=='mark':
                state=self.live_state; syms=state['symbols']; pos=self._live_positions(client,syms)
                if [pos[s][0] for s in syms]!=state['quantities']: raise RuntimeError('Broker position mismatch; reconcile')
                self.live_pnl=sum(pos[s][1]-state['baseline'][s] for s in syms)
                if self.live_pnl>=self.target or self.live_pnl<=-self.stop_loss or self.live_realized+self.live_pnl<=-self.daily_limit or self.live_recovery_requested:
                    self._recover_live(client,'emergency' if self.live_recovery_requested else 'exit')
        except Exception as e:
            self.live_armed=False; self.live_fault=bool(self.live_state); self.live_pnl=None
            try: self._save_live()
            except Exception: pass
            self._live_message('Execution stopped: '+str(e)+'; verify broker orders and positions')
        finally:
            self.live_busy=False
            if self.live_recovery_requested and self.live_state:
                Clock.schedule_once(lambda _: self._square_live(),0)

    def _tick(self,diff,quotes):
        super()._tick(diff,quotes)
        self.live_label.text=f'Live: {"RECOVERY REQUIRED" if self.live_fault else "ARMED" if self.live_armed else "DISARMED"} | Gross P&L: {self.live_pnl if self.live_pnl is not None else "--"}\nManaged realized: Rs {self.live_realized:.2f}'
        if self.live_busy or self.live_fault or not self.monitoring or self.halted: return
        if self.live_state and self.live_state.get('opened'):
            self._launch_live('close' if diff>self.exit_threshold else 'mark'); return
        if not self.live_armed or self.mode!='Automatic live': return
        if diff>=self.entry_threshold: self.live_rearm=True
        if self.live_rearm and diff<self.entry_threshold and self._ng_entry_rule(diff)[0]:
            self.live_rearm=False; self._launch_live('open')

if __name__ == '__main__':
    TradingApp44().run()
