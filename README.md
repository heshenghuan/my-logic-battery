# LogicBattery

Windows 托盘电量监控，为罗技 MX Anywhere 2 / 2s 写的：常驻显示鼠标电量，低于阈值时弹系统通知提醒充电。

## 功能

- 托盘图标常驻显示电量：电池格数 + 颜色（绿 = 正常，橙 = 已达阈值，红 = ≤10%，灰 = 未连接）
- 两种图标样式，菜单内随时切换：**电池 + 鼠标**（便于和系统自带电池图标区分）/ **仅电池**
- 电量低于阈值时弹 Windows 通知；电量回到「阈值 + 5%」以上才会再次提醒，避免反复打扰
- 左键和右键都打开同一个菜单
- 蓝牙与 Unifying 2.4G 双通道，`config.json` 里分成 `2.4g` / `bluetooth` 两个列表
- 启动时先轮询 2.4G 列表，没命中再轮询蓝牙列表；命中后固定跟踪这只设备，掉线才重新搜索
- 用蓝牙地址 / HID++ unitId 匹配，不靠名字猜
- 2.4G 通道会读回型号名与 unitId；每只鼠标可单独设置显示名
- 菜单内可开关开机自启

## 兼容性

- 系统：Windows 10 / 11（依赖 SetupAPI 与 Win32 托盘 API）
- 设备：
  - **MX Anywhere 2**：蓝牙 BLE 与 Unifying 接收器（PID `0xC52B`）两条通道均已实测通过
  - **MX Anywhere 2s**：经 Unifying 接收器实测通过，可读到电量与型号名（`Wireless Mobile Mouse MX Anywhere 2S`）；蓝牙通道尚未测试
- 两者同属 HID++ 2.0 设备，电池功能索引与设备索引均由程序自动探测，无需针对型号改配置

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

首次运行会在同目录生成 `config.json`，设备按连接方式分成 `2.4g` / `bluetooth` 两个列表。初始时两组都是空的，托盘图标显示为灰色电量，状态行是「未绑定设备」。

这时打开托盘菜单 → **发现设备**，程序会扫描并列出可绑定的设备：

```
发现设备 >
    重新扫描
    - - - -
    MX Anywhere 2S · 2.4G · 50%
    MX Anywhere 2 · 蓝牙 · 86%
```

点其中一项即可绑定，条目会写入 `config.json` 并立即开始显示电量。蓝牙只列在线且属于 HID over GATT 的设备（耳机之类不会出现），断开的设备不列出。

绑定后的条目形如：

```json
{
  "devices": {
    "2.4g": [
      {
        "name": "Wireless Mobile Mouse MX Anywhere 2S",
        "display_name": "",
        "address": "",
        "unit_id": "DEADBEEF"
      }
    ],
    "bluetooth": []
  }
}
```

之后配置就是白名单，程序只按它找设备。要加新设备，再走一次「发现设备」即可。

程序启动后先轮询 `2.4g` 列表，全部没命中再轮询 `bluetooth` 列表；命中一条即锁定这只设备，之后每轮只读它，读不到才重新搜索。

| 字段 | 说明 |
| --- | --- |
| `name` | 原始型号名，用于显示和兜底匹配 |
| `display_name` | 托盘上显示的名字，留空则显示 `name` |
| `address` | 蓝牙 BLE 地址，大写十六进制，蓝牙列表的稳定标识 |
| `unit_id` | HID++ 的 unitId，接收器列表的稳定标识，由设备固件决定、不随名称变化 |

`address` / `unit_id` 留空时会退回按 `name` 匹配。手动修改 `config.json` 后不需要重启：下一次轮询，或点菜单「立即刷新」，就会重新加载。

`--scan` 可以直接打印出这些值：

```powershell
.venv\Scripts\python.exe main.pyw --scan
```

```
蓝牙  地址=AABBCCDDEEFF  名称=MX Anywhere 2  已断开
2.4G  单元ID=DEADBEEF  名称=Wireless Mobile Mouse MX Anywhere 2S  电量 50%
```

新加一只鼠标时，把它接到电脑上跑一次 `--scan`，把对应行抄进 `2.4g` 或 `bluetooth` 列表即可。

旧版配置（扁平字段、`connection` 单列表、`alias`）会在启动时自动迁移到新的两组结构。

想先确认能不能读到电量（不启动托盘，只打印一次结果）：

```powershell
.venv\Scripts\python.exe main.pyw --once
```

正常时输出类似 `BatteryReading(level=50, source='2.4G', device_name='2S', detail='')`，其中 `device_name` 是 `display_name`，未设置时就是型号名。

## 使用

托盘图标上左键或右键都能打开菜单：

| 菜单项 | 说明 |
| --- | --- |
| 状态行 | 设备名 · 电量 · 连接方式；未连接时显示原因 |
| 立即刷新 | 立刻读一次电量 |
| 发现设备 | 展开候选列表（型号 · 通道 · 电量），已绑定的设备不再显示；点击一项即绑定并写入 `config.json`；子菜单里的「重新扫描」会刷新列表 |
| 电量低于 | 通知阈值，可选 10% ~ 50% |
| 图标样式 | 「电池 + 鼠标」或「仅电池」 |
| 开机自启 | 在启动文件夹创建/删除快捷方式 |
| 重启程序 | 用当前解释器重新启动自身，改完代码或配置后不用回项目目录手动启动 |
| 退出 | 关闭程序（托盘图标随之消失） |

不想要开机自启时，先在菜单里取消勾选，再退出即可；直接删文件夹会残留启动项。

## 配置文件

`config.json` 首次运行时自动生成，修改后自动热加载，下一次轮询或点「立即刷新」即生效（菜单里的阈值和图标样式会直接写回该文件）。

| 字段 | 说明 |
| --- | --- |
| `devices` | `{"2.4g": [...], "bluetooth": [...]}`，按 2.4G 优先的顺序搜索。每项含 `name` / `display_name` / `address` / `unit_id`，含义见上文安装部分 |
| `threshold` | 通知阈值（百分比） |
| `interval_minutes` | 轮询间隔（分钟），最小按 1 分钟处理 |
| `icon_style` | `both` = 电池 + 鼠标，`battery` = 仅电池 |

## 实现说明

- **蓝牙**：Windows 会把 BLE HID 设备的电量挂在设备节点属性 `DEVPKEY_Bluetooth_Battery`（`{104EA319-6EE2-4701-BD47-8DDBF425BBE5}, 2`）上，用 SetupAPI 直接读取，无需启动 PowerShell
- **2.4G**：Unifying 接收器不暴露系统属性，只能发 HID++ 2.0 报文。走 `usage 0xFF00 / 2` 的 20 字节长报告（report ID `0x11`），先 `root.getFeature(0x1000)` 拿电池功能索引，再调 `getBatteryLevelStatus`；型号名通过 `getFeature(0x0005)` 读取，稳定标识 unitId 则来自 `getFeature(0x0003)` 的首 4 字节。多字节字段为大端序。鼠标需切到接收器所在通道，否则接收器上找不到设备
- **图标**：用 Segoe MDL2 Assets 字形（`E850`~`E859` 为 0~9 格电池，`E962` 为鼠标）经 Pillow 渲染，鼠标周围擦出约 2px 透明缺口，缩到 16px 时不至于粘连

## 已知限制

- 只有 Windows 版本
- 多只鼠标同时在线时，2.4G 列表优先于蓝牙列表；同一列表内按书写顺序取第一条命中的，之后固定跟踪该设备
- 首次启动会自动扫描并列出可用设备，但不会自动绑定，必须从菜单里选择后才会写入 `config.json`
- 2.4G 上同一接收器里的罗技键盘也会出现在候选列表里，绑定前请确认型号
- `address` / `unit_id` 为空时才退回按 `name` 匹配；名称匹配按整名优先、单词边界包含，`MX Anywhere 2` 不会误匹配 `MX Anywhere 2S`
- 2.4G 通道要求鼠标已切到接收器；切走后蓝牙侧可能仍报缓存值
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
