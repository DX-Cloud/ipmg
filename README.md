<div align="center">

# ipmg

**网络设备 IP 一键配置工具**

把笔记本网卡切到设备管理网段、登录设备、再切回来——从"控制面板点四层对话框"变成"选一个数字"。

[![Release](https://img.shields.io/github/v/release/DX-Cloud/ipmg?sort=semver&label=release)](https://github.com/DX-Cloud/ipmg/releases)
[![Downloads](https://img.shields.io/github/downloads/DX-Cloud/ipmg/total)](https://github.com/DX-Cloud/ipmg/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D4)](#环境要求)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)](requirements.txt)
[![Tests](https://img.shields.io/badge/tests-219%20passed-3fb950)](tests)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[功能特性](#功能特性) · [快速开始](#快速开始) · [使用指南](#使用指南) · [命令行参数](#命令行参数) · [配置文件](#配置文件) · [架构](#架构) · [开发](#开发) · [常见问题](#常见问题) · [更新日志](CHANGELOG.md)

</div>

---

## 简介

网络工程师调设备时的日常：把笔记本网卡改到设备管理网段（如 `10.251.251.x`）→ 登录设备 Web 管理页面 → 改完再切回原来的 IP。手动做一次要经过"控制面板 → 网络连接 → 属性 → IPv4 → 手动填 IP/掩码/网关"，改回来还得再来一遍。

ipmg 把这个过程压缩成一次选择：

```text
选择网卡 → 选择设备 → 一键配置 → 打开管理页面 → 用完一键还原
```

- **地址自动算**：按设备 IP 与掩码推导网卡地址（取网段内最后一个可用主机地址，O(1) 计算，不遍历地址段），也支持手动指定
- **改动可回退**：配置前自动备份，支持多级撤销栈与历史记录恢复，失败不动网卡
- **纯 Windows 原生**：WMI 改地址、原生 TUI 交互（无需 GUI 框架）、单文件 exe 免安装
- **可常驻**：托盘 + 全局热键 + 快捷预设，常用的"切到某网段"一键完成，支持开机自启

## 功能特性

### 配置与恢复

| 功能 | 说明 |
| :--- | :--- |
| 一键配置 IP | 选择网卡与设备，自动计算网卡 IP 并写入静态地址 |
| 追加 IP | 确认时输入 `a` 把新地址**追加**到网卡，保留现有地址与默认网关，适合一台机器同时连多个网段 |
| 一键恢复 IP | 恢复为 DHCP 或原静态配置，完整还原本机全部 IPv4 地址（不只是主 IP） |
| 多级撤销栈 | 每次配置前自动备份，可恢复到最近 10 次中的任意一次 |
| IP 历史记录 | 保留最近 10 条静态 IP 配置（自动去重合并），可在恢复界面直接选用 |
| 断开网卡支持 | 未接线的网卡也会列出，可为其配置静态 IP（切回 DHCP 需要接线） |
| WMI 错误提示 | 常见返回码附带解决建议（如 84 = 网卡未连接、97 = IP 已被占用） |

### 常驻与自动化

| 功能 | 说明 |
| :--- | :--- |
| 常驻后台 | 托盘图标常驻、可隐藏控制台；单实例运行，重复启动会唤出既有窗口 |
| 全局热键 | 默认 `Ctrl+Alt+I` 唤出/收起主界面，设置页可改；被占用时明确提示 |
| 快捷预设 | 把「网卡 + 设备 + 覆盖/追加」存成预设并绑定热键，一键应用；可选应用前二次确认 |
| 开机自启 | 计划任务登录后静默以最高权限启动，**不弹 UAC**；设置页可开关 |
| 动作钩子 | 配置/恢复成功后自动执行自定义命令，便于对接通知与自动化 |
| 版本更新检测 | 启动时后台检测 GitHub（或镜像站）最新版本，可一键打开 Release 页面 |

### 设备与配置管理

| 功能 | 说明 |
| :--- | :--- |
| 设备管理 | 添加、编辑、删除、收藏设备（字段与校验见[配置文件](#配置文件)） |
| 设备分组 | 按分组/站点组织设备，配置时按分组浏览 |
| 设备搜索 | 按名称或 IP 模糊匹配（如"交换"、"251"），不区分大小写 |
| 虚拟网卡过滤 | 默认隐藏 VMware / Hyper-V / WSL / 蓝牙等虚拟网卡，可在设置中关闭 |
| 导入导出 | YAML 格式，便于团队共享；导入前严格校验，损坏文件拒绝导入 |
| 配置编辑器 | 独立 GUI 工具 `ipmg-config-editor.exe`，可视化编辑同一份配置 |

### 可靠性与工程

| 功能 | 说明 |
| :--- | :--- |
| 原子保存 | 配置写入采用临时文件 + 原子替换，保留上一版 `.bak`，异常不损坏原配置 |
| 操作闸门 | 串行化"对网卡有副作用"的操作，后台热键与前台操作不会互相踩踏 |
| 管理员自动提权 | 启动时检测权限并自动 UAC 提权；取消提权会提示后继续运行（网卡功能不可用） |
| 操作日志 | 配置/恢复/追加/热键应用等操作写入日志文件（设备与设置编辑不记录） |
| 环境自检 | `ipmg --selfcheck` 一次性检查单实例、托盘、热键、控制台隐藏、自启任务 |
| 自动化测试 | 219 个 stdlib unittest 用例，覆盖追加内核、配置流程、WMI 线程模型、常驻与热键 |

## 界面预览

<details open>
<summary><b>主菜单</b></summary>

```text
========================================
     网络设备IP一键配置工具 v1.4.1
========================================
  当前网卡: 以太网 2 (08:26:AE:3B:68:20)
  IP 地址 : 192.168.1.100 / 255.255.255.0  [DHCP]  网关: 192.168.1.1
----------------------------------------

请选择操作
-------------------------------------------------------
 > 1. 配置IP - 选择网卡和设备，一键配置
   2. 恢复IP - 恢复网卡IP配置（含历史记录与撤销）
   3. 管理设备 - 添加/编辑/删除设备（支持分组）
   4. 快捷预设 - 常驻热键一键应用配置
   5. 导出配置
   6. 导入配置
   7. 设置 - 交互偏好与钩子
   8. 检查更新 - 检测GitHub最新版本
   9. 退出
  快捷键: p=4 q=9 s=7 u=8
-------------------------------------------------------
请输入序号 (1-9, p/q/s/u=快捷键):
```

</details>

<details>
<summary><b>配置流程</b></summary>

```text
请选择网卡
-------------------------------------------------------
   1. [USB] 以太网 2 | 已连接 | IP: 192.168.1.100 | MAC: 08:26:AE:3B:68:20
 > 2. [以太网] 以太网 | 未连接 | 无IP | MAC: AA:BB:CC:DD:EE:FF  <-- 上次
   0. <-- 返回
-------------------------------------------------------

已选网卡: 以太网 2 | 请选择设备
-------------------------------------------------------
   1. ── [机房A] ──
 > 2. [*] 核心交换机 | 设备: 10.251.251.251 | -> 网卡IP: 10.251.251.254 [Web]
   3. [ ] 防火墙 | 设备: 192.168.0.1 | -> 网卡IP: 192.168.0.254 [Web]
   0. <-- 返回
-------------------------------------------------------
请输入序号 (1-3, 0=返回, 关键字=过滤):

网卡当前地址: 192.168.1.100
确认配置: 网卡 以太网 2 -> 10.251.251.254/255.255.255.0（设备 核心交换机 10.251.251.251）?
[Y]覆盖 / [n]取消 / [a]追加: y

正在备份网卡IP...
[OK] 网卡IP备份成功
正在配置网卡IP为 10.251.251.254...
[OK] IP配置成功
网卡当前地址: 10.251.251.254

管理页面: https://10.251.251.251
按 1 打开管理页面，按其他键返回: 1
[OK] 管理页面已打开
```

</details>

> 上例为普通终端（按序号输入）。在支持键盘交互的控制台里，菜单会额外显示一行
> `↑/↓ 或 j/k 移动, ←/→ 翻页, 回车确认, c 复制, f 过滤`，此时直接按方向键选择，不再显示序号提示。

## 环境要求

| 项目 | 要求 |
| :--- | :--- |
| 操作系统 | Windows 10 / 11（使用 WMI 与 Win32 API，不支持其它平台） |
| 权限 | 操作网卡需要管理员权限；程序启动时自动请求 UAC 提权 |
| 直接运行 exe | 无需安装 Python 或任何依赖 |
| 从源码运行 | Python 3.11+（开发环境为 3.11.1） |
| 终端 | Windows Terminal 与传统控制台均可；程序自动设置 UTF-8 代码页与 VT 支持 |

## 快速开始

1. 从 [Releases](https://github.com/DX-Cloud/ipmg/releases/latest) 下载 `ipmg.exe`，双击运行（会弹出 UAC 提权确认）
2. 首次运行进入 **3. 管理设备** 添加你的设备（名称、设备 IP、掩码即可）
3. 选择 **1. 配置IP** → 选网卡 → 选设备 → 确认 `Y` 覆盖（或 `a` 追加）→ 完成后可直接打开设备管理页面
4. 调完设备回到 **2. 恢复IP** → 选 **恢复为 DHCP（自动获取）**，或用撤销栈回到操作前的状态

## 安装

### 方式一：下载 exe（推荐）

从 [Releases](https://github.com/DX-Cloud/ipmg/releases/latest) 下载：

| 文件 | 说明 |
| :--- | :--- |
| `ipmg.exe` | 主程序（约 15 MB，单文件，免安装） |
| `ipmg-config-editor.exe` | 配置可视化编辑器（GUI，约 11 MB，无需管理员权限） |

> 首次启动约 3–5 秒（单文件 exe 需解压到临时目录），之后系统会缓存。

### 方式二：从源码运行

```powershell
git clone https://github.com/DX-Cloud/ipmg.git
cd ipmg
pip install -r requirements.txt

python main.py          # 需要管理员权限（会自动请求提权）
python main.py --help   # 查看全部命令行选项
```

## 使用指南

### 配置 IP

1. **选择网卡** — 列出所有物理网卡（含未连接的），上次使用的网卡标记"上次"
2. **选择设备** — 支持分组浏览与关键字即时过滤，收藏的设备标 `*`
3. **确认** — 显示网卡、设备、将要写入的地址与掩码，然后三选一：

   | 选择 | 行为 |
   | :--- | :--- |
   | `Y` / 回车 | **覆盖**（默认）：把网卡地址替换为计算出的新地址 |
   | `n` | 取消，不做任何改动 |
   | `a` | **追加**：保留网卡现有地址与默认网关，把新地址作为次要 IP 加入 |

4. **自动备份** — 覆盖与追加都会先备份当前配置；**备份失败默认中止**（没有回滚点就不动网卡），确认知情后可选择继续
5. **写入并回读校验** — 通过 WMI 写入，随后回读确认主 IP 与默认网关未丢失
6. **打开管理页面** — 可选择直接打开设备 Web 管理页面

> 前台操作与后台热键应用共用同一把[操作闸门](#架构)：两边不会并发写同一块网卡。前台最多等 30 秒，后台热键抢不到则直接拒绝。

### 追加 IP 的边界

追加是为了"一台机器同时连多个网段"，因此有明确的拒绝条件，**不会悄悄退化成覆盖**：

| 情况 | 行为 |
| :--- | :--- |
| 网卡当前是 DHCP 且只有 APIPA（169.254.x）或无地址 | 拒绝，提示先配置静态地址 |
| 目标地址已存在但掩码不同 | 拒绝，避免写出同 IP 双掩码 |
| 目标地址已存在且掩码相同 | 提示"无需追加"，不重复写入 |
| 追加后地址数超过 32 | 拒绝（防御性上限） |
| 追加后地址数达到 8 个 | 允许，但提示可能影响网络稳定性 |

追加写入后会校验：**主 IP 不能变、默认网关不能丢**。

### 恢复 IP

1. **选择网卡**
2. **选择恢复方式** — 列表依次为：

   ```text
   请选择要恢复的IP配置
   -------------------------------------------------------
      1. 恢复为 DHCP（自动获取）
      2. [撤销] 192.168.1.100/255.255.255.0  网关: 192.168.1.1  [2024-01-15 14:30:00]
      3. [撤销] DHCP  [2024-01-15 10:20:00]
      4. [历史] 10.251.251.254 / 255.255.255.0  网关: 无  [2024-01-14 16:45:30]
      0. <-- 返回
   -------------------------------------------------------
   ```

3. **确认并执行** — DHCP 走 `EnableDHCP`，静态走 `EnableStatic`（一次写入完整地址列表）

**撤销栈**：每次配置前备份当前配置（DHCP 或静态），最多 10 条；选择某个版本会直接恢复到它，跳过中间版本；恢复失败（如网卡断开）时栈保持不变，可重试。

**历史记录**：只记录静态 IP，最多 10 条，相同 IP+掩码+网关自动合并并更新时间戳，按网卡 MAC 分别存储。

### 设备管理

| 操作 | 说明 |
| :--- | :--- |
| 添加 / 编辑 / 删除 | 逐字段编辑，校验规则与配置文件一致 |
| 收藏 | 收藏的设备在列表中标记 `*` |
| 设置分组 | 加入已有分组或新建分组，配置时按分组浏览 |
| 搜索 | 配置流程中按名称或 IP 模糊匹配 |

### 导入导出

- **导出** — 把当前设备配置导出为 YAML 文件
- **导入** — 从 YAML 导入设备配置（**会覆盖现有设备**）
- 导入前严格校验结构与字段类型，损坏或格式错误的文件会中止导入，现有配置不受影响

### 常驻后台与全局热键

```powershell
ipmg --resident         # 常驻：托盘图标 + 全局热键，启动后隐藏控制台
ipmg --selfcheck        # 环境自检：单实例 / 热键 / 托盘 / 控制台隐藏 / 自启任务
```

- 常驻后按 **`Ctrl+Alt+I`**（设置页可改）唤出/收起主界面，点击托盘图标同样可以
- **单实例**：重复启动不会开出第二个进程，而是通知已有实例把自己的窗口显示并前置（非常驻实例显示控制台窗口，常驻实例恢复被隐藏的控制台）
- 常驻状态下主菜单选"退出"会询问：默认 **最小化到托盘**，也可选择 **完全退出**
- Windows Terminal（ConPTY）下没有可隐藏的真实窗口，会退化为"保留窗口"而不是隐藏
- 热键被其它软件占用不会导致启动失败，会在启动信息、自检与日志中明确报告，可换一个组合键

### 快捷预设（热键一键应用）

主菜单 **4. 快捷预设**（快捷键 `p`）：

1. 新增预设：选择设备 → 选择网卡 → 选择模式（覆盖/追加）→ 命名 → 可选绑定热键
2. **预设默认未启用**，启用后其热键才会注册；绑了热键但未启用的预设会在启动时常驻信息里提示
3. 应用时的安全护栏：
   - 抢不到操作闸门（已有操作在进行）直接拒绝，绝不排队
   - **备份失败即中止**
   - 设备或网卡找不到直接报错，不做兜底猜测
   - 可选 **应用前二次确认**（设置页开启）；确认与结果使用原生弹窗，不依赖系统通知设置

### 开机自启

在设置页把 **开机自动启动** 切到开启（内部创建计划任务 `ipmg-resident`）。等价的命令行写法：

```powershell
# 创建（需要管理员；/RL HIGHEST 免 UAC，/IT 保证有交互桌面供托盘与热键使用）
schtasks /Create /F /TN "ipmg-resident" /SC ONLOGON /RL HIGHEST /IT /DELAY 0000:15 `
         /TR "\"<exe路径>\" --resident --autostart-min"

schtasks /Query  /TN "ipmg-resident"    # 退出码 0 = 存在，1 = 不存在
schtasks /Delete /F /TN "ipmg-resident"
```

## 命令行参数

```text
ipmg [选项]

  -r, --resident       常驻后台（托盘图标 + 全局热键），启动后隐藏控制台
      --autostart-min  供开机自启任务使用：等价于 --resident，但不打印常驻启动信息
      --no-singleton   跳过多实例检查（调试用）
      --selfcheck      打印环境自检结果后退出（不需要管理员）
  -h, --help           显示帮助并退出
  -V, --version        显示版本号并退出
```

- 未知选项会报错并提示（退出码 2）；`/?` 等同 `--help`，非选项参数会被忽略
- 常驻开关同时受设置项 `settings.resident.enabled` 控制：命令行 `--resident` 优先
- `--selfcheck` 输出示例：

  ```text
  Python            : 3.11.1 (打包运行)
  管理员权限        : 否（改网卡会失败）
  配置文件          : C:\Users\<用户名>\ipmg\config.yaml
  设备数 / 预设数    : 22 / 1
  常驻设置          : enabled=True autostart=True 热键='Ctrl+Alt+I'
  单实例互斥体      : 可用
  托盘图标          : 可用
  唤出热键 Ctrl+Alt+I  : 可用
  预设热键 Ctrl+Alt+Shift+F1: 可用
  隐藏控制台        : 可用
  开机自启(计划任务) : supported=True exists=True
  自启命令行        : "F:\codelab\IPManager\ipmg.exe" --resident --autostart-min
  ------------------------------------------------------------------
  自检全部通过
  ```

## 配置文件

### 文件位置

```text
%USERPROFILE%\ipmg\
├── config.yaml                     # 设备、设置、预设、网卡记忆、备份与历史
├── config.yaml.bak                 # 上一版配置（原子保存时自动保留）
└── logs\
    └── ipmanager_YYYYMMDD.log      # 操作日志（按日期命名，不自动清理）
```

> 配置文件统一存放在用户目录。早期版本放在程序目录的同名文件**不会被自动读取或搬移**，如需保留请手动复制到 `%USERPROFILE%\ipmg\`。

### 设备字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| :--- | :--- | :---: | :--- | :--- |
| `name` | string | ✅ | — | 设备名称，如"核心交换机-A" |
| `device_ip` | string | ✅ | — | 设备管理口 IP |
| `subnet_mask` | string | ✅ | `255.255.255.0` | 子网掩码 |
| `group` | string | — | `""` | 分组/站点名，用于列表分组展示 |
| `ip_mode` | string | — | `auto` | 网卡 IP 策略：`auto`（自动计算）/ `manual`（手动指定） |
| `adapter_ip` | string | — | `""` | `ip_mode=manual` 时使用的网卡 IP |
| `gateway` | string | — | `""` | 网关（管理网段通常不需要） |
| `management_url` | string | — | `https://{device_ip}` | 设备 Web 管理页面 URL |
| `favorite` | bool | — | `false` | 是否收藏 |

### IP 计算模式

**自动（`auto`）**——按设备 IP 与掩码取网段内**最后一个可用主机地址**：

```text
设备 IP: 10.251.251.251 / 255.255.255.0
       → 网卡 IP: 10.251.251.254
```

排除网络地址与广播地址；若结果与设备 IP 相同则取倒数第二个；使用 O(1) 计算，不遍历地址段。

> 掩码 `0.0.0.0` 以及 /31、/32 网段会被拒绝（无法提供可用主机地址）。

**手动（`manual`）**——直接使用 `adapter_ip`。

### 设置项

设置页与 GUI 编辑器共用同一份 schema，持久化在 `config.yaml` 的 `settings` 段：

| 键 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `auto_open_page` | bool | `false` | 配置成功后自动打开管理页面 |
| `filter_virtual_adapters` | bool | `true` | 网卡列表默认过滤虚拟网卡 |
| `confirm_danger_default_no` | bool | `true` | 危险操作（删除/覆盖）默认拒绝 |
| `last_action` | string | `configure` | 主菜单记忆的上次动作 |
| `update_check.enabled` | bool | `true` | 启动时自动检查更新 |
| `update_check.url` | string | `""` | 自定义检测地址（镜像站），留空用内置源 |
| `hooks.after_configure` | string | `""` | 配置成功后执行的命令 |
| `hooks.after_restore` | string | `""` | 恢复成功后执行的命令 |
| `resident.enabled` | bool | `false` | 常驻后台（托盘 + 全局热键） |
| `resident.close_to_tray` | bool | `true` | 关闭窗口时保留常驻而不是退出 |
| `resident.autostart` | bool | `false` | 开机自动启动（计划任务，创建时需管理员） |
| `hotkeys.summon` | string | `Ctrl+Alt+I` | 唤出/收起热键，留空则不注册 |
| `notify.balloon` | bool | `true` | 操作结果用托盘气泡提示 |
| `notify.apply_requires_confirm` | bool | `false` | 快捷键应用配置前需要二次确认 |
| `configure.default_mode` | string | `overwrite` | 配置 IP 默认模式：`overwrite` / `append` |

> `last_action` 是主菜单记忆用的内部键，不在设置页与编辑器中展示；其余键均由 `SETTING_SCHEMA` 驱动，设置页与 GUI 编辑器共用。

### 快捷预设字段

预设是配置顶层的 `presets` 列表（不在 `settings` 内），上限 20 条：

| 字段 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `name` | string | `""` | 展示名 |
| `enabled` | bool | `false` | 是否启用（未启用则不注册其热键） |
| `adapter_mac` | string | `""` | 目标网卡 MAC（大写） |
| `device_name` / `device_ip` | string | `""` | 目标设备（至少填一个） |
| `mode` | string | `overwrite` | `overwrite` / `append` |
| `hotkey` | string | `""` | 绑定的全局热键，留空则不绑定 |

### 配置示例

```yaml
version: 1

devices:
  - name: 核心交换机
    group: 机房A
    device_ip: 10.251.251.251
    ip_mode: auto
    adapter_ip: ""
    subnet_mask: 255.255.255.0
    gateway: ""
    management_url: https://10.251.251.251
    favorite: true

  - name: 防火墙
    group: 机房A
    device_ip: 192.168.0.1
    ip_mode: manual
    adapter_ip: 192.168.0.100
    subnet_mask: 255.255.255.0
    management_url: https://192.168.0.1
    favorite: false

presets:
  - name: 切到核心交换机
    enabled: true
    adapter_mac: "08:26:AE:3B:68:20"
    device_name: 核心交换机
    device_ip: 10.251.251.251
    mode: overwrite
    hotkey: Ctrl+Alt+1

network_adapters:
  last_selected_mac: "08:26:AE:3B:68:20"

backups:
  "08:26:AE:3B:68:20":
    - ip: 192.168.1.100            # 栈顶 = 最近一次操作前的状态
      mask: 255.255.255.0
      gateway: 192.168.1.1
      is_dhcp: true
      adapter_name: 以太网 2
      ips: [192.168.1.100]         # 完整地址列表（索引 0 为主 IP）
      masks: [255.255.255.0]
      gateways: [192.168.1.1]
      timestamp: "2026-10-17 20:15:00"

ip_history:
  "08:26:AE:3B:68:20":
    - ip: 10.251.251.254
      mask: 255.255.255.0
      gateway: ""
      timestamp: "2026-10-17 20:15:00"

settings:
  auto_open_page: false
  filter_virtual_adapters: true
  resident:
    enabled: true
    close_to_tray: true
    autostart: false
  hotkeys:
    summon: Ctrl+Alt+I
  notify:
    balloon: true
    apply_requires_confirm: false
  configure:
    default_mode: overwrite
  update_check:
    enabled: true
    url: ""
  hooks:
    after_configure: ""
    after_restore: ""
```

## 项目结构

```text
ipmg/
├── main.py                       # 入口：参数解析、单实例、UAC 提权、自检、主循环
├── requirements.txt              # 运行依赖
├── ipmg.spec / ipmg-editor.spec  # PyInstaller 打包配置
├── assets/app.ico                # 应用图标（由 build/make_icon.py 生成）
├── README.md / CHANGELOG.md / LICENSE
│
├── core/                         # 业务核心（与界面解耦）
│   ├── adapter_manager.py        #   网卡枚举（GetAdaptersAddresses）
│   ├── config_manager.py         #   配置读写、schema、设置、备份与历史
│   ├── ip_configurator.py        #   WMI 写地址（EnableStatic / SetGateways / EnableDHCP）
│   ├── network_utils.py          #   IP 计算与校验
│   ├── operations.py             #   业务操作层：配置/追加/恢复/撤销/钩子
│   ├── opgate.py                 #   操作闸门（串行化网卡副作用操作）
│   ├── singleton.py              #   单实例互斥体 + 唤出事件
│   ├── win32host.py              #   Win32 事件线程：全局热键、托盘、原生弹窗
│   ├── autostart.py              #   开机自启（计划任务）
│   ├── browser_launcher.py       #   浏览器启动（提权进程兼容）
│   ├── update_check.py           #   版本更新检测
│   └── version.py                #   版本号唯一来源
│
├── ui/                           # TUI 界面
│   ├── app.py                    #   主界面与各流程
│   ├── actions.py                #   动作注册表（菜单/快捷键的单一来源）
│   ├── widgets.py                #   统一交互组件（菜单/确认/剪贴板）
│   ├── console.py                #   可打断等待 + 跨线程事件队列
│   ├── header.py / status.py     #   标题栏与跨页面状态
│   ├── device_manager.py         #   设备管理
│   ├── settings.py               #   设置页
│   ├── presets.py                #   快捷预设管理
│   └── resident.py               #   常驻控制器（托盘 + 热键 + 预设应用）
│
├── utils/                        # 备份与日志
│   ├── backup.py
│   └── logger.py
│
├── editor/                       # 配置可视化编辑器（tkinter）
│   ├── app.py / storage.py
│   └── device_page.py / settings_page.py / backup_page.py
│
├── tests/                        # 219 个单元测试（stdlib unittest）
└── build/make_icon.py            # 生成 assets/app.ico（开发期工具）
```

## 架构

### 分层

```text
main.py                 入口/生命周期：参数 → 单实例 → 提权 → 常驻启动 → TUI 主循环
   │
   ├── ui/              TUI 展示与交互（不直接碰网卡）
   │     └── resident.py  常驻控制器：托盘/热键 → 调 core.operations
   │
   ├── core/operations.py   业务操作层：备份 → 写入 → 回读 → 钩子（界面与 CLI 共用）
   │     ├── core/ip_configurator.py   WMI 写地址（按线程缓存连接 + COM 初始化）
   │     ├── core/adapter_manager.py   网卡枚举
   │     └── utils/backup.py          备份/恢复
   │
   └── core/{opgate,singleton,win32host,autostart}.py  进程级基础设施
```

设计要点：**界面不直接操作网卡**。无论是手动配置还是热键应用预设，都走同一个 `core.operations` 管线，因此备份、回读校验、日志、钩子的行为完全一致。

### 关键实现

| 主题 | 做法 |
| :--- | :--- |
| **IP 写入** | WMI `Win32_NetworkAdapterConfiguration`：`EnableStatic(IPAddress=[...], SubnetMask=[...])` 一次写入完整地址数组（索引 0 为主 IP）、`SetGateways` 设网关、`EnableDHCP` 切回自动获取。不使用 `netsh`，不写注册表，不碰 DNS |
| **WMI 线程模型** | 连接**按线程缓存**，且每个线程首次使用前调用 `pythoncom.CoInitialize()`。WMI 底层是 COM：跨线程共用接口指针或未初始化 COM 都会失败（后者表现为"备份网卡IP失败"这类难查的错误）。连接失效时自动丢弃并重试一次 |
| **追加 IPv4** | 先读回现有地址与网关，再一次性写入"原有 + 新增"的完整列表；写入后回读校验主 IP 与网关未变 |
| **网卡枚举** | `iphlpapi.dll` 的 `GetAdaptersAddresses`（ctypes），取友好名称、MAC、地址、连接状态与类型；不过滤断开的网卡 |
| **管理员提权** | `ShellExecuteW("runas", ...)`；路径加引号以兼容空格；取消提权时提示后继续运行。打包模式下直接以 exe 自身提权 |
| **单实例与唤出** | 命名互斥体 `Local\ipmg.singleton`（创建后立即尝试取得所有权，避免残留对象误判）+ 命名事件 `Local\ipmg.activate`。互斥体检查在提权**之前**，重复启动不会白弹一次 UAC |
| **操作闸门** | 进程内可重入锁：前台 TUI 的配置/恢复/撤销与后台热键应用互斥（前台最多等 30 秒，后台以 `timeout=0` 抢锁，抢不到直接拒绝而不是排队） |
| **Win32 事件线程** | 隐藏窗口作消息宿主 → `RegisterHotKey`（`MOD_NOREPEAT`）→ `Shell_NotifyIconW` 托盘 → `GetMessageW` 循环。所有调用编入请求队列在宿主线程执行，跨线程方法同步返回结果，失败降级而不抛异常 |
| **可打断的 TUI** | `kbhit()` 轮询代替阻塞 `getwch()`，配合跨线程事件队列：热键唤出、后台结果、终端尺寸变化都能立即触发重绘，且空闲时不重绘 |
| **原子保存** | 临时文件 + `os.replace` 原子替换，保留 `config.yaml.bak`；配置损坏时自动备份并重建 |
| **无第三方 GUI 依赖** | 托盘/热键/弹窗全部用 ctypes 直接调用 Win32；运行期依赖只有 rich、PyYAML、netaddr、wmi、pywin32 |

## 开发

### 环境准备

```powershell
pip install -r requirements.txt
```

### 运行测试

```powershell
python -m unittest discover -s tests -p "test_*.py" -t .
```

219 个用例，覆盖：追加 IP 内核、三选一确认、配置流程、配置 schema 与迁移、预设与热键、常驻控制器、计划任务、WMI 线程模型、Win32 模块。

约定：**测试绝不调用真实的 WMI 写方法**（`EnableStatic` / `EnableDHCP` / `SetGateways`），也不修改本机网卡配置；计划任务相关测试全部 mock `subprocess`。

### 打包

```powershell
pip install "pyinstaller>=6.0"

python build/make_icon.py                       # 生成图标（一次性，产物已入库）
python -m PyInstaller ipmg.spec --noconfirm --clean          # 主程序
python -m PyInstaller ipmg-editor.spec --noconfirm --clean   # 配置编辑器

Copy-Item dist\ipmg.exe .\ -Force
Copy-Item dist\ipmg-config-editor.exe .\ -Force
```

产物：主程序约 15 MB，编辑器约 11 MB。spec 中已排除 tkinter/PIL 等未使用依赖（编辑器 spec 保留 tkinter）。

### 提交规范

采用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：`feat:` / `fix:` / `refactor:` / `docs:` / `chore:`，破坏性变更用 `!` 标注。版本号集中在 `core/version.py`，每次发版同时更新 [CHANGELOG.md](CHANGELOG.md)。

## 常见问题

<details>
<summary><b>启动与权限</b></summary>

**Q：双击后闪退？**
A：程序需要管理员权限。若 UAC 被系统策略拦截，右键 `ipmg.exe` → 属性 → 兼容性 → 勾选"以管理员身份运行"。

**Q：取消了 UAC 授权，程序还在运行但配置失败？**
A：提权失败或取消时会提示并继续运行（可查看设备与配置），但写网卡 IP 必须管理员权限，请以管理员身份重新运行。

**Q：`ipmg --help` 没反应 / 直接进了界面？**
A：1.4.1 起支持 `--help`；v1.4 及更早的发布版会把 `--help` 忽略并直接启动界面，请升级或查看本文件的[命令行参数](#命令行参数)。

**Q：提示"ipmg 已在运行"？**
A：程序是单实例的。第二个实例会通知既有实例，由后者把自己的窗口显示并前置（常驻实例会恢复被隐藏的控制台），然后第二个实例直接退出。若确认没有实例却报占用，可能是上一个实例异常退出后残留了同名互斥体，1.4.1 起已改为"创建后立即尝试取得所有权"，不会再误判。

**Q：备份失败还能继续配置吗？**
A：默认不能——没有回滚点就不动网卡，程序会中止并提示。若你确认知情（例如网卡未连接、本来就无可备份内容），可在随后的确认框里选择继续。

</details>

<details>
<summary><b>网卡与 IP</b></summary>

**Q：恢复 DHCP 失败，提示"网卡当前未连接"？**
A：断开网线的网卡无法切换到 DHCP 模式（WMI 返回错误码 84）。接入网线后重试。

**Q：WLAN 网卡配好 IP 后，撤销恢复不了原地址？**
A：未连接的无线网卡通常没有任何 IPv4（只有 169.254.x 的 APIPA），因此备份内容为空，撤销时无地址可还原。程序会在结果里提示这一点。

**Q：配置后设备 ping 不通？**
A：v1.4 起已移除 Ping 验证（实测不可靠）。请确认网卡确实拿到了目标网段地址（标题栏会实时显示），并检查设备管理口地址是否正确。

**Q：支持无线网卡吗？**
A：支持，无线网卡同样会列出；注意 WiFi 连接状态会影响能否切回 DHCP。

**Q：同一网段能有多个网卡用同一个 IP 吗？**
A：不能。同一网段内的地址必须唯一，否则会造成冲突。

</details>

<details>
<summary><b>常驻、热键与预设</b></summary>

**Q：按了预设热键没反应？**
A：按顺序检查：① 预设是否**已启用**（未启用不会注册热键，启动信息会提示）；② 热键是否被别的软件占用（`--selfcheck` 会报"不可用"）；③ 是否已开启常驻（设置里 `resident.enabled` 或 `--resident`）。

**Q：预设应用后没有提示？**
A：默认走标题栏 + 托盘气泡 + 日志；若系统关闭了通知（专注助手），气泡可能不显示。在设置里开启**应用前需要二次确认**后，确认与结果都会用原生弹窗。

**Q：点窗口右上角 X 后程序还在托盘里吗？**
A：控制台程序无法阻止系统在收到关闭事件后结束进程。常驻模式默认会隐藏控制台，正常不会点到 X；如需退出请在主菜单选"退出"（可选完全退出）。程序会保证退出时清理托盘图标与热键。

**Q：在 Windows Terminal 里无法隐藏控制台？**
A：ConPTY 下没有可隐藏的真实窗口，程序会退化为保留窗口。需要真正隐藏控制台请用传统控制台窗口运行。

**Q：开机自启没生效？**
A：`--selfcheck` 查看"开机自启(计划任务)"是否为 `exists=True`；创建/删除任务需要管理员权限。任务名 `ipmg-resident`，登录后延迟 15 秒启动。

**Q：怎么彻底关掉常驻？**
A：设置页关闭"常驻后台"与"开机自动启动"，然后在主菜单选"退出"并选择完全退出。

</details>

<details>
<summary><b>配置与数据</b></summary>

**Q：配置文件在哪里？**
A：`%USERPROFILE%\ipmg\config.yaml`（即 `C:\Users\<用户名>\ipmg\config.yaml`）。

**Q：日志在哪里？为什么不清理？**
A：`%USERPROFILE%\ipmg\logs\ipmanager_YYYYMMDD.log`，按日期命名，当前不做轮转与自动清理，长期使用请自行归档。

**Q：如何在多台电脑间共享设备配置？**
A：用"导出配置"生成 YAML，在另一台机器"导入配置"。也可直接用配置编辑器维护同一份文件。

**Q：导入失败提示"导入失败"？**
A：导入前会严格校验结构（如 `devices` 必须是列表、字段类型正确），损坏文件会被拒绝且**不影响现有配置**。请检查文件内容或重新导出。

**Q：配置文件损坏了怎么办？**
A：程序会自动备份损坏文件并重建默认配置；上一版内容保留在 `config.yaml.bak`。

</details>

## 已知限制

1. **仅支持 Windows** —— 依赖 WMI、Win32 API 与 `os.startfile()`
2. **断开网卡的 DHCP 恢复** —— 无连接的网卡无法切回 DHCP（错误码 84），需接线后重试
3. **控制台窗口关闭** —— 点 X 会被系统结束进程（控制台程序固有限制），常驻模式默认隐藏控制台以规避
4. **无 IPv4 的网卡备份为空** —— 未连接/APIPA 网卡没有可备份的地址，撤销时无法还原
5. **日志不轮转** —— 日志文件只按日期新建，不自动删除旧文件
6. **`--help` 与 `--version`** —— 自 1.4.1 起提供；v1.4 及更早的发布版不支持（参数被忽略）

## 技术栈

| 组件 | 版本 | 用途 |
| :--- | :--- | :--- |
| Python | 3.11+ | 运行环境 |
| [Rich](https://github.com/Textualize/rich) | >=13.0.0 | 终端渲染（颜色、表格、样式） |
| [PyYAML](https://pyyaml.org/) | >=6.0 | 配置文件读写 |
| [WMI](https://pypi.org/project/WMI/) | >=1.5.0 | 网卡 IP 配置（`Win32_NetworkAdapterConfiguration`） |
| [pywin32](https://github.com/mhammond/pywin32) | >=306 | COM 初始化与 Windows API 支持 |
| [netaddr](https://github.com/netaddr/netaddr) | >=0.9.0 | 网段分析与地址计算 |
| tkinter | 标准库 | 配置可视化编辑器 GUI |
| [PyInstaller](https://pyinstaller.org/) | >=6.0 | 打包单文件 exe（开发依赖） |

## 贡献

欢迎提交 Issue 与 Pull Request：

1. Fork 并创建特性分支（`feat/xxx` 或 `fix/xxx`）
2. 保持测试全绿：`python -m unittest discover -s tests -p "test_*.py" -t .`
3. 涉及网卡写入的改动请在真机验证并说明测试环境（网卡型号、是否多 IP、是否有线/无线）
4. 提交信息遵循 Conventional Commits

## License

本项目采用 [MIT 许可证](LICENSE) 发布。

```text
Copyright (c) 2026 DX-Cloud
```
