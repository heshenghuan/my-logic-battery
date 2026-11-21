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
cfgmgr32 = ctypes.WinDLL("cfgmgr32")

DIGCF_PRESENT = 0x02
ERROR_INSUFFICIENT_BUFFER = 122
DEVPROP_TYPE_BYTE = 0x03
DEVPROP_TYPE_UINT32 = 0x07
DEVPROP_TYPE_STRING = 0x12

GUID_DEVCLASS_BLUETOOTH = uuid.UUID("{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}")
GUID_DEVCLASS_HID = uuid.UUID("{745a17a0-74d3-11d0-b6fe-00a0c90f57da}")
DEVPKEY_BLUETOOTH_BATTERY = uuid.UUID("{104ea319-6ee2-4701-bd47-8ddbf425bbe5}"), 2
DEVPKEY_DEVICE_FRIENDLY_NAME = uuid.UUID("{a45c254e-df1c-4efd-8020-67d146a850e0}"), 14
DEVPKEY_DEVICE_PARENT = uuid.UUID("{4340a6c5-93fa-4706-972c-7b648008a5a7}"), 8
BLE_HID_SERVICE = "00001812"

# 鼠标关机后设备节点仍报告 IsPresent=True、电量属性也保留旧值，
# 只有节点状态里的 DN_DEVICE_DISCONNECTED 会翻转，用它区分“断开但节点还在”。
DN_DEVICE_DISCONNECTED = 0x02000000


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

cfgmgr32.CM_Get_DevNode_Status.argtypes = [
    ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(wintypes.ULONG),
    wintypes.DWORD, wintypes.ULONG,
]
cfgmgr32.CM_Get_DevNode_Status.restype = wintypes.LONG


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
    if not ok and ctypes.get_last_error() == ERROR_INSUFFICIENT_BUFFER \
            and needed.value > ctypes.sizeof(buf):
        # 属性值超过固定缓冲时按所需大小扩容重试一次
        buf = (ctypes.c_ubyte * needed.value)()
        ok = setupapi.SetupDiGetDevicePropertyW(
            handle, ctypes.byref(info), ctypes.byref(prop_key),
            ctypes.byref(prop_type), buf, ctypes.sizeof(buf),
            ctypes.byref(needed), 0,
        )
    if not ok:
        return None, None
    return prop_type.value, bytes(buf[: needed.value])


def _device_parent(handle, info) -> str:
    """父设备实例 ID，用于确认 BLE 节点是否属于 HID over GATT 服务。"""
    prop_type, data = _get_property(handle, info, DEVPKEY_DEVICE_PARENT)
    if prop_type == DEVPROP_TYPE_STRING and data:
        return data.decode("utf-16-le", "ignore").rstrip("\x00")
    return ""


def _ble_hid_addresses() -> set[str]:
    r"""HID over GATT 设备的 BLE 地址集合。

    BTHLE\DEV_<地址> 节点本身不带 HID 信息，但它的子节点会以
    BTHLEDEVICE\{00001812-...}_DEV_..._<地址> 的形式出现在 HID 类里，
    父设备指回 BTHLE\DEV_<地址>。用这层关系过滤掉耳机等非 HID 设备。
    """
    addresses: set[str] = set()
    try:
        with _DeviceEnumerator(GUID_DEVCLASS_HID) as devices:
            for info in devices:
                instance_id = _instance_id(devices._handle, info)
                if BLE_HID_SERVICE not in instance_id.upper():
                    continue
                parent = _device_parent(devices._handle, info)
                parts = parent.split("\\")
                if len(parts) > 1 and parts[0].upper() == "BTHLE" \
                        and parts[1].upper().startswith("DEV_"):
                    addresses.add(parts[1][4:].upper())
    except OSError:
        pass
    return addresses


class BluetoothBatterySource:
    """通过 Windows 设备属性读取蓝牙 BLE 鼠标电量。"""

    source_name = "蓝牙"

    def __init__(self, address: str, friendly_name: str,
                 display_name: str = "") -> None:
        self.address = address.strip().upper()
        self.friendly_name = friendly_name.strip()
        self.display_name = display_name.strip()

    def _label(self, actual: str) -> str:
        return self.display_name or actual or self.friendly_name

    def read(self) -> BatteryReading:
        try:
            with _DeviceEnumerator(GUID_DEVCLASS_BLUETOOTH) as devices:
                by_name = None
                disconnected = None
                for info in devices:
                    instance_id = _instance_id(devices._handle, info)
                    if not instance_id.upper().startswith("BTHLE\\DEV_"):
                        continue

                    name = self._friendly_name(devices._handle, info)
                    match_address = bool(self.address) and self.address in instance_id.upper()
                    match_name = (not self.address) and bool(name) and _name_score(self.friendly_name, name) > 0
                    if not (match_address or match_name):
                        continue

                    if self._is_disconnected(info):
                        disconnected = name or self.friendly_name
                        continue

                    if match_address:
                        return self._reading(devices._handle, info, name)
                    if by_name is None:
                        by_name = self._reading(devices._handle, info, name)

                if by_name is not None:
                    return by_name
                if disconnected is not None:
                    return BatteryReading(None, self.source_name,
                                          self._label(disconnected), "设备不在线")
        except OSError as exc:
            return BatteryReading(None, self.source_name, self.friendly_name,
                                  f"枚举蓝牙设备失败: {exc}")

        return BatteryReading(None, self.source_name, self.friendly_name,
                              "未发现已连接的蓝牙设备")

    def _is_disconnected(self, info) -> bool:
        """节点还在但设备已断开时，节点状态会带 DN_DEVICE_DISCONNECTED。"""
        status = wintypes.ULONG()
        problem = wintypes.ULONG()
        result = cfgmgr32.CM_Get_DevNode_Status(
            ctypes.byref(status), ctypes.byref(problem), info.DevInst, 0
        )
        if result != 0:
            return False
        return bool(status.value & DN_DEVICE_DISCONNECTED)

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
                return BatteryReading(level, self.source_name, self._label(name))
        return BatteryReading(None, self.source_name, self._label(name),
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
    DEVICE_NAME_FEATURE = 0x0005
    DEVICE_INFO_FEATURE = 0x0003
    ERROR_MARKER = 0x8F
    TIMEOUT_MS = 400
    _cached_index: int | None = None
    _name_cache: dict[int, str] = {}
    _unit_cache: dict[int, str] = {}

    def __init__(self, friendly_name: str, display_name: str = "",
                 unit_id: str = "") -> None:
        self.friendly_name = friendly_name.strip()
        self.display_name = display_name.strip()
        self.unit_id = unit_id.strip().upper()

    def _label(self, actual: str) -> str:
        return self.display_name or actual or self.friendly_name

    def read(self) -> BatteryReading:
        try:
            import hid  # type: ignore
        except ImportError:
            return BatteryReading(None, self.source_name, self._label(""),
                                  "缺少 hidapi 依赖")

        path = self._long_channel_path(hid)
        if not path:
            return BatteryReading(None, self.source_name, self._label(""),
                                  "未发现 Unifying 接收器")

        try:
            device = hid.device()
            device.open_path(path)
            device.set_nonblocking(0)
        except OSError:
            return BatteryReading(None, self.source_name, self._label(""),
                                  "接收器打不开，可能被其他程序独占")

        try:
            while device.read(64, 30):
                pass
            found = self._query_level(device)
            if found is None:
                # 设备休眠时可能漏掉第一包，清空缓冲后重试一次
                while device.read(64, 30):
                    pass
                found = self._query_level(device)
            if found is not None:
                index, level = found
                name = self._name_for(device, index)
                return BatteryReading(level, self.source_name, self._label(name))
        finally:
            device.close()

        return BatteryReading(None, self.source_name, self._label(""),
                              "接收器上未检测到已连接的设备")

    def _long_channel_path(self, hid_module) -> bytes | None:
        for info in hid_module.enumerate(self.VENDOR_ID, 0):
            if info["product_id"] in self.PRODUCT_IDS \
                    and info["usage_page"] == self.USAGE_PAGE \
                    and info["usage"] == self.USAGE_LONG:
                return info["path"]
        return None

    def _query_level(self, device) -> tuple[int, int] | None:
        indexes = list(range(1, 7))
        if self._cached_index in indexes:
            indexes.remove(self._cached_index)
            indexes.insert(0, self._cached_index)

        fallback = None
        for index in indexes:
            level = self._read_level(device, index)
            if level is None:
                continue
            if self.unit_id:
                # 配了 unit_id 就只认它，读不到就跳过，避免认成别的鼠标
                if self._unit_for(device, index) == self.unit_id:
                    self._cached_index = index
                    return index, level
                continue
            score = _name_score(self.friendly_name, self._name_for(device, index))
            if score >= 2:
                self._cached_index = index
                return index, level
            if score == 1 and fallback is None:
                fallback = (index, level)

        self._cached_index = fallback[0] if fallback else None
        return fallback

    def _unit_for(self, device, index: int) -> str:
        if index not in self._unit_cache:
            unit = self._read_unit_id(device, index)
            if unit:
                self._unit_cache[index] = unit
        return self._unit_cache.get(index, "")

    def _read_unit_id(self, device, index: int) -> str:
        """读 Device Information(0x0003) 的 unitId，作为接收器侧的稳定标识。"""
        response = self._request(
            device, index,
            [0x00, self.SW_ID,
             self.DEVICE_INFO_FEATURE >> 8, self.DEVICE_INFO_FEATURE & 0xFF],
        )
        if not response or response[2] == self.ERROR_MARKER:
            return ""
        feature_index = response[4]
        if not 0 < feature_index < 0xFF:
            return ""
        response = self._request(device, index, [feature_index, self.SW_ID])
        if not response or response[2] == self.ERROR_MARKER:
            return ""
        return bytes(response[4:8]).hex().upper()

    def _name_for(self, device, index: int) -> str:
        if index not in self._name_cache:
            name = self._read_name(device, index)
            if name:
                self._name_cache[index] = name
        return self._name_cache.get(index, "")

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

    def _read_name(self, device, index: int) -> str:
        """读 HID++ 2.0 的 Device Name(0x0005)，分 16 字节一块取回完整型号名。"""
        response = self._request(
            device, index,
            [0x00, self.SW_ID,
             self.DEVICE_NAME_FEATURE >> 8, self.DEVICE_NAME_FEATURE & 0xFF],
        )
        if not response or response[2] == self.ERROR_MARKER:
            return ""
        feature_index = response[4]
        if not 0 < feature_index < 0xFF:
            return ""

        response = self._request(device, index, [feature_index, self.SW_ID])
        if not response or response[2] == self.ERROR_MARKER:
            return ""
        length = response[4]
        if not 0 < length <= 64:
            return ""

        raw = bytearray()
        while len(raw) < length:
            response = self._request(
                device, index,
                [feature_index, (1 << 4) | self.SW_ID, len(raw)],
            )
            if not response or response[2] == self.ERROR_MARKER:
                return ""
            raw.extend(response[4:20])

        return raw[:length].decode("utf-8", "ignore").strip("\x00 ")


def scan() -> list[dict]:
    """列出当前可见的鼠标以及可写进 config.json 的稳定标识。"""
    found: list[dict] = []

    bluetooth = BluetoothBatterySource("", "")
    hid_addresses = _ble_hid_addresses()
    try:
        with _DeviceEnumerator(GUID_DEVCLASS_BLUETOOTH) as devices:
            for info in devices:
                instance_id = _instance_id(devices._handle, info)
                if not instance_id.upper().startswith("BTHLE\\DEV_"):
                    continue
                name = bluetooth._friendly_name(devices._handle, info)
                online = not bluetooth._is_disconnected(info)
                reading = bluetooth._reading(devices._handle, info, name) if online else None
                parts = instance_id.split("\\")
                address = parts[1][4:] if len(parts) > 1 else ""
                found.append({
                    "connection": "bluetooth",
                    "address": address.upper(),
                    "name": name,
                    "online": online,
                    "hid": address.upper() in hid_addresses,
                    "level": reading.level if reading else None,
                })
    except OSError:
        pass

    try:
        import hid  # type: ignore
    except ImportError:
        return found

    receiver = ReceiverBatterySource("")
    path = receiver._long_channel_path(hid)
    if not path:
        return found

    device = None
    try:
        device = hid.device()
        device.open_path(path)
        device.set_nonblocking(0)
        for index in range(1, 7):
            level = receiver._read_level(device, index)
            if level is None:
                continue
            found.append({
                "connection": "2.4g",
                "index": index,
                "unit_id": receiver._unit_for(device, index),
                "name": receiver._name_for(device, index),
                "level": level,
            })
    except OSError:
        pass
    finally:
        if device is not None:
            device.close()

    return found


def candidates() -> list[dict]:
    """扫描并筛选可绑定的设备。

    2.4G 侧能回应电池请求的即候选；蓝牙侧要求在线且属于 HID over GATT，
    避免把耳机之类也列进来。
    """
    return [
        row for row in scan()
        if row["connection"] == "2.4g"
        or (row["online"] and row.get("hid"))
    ]


def _name_score(expected: str, actual: str) -> int:
    """比较配置名与 HID++ 读回的型号名。

    2 = 完全匹配，1 = 可作为备选，0 = 不匹配。
    读不到型号名时按 1 处理，避免没有 0x0005 功能的老设备永远匹配不上；
    前缀后面紧跟字母数字（如 "MX Anywhere 2" 对 "MX Anywhere 2S"）不算匹配。
    """
    expected = " ".join(expected.lower().split())
    actual = " ".join(actual.lower().split())
    if not expected:
        return 2
    if not actual:
        return 1
    if expected == actual:
        return 2

    start = actual.find(expected)
    while start != -1:
        end = start + len(expected)
        if (start == 0 or not actual[start - 1].isalnum()) and \
                (end == len(actual) or not actual[end].isalnum()):
            return 1
        start = actual.find(expected, start + 1)
    return 0


def read_receiver_device(device: dict) -> BatteryReading:
    return ReceiverBatterySource(
        str(device.get("name") or "").strip(),
        str(device.get("display_name") or "").strip(),
        str(device.get("unit_id") or "").strip(),
    ).read()


def read_bluetooth_device(device: dict) -> BatteryReading:
    return BluetoothBatterySource(
        str(device.get("address") or "").strip(),
        str(device.get("name") or "").strip(),
        str(device.get("display_name") or "").strip(),
    ).read()


READERS = {
    "2.4g": read_receiver_device,
    "bluetooth": read_bluetooth_device,
}


def find_device(groups: dict) -> tuple[tuple[str, dict] | None, BatteryReading]:
    """先轮询 2.4G 组，全部未命中再轮询蓝牙组。

    返回 (命中的连接方式与配置项, 读数)；都没命中时第一项为 None。
    """
    last = None
    for key in ("2.4g", "bluetooth"):
        reader = READERS[key]
        for device in groups.get(key) or []:
            reading = reader(device)
            if reading.ok:
                return (key, device), reading
            last = reading

    return None, last or BatteryReading(None, "", "", "config.json 里没有配置设备")


def read_battery(groups: dict) -> BatteryReading:
    """按 2.4G 优先的顺序找一只在线的鼠标并返回读数。"""
    return find_device(groups)[1]
