"""鼠标电量托盘监控。"""

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

from battery import (
    BatteryReading,
    Candidate,
    MouseDevice,
    device_from_json,
    read_any,
    scan_all,
)

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
    # 按列表顺序轮询；2.4G 设备建议排在蓝牙前面
    "devices": [],
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


def _build_devices(config: dict) -> list[MouseDevice]:
    devices: list[MouseDevice] = []
    for item in config.get("devices") or []:
        try:
            device = device_from_json(item)
        except (ValueError, KeyError):
            continue
        if not (device.name or device.stable_id):
            continue
        devices.append(device)
    return devices


_LEGACY_CONFIG_KEYS = ("device_name", "device_address", "alias")


def _is_legacy_config(stored: dict) -> bool:
    devices = stored.get("devices")
    if isinstance(devices, dict):
        return True
    if isinstance(devices, list):
        return any(
            isinstance(item, dict) and ("protocol" in item or "connection" in item)
            for item in devices
        )
    return any(key in stored for key in _LEGACY_CONFIG_KEYS)


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
            stored = json.load(fp)
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}

    if _is_legacy_config(stored):
        print(
            "[LogicBattery] 检测到旧版 config.json（分组 / protocol / connection / "
            "device_name 等格式），当前版本不再自动迁移，已按空设备列表处理。\n"
            "  如需保留设备，请删除 config.json 后重新从托盘菜单「发现设备」绑定。",
            file=sys.stderr,
        )

    config = dict(DEFAULT_CONFIG)
    config.update(stored)
    if not isinstance(config["devices"], list):
        config["devices"] = []
    return config


def save_config(config: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
            json.dump(config, fp, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _config_mtime() -> float:
    try:
        return os.path.getmtime(CONFIG_PATH)
    except OSError:
        return 0.0


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
        self.devices = _build_devices(self.config)
        self._config_mtime = _config_mtime()
        self.level: int | None = None
        self.source = ""
        self.detail = ""
        self.device_name = ""
        self.updated: datetime | None = None
        self.alerted = False
        self.active: MouseDevice | None = None
        self.discovered: list[Candidate] = []
        self.scanning = False
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
        # 先扫一次，打开「发现设备」时就能看到候选
        self._start_scan()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._refresh()
            self._stop.wait(max(1, self.config["interval_minutes"]) * 60)

    def _refresh(self) -> None:
        self._reload_config_if_changed()

        if not self.devices:
            self.active = None
            self._apply(BatteryReading(None, "", "", "未绑定设备"))
            return

        # 已经锁定设备时只查它；读不到就认为掉线，重新找一遍
        if self.active is not None:
            reading = self.active.read()
            if reading.ok:
                self._apply(reading)
                self._notify_if_needed(reading)
                return
            self.active = None

        self.active, reading = read_any(self.devices)
        self._apply(reading)
        self._notify_if_needed(reading)

    def _has_devices(self) -> bool:
        return bool(self.devices)

    def _reload_config_if_changed(self) -> None:
        """配置文件被手动改过时重新加载，无需重启程序。"""
        mtime = _config_mtime()
        if mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        self.config = load_config()
        self.devices = _build_devices(self.config)
        self._config_mtime = _config_mtime()
        # 设备条目可能被改过，之前的锁定作废，按新配置重新找
        self.active = None

    def _apply(self, reading: BatteryReading) -> None:
        self.level = reading.level
        self.source = reading.source
        self.detail = reading.detail
        self.device_name = reading.device_name
        self.updated = datetime.now()

        if self.icon is not None:
            self.icon.icon = battery_image(
                reading.level, self.config["threshold"], self.config["icon_style"]
            )
            self.icon.title = self._status_text()
            self.icon.update_menu()

    def _status_text(self) -> str:
        if not self._has_devices():
            return "未绑定设备 · 菜单「发现设备」"
        name = self.device_name or "鼠标"
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
                f"{self.device_name or APP_NAME} 电量偏低",
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
            pystray.MenuItem(
                lambda item: "发现设备（扫描中…）" if self.scanning else "发现设备",
                pystray.Menu(lambda: self._discovery_items()),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("电量低于", threshold_menu),
            pystray.MenuItem("图标样式", style_menu),
            pystray.MenuItem(
                "开机自启",
                self._on_toggle_autostart,
                checked=lambda item: os.path.exists(SHORTCUT_PATH),
            ),
            pystray.MenuItem("重启程序", self._on_restart),
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

    # --- 设备发现与绑定 ---

    def _discovery_items(self):
        yield pystray.MenuItem("重新扫描", self._on_scan)
        if self.scanning:
            yield pystray.MenuItem("扫描中…", None, enabled=False)
            return
        if not self.discovered:
            yield pystray.MenuItem("未发现可用设备", None, enabled=False)
            return
        rows = self._unbound_rows()
        if not rows:
            yield pystray.MenuItem("设备均已绑定", None, enabled=False)
            return
        yield pystray.Menu.SEPARATOR
        for row in rows:
            yield pystray.MenuItem(
                self._row_label(row), self._make_bind(row)
            )

    def _unbound_rows(self) -> list[Candidate]:
        """过滤掉已经写进配置的设备。"""
        bound = {device.uid for device in self.devices}
        return [row for row in self.discovered if row.device.uid not in bound]

    def _row_label(self, row: Candidate) -> str:
        device = row.device
        name = device.name or "未知设备"
        for prefix in ("Wireless Mobile Mouse ", "Logitech "):
            if name.startswith(prefix):
                name = name[len(prefix):]
        if len(name) > 34:
            name = name[:33] + "…"
        suffix = f" · {row.level}%" if row.level is not None else ""
        return f"{name} · {device.channel_label}{suffix}"

    def _make_bind(self, row: Candidate):
        def action(icon: pystray.Icon, item: pystray.MenuItem) -> None:
            threading.Thread(target=self._bind, args=(row,), daemon=True).start()

        return action

    def _on_scan(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._start_scan(notify=True)

    def _start_scan(self, notify: bool = False) -> None:
        if self.scanning:
            return
        self.scanning = True
        if self.icon is not None:
            self.icon.update_menu()
        threading.Thread(
            target=self._scan_worker, args=(notify,), daemon=True
        ).start()

    def _scan_worker(self, notify: bool) -> None:
        try:
            rows = scan_all(only_bindable=True)
        except Exception:
            rows = []
        self.discovered = rows
        self.scanning = False
        if self.icon is not None:
            self.icon.update_menu()
        if notify:
            if rows:
                self._notify(
                    f"发现 {len(rows)} 个设备，请重新打开菜单选择。", "扫描完成"
                )
            else:
                self._notify(
                    "没有发现可用设备，请确认鼠标已开机并连接。", "未发现设备"
                )

    def _bind(self, row: Candidate) -> None:
        device = row.device
        if not (device.stable_id or device.name):
            return

        if not any(existing.uid == device.uid for existing in self.devices):
            self.config["devices"].append(device.to_json())
            save_config(self.config)

        # 保存后重新加载，用规范化后的条目锁定设备，避免与配置文件不一致
        self.config = load_config()
        self._config_mtime = _config_mtime()
        self.devices = _build_devices(self.config)
        self.active = next(
            (item for item in self.devices if item.uid == device.uid), None
        )
        self._refresh()
        if self.icon is not None:
            self.icon.update_menu()

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

    def _on_restart(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        try:
            subprocess.Popen(
                [sys.executable, os.path.join(BASE_DIR, "main.pyw")],
                cwd=BASE_DIR,
                close_fds=True,
                creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        except OSError:
            self._notify("重启失败，请手动启动程序。", "重启失败")
            return
        self._on_quit(icon, item)

    def _on_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._stop.set()
        icon.stop()


def main() -> None:
    if "--scan" in sys.argv:
        rows = list(scan_all())
        for row in rows:
            device = row.device
            if device.type == "bluetooth":
                if not row.online:
                    state = "已断开"
                elif row.level is None:
                    state = "未上报电量"
                else:
                    state = f"电量 {row.level}%"
                print(f"蓝牙  地址={device.stable_id or '-'}  名称={device.name}  {state}")
            else:
                print(f"{device.channel_label}  单元ID={device.stable_id or '-'}  "
                      f"名称={device.name}  电量 {row.level}%")
        if rows:
            print("以上标识为真实设备信息，请勿公开发布。")
        return

    monitor = Monitor()
    if "--once" in sys.argv:
        print(read_any(monitor.devices)[1])
        return
    monitor.run()


if __name__ == "__main__":
    main()
