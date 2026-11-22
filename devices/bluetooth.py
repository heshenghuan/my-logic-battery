"""蓝牙 BLE 设备：通过 Windows 设备属性读取电量。

电量挂在 DEVPKEY_Bluetooth_Battery 上，对所有实现了 GATT Battery Service
的 BLE 设备通用，与品牌无关。
"""

from __future__ import annotations

from battery import BatteryReading, Candidate, MouseDevice, _name_score
from winapi import (
    ble_hid_addresses,
    bluetooth_battery,
    enumerate_bluetooth,
    friendly_name,
    instance_id,
    is_disconnected,
)


class BluetoothMouseDevice(MouseDevice):
    """通过 Windows 设备属性读取蓝牙 BLE 鼠标电量。"""

    type = "bluetooth"
    channel_label = "蓝牙"
    stable_field = "address"

    @property
    def address(self) -> str:
        return self.stable_id

    def read(self) -> BatteryReading:
        try:
            with enumerate_bluetooth() as devices:
                by_name = None
                disconnected = None
                for info in devices:
                    dev_instance = instance_id(devices.handle, info)
                    if not dev_instance.upper().startswith("BTHLE\\DEV_"):
                        continue

                    name = friendly_name(devices.handle, info)
                    match_address = bool(self.address) and self.address in dev_instance.upper()
                    match_name = (not self.address) and bool(name) \
                        and _name_score(self.name, name) > 0
                    if not (match_address or match_name):
                        continue

                    if is_disconnected(info.DevInst):
                        disconnected = name or self.name
                        continue

                    if match_address:
                        return self._reading(devices.handle, info, name)
                    if by_name is None:
                        by_name = self._reading(devices.handle, info, name)

                if by_name is not None:
                    return by_name
                if disconnected is not None:
                    return BatteryReading(None, self.channel_label,
                                          self._label(disconnected), "设备不在线")
        except OSError as exc:
            return BatteryReading(None, self.channel_label, self.name,
                                  f"枚举蓝牙设备失败: {exc}")

        return BatteryReading(None, self.channel_label, self.name,
                              "未发现已连接的蓝牙设备")

    def _reading(self, handle, info, name: str) -> BatteryReading:
        level = bluetooth_battery(handle, info)
        if level is not None:
            return BatteryReading(level, self.channel_label, self._label(name))
        return BatteryReading(None, self.channel_label, self._label(name),
                              "设备未上报电量")

    @classmethod
    def scan(cls) -> list[Candidate]:
        found: list[Candidate] = []
        hid_addresses = ble_hid_addresses()
        try:
            with enumerate_bluetooth() as devices:
                for info in devices:
                    dev_instance = instance_id(devices.handle, info)
                    if not dev_instance.upper().startswith("BTHLE\\DEV_"):
                        continue
                    name = friendly_name(devices.handle, info)
                    online = not is_disconnected(info.DevInst)
                    device = cls(name)
                    reading = device._reading(devices.handle, info, name) if online else None
                    parts = dev_instance.split("\\")
                    address = parts[1][4:] if len(parts) > 1 else ""
                    found.append(Candidate(
                        device=cls(name, "", address),
                        level=reading.level if reading else None,
                        online=online,
                        bindable=online and address.upper() in hid_addresses,
                    ))
        except OSError:
            pass
        return found
