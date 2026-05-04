# ipmg - 网络设备IP一键配置工具

> 一个 Windows 命令行 TUI 工具，专为网络工程师设计，用于一键切换网卡的 IP 配置以对接不同管理网段的网络设备（交换机、路由器、防火墙、AP、AC 等）。

> 本项目完全由Vibe Coding完成(Model:GLM-5.1 | IDE:VSCode | Agent:Costrict)。

## 目录

- [背景](#背景)
- [功能特性](#功能特性)
- [界面预览](#界面预览)
- [快速开始](#快速开始)
- [使用指南](#使用指南)
  - [配置IP流程](#配置ip流程)
  - [恢复IP流程](#恢复ip流程)
  - [设备管理](#设备管理)
  - [导入导出](#导入导出)
- [配置文件详解](#配置文件详解)
  - [设备配置字段](#设备配置字段)
  - [IP模式说明](#ip模式说明)
  - [配置文件示例](#配置文件示例)
- [项目结构](#项目结构)
- [模块说明](#模块说明)
- [技术实现](#技术实现)
- [编译打包](#编译打包)
- [注意事项](#注意事项)
- [常见问题](#常见问题)
- [技术栈](#技术栈)
- [License](#license)

## 背景

网络工程师在日常运维中，经常需要将笔记本电脑的网卡 IP 改到设备管理网段（如 `10.251.251.x`），登录设备 Web 管理页面完成配置后，再改回原来的 IP。这个过程频繁且繁琐：

1. 打开"控制面板 → 网络连接 → 属性 → IPv4 → 手动填写 IP/掩码/网关"
2. 浏览器输入设备管理地址
3. 操作完成后又要改回 DHCP 或原来的静态 IP

**ipmg** 将这个流程简化为一个数字选择：

```
选择网卡 → 选择设备 → 一键配置 → 自动Ping验证 → 打开管理页面
```

操作完成后一键恢复原始 IP，全程不超过 10 秒。

## 功能特性

| 功能 | 说明 |
|------|------|
| 一键配置 IP | 选择网卡和设备，自动计算网卡 IP 并配置静态地址 |
| 一键恢复 IP | 自动备份操作前的 IP 配置，完成后一键恢复（DHCP 或静态） |
| 设备管理 | 添加、编辑、删除、收藏常用设备配置 |
| 自动 Ping 验证 | 配置完成后自动 Ping 设备验证连通性 |
| 管理页面直达 | 配置完成后直接打开设备 Web 管理页面 |
| 网卡状态实时显示 | 标题栏显示当前选中网卡的 IP、掩码、网关、DHCP/静态状态 |
| 断开网卡支持 | 未接线的网卡也会显示，可配置静态 IP（恢复 DHCP 需要接线） |
| 配置导入导出 | YAML 格式，方便团队共享设备配置 |
| 管理员自动提权 | 启动时自动检测权限，非管理员自动 UAC 提权 |
| 操作日志 | 所有操作自动记录到日志文件，便于审计排查 |

## 界面预览

### 主菜单

```
========================================
     网络设备IP一键配置工具 v1.0
========================================
  当前网卡: 以太网 2 (08:26:AE:3B:68:20)
  IP 地址 : 192.168.1.100 / 255.255.255.0  [DHCP]  网关: 192.168.1.1
----------------------------------------

请选择操作
-------------------------------------------------------
 > 1. 配置IP - 选择网卡和设备，一键配置
   2. 恢复IP - 恢复网卡原始IP配置
   3. 管理设备 - 添加/编辑/删除设备
   4. 导出配置
   5. 导入配置
   6. 退出
-------------------------------------------------------
请输入序号 (默认 1):
```

### 配置流程

```
正在获取网卡列表...

请选择网卡
-------------------------------------------------------
   1. [USB] 以太网 2 | 已连接 | IP: 192.168.1.100 | MAC: 08:26:AE:3B:68:20
 > 2. [以太网] 以太网 | 未连接 | 无IP | MAC: AA:BB:CC:DD:EE:FF  <-- 上次
   0. <-- 返回上一页
-------------------------------------------------------

已选网卡: 以太网 2 | 请选择设备
-------------------------------------------------------
 > 1. [*] 核心交换机 | 设备: 10.251.251.251 | -> 网卡IP: 10.251.251.254 [Web]
   2. [ ] 防火墙 | 设备: 192.168.0.1 | -> 网卡IP: 192.168.0.254 [Web]
   0. <-- 返回上一页
-------------------------------------------------------

--- 配置确认 ---
  网卡:     以太网 2 (08:26:AE:3B:68:20)
  设备名称: 核心交换机
  设备IP:   10.251.251.251
  网卡将配置IP: 10.251.251.254
  子网掩码: 255.255.255.0

确认执行配置？(Y/n): y

正在备份网卡IP...
[OK] 网卡IP备份成功
正在配置网卡IP为 10.251.251.254...
[OK] IP配置成功

正在Ping设备 10.251.251.251 验证连通性...
[OK] 设备 10.251.251.251 可达

管理页面: https://10.251.251.251
按 1 打开管理页面，按其他键返回: 1
[OK] 管理页面已打开
```

## 快速开始

### 方式一：直接运行 exe（推荐）

1. 下载 `ipmg.exe`
2. 双击运行（会自动弹出 UAC 提权确认）
3. 首次运行选择"管理设备"添加你的设备信息
4. 之后选择"配置IP"即可一键切换

### 方式二：从源码运行

```bash
# 克隆项目
git clone https://github.com/<your-username>/ipmg.git
cd ipmg

# 安装依赖
pip install -r requirements.txt

# 以管理员身份运行（需要管理员权限操作网卡）
python main.py
```

## 使用指南

### 配置IP流程

完整的配置流程包含以下步骤：

1. **选择网卡** — 列出所有物理网卡（含未连接的），上次使用的网卡会标记"上次"
2. **选择设备** — 从已添加的设备列表中选择，收藏的设备标有 `*`
3. **确认配置** — 显示网卡、设备IP、将要配置的网卡IP、子网掩码等信息
4. **自动备份** — 自动备份当前网卡的 IP 配置
5. **执行配置** — 通过 WMI 设置静态 IP
6. **Ping 验证** — 自动 Ping 设备检查连通性
7. **打开管理页面** — 可选择直接打开设备的 Web 管理页面

### 恢复IP流程

1. **选择网卡** — 仅显示有备份记录的网卡
2. **显示备份信息** — 显示备份的 IP 和模式（DHCP/静态）
3. **确认恢复** — 确认后执行恢复
4. **执行恢复** — DHCP 模式调用 `EnableDHCP`，静态模式调用 `EnableStatic`

### 设备管理

支持以下操作：
- **添加设备** — 输入设备名称、IP、子网掩码、网关、管理URL
- **编辑设备** — 修改已有设备的任意字段
- **删除设备** — 确认后删除
- **收藏/取消收藏** — 收藏的设备在列表中标记 `*`

### 导入导出

- **导出** — 将当前所有设备配置导出为 YAML 文件
- **导入** — 从 YAML 文件导入设备配置（会覆盖当前配置）

## 配置文件详解

配置文件存放在 `%USERPROFILE%\ipmg\config.yaml`：

```
C:\Users\<用户名>\ipmg\
├── config.yaml              # 设备配置、网卡记忆、IP 备份
└── logs\
    └── ipmanager_YYYYMMDD.log   # 操作日志
```

### 设备配置字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `name` | string | ✅ | — | 设备名称，如"核心交换机-A" |
| `device_ip` | string | ✅ | — | 设备管理口 IP 地址 |
| `ip_mode` | string | — | `auto` | 网卡 IP 策略：`auto`（自动计算）或 `manual`（手动指定） |
| `adapter_ip` | string | — | `""` | 当 `ip_mode=manual` 时，手动指定的网卡 IP |
| `subnet_mask` | string | ✅ | `255.255.255.0` | 子网掩码 |
| `gateway` | string | — | `""` | 网关（可选，通常管理网段不需要） |
| `management_url` | string | — | `https://{device_ip}` | 设备 Web 管理页面 URL |
| `favorite` | bool | — | `false` | 是否收藏 |

### IP模式说明

**自动模式（auto）**：

根据设备 IP 和子网掩码，自动计算网段的最后一个可用 IP 作为网卡 IP。

```
设备 IP: 10.251.251.251 / 255.255.255.0
       → 网卡 IP 自动计算为: 10.251.251.254
```

计算逻辑：遍历网段所有地址，排除设备 IP、网络地址、广播地址，取最后一个可用 IP。

**手动模式（manual）**：

直接使用 `adapter_ip` 字段指定的 IP 地址。

### 配置文件示例

```yaml
devices:
  - name: 核心交换机
    device_ip: 10.251.251.251
    ip_mode: auto
    adapter_ip: ""
    subnet_mask: 255.255.255.0
    gateway: ""
    management_url: https://10.251.251.251
    favorite: true

  - name: 防火墙
    device_ip: 192.168.0.1
    ip_mode: manual
    adapter_ip: 192.168.0.100
    subnet_mask: 255.255.255.0
    gateway: ""
    management_url: https://192.168.0.1
    favorite: false

network_adapters:
  last_selected_mac: "AA:BB:CC:DD:EE:FF"

backups:
  AA:BB:CC:DD:EE:FF:
    ip: 192.168.1.100
    mask: 255.255.255.0
    gateway: 192.168.1.1
    is_dhcp: true
    adapter_name: "以太网"
```

## 项目结构

```
ipmg/
├── main.py                    # 程序入口
├── requirements.txt           # Python 依赖清单
├── .gitignore                 # Git 忽略规则
│
├── core/                      # 核心功能模块
│   ├── __init__.py
│   ├── adapter_manager.py     # 网卡枚举与选择
│   ├── config_manager.py      # 配置文件管理
│   ├── ip_configurator.py     # IP 配置执行
│   ├── network_utils.py       # 网络工具函数
│   └── browser_launcher.py    # 浏览器启动
│
├── ui/                        # TUI 界面模块
│   ├── __init__.py
│   ├── app.py                 # 主界面与流程控制
│   ├── device_manager.py      # 设备管理界面
│   └── header.py              # 固定标题栏
│
└── utils/                     # 工具模块
    ├── __init__.py
    ├── backup.py              # IP 备份与恢复
    └── logger.py              # 日志记录
```

## 模块说明

### core/adapter_manager.py

通过 Windows `AllocateAndGetTcpIpTable2` API（ctypes 调用）枚举所有物理网卡，获取：
- 网卡友好名称（如"以太网 2"）
- MAC 地址
- 当前 IP / 子网掩码
- 连接状态（已连接 / 未连接 / 介质断开）
- 网卡类型（以太网 / 无线 / USB）

支持断开的网卡显示，不会过滤掉 `NetConnectionStatus=7` 的适配器。

### core/config_manager.py

YAML 格式的配置文件管理：
- 自动创建默认配置（首次运行）
- 配置文件损坏时自动备份并重建
- 设备配置规范化（补充缺失字段）
- 导入导出功能

### core/ip_configurator.py

通过 WMI `Win32_NetworkAdapterConfiguration` 执行 IP 配置：
- `EnableStatic()` — 设置静态 IP 和子网掩码
- `SetGateways()` — 设置默认网关
- `EnableDHCP()` — 恢复 DHCP 自动获取
- `get_current_ip_config()` — 查询当前 IP 配置状态

### core/network_utils.py

网络工具函数：
- `ping_host()` — ICMP/TCP Ping 检测主机可达性
- `check_ip_conflict()` — IP 冲突检测
- `calculate_adapter_ip_auto()` — 自动计算网卡 IP（取网段最后可用 IP）
- `validate_ip()` / `validate_subnet_mask()` — IP 和掩码格式验证

### core/browser_launcher.py

在管理员进程中启动浏览器（`webbrowser.open()` 在管理员进程中会静默失败）：
- 优先使用 `os.startfile()`
- 备选使用 `ctypes.windll.shell32.ShellExecuteW`

### ui/app.py

主 TUI 界面：
- `_pick_option()` — 自定义数字选择菜单（支持返回上一页、默认选项）
- `show_main_menu()` — 主菜单
- `run_configure_flow()` — 配置 IP 完整流程（10 步）
- `run_restore_flow()` — 恢复 IP 流程
- `run_export_import_flow()` — 导入导出流程

### ui/header.py

固定标题栏模块：
- 每次进入新页面时 `cls` 清屏 + 重绘标题栏
- 实时显示当前选中网卡的 IP、掩码、网关、DHCP/静态状态

### utils/backup.py

网卡 IP 备份与恢复：
- `backup_adapter_config()` — 通过 WMI 查询当前 IP 并保存到配置文件
- `restore_adapter_config()` — 根据备份类型恢复 DHCP 或静态 IP

### utils/logger.py

Python logging 日志模块：
- 日志文件按日期滚动
- 自动创建日志目录

## 技术实现

### IP 配置原理

使用 WMI（Windows Management Instrumentation）的 `Win32_NetworkAdapterConfiguration` 类操作网卡 IP：

```python
import wmi

c = wmi.WMI()

# 查找网卡
for adapter in c.Win32_NetworkAdapter(NetConnectionID="以太网 2"):
    # 通过 Index 关联到配置对象
    for config in c.Win32_NetworkAdapterConfiguration(Index=adapter.DeviceID):
        # 设置静态 IP
        config.EnableStatic(IPAddress=["10.251.251.254"], SubnetMask=["255.255.255.0"])
        # 恢复 DHCP
        config.EnableDHCP()
```

### 网卡枚举原理

使用 Windows IP Helper API（`iphlpapi.dll`）的 `AllocateAndGetTcpIpTable2` 函数获取所有网卡接口信息，包括：
- `MIB_IF_ROW2` 结构体中的接口类型、连接状态、物理地址
- `MIB_UNICASTIPADDRESS_ROW` 结构体中的 IP 地址和前缀长度

### 管理员提权

通过 `ctypes.windll.shell32.ShellExecuteW` 调用 `runas` 动词实现 UAC 提权：

```python
ctypes.windll.shell32.ShellExecuteW(
    None, "runas", sys.executable,
    " ".join(sys.argv), None, 1
)
```

## 编译打包

使用 PyInstaller 打包为单文件 exe：

```bash
# 安装 PyInstaller
pip install pyinstaller

# 编译（单文件模式）
python -m PyInstaller --onefile --name ipmg --distpath . --clean main.py
```

编译产物约 15MB，首次启动约 3-5 秒（解压到临时目录）。

## 注意事项

1. **管理员权限** — 程序必须以管理员权限运行才能操作网卡 IP，启动时会自动 UAC 提权
2. **DHCP 恢复需要接线** — 断开的网卡无法切换到 DHCP 模式（WMI `EnableDHCP` 返回错误码 84），需要接入网线后重试
3. **仅支持 Windows** — 使用了 WMI、Windows API、`os.startfile()` 等 Windows 专有接口
4. **网卡 IP 冲突** — 同一网段只能有一个网卡配置该 IP
5. **中文编码** — 程序启动时自动设置控制台为 UTF-8 编码

## 常见问题

**Q: 启动后闪退？**
A: 程序需要管理员权限。右键 `ipmg.exe` → 属性 → 兼容性 → 勾选"以管理员身份运行此程序"。

**Q: 恢复 DHCP 失败，提示"网卡当前未连接"？**
A: 断开网线的网卡无法切换到 DHCP 模式。请先接入网线，再执行恢复操作。

**Q: 打开管理页面没反应？**
A: 在管理员进程中浏览器启动方式不同，程序已做兼容处理。如果仍无法打开，请手动复制 URL 到浏览器。

**Q: 配置文件在哪里？**
A: `%USERPROFILE%\ipmg\config.yaml`（即 `C:\Users\<你的用户名>\ipmg\config.yaml`）。

**Q: 如何在多台电脑上共享设备配置？**
A: 使用"导出配置"功能导出 YAML 文件，在其他电脑上使用"导入配置"导入。

**Q: 支持无线网卡吗？**
A: 支持。无线网卡也会在网卡列表中显示，但需要注意 WiFi 连接状态。

## 技术栈

| 库 | 版本 | 用途 |
|----|------|------|
| Python | 3.14+ | 运行环境 |
| [Rich](https://github.com/Textualize/rich) | >=13.0.0 | 终端美化输出（颜色、表格、样式） |
| [PyYAML](https://pyyaml.org/) | >=6.0 | YAML 配置文件读写 |
| [WMI](https://pypi.org/project/WMI/) | >=1.5.0 | Windows 管理规范接口（IP 配置操作） |
| [pywin32](https://github.com/mhammond/pywin32) | >=306 | Windows API 调用支持 |
| [netaddr](https://github.com/netaddr/netaddr) | >=0.9.0 | IP 地址计算（网段分析、可用 IP 计算） |
| [PyInstaller](https://pyinstaller.org/) | >=6.0 | 打包为单文件 exe（开发依赖） |

## License

[MIT](LICENSE)
