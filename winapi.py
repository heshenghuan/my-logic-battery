"""Windows SetupAPI / CFGMGR32 封装。

集中放置设备枚举、属性读取所需的常量、ctypes 结构和系统 API 声明，
battery.py 只保留设备类定义和电量读取逻辑。
"""

from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes

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


class DeviceEnumerator:
    """枚举某个设备类下的在线设备，离开 with 块时释放句柄。"""

    def __init__(self, class_guid: uuid.UUID) -> None:
        self._guid = GUID.from_uuid(class_guid)
        self.handle = None

    def __enter__(self) -> "DeviceEnumerator":
        handle = setupapi.SetupDiGetClassDevsW(
            ctypes.byref(self._guid), None, None, DIGCF_PRESENT
        )
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = handle
        return self

    def __exit__(self, *exc) -> None:
        if self.handle:
            setupapi.SetupDiDestroyDeviceInfoList(self.handle)
            self.handle = None

    def __iter__(self):
        index = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiEnumDeviceInfo(self.handle, index, ctypes.byref(info)):
                return
            yield info
            index += 1


def enumerate_bluetooth() -> DeviceEnumerator:
    return DeviceEnumerator(GUID_DEVCLASS_BLUETOOTH)


def instance_id(handle, info) -> str:
    buf = ctypes.create_unicode_buffer(512)
    needed = wintypes.DWORD()
    if setupapi.SetupDiGetDeviceInstanceIdW(
        handle, ctypes.byref(info), buf, len(buf), ctypes.byref(needed)
    ):
        return buf.value
    return ""


def get_property(handle, info, key: tuple[uuid.UUID, int]):
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


def device_parent(handle, info) -> str:
    """父设备实例 ID，用于确认 BLE 节点是否属于 HID over GATT 服务。"""
    prop_type, data = get_property(handle, info, DEVPKEY_DEVICE_PARENT)
    if prop_type == DEVPROP_TYPE_STRING and data:
        return data.decode("utf-16-le", "ignore").rstrip("\x00")
    return ""


def friendly_name(handle, info) -> str:
    prop_type, data = get_property(handle, info, DEVPKEY_DEVICE_FRIENDLY_NAME)
    if prop_type == DEVPROP_TYPE_STRING and data:
        return data.decode("utf-16-le", "ignore").rstrip("\x00")
    return ""


def bluetooth_battery(handle, info) -> int | None:
    prop_type, data = get_property(handle, info, DEVPKEY_BLUETOOTH_BATTERY)
    if prop_type in (DEVPROP_TYPE_BYTE, DEVPROP_TYPE_UINT32) and data:
        level = int.from_bytes(data, "little")
        if 0 <= level <= 100:
            return level
    return None


def is_disconnected(devinst: int) -> bool:
    """节点还在但设备已断开时，节点状态会带 DN_DEVICE_DISCONNECTED。"""
    status = wintypes.ULONG()
    problem = wintypes.ULONG()
    result = cfgmgr32.CM_Get_DevNode_Status(
        ctypes.byref(status), ctypes.byref(problem), devinst, 0
    )
    if result != 0:
        return False
    return bool(status.value & DN_DEVICE_DISCONNECTED)


def ble_hid_addresses() -> set[str]:
    r"""HID over GATT 设备的 BLE 地址集合。

    BTHLE\DEV_<地址> 节点本身不带 HID 信息，但它的子节点会以
    BTHLEDEVICE\{00001812-...}_DEV_..._<地址> 的形式出现在 HID 类里，
    父设备指回 BTHLE\DEV_<地址>。用这层关系过滤掉耳机等非 HID 设备。
    """
    addresses: set[str] = set()
    try:
        with DeviceEnumerator(GUID_DEVCLASS_HID) as devices:
            for info in devices:
                dev_instance = instance_id(devices.handle, info)
                if BLE_HID_SERVICE not in dev_instance.upper():
                    continue
                parent = device_parent(devices.handle, info)
                parts = parent.split("\\")
                if len(parts) > 1 and parts[0].upper() == "BTHLE" \
                        and parts[1].upper().startswith("DEV_"):
                    addresses.add(parts[1][4:].upper())
    except OSError:
        pass
    return addresses
