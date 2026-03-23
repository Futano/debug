"""
功能感知记忆管理模块
实现 GPTDroid 论文中的 Functionality-aware Memory 机制
用于记录测试历史并生成记忆相关的提示词

数据结构：
- Activities: 静态数据，从 Manifest 读取
- Functions: LLM 通过 Function Query 总结得到的语义化功能
- Operation History: 运行时记录的测试操作历史
"""

from typing import List, Dict, Optional
from collections import deque


class TestingSequenceMemorizer:
    """
    测试序列记忆器

    统一管理测试过程中的所有记忆数据：
    1. Activity 追踪（从 Manifest）
    2. Widget 追踪（运行时记录）
    3. Function 追踪（LLM 总结）
    4. 操作历史（最近5步）

    遵循 GPTDroid 论文的记忆机制设计
    """

    MAX_HISTORY_STEPS = 5

    def __init__(self):
        """初始化测试序列记忆器"""
        # App 信息
        self.app_name: str = ""

        # Activity 追踪: {activity_name: {visits: int, status: str}}
        self.activity_info: Dict[str, Dict] = {}

        # Widget 追踪: {activity_name: {widget_id: visits}}
        self.widget_visits: Dict[str, Dict[str, int]] = {}

        # Function 追踪（LLM 总结）: {function_name: {visits: int, status: str}}
        self.explored_functions: Dict[str, Dict] = {}

        # 操作历史（最近5步）
        self.operation_history: deque = deque(maxlen=self.MAX_HISTORY_STEPS)

        # 当前测试功能
        self.current_function: Optional[str] = None
        self.current_function_status: Optional[str] = None

        # 步骤计数器
        self.step_counter: int = 0

    # ==================== 配置方法 ====================

    def set_app_name(self, app_name: str) -> None:
        """设置应用名称"""
        self.app_name = app_name

    def register_activity(self, activity_name: str, status: str = "unvisited") -> None:
        """注册 Activity（从 Manifest）"""
        if activity_name not in self.activity_info:
            self.activity_info[activity_name] = {"visits": 0, "status": status}

    def register_activities(self, activities: List[str]) -> None:
        """批量注册 Activities"""
        for activity in activities:
            self.register_activity(activity)

    def register_activities(self, activities: List[str]) -> None:
        """批量注册 Activities"""
        for activity in activities:
            self.register_activity(activity)

    # ==================== 记录方法 ====================

    def record_activity_visit(self, activity_name: str) -> None:
        """记录 Activity 访问"""
        self.register_activity(activity_name)
        self.activity_info[activity_name]["visits"] += 1
        self.activity_info[activity_name]["status"] = "visited"

    def record_widget_visit(self, activity_name: str, widget_identifier: str) -> None:
        """记录 Widget 访问"""
        if activity_name not in self.widget_visits:
            self.widget_visits[activity_name] = {}
        self.widget_visits[activity_name][widget_identifier] = \
            self.widget_visits[activity_name].get(widget_identifier, 0) + 1

    def record_operation(
        self,
        activity_name: str,
        widgets_tested: List[Dict] = None,
        operation: str = None,
        target_widget: str = None,
        success: bool = True
    ) -> None:
        """
        记录操作历史

        Args:
            activity_name: Activity 名称
            widgets_tested: 已测试的控件列表 [{name, visits}]（可选）
            operation: 操作类型
            target_widget: 目标控件
            success: 是否成功
        """
        self.step_counter += 1

        # 如果没有提供 widgets_tested，自动构建
        if widgets_tested is None:
            widgets_tested = self.get_widgets_tested(activity_name)

        # 添加到操作历史
        self.operation_history.appendleft({
            "activity_name": activity_name,
            "widgets_tested": widgets_tested or [],
            "operation": operation or "unknown",
            "target_widget": target_widget or "unknown",
            "success": success
        })

        # 同时更新 Activity 和 Widget 访问
        if activity_name:
            self.record_activity_visit(activity_name)
        if target_widget:
            self.record_widget_visit(activity_name, target_widget)

        status_str = "成功" if success else "失败"
        print(f"[记忆更新] 步骤 {self.step_counter}: {operation} '{target_widget}' @ {activity_name} [{status_str}]")

    # 兼容旧方法名
    def update_step(
        self,
        activity_name: str,
        operation: str,
        widget_name: str,
        target_function: Optional[str] = None,
        success: bool = True
    ) -> None:
        """
        更新测试步骤记录（兼容旧接口）

        Args:
            activity_name: 当前 Activity 名称
            operation: 执行的操作类型
            widget_name: 操作的目标控件名称
            target_function: 该操作对应的功能名称（可选）
            success: 操作是否成功
        """
        self.record_operation(
            activity_name=activity_name,
            operation=operation,
            target_widget=widget_name,
            success=success
        )

        # 更新当前功能
        if target_function:
            self.update_function(target_function, "testing")

    def update_function(self, function_name: str, status: str = "testing") -> None:
        """
        更新或添加功能（来自 LLM 总结）

        Args:
            function_name: 功能名称
            status: 状态 ("testing" 或 "tested")
        """
        if function_name not in self.explored_functions:
            self.explored_functions[function_name] = {"visits": 0, "status": status}

        self.explored_functions[function_name]["visits"] += 1
        self.explored_functions[function_name]["status"] = status

        # 更新当前功能
        self.current_function = function_name
        self.current_function_status = status
        print(f"[功能更新] {function_name} ({status})")

    def infer_function_from_activity(self, activity_name: str) -> Optional[str]:
        """
        从 Activity 名称推断功能（当 LLM 未返回时的后备机制）

        Args:
            activity_name: Activity 名称

        Returns:
            推断的功能名称
        """
        # Activity 名称到功能的映射规则
        activity_to_function = {
            "login": "Login",
            "signin": "Login",
            "sign_in": "Login",
            "register": "Register",
            "signup": "Register",
            "sign_up": "Register",
            "search": "Search",
            "settings": "Settings",
            "profile": "Profile",
            "home": "Home",
            "main": "Main",
            "splash": "Splash",
            "menu": "Menu",
            "detail": "View Details",
            "result": "View Results",
            "list": "List View",
            "add": "Add Item",
            "edit": "Edit Item",
            "delete": "Delete Item",
            "save": "Save",
            "submit": "Submit",
            "cancel": "Cancel",
            "back": "Navigation",
        }

        # 转换为小写进行匹配
        activity_lower = activity_name.lower()

        # 精确匹配
        if activity_lower in activity_to_function:
            return activity_to_function[activity_lower]

        # 部分匹配
        for key, value in activity_to_function.items():
            if key in activity_lower:
                return value

        # 默认返回 Activity 名称作为功能
        return activity_name

    def set_current_function(self, function_name: str, status: str = "testing") -> None:
        """设置当前测试功能"""
        self.current_function = function_name
        self.current_function_status = status

    # ==================== 数据获取方法 ====================

    def get_activities_info(self) -> List[Dict]:
        """获取 Activities 信息列表"""
        return [
            {"name": name, "visit_time": info.get("visits", 0), "status": info.get("status", "unvisited")}
            for name, info in self.activity_info.items()
        ]

    def get_covered_activities(self) -> List[Dict]:
        """获取已覆盖的 Activities"""
        covered = []
        seen = set()
        for op in reversed(list(self.operation_history)):
            activity = op.get("activity_name")
            if activity and activity not in seen:
                seen.add(activity)
                info = self.activity_info.get(activity, {})
                covered.append({"name": activity, "visit_time": info.get("visits", 0)})
        return list(reversed(covered))

    def get_explored_functions(self) -> Dict[str, Dict]:
        """获取已探索的功能"""
        return self.explored_functions.copy()

    def get_operation_history(self) -> List[Dict]:
        """获取操作历史列表"""
        return list(self.operation_history)

    def get_widget_visits(self, activity_name: str) -> Dict[str, int]:
        """获取指定 Activity 的 Widget 访问记录"""
        return self.widget_visits.get(activity_name, {})

    def get_widgets_tested(self, activity_name: str) -> List[Dict]:
        """获取指定 Activity 已测试的 Widgets"""
        widget_visits = self.get_widget_visits(activity_name)
        return [{"name": name, "visits": visits} for name, visits in widget_visits.items()]

    # ==================== 统计方法 ====================

    def get_step_count(self) -> int:
        """获取测试步骤数"""
        return self.step_counter

    def get_current_function(self) -> Optional[str]:
        """获取当前测试功能"""
        return self.current_function

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            "app_name": self.app_name,
            "total_activities": len(self.activity_info),
            "visited_activities": sum(1 for a in self.activity_info.values() if a.get("status") == "visited"),
            "total_functions": len(self.explored_functions),
            "tested_functions": sum(1 for f in self.explored_functions.values() if f.get("status") == "tested"),
            "total_steps": self.step_counter,
            "current_function": self.current_function
        }

    def get_memory_prompt(self) -> str:
        """
        生成记忆提示词（用于 Bug 报告）

        Returns:
            格式化的测试历史字符串
        """
        lines = ["测试历史记录:", ""]

        # 添加已探索功能
        if self.explored_functions:
            lines.append("已探索功能:")
            for name, info in self.explored_functions.items():
                visits = info.get("visits", 0)
                status = info.get("status", "unknown")
                lines.append(f"  - {name}: {visits}次访问, 状态: {status}")
            lines.append("")

        # 添加操作历史
        if self.operation_history:
            lines.append("最近操作历史:")
            for i, entry in enumerate(self.operation_history, 1):
                activity = entry.get("activity_name", "Unknown")
                operation = entry.get("operation", "Unknown")
                target = entry.get("target_widget", "Unknown")
                lines.append(f"  {i}. [{activity}] {operation} -> {target}")
            lines.append("")

        # 添加 Activity 访问情况
        if self.activity_info:
            lines.append("Activity访问情况:")
            for name, info in self.activity_info.items():
                visits = info.get("visits", 0)
                status = info.get("status", "unvisited")
                lines.append(f"  - {name}: {visits}次访问, 状态: {status}")

        return "\n".join(lines) if lines else "[无测试历史记录]"

    # ==================== 清理方法 ====================

    def clear_memory(self) -> None:
        """清空所有记忆记录"""
        self.activity_info.clear()
        self.widget_visits.clear()
        self.explored_functions.clear()
        self.operation_history.clear()
        self.current_function = None
        self.current_function_status = None
        self.step_counter = 0
        print("[记忆清空] 所有测试历史已清除")


# 测试入口
if __name__ == "__main__":
    memory = TestingSequenceMemorizer()

    # 设置 App
    memory.set_app_name("TestApp")

    # 注册 Activities
    memory.register_activities(["MainActivity", "SearchActivity", "SettingsActivity"])

    # 更新功能
    memory.update_function("Login", "tested")
    memory.update_function("Search", "testing")

    # 记录操作
    memory.record_operation("MainActivity", [{"name": "Search", "visits": 1}], "Click", "Search")
    memory.record_operation("SearchActivity", [{"name": "SearchBox", "visits": 1}], "Input", "SearchBox")

    # 打印统计
    print("\n" + "=" * 60)
    print("统计信息:")
    print("=" * 60)
    print(memory.get_stats())

    print("\nActivities Info:")
    print(memory.get_activities_info())

    print("\nCovered Activities:")
    print(memory.get_covered_activities())

    print("\nExplored Functions:")
    print(memory.get_explored_functions())

    print("\nOperation History:")
    for op in memory.get_operation_history():
        print(f"  {op}")