"""
全局探索记忆缓存模块
实现跨会话的长期记忆，记录已探索的 Activity-Widget 路径
防止大模型重复走同一条路径
"""

import json
from pathlib import Path
from typing import Dict, List, Set, Optional
from collections import defaultdict


# 缓存文件路径
CACHE_FILE = Path("temp_data/exploration_cache.json")


class ExplorationCache:
    """
    全局探索记忆缓存管理器

    数据结构：
    {
        "ActivityName1": ["WidgetName1", "WidgetName2", ...],
        "ActivityName2": ["WidgetName3", ...],
        ...

    }
    """

    def __init__(self, cache_file: Path = CACHE_FILE):
        """
        初始化缓存管理器

        Args:
            cache_file: 缓存文件路径
        """
        self.cache_file = cache_file
        self.cache: Dict[str, List[str]] = {}
        self._load_cache()

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

    def _save_cache(self) -> None:
        """保存缓存到文件"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
            print(f"[探索缓存] 已保存缓存到 {self.cache_file}")
        except IOError as e:
            print(f"[探索缓存] 保存失败: {e}")

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