"""设备类注册表。

新增设备时：
1. 在本目录下新建一个模块，定义继承 MouseDevice（或 _HidMouseDevice）的类，
   并设置 type / stable_field，实现 read() 与 scan()。
2. 在下方 DEVICE_CLASSES 里按 type 注册。2.4G 设备排在蓝牙前面，
   列表顺序即 scan_all / read_any 的遍历优先级。
"""

from __future__ import annotations

from devices.bluetooth import BluetoothMouseDevice
from devices.logitech import LogitechReceiverDevice
from devices.mloong import MloongReceiverDevice

DEVICE_CLASSES: dict[str, type] = {
    LogitechReceiverDevice.type: LogitechReceiverDevice,
    MloongReceiverDevice.type: MloongReceiverDevice,
    BluetoothMouseDevice.type: BluetoothMouseDevice,
}

__all__ = ["DEVICE_CLASSES"]
