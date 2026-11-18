# LogicBattery

Windows 托盘电量监控，为罗技 MX Anywhere 2 写的：常驻显示鼠标电量，低于阈值时弹系统通知提醒充电。

## 功能

- 托盘图标常驻显示电量：电池格数 + 颜色（绿 = 正常，橙 = 已达阈值，红 = ≤10%，灰 = 未连接）
- 两种图标样式，菜单内随时切换：**电池 + 鼠标**（便于和系统自带电池图标区分）/ **仅电池**
- 电量低于阈值时弹 Windows 通知；电量回到「阈值 + 5%」以上才会再次提醒，避免反复打扰
- 左键和右键都打开同一个菜单
- 蓝牙与 Unifying 2.4G 双通道读取，两条通道都在线时优先走 2.4G
- 菜单内可开关开机自启

## 兼容性

- 系统：Windows 10 / 11（依赖 SetupAPI 与 Win32 托盘 API）
- 设备：目前只在 **MX Anywhere 2** 上实测通过，蓝牙 BLE 与 Unifying 接收器（PID `0xC52B`）两条通道均已验证
- **MX Anywhere 2s 尚未测试**。两者同属 HID++ 2.0 设备，理论协议一致，但电池功能索引与设备索引需要实测确认，后续有机会再验证

## 安装

前置条件：

- Windows 10 / 11
- Python 3.10 以上（开发环境为 3.14.9），安装时勾选 **Add python.exe to PATH**
- 鼠标已通过蓝牙或接收器连上电脑

下面的命令都在项目根目录执行，示例路径为 `d:\Projects\my-logic-battery`，请换成你本地的实际路径。

**1. 创建虚拟环境**

依赖统一装在项目内的 `.venv` 里，不污染系统 Python；该目录已被 `.gitignore` 排除。

```powershell
cd d:\Projects\my-logic-battery
python -m venv .venv
```

如果 `python` 不是 3.10 以上，改用版本管理器指定解释器，例如 `py -3.12 -m venv .venv`。

**2. 按 requirements.txt 安装依赖**

用虚拟环境自带的 pip 安装 pystray / pillow / hidapi。务必通过 `.venv\Scripts\python` 调用，否则会装进系统环境：

```powershell
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
```

不需要先执行 `Activate.ps1`，直接指定解释器路径就不会装错位置。

**3. 启动**

```powershell
.venv\Scripts\pythonw.exe main.pyw
```

也可以直接双击 `run.vbs`。它调用的同样是 `.venv\Scripts\pythonw.exe`，所以要先完成第 1、2 步，否则会启动失败。

首次运行会在同目录生成 `config.json`。

生成的 `config.json` 里 `device_address` 默认留空，此时程序按 `device_name`（默认 `MX Anywhere 2`）匹配已连接的蓝牙设备，开箱即用，一般不需要改动：

```json
{
  "device_address": "",
  "device_name": "MX Anywhere 2"
}
```

只有在下面两种情况才需要填地址：手里有多只同名鼠标，或按名称匹配不到设备。此时把目标设备的 BLE 地址写成大写十六进制填进去即可：

```json
{ "device_address": "A1B2C3D4E5F6" }
```

地址可在设备管理器的蓝牙设备实例 ID（`BTHLE\DEV_<地址>`）中查看。注意：2.4G 通道不依赖该地址，程序会直接在接收器上扫描设备索引。

想先确认能不能读到电量（不启动托盘，只打印一次结果）：

```powershell
.venv\Scripts\python.exe main.pyw --once
```

正常时输出类似 `BatteryReading(level=90, source='2.4G', device_name='MX Anywhere 2', detail='')`。

## 使用

托盘图标上左键或右键都能打开菜单：

| 菜单项 | 说明 |
| --- | --- |
| 状态行 | 设备名 · 电量 · 连接方式；未连接时显示原因 |
| 立即刷新 | 立刻读一次电量 |
| 电量低于 | 通知阈值，可选 10% ~ 50% |
| 图标样式 | 「电池 + 鼠标」或「仅电池」 |
| 开机自启 | 在启动文件夹创建/删除快捷方式 |
| 退出 | 关闭程序（托盘图标随之消失） |

不想要开机自启时，先在菜单里取消勾选，再退出即可；直接删文件夹会残留启动项。

## 配置文件

`config.json` 首次运行时自动生成，修改后需重启程序生效。

| 字段 | 说明 |
| --- | --- |
| `device_address` | 蓝牙 BLE 地址，大写十六进制，可在设备管理器的蓝牙设备实例 ID（`BTHLE\DEV_...`）中查看；留空则完全按 `device_name` 匹配 |
| `device_name` | 友好名称，地址匹配失败时按名称回退匹配 |
| `threshold` | 通知阈值（百分比） |
| `interval_minutes` | 轮询间隔（分钟），最小按 1 分钟处理 |
| `icon_style` | `both` = 电池 + 鼠标，`battery` = 仅电池 |

## 实现说明

- **蓝牙**：Windows 会把 BLE HID 设备的电量挂在设备节点属性 `DEVPKEY_Bluetooth_Battery`（`{104EA319-6EE2-4701-BD47-8DDBF425BBE5}, 2`）上，用 SetupAPI 直接读取，无需启动 PowerShell
- **2.4G**：Unifying 接收器不暴露系统属性，只能发 HID++ 2.0 报文。走 `usage 0xFF00 / 2` 的 20 字节长报告（report ID `0x11`），先 `root.getFeature(0x1000)` 拿电池功能索引，再调 `getBatteryLevelStatus`；多字节字段为大端序。鼠标需切到接收器所在通道，否则接收器上找不到设备
- **图标**：用 Segoe MDL2 Assets 字形（`E850`~`E859` 为 0~9 格电池，`E962` 为鼠标）经 Pillow 渲染，鼠标周围擦出约 2px 透明缺口，缩到 16px 时不至于粘连

## 已知限制

- 仅支持 Windows
- 2.4G 通道要求鼠标已切到接收器；切走后蓝牙侧可能仍报缓存值，因此程序优先尝试 2.4G
- 依赖系统字体 `C:\Windows\Fonts\segmdl2.ttf`（Win10/11 自带）
- MDL2 只有 9 格电池字形，90% 与 100% 的图标相同；`E85A` 起是带插头的充电图标，故未使用
- 左键弹菜单复用了 pystray 的私有方法，pystray 大版本升级后可能失效；届时会自动退回「左键 = 立即刷新」
- 16px 托盘尺寸下，鼠标图形会退化成右上角一个小凸起，属像素密度限制

## 文件结构

```
main.pyw          托盘程序：图标、菜单、阈值状态机、通知、开机自启
battery.py        电量读取：蓝牙设备属性 + 2.4G HID++ 2.0
run.vbs           静默启动（pythonw，无控制台）
requirements.txt  依赖：pystray / pillow / hidapi
config.json       运行时生成，本地配置
```
