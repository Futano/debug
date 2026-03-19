"""
全局探索记忆缓存模块
实现跨会话的长期记忆，记录已探索的 Activity-Widget 路径
防止大模型重复走同一条路径

新增功能：
- 无效操作黑名单：记录在当前页面点击后无效果的控件
- 状态刷新法则：只有当操作有效（UI状态改变）时才清空黑名单
"""

import json
from pathlib import Path
from typing import Dict, List, Set, Optional
from collections import defaultdict


# 缓存文件路径
CACHE_FILE = Path("temp_data/exploration_cache.json")
# 无效操作黑名单文件路径
BLACKLIST_FILE = Path("temp_data/ineffective_widgets_blacklist.json")


class ExplorationCache:
    """
    全局探索记忆缓存管理器

    数据结构：
    {
        "ActivityName1": ["WidgetName1", "WidgetName2", ...],
        "ActivityName2": ["WidgetName3", ...],
        ...
    }

    无效操作黑名单数据结构：
    {
        "ActivityName1": ["IneffectiveWidget1", "IneffectiveWidget2", ...],
        ...
    }
    """

    def __init__(self, cache_file: Path = CACHE_FILE, blacklist_file: Path = BLACKLIST_FILE):
        """
        初始化缓存管理器

        Args:
            cache_file: 缓存文件路径
            blacklist_file: 无效操作黑名单文件路径
        """
        self.cache_file = cache_file
        self.blacklist_file = blacklist_file
        self.cache: Dict[str, List[str]] = {}
        self.blacklist: Dict[str, List[str]] = {}  # 无效操作黑名单
        self._load_cache()
        self._load_blacklist()

    def _load_cache(self) -> None:
        """从文件加载缓存"""
        # 确保目录存在
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.cache = json.load(f)
                print(f"[探索缓存] 已加载缓存，包含 {len(self.cache)} 个 Activity 的探索记录")
            except (json.JSONDecodeError, IOError) as e:
                print(f"[探索缓存] 加载失败，初始化为空: {e}")
                self.cache = {}
        else:
            print("[探索缓存] 缓存文件不存在，初始化为空")
            self.cache = {}

    def _load_blacklist(self) -> None:
        """从文件加载无效操作黑名单"""
        if self.blacklist_file.exists():
            try:
                with open(self.blacklist_file, 'r', encoding='utf-8') as f:
                    self.blacklist = json.load(f)
                print(f"[黑名单] 已加载无效操作黑名单，包含 {len(self.blacklist)} 个 Activity 的黑名单")
            except (json.JSONDecodeError, IOError) as e:
                print(f"[黑名单] 加载失败，初始化为空: {e}")
                self.blacklist = {}
        else:
            self.blacklist = {}

    def _save_cache(self) -> None:
        """保存缓存到文件"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
            print(f"[探索缓存] 已保存缓存到 {self.cache_file}")
        except IOError as e:
            print(f"[探索缓存] 保存失败: {e}")

    def _save_blacklist(self) -> None:
        """保存黑名单到文件"""
        try:
            with open(self.blacklist_file, 'w', encoding='utf-8') as f:
                json.dump(self.blacklist, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[黑名单] 保存失败: {e}")

    def record_exploration(self, activity_name: str, widget_name: str) -> None:
        """
        记录一次探索

        Args:
            activity_name: Activity 名称
            widget_name: 控件名称
        """
        if not activity_name or not widget_name:
            return

        # 初始化 Activity 的列表
        if activity_name not in self.cache:
            self.cache[activity_name] = []

        # 避免重复记录
        if widget_name not in self.cache[activity_name]:
            self.cache[activity_name].append(widget_name)
            self._save_cache()
            print(f"[探索缓存] 记录探索: {activity_name} -> {widget_name}")

    def is_explored(self, activity_name: str, widget_name: str) -> bool:
        """
        检查某个控件是否已被探索

        Args:
            activity_name: Activity 名称
            widget_name: 控件名称

        Returns:
            True 表示已探索，False 表示未探索
        """
        if activity_name not in self.cache:
            return False
        return widget_name in self.cache[activity_name]

    def get_explored_widgets(self, activity_name: str) -> Set[str]:
        """
        获取某个 Activity 已探索的控件集合

        Args:
            activity_name: Activity 名称

        Returns:
            已探索控件名称集合
        """
        return set(self.cache.get(activity_name, []))

    def mark_widget_as_explored(self, widget_name: str, activity_name: str) -> str:
        """
        给控件名称添加已探索标记

        Args:
            widget_name: 原始控件名称
            activity_name: Activity 名称

        Returns:
            带标记的控件名称（如果已探索），否则返回原始名称
        """
        if self.is_explored(activity_name, widget_name):
            return f"{widget_name} [ALREADY EXPLORED]"
        return widget_name

    def get_statistics(self) -> Dict:
        """
        获取缓存统计信息

        Returns:
            包含统计信息的字典
        """
        total_activities = len(self.cache)
        total_widgets = sum(len(widgets) for widgets in self.cache.values())
        return {
            "total_activities": total_activities,
            "total_explored_widgets": total_widgets,
            "activities": {k: len(v) for k, v in self.cache.items()}
        }

    def clear_cache(self) -> None:
        """清空缓存"""
        self.cache = {}
        self._save_cache()
        print("[探索缓存] 已清空所有探索记录")

    # ==================== 无效操作黑名单功能 ====================

    def add_to_blacklist(self, activity_name: str, widget_name: str) -> None:
        """
        将控件添加到无效操作黑名单

        当控件被点击但 UI 状态未发生变化（NO_EFFECT）时调用

        Args:
            activity_name: Activity 名称
            widget_name: 无效控件名称（可以是 text 或 resource_id）
        """
        if not activity_name or not widget_name:
            return

        # 初始化 Activity 的黑名单列表
        if activity_name not in self.blacklist:
            self.blacklist[activity_name] = []

        # 避免重复添加
        if widget_name not in self.blacklist[activity_name]:
            self.blacklist[activity_name].append(widget_name)
            self._save_blacklist()
            print(f"[黑名单] 添加无效控件: {activity_name} -> {widget_name}")

    def is_blacklisted(self, activity_name: str, widget_name: str) -> bool:
        """
        检查控件是否在无效操作黑名单中

        Args:
            activity_name: Activity 名称
            widget_name: 控件名称

        Returns:
            True 表示在黑名单中（无效控件），False 表示不在黑名单中
        """
        if activity_name not in self.blacklist:
            return False
        return widget_name in self.blacklist[activity_name]

    def get_blacklisted_widgets(self, activity_name: str) -> Set[str]:
        """
        获取某个 Activity 的无效操作黑名单

        Args:
            activity_name: Activity 名称

        Returns:
            无效控件名称集合
        """
        return set(self.blacklist.get(activity_name, []))

    def clear_blacklist(self, activity_name: str) -> None:
        """
        清空指定 Activity 的无效操作黑名单

        状态刷新法则：只有当操作有效（UI 状态已改变）时才调用此方法

        Args:
            activity_name: Activity 名称
        """
        if activity_name in self.blacklist:
            old_count = len(self.blacklist[activity_name])
            self.blacklist[activity_name] = []
            self._save_blacklist()
            print(f"[黑名单] 已清空 {activity_name} 的无效操作黑名单（共 {old_count} 个控件）")

    def get_blacklist_statistics(self) -> Dict:
        """
        获取黑名单统计信息

        Returns:
            包含统计信息的字典
        """
        total_activities = len(self.blacklist)
        total_widgets = sum(len(widgets) for widgets in self.blacklist.values())
        return {
            "total_activities_with_blacklist": total_activities,
            "total_blacklisted_widgets": total_widgets,
            "activities": {k: len(v) for k, v in self.blacklist.items()}
        }


# 测试入口
if __name__ == "__main__":
    cache = ExplorationCache()

    # 测试记录
    cache.record_exploration("MainActivity", "Search")
    cache.record_exploration("MainActivity", "Login")
    cache.record_exploration("SearchActivity", "SearchBox")

    # 测试查询
    print(f"\nMainActivity 已探索: {cache.get_explored_widgets('MainActivity')}")
    print(f"Search 是否已探索: {cache.is_explored('MainActivity', 'Search')}")
    print(f"Register 是否已探索: {cache.is_explored('MainActivity', 'Register')}")

    # 测试标记
    print(f"\n标记测试: {cache.mark_widget_as_explored('Search', 'MainActivity')}")
    print(f"标记测试: {cache.mark_widget_as_explored('Register', 'MainActivity')}")

    # 统计
    print(f"\n统计信息: {cache.get_statistics()}")