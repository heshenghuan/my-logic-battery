"""Mloong MX301 PRO 2.4G 接收器：厂商 64 字节报文读取电量。"""

from __future__ import annotations

from battery import BatteryReading, Candidate
from devices._hid import _HidMouseDevice


class MloongReceiverDevice(_HidMouseDevice):
    """通过 Mloong 2.4G 接收器的厂商接口读取电量。

    接收器暴露 usage 0xFF01 / 0x10 的 64 字节无编号报告：
    发送固定的电池查询帧，应答第 8 字节为电量。
    """

    type = "mloong_2.4g"
    MISSING_DETAIL = "未发现 Mloong 接收器"

    VENDOR_ID = 0xA8A5
    PRODUCT_ID = 0x2255
    USAGE_PAGE = 0xFF01
    USAGE = 0x10
    REPORT_LENGTH = 64
    TIMEOUT_MS = 500
    REQUEST = bytes.fromhex("5530a50b2e010101")

    def _channel_path(self, hid_module) -> tuple[bytes | None, object]:
        for info in hid_module.enumerate(self.VENDOR_ID, self.PRODUCT_ID):
            if info["usage_page"] == self.USAGE_PAGE \
                    and info["usage"] == self.USAGE:
                return (
                    info["path"],
                    (
                        str(info.get("serial_number") or "").upper(),
                        str(info.get("product_string") or ""),
                    ),
                )
        return None, None

    def _query(self, device, meta) -> tuple[int, str] | None:
        serial, name = meta
        if self.stable_id and serial and self.stable_id != serial:
            return None
        level = self._read_level(device)
        if level is None:
            return None
        return level, name

    def _read_level(self, device) -> int | None:
        while device.read(64, 30):
            pass
        frame = self.REQUEST.ljust(self.REPORT_LENGTH, b"\x00")
        if device.write(b"\x00" + frame) < 0:
            return None

        while True:
            response = device.read(self.REPORT_LENGTH, self.TIMEOUT_MS)
            if not response:
                return None
            if len(response) >= 9 and response[0] == 0xAA \
                    and response[1] == 0x30:
                level = response[8]
                return level if 0 <= level <= 100 else None

    @classmethod
    def scan(cls) -> list[Candidate]:
        try:
            import hid  # type: ignore
        except ImportError:
            return []

        receiver = cls("")
        path, meta = receiver._channel_path(hid)
        if not path:
            return []

        device = None
        try:
            device = hid.device()
            device.open_path(path)
            device.set_nonblocking(0)
            level = receiver._read_level(device)
            if level is None:
                return []
            serial, name = meta
            return [Candidate(cls(name, "", serial), level=level)]
        except OSError:
            return []
        finally:
            if device is not None:
                device.close()
