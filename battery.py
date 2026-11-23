"""鼠标电量读取的公共层。

本模块只保留：
- BatteryReading / Candidate：读数与扫描结果的数据结构
- MouseDevice：设备基类，定义公共属性（name / display_name / uid）与
  read() / scan() / to_json() / from_json() 接口
- _name_score：配置名与设备上报型号名的匹配打分
- device_from_json / scan_all / read_any：配置到设备实例的分发与轮询入口

各品牌/通道的具体实现放在 devices/ 包下，在 devices/__init__.py 的
DEVICE_CLASSES 里注册 type → 类。新增设备只需加一个模块并注册，
本模块无需改动。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BatteryReading:
    level: int | None
    source: str
    device_name: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.level is not None


@dataclass
class Candidate:
    """扫描结果：设备实例 + 当前读数。"""

    device: "MouseDevice"
    level: int | None = None
    online: bool = True
    bindable: bool = True


class MouseDevice:
    """鼠标设备的公共属性和接口。

    子类只负责协议细节：read() 读取电量，scan() 枚举当前可见设备。
    """

    type = ""
    channel_label = ""

    def __init__(self, name: str, display_name: str = "",
                 stable_id: str = "") -> None:
        self.name = name.strip()
        self.display_name = display_name.strip()
        self.stable_id = stable_id.strip().upper()

    @property
    def uid(self) -> str:
        return f"{self.type}:{self.stable_id or self.name}"

    def label(self) -> str:
        return self.display_name or self.name

    def _label(self, actual: str = "") -> str:
        return self.display_name or actual or self.name

    def read(self) -> BatteryReading:
        raise NotImplementedError

    def to_json(self) -> dict:
        return {
            "type": self.type,
            "name": self.name,
            "display_name": self.display_name,
            "unit_id": self.stable_id,
        }

    @classmethod
    def from_json(cls, data: dict) -> "MouseDevice":
        if not isinstance(data, dict):
            raise ValueError("设备配置项必须是对象")
        return cls(
            str(data.get("name") or ""),
            str(data.get("display_name") or data.get("alias") or ""),
            str(data.get("unit_id") or ""),
        )

    @classmethod
    def scan(cls) -> list[Candidate]:
        raise NotImplementedError


def _name_score(expected: str, actual: str) -> int:
    """比较配置名与设备上报的型号名。

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


def _device_classes() -> dict[str, type]:
    """延迟导入设备注册表。

    devices 包反向依赖本模块的 MouseDevice 等，顶层导入会形成环
    （import devices 失败）；改为函数内导入，两种入口顺序都能正常加载。
    """
    from devices import DEVICE_CLASSES
    return DEVICE_CLASSES


def device_from_json(data: dict) -> MouseDevice:
    cls = _device_classes().get(str(data.get("type") or ""))
    if cls is None:
        raise ValueError(f"未知设备类型: {data.get('type')!r}")
    return cls.from_json(data)


def scan_all(only_bindable: bool = False) -> list[Candidate]:
    """按注册表顺序扫描；2.4G 两个类在前，蓝牙在后。"""
    found: list[Candidate] = []
    for cls in _device_classes().values():
        try:
            found.extend(
                item for item in cls.scan()
                if item.bindable or not only_bindable
            )
        except OSError:
            continue
    return found


def read_any(devices: list[MouseDevice]) -> tuple[MouseDevice | None, BatteryReading]:
    """按配置顺序找第一只在线设备；都没命中时返回最后一次读数。"""
    last = BatteryReading(None, "", "", "config.json 里没有配置设备")
    for device in devices:
        reading = device.read()
        if reading.ok:
            return device, reading
        last = reading
    return None, last


# 设备注册表通过 _device_classes() 延迟导入，见上方定义。
