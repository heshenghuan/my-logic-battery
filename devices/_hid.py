"""2.4G HID 接收器类设备的读取模板。

子类只需实现 _channel_path() 和 _query()：
- _channel_path 返回 (设备路径, 附加信息)
- _query 返回 (电量, 设备名)，读不到返回 None
"""

from __future__ import annotations

from battery import BatteryReading, MouseDevice


class _HidMouseDevice(MouseDevice):
    """HID 接收器类设备读取电量的公共流程。"""

    channel_label = "2.4G"
    TIMEOUT_MS = 400
    MISSING_DETAIL = "未发现接收器"
    EMPTY_DETAIL = "接收器上未检测到已连接的设备"

    def read(self) -> BatteryReading:
        try:
            import hid  # type: ignore
        except ImportError:
            return BatteryReading(None, self.channel_label, self._label(),
                                  "缺少 hidapi 依赖")

        path, meta = self._channel_path(hid)
        if not path:
            return BatteryReading(None, self.channel_label, self._label(),
                                  self.MISSING_DETAIL)

        try:
            device = hid.device()
            device.open_path(path)
            device.set_nonblocking(0)
        except OSError:
            return BatteryReading(None, self.channel_label, self._label(),
                                  "接收器打不开，可能被其他程序独占")

        try:
            found = self._query(device, meta)
            if found is None:
                # 设备休眠时可能漏掉第一包，重试一次（清缓冲由各子类 _query 负责）
                found = self._query(device, meta)
        finally:
            device.close()

        if found is None:
            return BatteryReading(None, self.channel_label, self._label(),
                                  self.EMPTY_DETAIL)
        level, name = found
        return BatteryReading(level, self.channel_label, self._label(name))

    def _channel_path(self, hid_module) -> tuple[bytes | None, object]:
        raise NotImplementedError

    def _query(self, device, meta) -> tuple[int, str] | None:
        raise NotImplementedError
