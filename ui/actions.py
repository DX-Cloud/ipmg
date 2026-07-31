"""
动作注册表
主菜单、快捷键、main 循环的单一数据来源。
新增功能时只需注册一条 Action，菜单入口与快捷键自动获得。
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional


@dataclass
class Action:
    key: str                # 唯一标识（用于分发）
    label: str              # 菜单显示文本
    handler: Callable       # def handler(config) -> config
    hotkey: str = ""        # 快捷键字母（可选，数字序号自动分配）
    needs_save: bool = False  # 执行后是否需要保存配置


_ACTIONS: List[Action] = []
_ACTION_MAP: Dict[str, Action] = {}


def register(action: Action) -> Action:
    """注册动作。key 重复时覆盖。"""
    for i, existing in enumerate(_ACTIONS):
        if existing.key == action.key:
            _ACTIONS[i] = action
            break
    else:
        _ACTIONS.append(action)
    _ACTION_MAP[action.key] = action
    return action


def get_actions() -> List[Action]:
    """按注册顺序返回全部动作。"""
    return list(_ACTIONS)


def find_action(key: str) -> Optional[Action]:
    return _ACTION_MAP.get(key)


def action_keys() -> List[str]:
    return [a.key for a in _ACTIONS]
