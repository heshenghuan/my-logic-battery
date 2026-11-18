"""读取罗技 MX Anywhere 2 的电量。

蓝牙链路
    Windows 会把 BLE HID 设备的电量挂在设备节点属性
    DEVPKEY_Bluetooth_Battery ({104EA319-6EE2-4701-BD47-8DDBF425BBE5}, 2) 上。
    这里用 SetupAPI 直接读，避免每次轮询都启动一次 PowerShell。

2.4G(Unifying) 链路
    接收器不暴露任何系统属性，只能通过 HID++ 2.0 报文查询电量，
    实现见 ReceiverBatterySource。鼠标需切到接收器所在通道。
"""

from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes
from dataclasses import dataclass

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)

DIGCF_PRESENT = 0x02
DEVPROP_TYPE_UINT32 = 0x03
DEVPROP_TYPE_BYTE = 0x11
DEVPROP_TYPE_STRING = 0x12

GUID_DEVCLASS_BLUETOOTH = uuid.UUID("{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}")
DEVPKEY_BLUETOOTH_BATTERY = uuid.UUID("{104ea319-6ee2-4701-bd47-8ddbf425bbe5}"), 2
DEVPKEY_DEVICE_FRIENDLY_NAME = uuid.UUID("{a45c254e-df1c-4efd-8020-67d146a850e0}"), 14


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_uuid(cls, value: uuid.UUID) -> "GUID":
        guid = cls()
        ctypes.memmove(ctypes.byref(guid), value.bytes_le, 16)
        return guid


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD
]
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE

setupapi.SetupDiEnumDeviceInfo.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)
]
setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL

setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.LPWSTR,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]
setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL

setupapi.SetupDiGetDevicePropertyW.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(DEVPROPKEY),
    ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(ctypes.c_ubyte), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
]
setupapi.SetupDiGetDevicePropertyW.restype = wintypes.BOOL

setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL


@dataclass(frozen=True)
class BatteryReading:
    level: int | None
    source: str
    device_name: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.level is not None


class _DeviceEnumerator:
    """枚举某个设备类下的在线设备，离开 with 块时释放句柄。"""

    def __init__(self, class_guid: uuid.UUID) -> None:
        self._guid = GUID.from_uuid(class_guid)
        self._handle = None

    def __enter__(self) -> "_DeviceEnumerator":
        handle = setupapi.SetupDiGetClassDevsW(
            ctypes.byref(self._guid), None, None, DIGCF_PRESENT
        )
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        return self

    def __exit__(self, *exc) -> None:
        if self._handle:
            setupapi.SetupDiDestroyDeviceInfoList(self._handle)
            self._handle = None

    def __iter__(self):
        index = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiEnumDeviceInfo(self._handle, index, ctypes.byref(info)):
                return
            yield info
            index += 1


def _instance_id(handle, info) -> str:
    buf = ctypes.create_unicode_buffer(512)
    needed = wintypes.DWORD()
    if setupapi.SetupDiGetDeviceInstanceIdW(
        handle, ctypes.byref(info), buf, len(buf), ctypes.byref(needed)
    ):
        return buf.value
    return ""


def _get_property(handle, info, key: tuple[uuid.UUID, int]):
    prop_key = DEVPROPKEY()
    prop_key.fmtid = GUID.from_uuid(key[0])
    prop_key.pid = key[1]

    prop_type = wintypes.ULONG()
    buf = (ctypes.c_ubyte * 512)()
    needed = wintypes.DWORD()
    ok = setupapi.SetupDiGetDevicePropertyW(
        handle, ctypes.byref(info), ctypes.byref(prop_key),
        ctypes.byref(prop_type), buf, ctypes.sizeof(buf),
        ctypes.byref(needed), 0,
    )
    if not ok:
        return None, None
    return prop_type.value, bytes(buf[: needed.value])


class BluetoothBatterySource:
    """通过 Windows 设备属性读取蓝牙 BLE 鼠标电量。"""

    source_name = "蓝牙"

    def __init__(self, address: str, friendly_name: str) -> None:
        self.address = address.strip().upper()
        self.friendly_name = friendly_name.strip()

    def read(self) -> BatteryReading:
        try:
            with _DeviceEnumerator(GUID_DEVCLASS_BLUETOOTH) as devices:
                by_name = None
                for info in devices:
                    instance_id = _instance_id(devices._handle, info)
                    if not instance_id.upper().startswith("BTHLE\\DEV_"):
                        continue

                    name = self._friendly_name(devices._handle, info)
                    if self.address and self.address in instance_id.upper():
                        return self._reading(devices._handle, info, name)
                    if by_name is None and self.friendly_name and \
                            self.friendly_name.lower() in name.lower():
                        by_name = self._reading(devices._handle, info, name)

                if by_name is not None:
                    return by_name
        except OSError as exc:
            return BatteryReading(None, self.source_name, self.friendly_name,
                                  f"枚举蓝牙设备失败: {exc}")

        return BatteryReading(None, self.source_name, self.friendly_name,
                              "未发现已连接的蓝牙设备")

    def _friendly_name(self, handle, info) -> str:
        prop_type, data = _get_property(handle, info, DEVPKEY_DEVICE_FRIENDLY_NAME)
        if prop_type == DEVPROP_TYPE_STRING and data:
            return data.decode("utf-16-le", "ignore").rstrip("\x00")
        return ""

    def _reading(self, handle, info, name: str) -> BatteryReading:
        prop_type, data = _get_property(handle, info, DEVPKEY_BLUETOOTH_BATTERY)
        if prop_type in (DEVPROP_TYPE_BYTE, DEVPROP_TYPE_UINT32) and data:
            level = int.from_bytes(data, "little")
            if 0 <= level <= 100:
                return BatteryReading(level, self.source_name, name or self.friendly_name)
        return BatteryReading(None, self.source_name, name or self.friendly_name,
                              "设备未上报电量")


class ReceiverBatterySource:
    """通过 Unifying 接收器读取 2.4G 电量（HID++ 2.0）。

    接收器不暴露系统属性，只能发 HID++ 报文：
    先用 root.getFeature(0x1000) 取电池功能索引，再调 getBatteryLevelStatus。
    报文走 usage 0xFF00 / 2 的 20 字节长报告，多字节字段按大端序。
    鼠标需切到接收器所在通道，否则对应设备索引不会回包。
    """

    source_name = "2.4G"

    VENDOR_ID = 0x046D
    PRODUCT_IDS = (0xC52B, 0xC532)
    USAGE_PAGE = 0xFF00
    USAGE_LONG = 2
    REPORT_ID = 0x11
    REPORT_LENGTH = 20
    SW_ID = 0x01
    BATTERY_FEATURE = 0x1000
    ERROR_MARKER = 0x8F
    TIMEOUT_MS = 400
    _cached_index: int | None = None

    def __init__(self, friendly_name: str) -> None:
        self.friendly_name = friendly_name

    def read(self) -> BatteryReading:
        try:
            import hid  # type: ignore
        except ImportError:
            return BatteryReading(None, self.source_name, self.friendly_name,
                                  "缺少 hidapi 依赖")

        path = self._long_channel_path(hid)
        if not path:
            return BatteryReading(None, self.source_name, self.friendly_name,
                                  "未发现 Unifying 接收器")

        try:
            device = hid.device()
            device.open_path(path)
            device.set_nonblocking(0)
        except OSError:
            return BatteryReading(None, self.source_name, self.friendly_name,
                                  "接收器打不开，可能被其他程序独占")

        try:
            while device.read(64, 30):
                pass
            level = self._query_level(device)
        finally:
            device.close()

        if level is None:
            return BatteryReading(None, self.source_name, self.friendly_name,
                                  "接收器上未检测到已连接的设备")
        return BatteryReading(level, self.source_name, self.friendly_name)

    def _long_channel_path(self, hid_module) -> bytes | None:
        for info in hid_module.enumerate(self.VENDOR_ID, 0):
            if info["product_id"] in self.PRODUCT_IDS \
                    and info["usage_page"] == self.USAGE_PAGE \
                    and info["usage"] == self.USAGE_LONG:
                return info["path"]
        return None

    def _query_level(self, device) -> int | None:
        indexes = list(range(1, 7))
        if self._cached_index in indexes:
            indexes.remove(self._cached_index)
            indexes.insert(0, self._cached_index)

        for index in indexes:
            level = self._read_level(device, index)
            if level is not None:
                self._cached_index = index
                return level

        self._cached_index = None
        return None

    def _request(self, device, index: int, body: list[int]) -> list[int] | None:
        data = [self.REPORT_ID, index] + body
        data.extend([0] * (self.REPORT_LENGTH - len(data)))
        if device.write(data) < 0:
            return None

        while True:
            response = device.read(64, self.TIMEOUT_MS)
            if not response:
                return None
            if len(response) >= 5 and response[0] == self.REPORT_ID \
                    and response[1] == index:
                return response

    def _read_level(self, device, index: int) -> int | None:
        response = self._request(
            device, index,
            [0x00, self.SW_ID,
             self.BATTERY_FEATURE >> 8, self.BATTERY_FEATURE & 0xFF],
        )
        if not response or response[2] == self.ERROR_MARKER:
            return None

        feature_index = response[4]
        if not 0 < feature_index < 0xFF:
            return None

        response = self._request(device, index, [feature_index, self.SW_ID])
        if not response or response[2] == self.ERROR_MARKER:
            return None

        level = response[4]
        return level if 0 <= level <= 100 else None


def read_battery(address: str, friendly_name: str) -> BatteryReading:
    """优先取 2.4G 接收器的读数（更实时），没有则退回蓝牙。"""
    for source in (
        ReceiverBatterySource(friendly_name),
        BluetoothBatterySource(address, friendly_name),
    ):
        reading = source.read()
        if reading.ok:
            return reading
    return reading
