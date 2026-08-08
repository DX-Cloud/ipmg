"""
版本信息（唯一来源）
版本号集中管理，供标题栏、更新检测、Release 打包使用。
"""

# 程序当前版本（语义化版本号）
APP_VERSION = "1.3.0"
# 标题栏等界面展示用短版本号
APP_VERSION_DISPLAY = "v1.3"

# GitHub 仓库（用于更新检测与 Release 跳转）
GITHUB_REPO = "DX-Cloud/ipmg"

# 更新检测默认源（按序尝试）
DEFAULT_CHECK_URLS = [
    "https://api.github.com/repos/DX-Cloud/ipmg/releases/latest",
    "https://github.com/DX-Cloud/ipmg/releases/latest",
]

# 单次检测超时（秒）
CHECK_TIMEOUT = 3
