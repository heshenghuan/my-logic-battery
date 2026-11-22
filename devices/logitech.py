"""罗技 Unifying 接收器：HID++ 2.0 协议读取 2.4G 电量。"""

from __future__ import annotations

from battery import BatteryReading, Candidate, _name_score
from devices._hid import _HidMouseDevice


class LogitechReceiverDevice(_HidMouseDevice):
    """通过 Unifying 接收器读取 2.4G 电量（HID++ 2.0）。

    接收器不暴露系统属性，只能发 HID++ 报文：
    先用 root.getFeature(0x1000) 取电池功能索引，再调 getBatteryLevelStatus。
    报文走 usage 0xFF00 / 2 的 20 字节长报告，多字节字段按大端序。
    鼠标需切到接收器所在通道，否则对应设备索引不会回包。
    """

    type = "logitech_2.4g"

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
    MISSING_DETAIL = "未发现 Unifying 接收器"
    _cached_index: int | None = None
    _name_cache: dict[int, str] = {}
    _unit_cache: dict[int, str] = {}

    def _channel_path(self, hid_module) -> tuple[bytes | None, object]:
        for info in hid_module.enumerate(self.VENDOR_ID, 0):
            if info["product_id"] in self.PRODUCT_IDS \
                    and info["usage_page"] == self.USAGE_PAGE \
                    and info["usage"] == self.USAGE_LONG:
                return info["path"], None
        return None, None

    def _query(self, device, meta=None) -> tuple[int, str] | None:
        while device.read(64, 30):
            pass
        found = self._query_level(device)
        if found is None:
            return None
        index, level = found
        return level, self._name_for(device, index)

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
            if self.stable_id:
                # 配了稳定标识就只认它，读不到就跳过，避免认成别的鼠标
                if self._unit_for(device, index) == self.stable_id:
                    self._cached_index = index
                    return index, level
                continue
            score = _name_score(self.name, self._name_for(device, index))
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

    @classmethod
    def scan(cls) -> list[Candidate]:
        try:
            import hid  # type: ignore
        except ImportError:
            return []

        receiver = cls("")
        path, _ = receiver._channel_path(hid)
        if not path:
            return []

        found: list[Candidate] = []
        device = None
        try:
            device = hid.device()
            device.open_path(path)
            device.set_nonblocking(0)
            for index in range(1, 7):
                level = receiver._read_level(device, index)
                if level is None:
                    continue
                found.append(Candidate(
                    cls(receiver._name_for(device, index), "",
                        receiver._unit_for(device, index)),
                    level=level,
                ))
        except OSError:
            pass
        finally:
            if device is not None:
                device.close()
        return found
