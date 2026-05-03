# ipmg - 网络设备IP一键配置工具

一个 Windows TUI 命令行工具，用于一键切换网络适配器的 IP 配置，方便网络工程师快速对接不同管理网段的网络设备。

## 功能特性

- **一键配置 IP** — 选择网卡和设备，自动计算并配置静态 IP
- **一键恢复 IP** — 自动备份原始配置，操作后一键恢复（支持 DHCP 和静态 IP）
- **设备管理** — 添加/编辑/删除/收藏常用设备配置
- **Ping 验证** — 配置完成后自动 Ping 设备验证连通性
- **管理页面** — 配置完成后可直接打开设备 Web 管理页面
- **配置导入导出** — YAML 格式的配置文件，支持导出备份和导入恢复
- **管理员自动提权** — 启动时自动检测并以管理员权限重新运行

## 界面预览

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

## 快速开始

### 方式一：直接运行 exe（推荐）

1. 双击 `ipmg.exe`（会自动以管理员权限运行）

### 方式二：从源码运行

```bash
# 安装依赖
pip install -r requirements.txt

# 以管理员身份运行
python main.py
```

## 配置文件

配置文件自动存放在 `%USERPROFILE%\ipmg\` 目录下：

```
C:\Users\<用户名>\ipmg\
├── config.yaml          # 设备配置、网卡记忆、IP备份
└── logs\                # 操作日志（按日期滚动）
    └── ipmanager_YYYYMMDD.log
```

### 设备配置示例

首次运行会自动创建默认配置，也可手动编辑 `config.yaml`：

```yaml
devices:
  - name: 交换机-A
    device_ip: 10.251.251.251
    ip_mode: auto          # auto=自动计算网卡IP, manual=手动指定
    adapter_ip: ''
    subnet_mask: 255.255.255.0
    gateway: ''
    management_url: https://10.251.251.251
    favorite: true
```

**IP 模式说明：**
- `auto` — 自动取设备 IP 网段的最后可用 IP 作为网卡 IP
- `manual` — 手动指定 `adapter_ip` 字段的 IP 地址

## 项目结构

```
ipmg/
├── main.py                # 程序入口（管理员提权、主循环）
├── requirements.txt       # Python 依赖
├── core/
│   ├── adapter_manager.py # 网卡枚举（Windows API）
│   ├── config_manager.py  # YAML 配置文件读写
│   ├── ip_configurator.py # IP 配置（WMI EnableStatic/EnableDHCP）
│   ├── network_utils.py   # 网络工具（Ping、IP计算、验证）
│   └── browser_launcher.py# 浏览器启动
├── ui/
│   ├── app.py             # 主 TUI 界面（配置/恢复/导入导出流程）
│   ├── device_manager.py  # 设备管理界面（增删改查）
│   └── header.py          # 固定标题栏（状态显示）
└── utils/
    ├── backup.py          # 网卡IP备份与恢复
    └── logger.py          # 日志记录
```

## 技术栈

- **Python 3.14**
- [Rich](https://github.com/Textualize/rich) — 终端美化输出
- [PyYAML](https://pyyaml.org/) — YAML 配置读写
- [WMI](https://pypi.org/project/WMI/) — Windows 管理规范接口（IP 配置）
- [netaddr](https://github.com/netaddr/netaddr) — IP 地址计算
- [PyInstaller](https://pyinstaller.org/) — 单文件 exe 打包

## 编译

```bash
pip install pyinstaller
python -m PyInstaller --onefile --name ipmg --distpath . --clean main.py
```

## 注意事项

- 必须以**管理员权限**运行（程序会自动提权）
- 恢复 DHCP 时需要网卡**已连接网线**，否则会提示接线
- 仅支持 **Windows** 平台
- 支持显示所有网卡（含未连接的），断开的网卡也可配置静态 IP

## License

MIT
