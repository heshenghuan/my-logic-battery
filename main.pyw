"""罗技 MX Anywhere 2 电量托盘监控。

双击运行（.pyw 用 pythonw 启动，不会留下控制台窗口）。
电量低于设定阈值时通过系统通知提醒，托盘图标颜色随电量变化。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from functools import lru_cache

import pystray
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from battery import BatteryReading, read_battery

try:
    from pystray._util import win32 as _win32
    from pystray._win32 import Icon as _Win32Icon

    class _MenuOnClickIcon(_Win32Icon):
        """左键与右键都弹出同一个菜单。

        pystray 默认只在 WM_RBUTTONUP 时弹菜单，左键走 default 菜单项。
        这里把左键消息改写成右键消息，复用父类完整的弹菜单逻辑。
        """

        def _on_notify(self, wparam, lparam):
            if lparam == _win32.WM_LBUTTONUP:
                lparam = _win32.WM_RBUTTONUP
            super()._on_notify(wparam, lparam)

except (ImportError, AttributeError):
    _MenuOnClickIcon = None

APP_NAME = "LogicBattery"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(BASE_DIR, "main.pyw")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SHORTCUT_PATH = os.path.join(
    os.environ.get("APPDATA", ""),
    r"Microsoft\Windows\Start Menu\Programs\Startup",
    f"{APP_NAME}.lnk",
)

DEFAULT_CONFIG = {
    # 蓝牙 BLE 地址，形如 ECF2A3FCBAB3；留空则按 device_name 匹配
    "device_address": "",
    "device_name": "MX Anywhere 2",
    "threshold": 20,
    "interval_minutes": 5,
    "icon_style": "both",
}
THRESHOLD_CHOICES = (10, 15, 20, 25, 30, 40, 50)
ICON_STYLES = (("both", "电池 + 鼠标"), ("battery", "仅电池"))

ICON_SIZE = 64
ICON_FONT_PATH = r"C:\Windows\Fonts\segmdl2.ttf"
MOUSE_GLYPH = "\uE962"
BATTERY_GLYPHS_START = 0xE850  # E850 空电 ~ E859 九格；E85A 起是带插头的充电图标
BATTERY_XY = (2, 30)
MOUSE_XY = (40, 12)
GAP_FILTER = 5  # 鼠标周围擦出约 2px 透明缺口
COLOR_OK = (46, 204, 113)
COLOR_LOW = (243, 156, 18)
COLOR_CRITICAL = (231, 76, 60)
COLOR_UNKNOWN = (150, 150, 150)

_battery_font = ImageFont.truetype(ICON_FONT_PATH, 60)
_battery_solo_font = ImageFont.truetype(ICON_FONT_PATH, 76)
_mouse_font = ImageFont.truetype(ICON_FONT_PATH, 22)


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
            config.update(json.load(fp))
    except (OSError, ValueError):
        save_config(config)
    return config


def save_config(config: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
            json.dump(config, fp, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _glyph_layer(text: str, font: ImageFont.FreeTypeFont, color) -> Image.Image:
    canvas = Image.new("RGBA", (ICON_SIZE * 2, ICON_SIZE * 2), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).text((0, 0), text, font=font, fill=color)
    box = canvas.getbbox()
    return canvas.crop(box) if box else canvas


@lru_cache(maxsize=32)
def _icon_art(bars: int, color: tuple[int, int, int], icon_style: str) -> Image.Image:
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))

    if icon_style == "battery":
        battery = _glyph_layer(
            chr(BATTERY_GLYPHS_START + bars), _battery_solo_font, color
        )
        img.paste(battery, (
            (ICON_SIZE - battery.width) // 2, (ICON_SIZE - battery.height) // 2
        ), battery)
        return img

    battery = _glyph_layer(chr(BATTERY_GLYPHS_START + bars), _battery_font, color)
    mouse = _glyph_layer(MOUSE_GLYPH, _mouse_font, color)

    img.paste(battery, BATTERY_XY, battery)

    # 先擦掉鼠标周围的电池像素，缩到 16px 时两个图形才不会粘成一团
    offset = (GAP_FILTER - 1) // 2
    erase = Image.new("L", (ICON_SIZE, ICON_SIZE), 0)
    erase.paste(
        mouse.getchannel("A").filter(ImageFilter.MaxFilter(GAP_FILTER)),
        (MOUSE_XY[0] - offset, MOUSE_XY[1] - offset),
    )
    img.putalpha(ImageChops.subtract(img.getchannel("A"), erase))
    img.paste(mouse, MOUSE_XY, mouse)
    return img


def battery_image(level: int | None, threshold: int, icon_style: str) -> Image.Image:
    if level is None:
        color = COLOR_UNKNOWN
    elif level <= 10:
        color = COLOR_CRITICAL
    elif level <= threshold:
        color = COLOR_LOW
    else:
        color = COLOR_OK

    bars = 0 if level is None else min(9, max(0, round(level / 10)))
    return _icon_art(bars, color, icon_style)


def autostart_command() -> tuple[str, str]:
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pythonw if os.path.exists(pythonw) else sys.executable
    return exe, f'"{SCRIPT_PATH}"'


def _ps_quote(value: str) -> str:
    return value.replace("'", "''")


def _run_powershell(script: str) -> bool:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            capture_output=True,
            timeout=20,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def create_autostart_shortcut() -> bool:
    target, arguments = autostart_command()
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$shell = New-Object -ComObject WScript.Shell\n"
        f"$shortcut = $shell.CreateShortcut('{_ps_quote(SHORTCUT_PATH)}')\n"
        f"$shortcut.TargetPath = '{_ps_quote(target)}'\n"
        f"$shortcut.Arguments = '{_ps_quote(arguments)}'\n"
        f"$shortcut.WorkingDirectory = '{_ps_quote(BASE_DIR)}'\n"
        f"$shortcut.Description = '{APP_NAME}'\n"
        "$shortcut.Save()\n"
    )
    return _run_powershell(script) and os.path.exists(SHORTCUT_PATH)


def remove_autostart_shortcut() -> bool:
    try:
        os.remove(SHORTCUT_PATH)
        return True
    except OSError:
        return not os.path.exists(SHORTCUT_PATH)


class Monitor:
    def __init__(self) -> None:
        self.config = load_config()
        self.level: int | None = None
        self.source = ""
        self.detail = ""
        self.updated: datetime | None = None
        self.alerted = False
        self.icon: pystray.Icon | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- 托盘 ---

    def run(self) -> None:
        icon_class = _MenuOnClickIcon or pystray.Icon
        self.icon = icon_class(
            APP_NAME,
            battery_image(None, self.config["threshold"], self.config["icon_style"]),
            "正在读取电量…",
            self._build_menu(),
        )
        self.icon.run(setup=self._on_setup)

    def _on_setup(self, icon: pystray.Icon) -> None:
        icon.visible = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._refresh()
            self._stop.wait(max(1, self.config["interval_minutes"]) * 60)

    def _refresh(self) -> None:
        reading = read_battery(
            self.config["device_address"], self.config["device_name"]
        )
        self._apply(reading)
        self._notify_if_needed(reading)

    def _apply(self, reading: BatteryReading) -> None:
        self.level = reading.level
        self.source = reading.source
        self.detail = reading.detail
        self.updated = datetime.now()

        if self.icon is not None:
            self.icon.icon = battery_image(
                reading.level, self.config["threshold"], self.config["icon_style"]
            )
            self.icon.title = self._status_text()
            self.icon.update_menu()

    def _status_text(self) -> str:
        name = self.config["device_name"]
        if self.level is not None:
            return f"{name} · {self.level}% · {self.source}"
        if self.updated is None:
            return "正在读取电量…"
        return f"{name} · 未连接（{self.detail}）"

    def _notify_if_needed(self, reading: BatteryReading) -> None:
        if reading.level is None:
            return
        threshold = self.config["threshold"]
        if reading.level <= threshold and not self.alerted:
            self.alerted = True
            self._notify(
                f"当前电量 {reading.level}%，已低于设定的 {threshold}%。\n"
                f"连接方式：{reading.source}，请及时充电。",
                f"{self.config['device_name']} 电量偏低",
            )
        elif reading.level > threshold + 5:
            self.alerted = False

    def _notify(self, message: str, title: str) -> None:
        if self.icon is None:
            return
        try:
            self.icon.notify(message, title)
        except Exception:
            pass

    # --- 菜单动作 ---

    def _build_menu(self) -> pystray.Menu:
        threshold_menu = pystray.Menu(
            *[
                pystray.MenuItem(
                    f"{value}%",
                    self._make_set_threshold(value),
                    checked=self._make_threshold_checked(value),
                    radio=True,
                )
                for value in THRESHOLD_CHOICES
            ]
        )
        style_menu = pystray.Menu(
            *[
                pystray.MenuItem(
                    label,
                    self._make_set_icon_style(value),
                    checked=self._make_style_checked(value),
                    radio=True,
                )
                for value, label in ICON_STYLES
            ]
        )
        return pystray.Menu(
            pystray.MenuItem(lambda item: self._status_text(), None, enabled=False),
            pystray.MenuItem(
                "立即刷新", self._on_refresh, default=_MenuOnClickIcon is None
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("电量低于", threshold_menu),
            pystray.MenuItem("图标样式", style_menu),
            pystray.MenuItem(
                "开机自启",
                self._on_toggle_autostart,
                checked=lambda item: os.path.exists(SHORTCUT_PATH),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._on_quit),
        )

    def _make_set_threshold(self, value: int):
        def action(icon: pystray.Icon, item: pystray.MenuItem) -> None:
            self.config["threshold"] = value
            save_config(self.config)
            if self.level is not None and self.level > value + 5:
                self.alerted = False
            if self.icon is not None:
                self.icon.icon = battery_image(
                    self.level, value, self.config["icon_style"]
                )
                self.icon.update_menu()

        return action

    def _make_threshold_checked(self, value: int):
        return lambda item: self.config["threshold"] == value

    def _make_set_icon_style(self, value: str):
        def action(icon: pystray.Icon, item: pystray.MenuItem) -> None:
            self.config["icon_style"] = value
            save_config(self.config)
            if self.icon is not None:
                self.icon.icon = battery_image(
                    self.level, self.config["threshold"], value
                )
                self.icon.update_menu()

        return action

    def _make_style_checked(self, value: str):
        return lambda item: self.config["icon_style"] == value

    def _on_refresh(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        threading.Thread(target=self._refresh, daemon=True).start()

    def _on_toggle_autostart(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if os.path.exists(SHORTCUT_PATH):
            ok = remove_autostart_shortcut()
            message = "已取消开机自启"
        else:
            ok = create_autostart_shortcut()
            message = "已设置开机自启"
        if not ok:
            message = "设置开机自启失败，请检查权限"
        self._notify(message, APP_NAME)
        icon.update_menu()

    def _on_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._stop.set()
        icon.stop()


def main() -> None:
    monitor = Monitor()
    if "--once" in sys.argv:
        reading = read_battery(
            monitor.config["device_address"], monitor.config["device_name"]
        )
        print(reading)
        return
    monitor.run()


if __name__ == "__main__":
    main()
