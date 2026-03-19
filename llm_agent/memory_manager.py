"""
功能感知记忆管理模块
实现 GPTDroid 论文中的 Functionality-aware Memory 机制
用于记录测试历史并生成记忆相关的提示词
"""

from typing import List, Dict, Optional
from collections import deque


class TestingSequenceMemorizer:
    """
    测试序列记忆器
    记录测试过程中的页面、操作和功能信息，生成记忆提示词
    遵循 GPTDroid 论文的记忆机制设计
    """

    # 最大保留的历史步数（论文中保留最近5步）
    MAX_HISTORY_STEPS = 5

    def __init__(self):
        """
        初始化测试序列记忆器
        """
        # 已测试的页面列表（保留最近5个）
        self.tested_pages: deque = deque(maxlen=self.MAX_HISTORY_STEPS)

        # 已执行的操作列表（保留最近5个）
        self.tested_operations: deque = deque(maxlen=self.MAX_HISTORY_STEPS)

        # 当前正在测试的功能名称
        self.current_function: Optional[str] = None

        # 功能测试历史记录
        self.function_history: List[Dict] = []

        # 测试步骤计数器
        self.step_counter: int = 0

    def update_step(
        self,
        activity_name: str,
        operation: str,
        widget_name: str,
        target_function: Optional[str] = None,
        success: bool = True,
        effect_status: str = "OK"
    ) -> None:
        """
        更新测试步骤记录

        记录当前步骤的页面、操作和功能信息

        Args:
            activity_name: 当前 Activity 名称
            operation: 执行的操作类型（click/double-click/long press/scroll）
            widget_name: 操作的目标控件名称
            target_function: 该操作对应的功能名称（可选）
            success: 操作是否成功，默认为 True
            effect_status: 操作效果状态，可选值：
                - "OK": 操作成功且有效
                - "NO_EFFECT": 操作执行但 UI 无变化（可能是假按钮）
                - "FAILED": 操作执行失败
        """
        self.step_counter += 1

        # 记录测试页面
        self.tested_pages.append({
            "step": self.step_counter,
            "activity": activity_name
        })

        # 记录操作（包含成功/失败状态和效果状态）
        self.tested_operations.append({
            "step": self.step_counter,
            "operation": operation,
            "widget": widget_name,
            "activity": activity_name,
            "success": success,
            "effect_status": effect_status
        })

        # 更新当前功能
        if target_function:
            self.current_function = target_function
            self.function_history.append({
                "step": self.step_counter,
                "function": target_function,
                "operation": f"{operation} {widget_name}"
            })

        # 打印状态
        if effect_status == "NO_EFFECT":
            status_str = "无效操作"
        elif success:
            status_str = "成功"
        else:
            status_str = "失败"
        print(f"[记忆更新] 步骤 {self.step_counter}: {operation} '{widget_name}' @ {activity_name} [{status_str}]")

    def get_memory_prompt(self) -> str:
        """
        生成记忆部分的提示词

        按照论文格式生成历史页面和操作的记忆描述：
        1. 历史页面列表
        2. 最近5步的操作历史

        Returns:
            记忆部分的提示词字符串
        """
        # 构建历史页面部分
        activities_prompt = self._build_activities_prompt()

        # 构建操作历史部分
        operations_prompt = self._build_operations_prompt()

        # 拼接完整的记忆提示词
        memory_prompt = f"{activities_prompt}\n{operations_prompt}"
        return memory_prompt

    def _build_activities_prompt(self) -> str:
        """
        构建历史页面部分的提示词

        格式：'[History of tested activities: activity1, activity2, ...]'

        Returns:
            历史页面提示词字符串
        """
        if not self.tested_pages:
            return "[History of tested activities: None]"

        # 提取所有已测试的 Activity 名称（去重）
        activities = list(dict.fromkeys(
            page["activity"] for page in self.tested_pages
        ))

        activities_str = ", ".join(activities)
        return f"[History of tested activities: {activities_str}]"

    def _build_operations_prompt(self) -> str:
        """
        构建操作历史部分的提示词

        格式：'[History of latest tested pages and operations: Latest 5th step tested the page MainActivity and performed click on Button. Latest 4th step ...]'

        Returns:
            操作历史提示词字符串
        """
        if not self.tested_operations:
            return "[History of latest tested pages and operations: None]"

        # 构建每一步的描述
        steps_descriptions = []
        operations_list = list(self.tested_operations)

        # 从最新到最旧排序（Latest 5th -> Latest 1st）
        for i, op in enumerate(reversed(operations_list), 1):
            # 根据效果状态添加标记
            status_note = ""
            effect_status = op.get("effect_status", "OK")

            if effect_status == "NO_EFFECT":
                status_note = " [NO_EFFECT - click had no effect, button may be unclickable]"
            elif not op.get("success", True):
                status_note = " [FAILED - widget not found]"

            step_desc = (
                f"Latest {len(operations_list) - i + 1}th step "
                f"tested the page {op['activity']} "
                f"and performed {op['operation']} on {op['widget']}{status_note}"
            )
            steps_descriptions.append(step_desc)

        steps_str = ". ".join(steps_descriptions)
        return f"[History of latest tested pages and operations: {steps_str}.]"

    def get_function_query(self) -> str:
        """
        获取功能查询问题

        格式：'What is the functions currently being tested? Are we testing a new function? (<Function name> + <Status>)'

        Returns:
            功能查询问题字符串
        """
        query = (
            "What is the functions currently being tested? "
            "Are we testing a new function? "
            "(<Function name> + <Status>)"
        )
        return query

    def set_current_function(self, function_name: str) -> None:
        """
        设置当前正在测试的功能

        Args:
            function_name: 功能名称
        """
        self.current_function = function_name
        print(f"[功能更新] 当前测试功能: {function_name}")

    def get_current_function(self) -> Optional[str]:
        """
        获取当前正在测试的功能

        Returns:
            当前功能名称，未设置则返回 None
        """
        return self.current_function

    def get_step_count(self) -> int:
        """
        获取当前测试步骤数

        Returns:
            步骤计数
        """
        return self.step_counter

    def clear_memory(self) -> None:
        """
        清空所有记忆记录
        """
        self.tested_pages.clear()
        self.tested_operations.clear()
        self.current_function = None
        self.function_history.clear()
        self.step_counter = 0
        print("[记忆清空] 所有测试历史已清除")


# 测试入口
if __name__ == "__main__":
    # 测试记忆器
    memory = TestingSequenceMemorizer()

    # 模拟记录几个测试步骤
    memory.update_step("MainActivity", "click", "Search", "Search Function")
    memory.update_step("SearchActivity", "input", "SearchBox")
    memory.update_step("SearchActivity", "click", "SearchButton", "Search Function")
    memory.update_step("ResultActivity", "scroll", "ResultList")
    memory.update_step("ResultActivity", "click", "Item1", "View Details")

    # 获取记忆提示词
    memory_prompt = memory.get_memory_prompt()
    function_query = memory.get_function_query()

    print("\n" + "=" * 60)
    print("记忆提示词:")
    print("=" * 60)
    print(memory_prompt)

    print("\n" + "=" * 60)
    print("功能查询:")
    print("=" * 60)
    print(function_query)