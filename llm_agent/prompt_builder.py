"""
Prompt Builder Module
Generates structured prompts for LLM-based Android GUI testing
Supports three-phase prompt building: Initial, Test, and Feedback

依赖 TestingSequenceMemorizer 作为唯一数据源
"""

from typing import List, Dict, Optional, TYPE_CHECKING

# Handle both relative and absolute imports
try:
    from .prompt_templates import (
        GUIContextTemplate,
        FunctionMemoryTemplate,
        TestHistoryTemplate,
        SystemPromptTemplate
    )
    from .memory_manager import TestingSequenceMemorizer
except ImportError:
    from prompt_templates import (
        GUIContextTemplate,
        FunctionMemoryTemplate,
        TestHistoryTemplate,
        SystemPromptTemplate
    )
    from memory_manager import TestingSequenceMemorizer

if TYPE_CHECKING:
    from .exploration_cache import ExplorationCache


class PromptGenerator:
    """
    Prompt Generator for Android GUI Testing

    使用 TestingSequenceMemorizer 作为唯一数据源
    生成结构化提示词，遵循新的模板系统：
    - GUIContext[1,2,3,4,5,6]: App, Page, Widget, Operation Questions, Feedback
    - FunctionMemory[1,2,3,4]: Explored Function, Covered Activities, History, Function Query

    支持三阶段提示词构建：
    - Initial phase: GUIContext[1,2,3,4,5] + FunctionMemory[1,2,3,4]
    - Test phase (success): Success message + GUIContext[2,3,4,5] + FunctionMemory[1,2,3,4]
    - Feedback phase (failure): GUIContext[6] + GUIContext[3,4,5] + FunctionMemory[3,4]
    """

    def __init__(
        self,
        memory_manager: Optional[TestingSequenceMemorizer] = None,
        exploration_cache: Optional["ExplorationCache"] = None
    ):
        """
        初始化 Prompt Generator

        Args:
            memory_manager: 记忆管理器实例（唯一数据源）
            exploration_cache: 探索缓存实例（可选）
        """
        # 使用传入的 memory_manager 或创建新的
        self.memory = memory_manager or TestingSequenceMemorizer()
        self.exploration_cache = exploration_cache

    # ==================== 配置方法 ====================

    def set_app_name(self, app_name: str) -> None:
        """设置应用名称"""
        self.memory.set_app_name(app_name)

    def register_activity(self, activity_name: str, status: str = "unvisited") -> None:
        """注册 Activity"""
        self.memory.register_activity(activity_name, status)

    # ==================== 委托到 memory_manager 的方法 ====================

    def record_activity_visit(self, activity_name: str) -> None:
        """记录 Activity 访问"""
        self.memory.record_activity_visit(activity_name)

    def record_widget_visit(self, activity_name: str, widget_identifier: str) -> None:
        """记录 Widget 访问"""
        self.memory.record_widget_visit(activity_name, widget_identifier)

    def record_operation(
        self,
        activity_name: str,
        widgets_tested: List[Dict],
        operation: str,
        target_widget: str,
        success: bool = True
    ) -> None:
        """记录操作"""
        self.memory.record_operation(activity_name, widgets_tested, operation, target_widget, success)

    def update_function(self, function_name: str, status: str = "testing") -> None:
        """更新功能状态"""
        self.memory.update_function(function_name, status)

    def set_current_function(self, function_name: str, status: str = "testing") -> None:
        """设置当前测试功能"""
        self.memory.set_current_function(function_name, status)

    # ==================== Widget 处理 ====================

    def _get_widget_identifier(self, widget: Dict) -> Optional[str]:
        """获取 Widget 标识符"""
        class_name = widget.get("class", "")

        # For EditText controls, prioritize resource_id (text is dynamic/hint text)
        if "EditText" in class_name:
            resource_id = widget.get("resource_id", "")
            if resource_id:
                return resource_id.split("/")[-1] if "/" in resource_id else resource_id
            # Fallback to hint text if no resource_id
            text = widget.get("text", "")
            if text and text.strip():
                return text.strip()
            return "EditText"

        # For other widgets, prioritize text (more readable/stable for Buttons, TextViews, etc.)
        text = widget.get("text", "")
        if text and text.strip():
            return text.strip()

        resource_id = widget.get("resource_id", "")
        if resource_id:
            return resource_id.split("/")[-1] if "/" in resource_id else resource_id

        return None

    def _process_widgets_for_template(
        self,
        activity_name: str,
        widgets: List[Dict]
    ) -> tuple[List[str], List[str], List[Dict]]:
        """处理 Widgets 用于模板渲染"""
        if not widgets:
            return [], [], []

        # 分离上下区域控件
        upper_widgets = [w for w in widgets if w.get("position") == "upper"]
        lower_widgets = [w for w in widgets if w.get("position") == "lower"]

        # 提取名称
        upper_names = [n for n in (self._get_widget_identifier(w) for w in upper_widgets) if n]
        lower_names = [n for n in (self._get_widget_identifier(w) for w in lower_widgets) if n]

        # 处理控件（添加 Nearby 信息）
        processed_widgets = self._add_nearby_info(widgets)

        return upper_names, lower_names, processed_widgets

    def _add_nearby_info(self, widgets: List[Dict]) -> List[Dict]:
        """添加 Nearby 信息"""
        processed = []

        for i, widget in enumerate(widgets):
            widget_id = self._get_widget_identifier(widget)
            if not widget_id:
                continue

            class_name = widget.get("class", "")
            category = class_name.split(".")[-1] if class_name else "Widget"

            # Nearby widgets
            nearby = []
            if i > 0:
                prev_id = self._get_widget_identifier(widgets[i - 1])
                prev_class = widgets[i - 1].get("class", "").split(".")[-1] if widgets[i - 1].get("class") else "Widget"
                if prev_id:
                    nearby.append(f"[{prev_class}: {prev_id}]")

            if i < len(widgets) - 1:
                next_id = self._get_widget_identifier(widgets[i + 1])
                next_class = widgets[i + 1].get("class", "").split(".")[-1] if widgets[i + 1].get("class") else "Widget"
                if next_id:
                    nearby.append(f"[{next_class}: {next_id}]")

            # Build processed widget with original_text for EditText
            processed_widget = {
                "text": widget_id,
                "resource_id": widget.get("resource_id", ""),
                "category": category,
                "nearby": nearby,
                "activity": widget.get("activity", "")
            }

            # For EditText, include the original text (hint or current content)
            if "EditText" in class_name:
                original_text = widget.get("text", "")
                if original_text and original_text.strip():
                    processed_widget["original_text"] = original_text.strip()

            processed.append(processed_widget)

        return processed

    # ==================== 提示词构建 ====================

    def build_system_prompt(self) -> str:
        """构建系统提示词"""
        return SystemPromptTemplate.get_system_prompt()

    def build_test_prompt(
        self,
        activity_or_widgets,
        widgets_or_activity=None,
        memorizer=None
    ) -> str:
        """
        构建 Test Prompt - 支持新旧签名

        新签名: build_test_prompt(widgets, activity_name)
        旧签名: build_test_prompt(activity_name, widgets, memorizer)
        """
        if isinstance(activity_or_widgets, str):
            # 旧签名
            activity_name = activity_or_widgets
            widgets = widgets_or_activity
            return self._build_initial_prompt_internal(widgets, activity_name)
        else:
            # 新签名
            widgets = activity_or_widgets
            activity_name = widgets_or_activity
            return self._build_test_prompt_internal(widgets, activity_name)

    def build_initial_prompt(
        self,
        widgets: List[Dict],
        activity_name: str,
        app_name: Optional[str] = None
    ) -> str:
        """
        构建初始阶段提示词

        组合: GUIContext[1,2,3,4,5] + FunctionMemory[1,2,3,4]
        """
        if app_name:
            self.memory.set_app_name(app_name)
        return self._build_initial_prompt_internal(widgets, activity_name)

    def _build_initial_prompt_internal(self, widgets: List[Dict], activity_name: str) -> str:
        """内部: 构建初始阶段提示词"""
        self.memory.register_activity(activity_name)

        upper_names, lower_names, processed_widgets = self._process_widgets_for_template(activity_name, widgets)
        widget_visits = self.memory.get_widget_visits(activity_name)

        parts = [
            # GUIContext[1,2,3]
            GUIContextTemplate.app_info(self.memory.app_name, self.memory.get_activities_info()),
            GUIContextTemplate.page_info(activity_name, upper_names, lower_names),
            GUIContextTemplate.widget_info(processed_widgets, widget_visits),
            # GUIContext[4,5]
            GUIContextTemplate.action_operation_question(),
            GUIContextTemplate.input_operation_question(),
            # FunctionMemory[1,2,3,4]
            FunctionMemoryTemplate.explored_function(self.memory.get_explored_functions()),
            FunctionMemoryTemplate.covered_activities(self.memory.get_covered_activities()),
            FunctionMemoryTemplate.latest_tested_history(self.memory.get_operation_history()),
            FunctionMemoryTemplate.function_query(self.memory.current_function, self.memory.current_function_status)
        ]

        return "\n\n".join(parts)

    def _build_test_prompt_internal(self, widgets: List[Dict], activity_name: str) -> str:
        """内部: 构建测试阶段提示词（成功后）"""
        self.memory.register_activity(activity_name)

        upper_names, lower_names, processed_widgets = self._process_widgets_for_template(activity_name, widgets)
        widget_visits = self.memory.get_widget_visits(activity_name)

        parts = [
            "We successfully did the above operation.",
            # GUIContext[2,3]
            GUIContextTemplate.page_info(activity_name, upper_names, lower_names),
            GUIContextTemplate.widget_info(processed_widgets, widget_visits),
            # GUIContext[4,5]
            GUIContextTemplate.action_operation_question(),
            GUIContextTemplate.input_operation_question(),
            # FunctionMemory[1,2,3,4]
            FunctionMemoryTemplate.explored_function(self.memory.get_explored_functions()),
            FunctionMemoryTemplate.covered_activities(self.memory.get_covered_activities()),
            FunctionMemoryTemplate.latest_tested_history(self.memory.get_operation_history()),
            FunctionMemoryTemplate.function_query(self.memory.current_function, self.memory.current_function_status)
        ]

        return "\n\n".join(parts)

    def build_feedback_prompt(
        self,
        widgets: List[Dict],
        activity_name: str,
        failed_widget: str
    ) -> str:
        """
        构建反馈阶段提示词（失败后）

        组合: GUIContext[6] + GUIContext[3,4,5] + FunctionMemory[3,4]
        """
        self.memory.register_activity(activity_name)

        _, _, processed_widgets = self._process_widgets_for_template(activity_name, widgets)
        widget_visits = self.memory.get_widget_visits(activity_name)

        parts = [
            # GUIContext[6]
            GUIContextTemplate.testing_feedback(failed_widget),
            # GUIContext[3]
            GUIContextTemplate.widget_info(processed_widgets, widget_visits),
            # GUIContext[4,5]
            GUIContextTemplate.action_operation_question(),
            GUIContextTemplate.input_operation_question(),
            # FunctionMemory[3,4]
            FunctionMemoryTemplate.latest_tested_history(self.memory.get_operation_history()),
            FunctionMemoryTemplate.function_query(self.memory.current_function, self.memory.current_function_status)
        ]

        return "\n\n".join(parts)

    # ==================== 兼容方法 ====================

    def build_test_prompt_legacy(
        self,
        activity_name: str,
        parsed_widgets: List[Dict],
        memorizer=None
    ) -> str:
        """旧方法，向后兼容"""
        return self.build_initial_prompt(parsed_widgets, activity_name)

    # ==================== 工具方法 ====================

    def clear_history(self) -> None:
        """清空历史"""
        self.memory.clear_memory()
        print("[PromptGenerator] History cleared")

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return self.memory.get_stats()

    # ==================== 属性访问（向后兼容）====================

    @property
    def app_name(self) -> str:
        return self.memory.app_name

    @property
    def activity_info(self) -> Dict[str, Dict]:
        return self.memory.activity_info

    @property
    def widget_visits(self) -> Dict[str, Dict[str, int]]:
        return self.memory.widget_visits

    @property
    def explored_functions(self) -> Dict[str, Dict]:
        return self.memory.explored_functions

    @property
    def current_function(self) -> Optional[str]:
        return self.memory.current_function

    @property
    def current_function_status(self) -> Optional[str]:
        return self.memory.current_function_status


# 测试入口
if __name__ == "__main__":
    # 创建共享的 memory_manager
    memory = TestingSequenceMemorizer()

    # 创建 generator，传入 memory_manager
    generator = PromptGenerator(memory_manager=memory)
    generator.set_app_name("Wikipedia")

    # 更新功能
    memory.update_function("Login", "tested")
    memory.update_function("Search", "testing")

    # 记录操作
    memory.record_operation("MainActivity", [{"name": "Search", "visits": 1}], "Click", "Search")

    # Mock widgets
    mock_widgets = [
        {"text": "SearchBox", "class": "android.widget.EditText", "resource_id": "com.app:id/search_box", "position": "upper"},
        {"text": "Search", "class": "android.widget.Button", "resource_id": "com.app:id/search", "position": "lower"}
    ]

    print("=" * 60)
    print("Initial Prompt:")
    print("=" * 60)
    print(generator.build_initial_prompt(mock_widgets, "SearchActivity"))

    print("\n" + "=" * 60)
    print("Stats:")
    print("=" * 60)
    print(generator.get_stats())