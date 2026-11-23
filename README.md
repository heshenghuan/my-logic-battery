# LogicBattery

Windows 托盘电量监控，支持罗技 MX Anywhere 2 / 2s（蓝牙 / Unifying）和 Mloong MX301 PRO（2.4G）：常驻显示鼠标电量，低于阈值时弹系统通知提醒充电。

## 功能

- 托盘图标常驻显示电量：电池格数 + 颜色（绿 = 正常，橙 = 已达阈值，红 = ≤10%，灰 = 未连接）
- 两种图标样式，菜单内随时切换：**电池 + 鼠标**（便于和系统自带电池图标区分）/ **仅电池**
- 电量低于阈值时弹 Windows 通知；电量回到「阈值 + 5%」以上才会再次提醒，避免反复打扰
- 左键和右键都打开同一个菜单
- 设备统一为 `devices` 列表，每条用 `type` 指定设备类；列表顺序即轮询优先级，建议把 2.4G 排在蓝牙前面
- 启动时按列表顺序轮询，命中一条即锁定这只设备，掉线才重新搜索
- 用蓝牙地址 / 接收器标识（罗技 HID++ unitId 或 Mloong 序列号）匹配，不靠名字猜
- 2.4G 通道会读回型号名与 unitId；每只鼠标可单独设置显示名
- 菜单内可开关开机自启

## 兼容性

- 系统：Windows 10 / 11（依赖 SetupAPI 与 Win32 托盘 API）
- 设备：
  - **MX Anywhere 2**：蓝牙 BLE 与 Unifying 接收器（PID `0xC52B`）两条通道均已实测通过
  - **MX Anywhere 2s**：经 Unifying 接收器实测通过，可读到电量与型号名（`Wireless Mobile Mouse MX Anywhere 2S`）；蓝牙通道尚未测试
  - **Mloong MX301 PRO**：2.4G 接收器（VID `0xA8A5` / PID `0x2255`）已实测通过，可读到电量与型号名；蓝牙通道走系统属性，尚未实测
- 罗技设备同属 HID++ 2.0，电池功能索引与设备索引均由程序自动探测，无需针对型号改配置；Mloong 使用单独的厂商报文，绑定项的类型为 `mloong_2.4g`

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

首次运行会在同目录生成 `config.json`，`devices` 初始为空列表。托盘图标显示为灰色电量，状态行是「未绑定设备」。

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
  "devices": [
    {
      "type": "logitech_2.4g",
      "name": "Wireless Mobile Mouse MX Anywhere 2S",
      "display_name": "",
      "unit_id": "DEADBEEF"
    },
    {
      "type": "mloong_2.4g",
      "name": "MX301 PRO_2.4G",
      "display_name": "",
      "unit_id": "D1K_XXXXXXXXXXXX_A72V1"
    },
    {
      "type": "bluetooth",
      "name": "MX Anywhere 2",
      "display_name": "",
      "unit_id": "AABBCCDDEEFF"
    }
  ]
}
```

之后配置就是白名单，程序只按它找设备。要加新设备，再走一次「发现设备」即可。

程序按列表顺序轮询，命中一条即锁定这只设备，之后每轮只读它，读不到才重新搜索。因此 2.4G 条目建议排在蓝牙前面。

| 字段 | 说明 |
| --- | --- |
| `type` | 设备类：`logitech_2.4g`、`mloong_2.4g` 或 `bluetooth` |
| `name` | 原始型号名，用于显示和兜底匹配 |
| `display_name` | 托盘上显示的名字，留空则显示 `name` |
| `unit_id` | 稳定标识，所有类型统一用这一字段：罗技 2.4G 为 HID++ unitId，Mloong 2.4G 为接收器序列号，蓝牙为 BLE 地址（大写十六进制）。由设备固件决定、不随名称变化 |

`type` 与 `unit_id` 共同构成设备 uid（如 `mloong_2.4g:D1K_...`），uid 不写进配置；`unit_id` 留空时退回按 `name` 匹配。手动修改 `config.json` 后不需要重启：下一次轮询，或点菜单「立即刷新」，就会重新加载。

`--scan` 可以直接打印出这些值：

```powershell
.venv\Scripts\python.exe main.pyw --scan
```

```
蓝牙  单元ID=AABBCCDDEEFF  名称=MX Anywhere 2  已断开
2.4G  单元ID=DEADBEEF  名称=Wireless Mobile Mouse MX Anywhere 2S  电量 50%
2.4G  单元ID=D1K_XXXXXXXXXXXX_A72V1  名称=MX301 PRO_2.4G  电量 90%
```

新加一只鼠标时，把它接到电脑上跑一次 `--scan`，建议直接走菜单「发现设备」绑定，会自动写入 `type` 和稳定标识；手动写配置时，罗技 2.4G 用 `logitech_2.4g`，Mloong 用 `mloong_2.4g`，蓝牙用 `bluetooth`。

不再兼容旧版配置。若 `config.json` 仍是旧格式（`2.4g` / `bluetooth` 分组、`protocol`、`connection` 等），直接删除该文件再启动，程序会按首次运行生成空的 `devices` 列表，再走菜单「发现设备」绑定即可。

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
| `devices` | 设备列表，每项用 `type` 指定设备类（`logitech_2.4g` / `mloong_2.4g` / `bluetooth`）；列表顺序即轮询优先级，建议 2.4G 在前，字段含义见上文安装部分 |
| `threshold` | 通知阈值（百分比） |
| `interval_minutes` | 轮询间隔（分钟），最小按 1 分钟处理 |
| `icon_style` | `both` = 电池 + 鼠标，`battery` = 仅电池 |

## 实现说明

- **蓝牙**：Windows 会把 BLE HID 设备的电量挂在设备节点属性 `DEVPKEY_Bluetooth_Battery`（`{104EA319-6EE2-4701-BD47-8DDBF425BBE5}, 2`）上，用 SetupAPI 直接读取，无需启动 PowerShell
- **2.4G**：Unifying 接收器不暴露系统属性，只能发 HID++ 2.0 报文。走 `usage 0xFF00 / 2` 的 20 字节长报告（report ID `0x11`），先 `root.getFeature(0x1000)` 拿电池功能索引，再调 `getBatteryLevelStatus`；型号名通过 `getFeature(0x0005)` 读取，稳定标识 unitId 则来自 `getFeature(0x0003)` 的首 4 字节。多字节字段为大端序。鼠标需切到接收器所在通道，否则接收器上找不到设备
- **2.4G（Mloong MX301）**：接收器在 `usage 0xFF01 / 0x10` 接口上暴露 64 字节无编号报告，帧格式见下节；稳定标识用接收器序列号，存进 `unit_id`
- **图标**：用 Segoe MDL2 Assets 字形（`E850`~`E859` 为 0~9 格电池，`E962` 为鼠标）经 Pillow 渲染，鼠标周围擦出约 2px 透明缺口，缩到 16px 时不至于粘连

### Mloong 2.4G 协议

请求固定为 8 字节头 + 56 个 `00`，应答为 10 字节头 + 54 个 `00`：

```
请求：55 30 a5 0b 2e 01 01 01  00 × 56
应答：aa 30 a5 0b 0a 01 01 01  LL CC  00 × 54
```

| 偏移 | 含义 |
| --- | --- |
| 0 | `55` 请求 / `AA` 应答 |
| 1 | 命令码，电池为 `0x30` |
| 2–3 | 固定 `A5 0B` |
| 4 | 负载长度：请求 `0x2E`，应答 `0x0A` |
| 5–7 | 固定 `01 01 01` |
| 8 | 电量百分比 0–100（`LL`） |
| 9 | 充电标志（`CC`），0 = 未充电；程序暂未展示 |

Windows 下 hidapi 写入前要补一个 `0x00` report ID，读回的数据不含该字节。报文由 Mloong 官方 Web HUB 的设备包（`driver.wasm`）逆向得到，并非公开文档。

## 已知限制

- 只有 Windows 版本
- 多只鼠标同时在线时，2.4G 列表优先于蓝牙列表；同一列表内按书写顺序取第一条命中的，之后固定跟踪该设备
- 首次启动会自动扫描并列出可用设备，但不会自动绑定，必须从菜单里选择后才会写入 `config.json`
- 2.4G 上同一接收器里的罗技键盘也会出现在候选列表里，绑定前请确认型号
- Mloong 协议目前只按 MX301 PRO V2 的设备包实现并实测 2.4G 通道；同 VID/PID 的 MX301 V2（非 PRO）未验证，应答中的充电标志也暂未显示
- `unit_id` 为空时才退回按 `name` 匹配；名称匹配按整名优先、单词边界包含，`MX Anywhere 2` 不会误匹配 `MX Anywhere 2S`
- 2.4G 通道要求鼠标已切到接收器；切走后蓝牙侧可能仍报缓存值
- 依赖系统字体 `C:\Windows\Fonts\segmdl2.ttf`（Win10/11 自带）
- MDL2 只有 9 格电池字形，90% 与 100% 的图标相同；`E85A` 起是带插头的充电图标，故未使用
- 左键弹菜单复用了 pystray 的私有方法，pystray 大版本升级后可能失效；届时会自动退回「左键 = 立即刷新」
- 16px 托盘尺寸下，鼠标图形会退化成右上角一个小凸起，属像素密度限制

## 文件结构

```
main.pyw          托盘程序：图标、菜单、阈值状态机、通知、开机自启
battery.py        公共层：MouseDevice 基类、读数结构、设备分发与轮询入口
devices/          设备实现包，新增设备在此加模块并注册
  __init__.py     DEVICE_CLASSES 注册表（列表顺序即轮询优先级，2.4G 在前）
  _hid.py         2.4G HID 接收器的读取模板基类
  bluetooth.py    蓝牙 BLE：读系统电量属性（与品牌无关）
  logitech.py     罗技 Unifying：HID++ 2.0
  mloong.py       Mloong MX301：厂商 64 字节报文
winapi.py         Windows SetupAPI / CFGMGR32 封装：设备枚举与属性读取
run.vbs           静默启动（pythonw，无控制台）
requirements.txt  依赖：pystray / pillow / hidapi
config.json       运行时生成，本地配置
```
