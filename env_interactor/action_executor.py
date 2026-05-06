"""
动作执行器模块
解析 LLM 响应并执行相应的 GUI 操作
集成崩溃检测机制，实现 GPTDroid 论文的 Bug Oracle 功能
支持多模态 Bug 分析引擎（崩溃 + 逻辑错误）

支持 ReAct JSON 格式解析，提高 LLM 决策智商
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

# 导入 ADB 控制器
from env_interactor.adb_utils import ADBController

# 类型检查时导入，避免循环依赖
if TYPE_CHECKING:
    from llm_agent.memory_manager import TestingSequenceMemorizer
    from llm_agent.bug_analysis_engine import BugAnalysisEngine, BugReport
    from llm_agent.screenshot_manager import ScreenshotManager


# Bug 报告保存目录
BUG_REPORTS_DIR = Path("bug_reports")


# 解析结果数据类
class ParsedAction:
    """
    解析后的动作数据结构

    支持 ReAct JSON 格式，包含推理和动作信息
    支持功能查询响应解析（FunctionName + Status）
    支持多输入操作（多个 Widget/Input 对）
    支持输入后操作（OperationWidget）
    支持预期结果和 Bug 检测

    JSON 格式示例:
    - 非输入操作:
      {"Thought": "...", "Function": "...", "Status": "Yes/No", "Operation": "click", "Widget": "Button", "Expected_Result": "...", "Bug_Detected": false}
    - 输入操作:
      {"Thought": "...", "Function": "...", "Status": "Yes/No", "Widget": "InputField", "Input": "text", "Operation": "click", "OperationWidget": "Submit", "Expected_Result": "..."}
    - Bug 检测:
      {"Thought": "...", "Function": "...", "Status": "Yes/No", "Operation": "click", "Widget": "Button", "Expected_Result": "...", "Bug_Detected": true, "Bug_Description": {...}}
    """
    def __init__(
        self,
        operation: Optional[str] = None,
        widget: Optional[str] = None,
        input_text: Optional[str] = None,
        thought: Optional[str] = None,
        page_description: Optional[str] = None,  # NEW: 页面描述
        status: Optional[str] = None,
        function_name: Optional[str] = None,
        function_status: Optional[str] = None,
        input_sequence: Optional[List[Tuple[str, str, Optional[str]]]] = None,  # 三元组：(widget, input_text, content_desc)
        operation_widget: Optional[str] = None,
        external_redirect: bool = False,
        redirect_package: Optional[str] = None,
        expected_result: Optional[str] = None,
        bug_detected: bool = False,
        bug_description: Optional[Dict] = None,
        widget_type: Optional[str] = None,
        operation_widget_type: Optional[str] = None,
        widget_content_desc: Optional[str] = None,  # 新增：用于区分同 resource-id 的字段
        target_x: Optional[int] = None,  # NEW: Target center X coordinate for visual positioning
        target_y: Optional[int] = None   # NEW: Target center Y coordinate for visual positioning
    ):
        self.operation = operation
        self.widget = widget
        self.input_text = input_text
        self.thought = thought  # ReAct: 推理过程
        self.page_description = page_description  # NEW: 页面描述（来自 LLM 的 Page_Description 字段）
        self.status = status    # ReAct: 状态信息
        self.function_name = function_name  # Function Query: 功能名称
        self.function_status = function_status  # Function Query: 功能状态 (tested/testing)
        self.input_sequence = input_sequence  # 多输入序列: [(widget, input_text, content_desc), ...]
        self.operation_widget = operation_widget  # 输入后的操作目标控件（如 Submit 按钮）
        self.external_redirect = external_redirect  # 是否触发了外部应用跳转
        self.redirect_package = redirect_package  # 跳转到的外部应用包名
        self.expected_result = expected_result  # 预期结果
        self.bug_detected = bug_detected  # 是否检测到 Bug
        self.bug_description = bug_description  # Bug 描述 {"type": "...", "severity": "...", "description": "..."}
        self.widget_type = widget_type  # 控件类型 (TextView, EditText, Button, etc.)
        self.operation_widget_type = operation_widget_type  # 操作目标控件类型
        self.widget_content_desc = widget_content_desc  # 用于区分同 resource-id 的字段（如 Front/Back）
        self.target_x = target_x  # NEW: 目标控件中心 X 坐标（用于视觉定位）
        self.target_y = target_y  # NEW: 目标控件中心 Y 坐标（用于视觉定位）

    def is_valid(self) -> bool:
        """检查是否为有效的动作"""
        # 必须有操作类型
        if not self.operation:
            return False

        # 系统级动作（back, scroll_down, scroll_up）不需要 widget
        system_actions = {"back", "scroll_down", "scroll_up"}
        if self.operation in system_actions:
            return True

        # 普通滚动操作也不需要 widget
        if self.operation == "scroll":
            return True

        # click 类操作：如果提供了视觉定位坐标 (TargetX, TargetY)，则不需要 widget
        if self.target_x is not None and self.target_y is not None:
            return True

        # 其他 click 类操作必须有 widget
        if not self.widget:
            return False

        return True

    def __repr__(self) -> str:
        parts = [f"operation='{self.operation}'"]
        if self.widget:
            parts.append(f"widget='{self.widget}'")
        if self.widget_type:
            parts.append(f"widget_type='{self.widget_type}'")
        if self.widget_content_desc:
            parts.append(f"content_desc='{self.widget_content_desc}'")
        if self.target_x and self.target_y:
            parts.append(f"target=({self.target_x},{self.target_y})")
        if self.input_text:
            parts.append(f"input='{self.input_text[:30]}...'" if len(self.input_text) > 30 else f"input='{self.input_text}'")
        if self.operation_widget:
            parts.append(f"op_widget='{self.operation_widget}'")
        if self.operation_widget_type:
            parts.append(f"op_widget_type='{self.operation_widget_type}'")
        if self.input_sequence:
            parts.append(f"inputs={len(self.input_sequence)}")
        if self.thought:
            parts.append(f"thought='{self.thought[:50]}...'" if len(self.thought or '') > 50 else f"thought='{self.thought}'")
        if self.function_name:
            parts.append(f"function='{self.function_name}'")
            if self.function_status:
                parts.append(f"status='{self.function_status}'")
        if self.expected_result:
            parts.append(f"expected='{self.expected_result[:30]}...'" if len(self.expected_result) > 30 else f"expected='{self.expected_result}'")
        if self.bug_detected:
            parts.append(f"BUG_DETECTED=True")
        return f"ParsedAction({', '.join(parts)})"

    def has_function_info(self) -> bool:
        """Check if parsed action contains function information"""
        return self.function_name is not None and self.function_status is not None

    def has_multiple_inputs(self) -> bool:
        """Check if parsed action has multiple input operations"""
        return self.input_sequence is not None and len(self.input_sequence) > 0

    def has_input_with_operation(self) -> bool:
        """Check if this is an input operation followed by another operation (e.g., click submit)"""
        return self.input_text is not None and self.operation_widget is not None


class ActionExecutor:
    """
    动作执行器类
    解析 LLM 的决策响应，找到目标控件并执行相应的操作
    集成崩溃检测机制（Bug Oracle）

    支持全局系统级动作（System-level Navigation）：
    - back: 返回键
    - scroll_down: 向下滚动屏幕
    - scroll_up: 向上滚动屏幕
    """

    # 支持的操作类型
    VALID_OPERATIONS = {
        # 控件操作
        "click", "double-click", "double click",
        "long press", "longpress", "scroll",
        "input", "type",
        # 全局系统级动作（无需指定 Widget）
        "back", "scroll_down", "scroll_up",
    }

    # 系统级动作列表（不需要 Widget）
    SYSTEM_LEVEL_ACTIONS = {"back", "scroll_down", "scroll_up"}

    def __init__(
        self,
        bug_analysis_engine: Optional["BugAnalysisEngine"] = None,
        screenshot_manager: Optional["ScreenshotManager"] = None
    ):
        """
        初始化动作执行器

        Args:
            bug_analysis_engine: Bug 分析引擎（可选，用于增强 Bug 检测）
            screenshot_manager: 截图管理器（可选，用于 Bug 报告）
        """
        # Bug 分析引擎（多模态增强）
        self.bug_analysis_engine = bug_analysis_engine
        self.screenshot_manager = screenshot_manager

        # 最后一次崩溃检测结果（供外部查询）
        self.last_crash_detected: bool = False
        self.last_crash_log: str = ""

        # 最后一次 Bug 报告（来自 BugAnalysisEngine）
        self.last_bug_report: Optional["BugReport"] = None

    def set_bug_analysis_engine(self, engine: "BugAnalysisEngine") -> None:
        """设置 Bug 分析引擎"""
        self.bug_analysis_engine = engine

    def set_screenshot_manager(self, manager: "ScreenshotManager") -> None:
        """设置截图管理器"""
        self.screenshot_manager = manager

    def execute_action(
        self,
        llm_response: str,
        parsed_widgets: List[Dict],
        adb_controller: ADBController,
        memory_manager: Optional["TestingSequenceMemorizer"] = None,
        activity_name: str = "UnknownActivity",
        target_package: Optional[str] = None
    ) -> Tuple[bool, Optional[ParsedAction]]:
        """
        执行 LLM 决策的动作，并在执行后检测崩溃和外部跳转

        完整流程：
        1. 解析 LLM 响应，提取操作类型、目标控件名和输入文本
        2. 在控件列表中查找匹配的控件
        3. 计算目标控件的中心坐标
        4. 调用 ADB 执行相应的操作
        5. 检测是否发生崩溃，如有崩溃则保存 Bug 报告
        6. 检测是否发生外部应用跳转，如有跳转则返回目标应用

        Args:
            llm_response: LLM 的响应字符串
            parsed_widgets: 解析后的控件列表
            adb_controller: ADB 控制器实例
            memory_manager: 记忆管理器实例，用于生成复现路径
            activity_name: 当前 Activity 名称，用于 Bug 报告
            target_package: 目标应用的包名，用于检测外部跳转

        Returns:
            元组 (success, action):
            - success: True 表示执行成功，False 表示执行失败
            - action: 解析后的 ParsedAction 对象，解析失败时为 None
              - action.external_redirect: True 表示触发了外部应用跳转
              - action.redirect_package: 跳转到的外部应用包名

        注意：崩溃检测结果存储在 self.last_crash_detected 和 self.last_crash_log 中
        """
        print("\n" + "=" * 50)
        print("动作执行层 - 执行 LLM 决策")
        print("=" * 50)

        # 重置崩溃检测状态
        self.last_crash_detected = False
        self.last_crash_log = ""

        # 步骤1：解析 LLM 响应
        action = self._parse_llm_response(llm_response)

        if not action.is_valid():
            print(f"[执行失败] 无法解析 LLM 响应: {llm_response[:100]}...")
            return False, None

        print(f"[解析结果] {action}")

        # ========== 处理系统级动作（无需匹配控件）==========
        # 处理 back 操作（物理返回键）
        if action.operation == "back":
            print("[系统动作] 按下系统返回键")
            success = adb_controller.go_back()
            time.sleep(1)  # 等待页面切换

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action,
                widgets=parsed_widgets, target_package=target_package
            )

            return success, action

        # 处理 scroll_down 操作（向下滚动屏幕）
        if action.operation == "scroll_down":
            print("[系统动作] 向下滚动屏幕（查看下方内容）")
            success = adb_controller.scroll_down()
            time.sleep(1)  # 等待滚动完成

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action,
                widgets=parsed_widgets, target_package=target_package
            )

            return success, action

        # 处理 scroll_up 操作（向上滚动屏幕）
        if action.operation == "scroll_up":
            print("[系统动作] 向上滚动屏幕（查看上方内容）")
            success = adb_controller.scroll_up()
            time.sleep(1)  # 等待滚动完成

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action,
                widgets=parsed_widgets, target_package=target_package
            )

            return success, action

        # ========== 处理控件级操作（需要匹配控件）==========

        # 处理多输入操作（多个 Widget/Input 对）
        if action.has_multiple_inputs():
            success = self._execute_multiple_inputs_action(action, parsed_widgets, adb_controller)

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action,
                widgets=parsed_widgets, target_package=target_package
            )

            # 检测外部跳转
            self._check_external_redirect(adb_controller, target_package, action)

            return success, action

        # 处理输入+操作组合（Widget + Input + Operation + OperationWidget）
        # 例如：输入文本到输入框，然后点击 Submit 按钮
        if action.has_input_with_operation():
            success = self._execute_input_then_operation(action, parsed_widgets, adb_controller)

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action,
                widgets=parsed_widgets, target_package=target_package
            )

            # 检测外部跳转
            self._check_external_redirect(adb_controller, target_package, action)

            return success, action

        # 处理输入操作（operation == 'input' 或有 input_text）
        if action.operation == "input" or (action.input_text and action.widget):
            success = self._execute_input_action(action, parsed_widgets, adb_controller)

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action,
                widgets=parsed_widgets, target_package=target_package
            )

            # 检测外部跳转
            self._check_external_redirect(adb_controller, target_package, action)

            return success, action

        # 处理滚动操作（支持方向：up/down）
        if action.operation == "scroll":
            # 解析滚动方向
            scroll_direction = "down"  # 默认向下滚动
            if action.widget:
                widget_lower = action.widget.lower().strip()
                if widget_lower in ("up", "down", "upward", "downward"):
                    scroll_direction = "up" if "up" in widget_lower else "down"

            success = self._execute_scroll_action(parsed_widgets, adb_controller, direction=scroll_direction)

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action,
                widgets=parsed_widgets, target_package=target_package
            )

            return success, action

        # ========== NEW: 视觉定位坐标直接点击（无 widget 名称时）==========
        # 如果提供了 TargetX/TargetY 但没有 widget 名称，直接使用坐标点击
        if action.target_x is not None and action.target_y is not None and not action.widget:
            print(f"[视觉定位] 直接使用坐标点击: ({action.target_x}, {action.target_y})")

            # 在控件列表中查找最接近该坐标的控件（用于日志记录）
            closest_widget = None
            min_distance = float('inf')
            for widget in parsed_widgets:
                cx, cy = self._calculate_center(widget)
                if cx is not None and cy is not None:
                    distance = abs(cx - action.target_x) + abs(cy - action.target_y)
                    if distance < min_distance:
                        min_distance = distance
                        closest_widget = widget

            if closest_widget:
                rid = closest_widget.get("resource_id", "")
                print(f"[视觉定位] 最近控件: {rid}, 距离: {min_distance}px")

            # 执行点击
            success = self._perform_operation(
                action.operation, action.target_x, action.target_y, adb_controller
            )

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action,
                widgets=parsed_widgets, target_package=target_package
            )

            # 检测外部跳转
            self._check_external_redirect(adb_controller, target_package, action)

            return success, action

        # 步骤2：查找匹配的控件
        target_widget = self._find_target_widget(
            action.widget, parsed_widgets,
            widget_type=action.widget_type,
            target_x=action.target_x,  # NEW: 视觉定位坐标
            target_y=action.target_y   # NEW: 视觉定位坐标
        )
        if not target_widget:
            print(f"[执行失败] 未找到名为 '{action.widget}' 的控件")
            return False, action

        print(f"[找到控件] ID: {target_widget.get('resource_id', 'N/A')}")
        print(f"[控件坐标] bounds: {target_widget.get('bounds', 'N/A')}")

        # 步骤3：计算中心坐标
        center_x, center_y = self._calculate_center(target_widget)
        if center_x is None or center_y is None:
            print("[执行失败] 无法计算控件中心坐标")
            return False, action

        print(f"[计算坐标] 中心点: ({center_x}, {center_y})")

        # 步骤4：执行操作
        success = self._perform_operation(
            action.operation, center_x, center_y, adb_controller
        )

        # 执行后检测崩溃
        self._check_crash_after_action(
            adb_controller, memory_manager, activity_name, action,
            widgets=parsed_widgets, target_package=target_package
        )

        # 检测外部跳转
        self._check_external_redirect(adb_controller, target_package, action)

        return success, action

    def _check_external_redirect(
        self,
        adb_controller: ADBController,
        target_package: Optional[str],
        action: ParsedAction
    ) -> None:
        """
        检测并处理外部应用跳转

        当操作触发了外部应用跳转时：
        1. 标记 action.external_redirect = True
        2. 记录跳转到的包名 action.redirect_package
        3. 按 back 返回目标应用

        Args:
            adb_controller: ADB 控制器实例
            target_package: 目标应用包名
            action: 当前执行的动作对象
        """
        if not target_package:
            return

        # 获取当前焦点应用包名
        current_package = adb_controller.get_current_package()

        if current_package != target_package:
            print(f"\n[外部跳转检测] 检测到应用跳转!")
            print(f"  - 目标应用: {target_package}")
            print(f"  - 跳转到: {current_package}")

            # 标记跳转信息
            action.external_redirect = True
            action.redirect_package = current_package

            # 按 back 返回目标应用
            print(f"[外部跳转处理] 按下 back 键返回目标应用...")
            adb_controller.go_back()
            time.sleep(1)

            # 验证是否返回成功
            new_package = adb_controller.get_current_package()
            if new_package == target_package:
                print(f"[外部跳转处理] 成功返回目标应用: {target_package}")
            else:
                print(f"[外部跳转处理] 警告: 当前包名 {new_package}，可能需要再次按 back")

    def _check_crash_after_action(
        self,
        adb_controller: ADBController,
        memory_manager: Optional["TestingSequenceMemorizer"],
        activity_name: str,
        action: ParsedAction,
        widgets: Optional[List[Dict]] = None,
        target_package: Optional[str] = None
    ) -> None:
        """
        在执行动作后检测崩溃，并保存 Bug 报告

        智能崩溃检测流程（优化版）：
        1. 快速检查（0.5秒后）- 大多数崩溃会立即发生
        2. 异步轮询检测（最长 2 秒，发现崩溃立即返回）
        3. 如果配置了 BugAnalysisEngine，使用多模态分析增强

        注意：logcat 缓存清空应在动作执行前完成（由调用方负责）

        检测结果存储在 self.last_crash_detected 和 self.last_crash_log 中

        Args:
            adb_controller: ADB 控制器实例
            memory_manager: 记忆管理器实例
            activity_name: 当前 Activity 名称
            action: 刚执行的动作
            widgets: 当前控件列表（用于逻辑错误检测）
            target_package: 被测应用包名，用于过滤误报
        """
        print("\n[Bug Oracle] 正在检测 Bug...")

        # 重置 Bug 报告
        self.last_bug_report = None

        # ========== 智能崩溃检测（优化：异步轮询）==========
        # 策略：先快速检查，然后轮询，最长等待 2 秒
        # 大多数崩溃会在 0.5 秒内发生，优化后平均检测时间从 2 秒降至 0.5-1 秒

        max_wait_time = 2.0  # 最长等待时间
        check_interval = 0.25  # 检查间隔
        elapsed = 0.0
        crash_detected = False

        print("[Bug Oracle] 智能检测中（最长 2 秒，发现崩溃立即返回）...")

        while elapsed < max_wait_time:
            # 快速检查崩溃（带包名过滤）
            crash_log = adb_controller.check_for_crash(target_package=target_package)

            if crash_log:
                crash_detected = True
                print(f"[Bug Oracle] 检测到崩溃！（耗时: {elapsed:.2f}秒）")
                break

            # 未检测到崩溃，继续等待
            time.sleep(check_interval)
            elapsed += check_interval

        # ========== 处理检测结果 ==========
        if crash_detected:
            print("=" * 60)
            print("🚨 [崩溃检测] 发现应用崩溃！")
            print("=" * 60)

            # 更新崩溃状态
            self.last_crash_detected = True
            self.last_crash_log = crash_log

            # 使用 BugAnalysisEngine 增强分析（如果可用）
            if self.bug_analysis_engine:
                try:
                    self.bug_analysis_engine.set_adb_controller(adb_controller)
                    if self.screenshot_manager:
                        self.bug_analysis_engine.set_screenshot_manager(self.screenshot_manager)

                    # 获取操作历史
                    operation_history = []
                    if memory_manager:
                        operation_history = list(memory_manager.operation_history)

                    bug_report = self.bug_analysis_engine.create_crash_report(
                        activity_name=activity_name,
                        operation=action.operation or "unknown",
                        widget=action.widget or "",
                        operation_history=operation_history,
                        target_package=target_package
                    )

                    if bug_report:
                        print(f"   严重程度: {bug_report.severity.value}")
                        print(f"   描述: {bug_report.title}")
                        self.last_bug_report = bug_report
                        return

                except Exception as e:
                    print(f"[BugAnalysisEngine] 分析失败: {e}")

            # 保存 Bug 报告
            self._save_bug_report(
                memory_manager=memory_manager,
                activity_name=activity_name,
                action=action,
                crash_log=crash_log
            )
        else:
            self.last_crash_detected = False
            self.last_crash_log = ""
            print(f"[Bug Oracle] 未检测到 Bug，继续测试...（检测耗时: {elapsed:.2f}秒）")

    def _save_bug_report(
        self,
        memory_manager: Optional["TestingSequenceMemorizer"],
        activity_name: str,
        action: ParsedAction,
        crash_log: str
    ) -> None:
        """
        保存 Bug 报告到文件

        报告内容包括：
        1. 崩溃时间戳
        2. 测试历史（memory prompt）
        3. 触发崩溃的操作
        4. 完整的崩溃堆栈

        Args:
            memory_manager: 记忆管理器实例
            activity_name: 当前 Activity 名称
            action: 触发崩溃的动作
            crash_log: 崩溃日志
        """
        # 确保报告目录存在
        BUG_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        # 生成报告文件名（带时间戳）
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = BUG_REPORTS_DIR / f"crash_report_{timestamp}.txt"

        try:
            # 构建报告内容
            report_lines = [
                "=" * 70,
                "GPTDroid Bug Report - 应用崩溃报告",
                "=" * 70,
                "",
                f"📅 崩溃时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"📍 所在页面: {activity_name}",
                f"⚡ 触发操作: {action.operation} on '{action.widget}'",
                "",
                "-" * 70,
                "📋 测试历史（复现路径）",
                "-" * 70,
            ]

            # 添加记忆提示词（如果有）
            if memory_manager:
                memory_prompt = memory_manager.get_memory_prompt()
                report_lines.append(memory_prompt)
            else:
                report_lines.append("[无测试历史记录]")

            report_lines.extend([
                "",
                "-" * 70,
                "💥 崩溃堆栈",
                "-" * 70,
                crash_log,
                "",
                "=" * 70,
                "报告生成完毕 - 请将此报告提供给开发团队",
                "=" * 70,
            ])

            # 写入文件
            report_content = '\n'.join(report_lines)
            report_file.write_text(report_content, encoding='utf-8')

            print(f"[Bug报告] 已保存到: {report_file}")
            print(f"[Bug报告] 开发者可根据此报告复现和修复问题")

        except Exception as e:
            print(f"[Bug报告] 保存失败: {e}")

    def _parse_llm_response(self, llm_response: str) -> ParsedAction:
        """
        解析 LLM 响应，优先使用 ReAct JSON 格式，回退兼容旧格式

        解析策略：
        1. 尝试提取 JSON 代码块（```json ... ```）
        2. 尝试直接解析 JSON 对象
        3. 回退到正则表达式解析旧格式

        Args:
            llm_response: LLM 响应字符串

        Returns:
            ParsedAction 对象，包含解析出的操作、控件和输入文本
        """
        action = ParsedAction()

        if not llm_response:
            return action

        # ========== 优先尝试 JSON 解析（ReAct 格式）==========
        json_action = self._parse_json_response(llm_response)
        if json_action and json_action.is_valid():
            print(f"[JSON解析成功] {json_action}")
            return json_action

        # ========== 回退到正则表达式解析（旧格式兼容）==========
        print("[JSON解析失败] 回退到正则表达式解析...")
        return self._parse_regex_response(llm_response)

    def parse_action_only(self, llm_response: str) -> Optional[ParsedAction]:
        """
        仅解析 LLM 响应，不执行动作

        用于在执行动作之前检查 Bug 断言，确保监管者审查使用正确的上下文快照。

        Args:
            llm_response: LLM 响应字符串

        Returns:
            ParsedAction 对象，解析失败时为 None
        """
        action = self._parse_llm_response(llm_response)
        if action and action.is_valid():
            return action
        return None

    def _parse_json_response(self, llm_response: str) -> Optional[ParsedAction]:
        """
        解析 ReAct JSON 格式的 LLM 响应

        支持格式：
        1. Markdown 代码块: ```json\n{...}\n```
        2. 纯 JSON 对象: {...}

        JSON 字段映射：
        - Thought -> action.thought
        - Action_Type -> action.operation
        - Target_Widget -> action.widget
        - Input_Content -> action.input_text
        - Status -> action.status

        Args:
            llm_response: LLM 响应字符串

        Returns:
            ParsedAction 对象，解析失败返回 None
        """
        try:
            json_str = None

            # 策略1：提取 Markdown JSON 代码块
            json_block_pattern = r'```(?:json)?\s*([\s\S]*?)```'
            match = re.search(json_block_pattern, llm_response, re.IGNORECASE)
            if match:
                json_str = match.group(1).strip()
                print(f"[JSON提取] 从代码块提取: {json_str[:100]}...")

            # 策略2：直接查找 JSON 对象（如果没有代码块）
            if not json_str:
                # 查找第一个 { 和最后一个 }
                start_idx = llm_response.find('{')
                end_idx = llm_response.rfind('}')
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    json_str = llm_response[start_idx:end_idx + 1]
                    print(f"[JSON提取] 直接提取对象: {json_str[:100]}...")

            if not json_str:
                return None

            # 解析 JSON
            data = json.loads(json_str)

            # 构建 ParsedAction
            action = ParsedAction()

            # 提取 Thought
            action.thought = data.get('Thought') or data.get('thought') or data.get('reasoning')

            # ========== NEW: 提取 Page_Description ==========
            page_description = data.get('Page_Description') or data.get('page_description')
            if page_description and str(page_description).lower() not in ('null', 'none', ''):
                action.page_description = str(page_description).strip()
                print(f"[JSON解析] Page_Description: {action.page_description[:80]}...")

            # ========== 提取 Function 信息 ==========
            function_name = data.get('Function') or data.get('function')
            if function_name and str(function_name).lower() not in ('null', 'none', ''):
                action.function_name = str(function_name).strip()

                # Status: Yes = new function (testing), No = continue existing (tested)
                status_val = data.get('Status') or data.get('status')
                if status_val:
                    status_str = str(status_val).strip().lower()
                    if status_str == 'yes':
                        action.function_status = 'testing'
                    elif status_str == 'no':
                        action.function_status = 'tested'
                    else:
                        action.function_status = status_str
                else:
                    action.function_status = 'testing'

            # ========== 提取 Operation ==========
            # 新格式: Operation 字段
            # 旧格式: Action_Type 字段
            operation_val = data.get('Operation') or data.get('operation') or \
                           data.get('Action_Type') or data.get('action_type') or data.get('ActionType')
            if operation_val:
                action.operation = self._normalize_operation(str(operation_val).lower().strip())

            # ========== 提取 Widget ==========
            # 新格式: Widget 字段（输入框或操作目标）
            # 旧格式: Target_Widget 字段
            widget_val = data.get('Widget') or data.get('widget') or \
                        data.get('Target_Widget') or data.get('target_widget') or data.get('TargetWidget')
            if widget_val and str(widget_val).lower() not in ('null', 'none', ''):
                action.widget = str(widget_val).strip()

            # ========== 提取 WidgetType ==========
            # 控件类型：TextView, EditText, Button, ImageView 等
            widget_type_val = data.get('WidgetType') or data.get('widget_type')
            if widget_type_val and str(widget_type_val).lower() not in ('null', 'none', ''):
                action.widget_type = str(widget_type_val).strip()

            # ========== 提取 Input ==========
            # 新格式: Inputs 数组 (多输入支持)
            # 旧格式: Input 字段 (单输入)
            # 更旧格式: Input_Content 字段
            inputs_array = data.get('Inputs') or data.get('inputs')
            if inputs_array and isinstance(inputs_array, list):
                # 处理多输入数组格式（支持 ContentDesc 字段）
                input_sequence = []
                for item in inputs_array:
                    if isinstance(item, dict):
                        widget_name = item.get('Widget') or item.get('widget')
                        input_text = item.get('Input') or item.get('input')
                        # 新增：提取 ContentDesc 用于区分同 resource-id 的字段
                        content_desc = item.get('ContentDesc') or item.get('content_desc')
                        if widget_name and input_text:
                            # 三元组：(widget_name, input_text, content_desc)
                            input_sequence.append((str(widget_name).strip(), str(input_text).strip(), content_desc))
                if input_sequence:
                    action.input_sequence = input_sequence
                    # 设置第一个输入作为主 widget 和 input_text（向后兼容）
                    action.widget = input_sequence[0][0]
                    action.input_text = input_sequence[0][1]
                    # 新增：设置第一个输入的 content_desc
                    if input_sequence[0][2]:
                        action.widget_content_desc = str(input_sequence[0][2]).strip()
                    print(f"[JSON解析] 多输入序列: {input_sequence}")
            else:
                # 单输入格式
                input_val = data.get('Input') or data.get('input') or \
                           data.get('Input_Content') or data.get('input_content') or data.get('InputContent')
                if input_val and str(input_val).lower() not in ('null', 'none', ''):
                    action.input_text = str(input_val).strip()

            # ========== 提取 OperationWidget ==========
            # 新格式专用：输入操作后的目标控件（如 Submit 按钮）
            operation_widget = data.get('OperationWidget') or data.get('operation_widget')
            if operation_widget and str(operation_widget).lower() not in ('null', 'none', ''):
                action.operation_widget = str(operation_widget).strip()

            # ========== 提取 OperationWidgetType ==========
            # 操作目标控件类型
            operation_widget_type = data.get('OperationWidgetType') or data.get('operation_widget_type')
            if operation_widget_type and str(operation_widget_type).lower() not in ('null', 'none', ''):
                action.operation_widget_type = str(operation_widget_type).strip()

            # ========== 提取 Expected_Result ==========
            expected_result = data.get('Expected_Result') or data.get('expected_result')
            if expected_result and str(expected_result).lower() not in ('null', 'none', ''):
                action.expected_result = str(expected_result).strip()
                print(f"[JSON解析] Expected_Result: {action.expected_result[:50]}...")

            # ========== 提取 Bug_Detected 和 Bug_Description ==========
            bug_detected = data.get('Bug_Detected') or data.get('bug_detected')
            if bug_detected:
                action.bug_detected = bool(bug_detected)
                if action.bug_detected:
                    bug_desc = data.get('Bug_Description') or data.get('bug_description')
                    if bug_desc and isinstance(bug_desc, dict):
                        action.bug_description = bug_desc
                        print(f"[JSON解析] Bug检测: type={bug_desc.get('type')}, severity={bug_desc.get('severity')}")
                    elif bug_desc:
                        action.bug_description = {"description": str(bug_desc)}

            # ========== NEW: 提取 TargetX 和 TargetY (视觉定位坐标) ==========
            target_x = data.get('TargetX') or data.get('target_x') or data.get('targetx')
            target_y = data.get('TargetY') or data.get('target_y') or data.get('targety')
            if target_x is not None and target_y is not None:
                try:
                    action.target_x = int(target_x)
                    action.target_y = int(target_y)
                    print(f"[JSON解析] 视觉定位坐标: TargetX={action.target_x}, TargetY={action.target_y}")
                except (ValueError, TypeError) as e:
                    print(f"[JSON解析警告] TargetX/TargetY 转换失败: {e}")

            # ========== 后处理 ==========
            # 如果有 Input 但没有 Operation，默认为 input 操作
            if action.input_text and not action.operation:
                action.operation = "input"

            # 打印解析结果
            print(f"[JSON解析] Thought: {action.thought[:50] if action.thought else 'N/A'}...")
            print(f"[JSON解析] Operation: {action.operation}, Widget: {action.widget}, WidgetType: {action.widget_type}, Input: {action.input_text}")
            if action.operation_widget:
                print(f"[JSON解析] OperationWidget: {action.operation_widget}, OperationWidgetType: {action.operation_widget_type}")
            if action.function_name:
                print(f"[JSON解析] Function: {action.function_name} ({action.function_status})")

            return action

        except json.JSONDecodeError as e:
            print(f"[JSON解析错误] JSON 格式无效: {e}")
            return None
        except Exception as e:
            print(f"[JSON解析异常] {type(e).__name__}: {e}")
            return None

    def _parse_regex_response(self, llm_response: str) -> ParsedAction:
        """
        使用正则表达式解析旧格式的 LLM 响应（向后兼容）

        支持多种格式，能从包含废话的长文本中精准提取：
        - Function: "Add income". Status: Yes. Operation: "Click". Widget: "ADD INCOME".
        - Function: "Add income". Status: No. Widget: "Price". Input: "3500". Operation: "Click". Widget: "Submit".
        - Operation: "click" Widget: "Search"
        - Widget: "SearchBox" Input: "test query"

        Args:
            llm_response: LLM 响应字符串

        Returns:
            ParsedAction 对象
        """
        action = ParsedAction()

        if not llm_response:
            return action

        try:
            # ========== 提取 Function ==========
            # 格式: Function: "Add income". 或 Function: "Add income"
            function_match = re.search(r'[Ff]unction:\s*"([^"]+)"', llm_response)
            if function_match:
                action.function_name = function_match.group(1).strip()
                print(f"[解析] Function: {action.function_name}")

            # ========== 提取 Status ==========
            # 格式: Status: Yes. 或 Status: No.
            # Yes = new function (testing), No = continue existing (tested)
            status_match = re.search(r'[Ss]tatus:\s*(Yes|No)', llm_response, re.IGNORECASE)
            if status_match:
                status_value = status_match.group(1).strip().lower()
                # Yes = new function being tested, No = continuing existing function
                action.function_status = "testing" if status_value == "yes" else "tested"
                print(f"[解析] Function Status: {action.function_status}")
            expected_match = re.search(
                r'(?:Expected_Result|Expected Result|ExpectedResult):\s*"([^"]+)"',
                llm_response,
                re.IGNORECASE
            )
            if expected_match:
                action.expected_result = expected_match.group(1).strip()

            expected_match = re.search(
                r'(?:Expected_Result|Expected Result|ExpectedResult):\s*(.+?)(?=\n|Bug_Detected|Bug Description|$)',
                llm_response,
                re.IGNORECASE | re.DOTALL
            )

            # ========== 提取 Operation ==========
            # 优先匹配带双引号的格式，回退兼容不带引号的格式
            # 支持格式：
            # - Operation: "click"
            # - Operation: "long press"
            # - operation: click（无引号）
            operation_patterns = [
                # 优先：带双引号的格式 Operation: "value"
                r'[Oo]peration:\s*"([^"]+)"',
                # 回退：不带引号的格式 Operation: value（取到行尾或下一个关键字）
                r'[Oo]peration:\s*([a-zA-Z]+(?:\s+[a-zA-Z]+)?)(?=\s*(?:[Ww]idget|[Ii]nput|$|\n|\.))',
            ]

            for pattern in operation_patterns:
                match = re.search(pattern, llm_response, re.DOTALL | re.IGNORECASE)
                if match:
                    action.operation = match.group(1).strip().lower()
                    break

            # ========== 提取 Widget ==========
            # 支持多个 Widget/Input 对（输入场景）
            # 格式: Widget: "Price". Input: "3500". Widget: "Title". Input: "salary".
            # 取最后一个 Widget 作为目标控件，或者 Operation 后面的 Widget
            widget_matches = list(re.finditer(r'[Ww]idget:\s*"([^"]+)"', llm_response))
            if widget_matches:
                # 如果有 Operation，找 Operation 后面的 Widget
                if action.operation:
                    op_match = re.search(r'[Oo]peration:', llm_response, re.IGNORECASE)
                    if op_match:
                        op_pos = op_match.end()
                        for wm in widget_matches:
                            if wm.start() > op_pos:
                                action.widget = wm.group(1).strip()
                                break
                        # 如果没找到，取最后一个 Widget
                        if not action.widget:
                            action.widget = widget_matches[-1].group(1).strip()
                    else:
                        action.widget = widget_matches[-1].group(1).strip()
                else:
                    action.widget = widget_matches[-1].group(1).strip()

                # 过滤掉一些无效值
                if action.widget and action.widget.lower() in ('none', 'null', '', 'widget'):
                    action.widget = None

            # ========== 提取 Input 文本 ==========
            # 支持多个 Input，收集所有输入文本
            # 格式: Widget: "Price". Input: "3500". Widget: "Title". Input: "salary".
            input_matches = list(re.finditer(r'[Ii]nput:\s*"([^"]+)"', llm_response))
            widget_matches_for_input = list(re.finditer(r'[Ww]idget:\s*"([^"]+)"', llm_response))

            if input_matches:
                # 收集所有输入，用 || 分隔
                inputs = [m.group(1).strip() for m in input_matches]
                action.input_text = " || ".join(inputs)
                print(f"[解析] Input texts: {action.input_text}")

                # 构建 input_sequence（Widget/Input 配对）
                # 找到每个 Input 前面最近的 Widget
                input_sequence = []
                for input_match in input_matches:
                    input_pos = input_match.start()
                    input_val = input_match.group(1).strip()
                    # 找最近的 Widget
                    closest_widget = None
                    for wm in widget_matches_for_input:
                        if wm.start() < input_pos:
                            closest_widget = wm.group(1).strip()
                        else:
                            break
                    if closest_widget:
                        input_sequence.append((closest_widget, input_val, None))  # 三元组，content_desc 为 None

                if input_sequence:
                    action.input_sequence = input_sequence
                    print(f"[解析] Input sequence: {input_sequence}")

            # ========== 后处理和验证 ==========

            # 如果匹配到了 Input 文本但没有匹配到 Operation，自动补全为 input 操作
            if action.input_text and not action.operation:
                action.operation = "input"
                print("[解析推断] 检测到 Input 文本但无 Operation，自动设置为 'input'")

            # 标准化操作名称
            if action.operation:
                action.operation = self._normalize_operation(action.operation)

            # 清理 widget 名称（移除首尾引号和空白）
            if action.widget:
                action.widget = action.widget.strip('"\'').strip()

            print(f"[解析详情] operation={action.operation}, widget={action.widget}, input={action.input_text}")

            return action

        except Exception as e:
            print(f"[解析异常] {e}")
            return action

    def _normalize_operation(self, operation: str) -> str:
        """
        标准化操作名称

        Args:
            operation: 原始操作名称

        Returns:
            标准化后的操作名称
        """
        operation = operation.lower().strip().replace("-", " ").replace("_", " ")

        # 同义词映射
        operation_map = {
            # 控件操作同义词
            "tap": "click",
            "press": "click",
            "double tap": "double click",
            "doubleclick": "double click",
            "longpress": "long press",
            "long click": "long press",
            "swipe": "scroll",
            "type": "input",
            # 系统级动作同义词
            "go back": "back",
            "press back": "back",
            "back button": "back",
            "go home": "home",
            "press home": "home",
            "home button": "home",
            "scroll down": "scroll_down",
            "scroll screen down": "scroll_down",
            "page down": "scroll_down",
            "scroll up": "scroll_up",
            "scroll screen up": "scroll_up",
            "page up": "scroll_up",
        }

        return operation_map.get(operation, operation)

    def parse_function_query_response(self, llm_response: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse function query response from LLM

        Extracts FunctionName + Status from the LLM response to the function query.

        Expected formats:
        - JSON: {"Function": "Login", "Status": "testing"}
        - Text: Login + testing
        - Text with parentheses: (Login + testing)
        - Text: We are testing the Login function. (Login + testing)

        Args:
            llm_response: LLM response string

        Returns:
            Tuple of (function_name, function_status), or (None, None) if parsing fails
        """
        if not llm_response:
            return None, None

        print("\n[Function Query Parser] Parsing LLM response...")

        # Strategy 1: Try JSON parsing
        json_result = self._parse_function_from_json(llm_response)
        if json_result[0]:
            print(f"[Function Query Parser] JSON parsed: {json_result[0]} + {json_result[1]}")
            return json_result

        # Strategy 2: Try pattern matching (FunctionName + Status)
        pattern_result = self._parse_function_from_pattern(llm_response)
        if pattern_result[0]:
            print(f"[Function Query Parser] Pattern matched: {pattern_result[0]} + {pattern_result[1]}")
            return pattern_result

        print("[Function Query Parser] Failed to parse function info")
        return None, None

    def _parse_function_from_json(self, llm_response: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Try to parse function info from JSON format

        Args:
            llm_response: LLM response string

        Returns:
            Tuple of (function_name, function_status)
        """
        try:
            # Try to extract JSON from code block
            json_block_pattern = r'```(?:json)?\s*([\s\S]*?)```'
            match = re.search(json_block_pattern, llm_response, re.IGNORECASE)
            if match:
                json_str = match.group(1).strip()
                data = json.loads(json_str)

                function_name = data.get('Function') or data.get('function') or data.get('FunctionName') or data.get('function_name')
                function_status = data.get('Status') or data.get('status') or data.get('FunctionStatus') or data.get('function_status')

                if function_name:
                    return str(function_name).strip(), str(function_status or "testing").strip()

            # Try to find JSON object directly
            start_idx = llm_response.find('{')
            end_idx = llm_response.rfind('}')
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = llm_response[start_idx:end_idx + 1]
                data = json.loads(json_str)

                function_name = data.get('Function') or data.get('function') or data.get('FunctionName') or data.get('function_name')
                function_status = data.get('Status') or data.get('status') or data.get('FunctionStatus') or data.get('function_status')

                if function_name:
                    return str(function_name).strip(), str(function_status or "testing").strip()

        except (json.JSONDecodeError, Exception) as e:
            print(f"[Function JSON Parse] Failed: {e}")

        return None, None

    def _parse_function_from_pattern(self, llm_response: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Try to parse function info from text pattern

        Patterns:
        - FunctionName + Status (e.g., "Login + testing")
        - (FunctionName + Status)

        Args:
            llm_response: LLM response string

        Returns:
            Tuple of (function_name, function_status)
        """
        # Pattern 0 (NEW): Function: "xxx". Status: Yes/No.
        pattern0 = r'[Ff]unction:\s*"([^"]+)"[^.]*\.\s*[Ss]tatus:\s*(Yes|No)'
        match = re.search(pattern0, llm_response, re.IGNORECASE)
        if match:
            func_name = match.group(1).strip()
            status_val = match.group(2).strip().lower()
            # Yes = new function (testing), No = continue existing (tested)
            func_status = 'testing' if status_val == 'yes' else 'tested'
            return func_name, func_status

        # Pattern 1: (FunctionName + Status) with parentheses
        pattern1 = r'\(([A-Za-z_][A-Za-z0-9_\s]*)\s*\+\s*(tested|testing|new)\)'
        match = re.search(pattern1, llm_response, re.IGNORECASE)
        if match:
            return match.group(1).strip(), match.group(2).lower()

        # Pattern 2: FunctionName + Status without parentheses
        pattern2 = r'([A-Za-z_][A-Za-z0-9_\s]*)\s*\+\s*(tested|testing|new)'
        match = re.search(pattern2, llm_response, re.IGNORECASE)
        if match:
            return match.group(1).strip(), match.group(2).lower()

        # Pattern 3: "testing FunctionName" or "tested FunctionName"
        pattern3 = r'(testing|tested)\s+(?:the\s+)?([A-Za-z_][A-Za-z0-9_\s]*?)(?:\s+function|\s*$|\.)'
        match = re.search(pattern3, llm_response, re.IGNORECASE)
        if match:
            status = match.group(1).lower()
            func_name = match.group(2).strip()
            return func_name, status

        # Pattern 4: "FunctionName function" with status elsewhere
        pattern4 = r'([A-Za-z_][A-Za-z0-9_\s]*?)\s+function'
        match = re.search(pattern4, llm_response, re.IGNORECASE)
        if match:
            function_name = match.group(1).strip()
            # Check for status in the response
            if 'tested' in llm_response.lower():
                return function_name, 'tested'
            elif 'testing' in llm_response.lower():
                return function_name, 'testing'
            elif 'new' in llm_response.lower():
                return function_name, 'testing'
            return function_name, 'testing'

        return None, None

    def _execute_multiple_inputs_action(
        self,
        action: ParsedAction,
        parsed_widgets: List[Dict],
        adb_controller: ADBController
    ) -> bool:
        """
        执行多输入操作：依次点击每个输入框并输入文本

        支持三元组 (widget_name, input_text, content_desc)，用于区分同名控件

        Args:
            action: 解析后的动作，包含 input_sequence
            parsed_widgets: 控件列表
            adb_controller: ADB 控制器

        Returns:
            是否执行成功
        """
        print(f"[多输入操作] 共 {len(action.input_sequence)} 个输入")

        for i, input_item in enumerate(action.input_sequence, 1):
            # 支持三元组 (widget_name, input_text, content_desc) 或二元组 (widget_name, input_text)
            if len(input_item) == 3:
                widget_name, input_text, content_desc = input_item
            else:
                widget_name, input_text = input_item
                content_desc = None

            print(f"\n[输入 {i}/{len(action.input_sequence)}] 控件: {widget_name}, 文本: {input_text}")
            if content_desc:
                print(f"  [ContentDesc 提示] '{content_desc}'")

            # 查找输入框控件（传递 content_desc_hint）
            target_widget = self._find_target_widget(
                widget_name,
                parsed_widgets,
                widget_type="EditText",
                content_desc_hint=content_desc
            )
            if not target_widget:
                print(f"[执行失败] 未找到输入框: {widget_name}")
                return False

            # 计算坐标
            center_x, center_y = self._calculate_center(target_widget)
            if center_x is None or center_y is None:
                print("[执行失败] 无法计算控件坐标")
                return False

            # 点击输入框获取焦点
            print(f"[点击] 输入框 {widget_name} ({center_x}, {center_y})")
            if not adb_controller.click(center_x, center_y):
                print(f"[执行失败] 无法点击输入框: {widget_name}")
                return False

            # 等待焦点
            time.sleep(0.5)

            # 使用 UIAutomator2 清除旧文本并输入新文本
            print(f"[清除+输入] 使用 UIAutomator2 处理输入框 {widget_name}")
            # 将坐标信息传递给 clear_and_input_text
            target_widget["center_x"] = center_x
            target_widget["center_y"] = center_y
            if not adb_controller.clear_and_input_text(target_widget, input_text):
                print(f"[执行失败] 文本输入失败: {input_text}")
                return False

            # 收起键盘
            adb_controller.hide_keyboard()

            # 短暂等待
            time.sleep(0.3)

        print(f"\n[多输入成功] 已完成 {len(action.input_sequence)} 个输入")
        return True

    def _execute_input_then_operation(
        self,
        action: ParsedAction,
        parsed_widgets: List[Dict],
        adb_controller: ADBController
    ) -> bool:
        """
        执行输入+操作组合：先输入文本，然后执行操作（如点击 Submit）

        支持通过 widget_content_desc 区分同 resource-id 的多个输入框

        JSON 格式示例:
        {"Widget": "InputField", "ContentDesc": "Back", "Input": "text", "Operation": "click", "OperationWidget": "Submit"}

        Args:
            action: 解析后的动作，包含 widget, input_text, operation, operation_widget
            parsed_widgets: 控件列表
            adb_controller: ADB 控制器

        Returns:
            是否执行成功
        """
        print(f"[输入+操作] 输入框: {action.widget}, 文本: {action.input_text}")
        if action.widget_content_desc:
            print(f"  [ContentDesc 提示] '{action.widget_content_desc}'")
        print(f"[输入+操作] 后续操作: {action.operation} -> {action.operation_widget}")

        # 步骤1：输入文本
        # 查找输入框控件（传递 content_desc_hint）
        input_widget = self._find_target_widget(
            action.widget,
            parsed_widgets,
            widget_type=action.widget_type,
            content_desc_hint=action.widget_content_desc
        )
        if not input_widget:
            print(f"[执行失败] 未找到输入框: {action.widget}")
            return False

        # 计算输入框坐标
        input_x, input_y = self._calculate_center(input_widget)
        if input_x is None or input_y is None:
            print("[执行失败] 无法计算输入框坐标")
            return False

        # 点击输入框获取焦点
        print(f"[步骤1] 点击输入框 ({input_x}, {input_y})")
        if not adb_controller.click(input_x, input_y):
            print("[执行失败] 无法点击输入框")
            return False

        # 等待键盘弹出
        time.sleep(0.5)

        # 步骤2：使用 UIAutomator2 清除旧文本并输入新文本
        print(f"[步骤2] 使用 UIAutomator2 清除旧文本并输入: {action.input_text}")
        # 将坐标信息传递给 clear_and_input_text
        input_widget["center_x"] = input_x
        input_widget["center_y"] = input_y
        if not adb_controller.clear_and_input_text(input_widget, action.input_text):
            print("[执行失败] 文本输入失败")
            return False

        # 收起键盘（输入完成后收起，避免遮挡后续操作）
        adb_controller.hide_keyboard()

        # 短暂等待
        time.sleep(0.3)

        # 步骤3：执行后续操作
        print(f"[步骤3] 执行操作: {action.operation} -> {action.operation_widget}")

        # 查找操作目标控件
        target_widget = self._find_target_widget(action.operation_widget, parsed_widgets, widget_type=action.operation_widget_type)
        if not target_widget:
            print(f"[执行失败] 未找到操作目标控件: {action.operation_widget}")
            return False

        # 计算目标坐标
        target_x, target_y = self._calculate_center(target_widget)
        if target_x is None or target_y is None:
            print("[执行失败] 无法计算目标控件坐标")
            return False

        # 执行操作
        success = self._perform_operation(action.operation, target_x, target_y, adb_controller)

        if success:
            print(f"[输入+操作成功] 已输入文本并执行 {action.operation}")

        return success

    def _execute_input_action(
        self,
        action: ParsedAction,
        parsed_widgets: List[Dict],
        adb_controller: ADBController
    ) -> bool:
        """
        执行输入操作：先定位并点击输入框获取焦点，等待 1 秒后输入文本

        支持通过 widget_content_desc 区分同 resource-id 的多个输入框

        Args:
            action: 解析后的动作
            parsed_widgets: 控件列表
            adb_controller: ADB 控制器

        Returns:
            是否执行成功
        """
        print(f"[输入操作] 目标控件: {action.widget}, 输入文本: {action.input_text}")
        if action.widget_content_desc:
            print(f"  [ContentDesc 提示] '{action.widget_content_desc}'")

        # 查找输入框控件（传递 content_desc_hint）
        target_widget = self._find_target_widget(
            action.widget,
            parsed_widgets,
            widget_type=action.widget_type,
            content_desc_hint=action.widget_content_desc
        )
        if not target_widget:
            print(f"[执行失败] 未找到输入框: {action.widget}")
            return False

        # 计算坐标
        center_x, center_y = self._calculate_center(target_widget)
        if center_x is None or center_y is None:
            print("[执行失败] 无法计算控件坐标")
            return False

        # 步骤1：点击输入框获取焦点（输入前必须先点击让输入框获得焦点）
        print(f"[步骤1] 点击输入框获取焦点 ({center_x}, {center_y})")
        if not adb_controller.click(center_x, center_y):
            print("[执行失败] 无法点击输入框")
            return False

        # 步骤2：等待 0.5 秒让输入框获得焦点
        print("[步骤2] 等待输入框获得焦点...")
        time.sleep(0.5)

        # 步骤3：使用 UIAutomator2 清除旧文本并输入新文本
        print("[步骤3] 使用 UIAutomator2 清除旧文本并输入新文本...")
        # 将坐标信息传递给 clear_and_input_text
        target_widget["center_x"] = center_x
        target_widget["center_y"] = center_y
        if not adb_controller.clear_and_input_text(target_widget, action.input_text):
            print("[执行失败] 文本输入失败")
            return False

        # 收起键盘
        adb_controller.hide_keyboard()

        print("[输入成功] 文本已输入完成")
        return True

    def _execute_scroll_action(
        self,
        parsed_widgets: List[Dict],
        adb_controller: ADBController,
        direction: str = "down"
    ) -> bool:
        """
        执行滚动操作

        Args:
            parsed_widgets: 控件列表（用于确定滚动区域）
            adb_controller: ADB 控制器
            direction: 滚动方向，"down"（向下滚动，查看下方内容）或 "up"（向上滚动，查看上方内容）

        Returns:
            是否执行成功
        """
        # 标准化方向
        direction = direction.lower().strip()
        if direction not in ("up", "down", "upward", "downward"):
            direction = "down"

        is_scroll_down = direction in ("down", "downward")

        if is_scroll_down:
            print("[滚动操作] 向下滚动屏幕（查看下方内容）")
        else:
            print("[滚动操作] 向上滚动屏幕（查看上方内容）")

        # 计算屏幕中心位置作为滚动起点
        # 默认屏幕参数
        screen_center_x = 540
        screen_height = 1920
        scroll_distance = 500  # 滚动距离

        # 如果有控件信息，使用控件区域的中心
        if parsed_widgets:
            max_y = 0
            min_y = screen_height

            for widget in parsed_widgets:
                cy = widget.get('center_y', 0)
                if cy:
                    max_y = max(max_y, cy)
                    min_y = min(min_y, cy)

            if max_y > 0:
                scroll_start_y = max_y - 100  # 从底部区域开始
            else:
                scroll_start_y = 1400

            if min_y < screen_height:
                scroll_end_y = min_y + 100  # 滚动到顶部区域
            else:
                scroll_end_y = 400
        else:
            scroll_start_y = 1400
            scroll_end_y = 400

        # 根据方向确定滚动参数
        if is_scroll_down:
            # 向下滚动：从下往上滑动（手指从下往上划，内容往下走）
            start_y = scroll_start_y
            end_y = scroll_start_y - scroll_distance
        else:
            # 向上滚动：从上往下滑动（手指从上往下划，内容往上走）
            start_y = scroll_end_y
            end_y = scroll_end_y + scroll_distance

        print(f"[滚动参数] 起点: ({screen_center_x}, {start_y}), 终点: ({screen_center_x}, {end_y})")

        # 执行滑动
        return adb_controller.swipe(screen_center_x, start_y, screen_center_x, end_y, duration=500)

    def _find_target_widget(
        self,
        widget_name: str,
        parsed_widgets: List[Dict],
        widget_type: Optional[str] = None,
        content_desc_hint: Optional[str] = None,  # 新增参数：用于区分同 resource-id 的字段
        target_x: Optional[int] = None,  # NEW: 目标中心 X 坐标（视觉定位）
        target_y: Optional[int] = None   # NEW: 目标中心 Y 坐标（视觉定位）
    ) -> Optional[Dict]:
        """
        在控件列表中查找名称匹配的控件

        匹配策略（按优先级排序）：
        0B. 如果提供了 target_x/target_y，通过坐标距离匹配（最高优先级 - 视觉定位）
        0. 如果提供了 widget_type，通过 text + class 组合精确匹配
        1. 精确匹配 text 字段
        2A. resource-id + content_desc 组合匹配（用于区分同 resource-id 的字段）
        2. 精确匹配 resource-id 的最后一部分（如有多个匹配，会发出警告）
        3. 纯匹配 content-desc
        4. 模糊匹配（包含关系）
        5. 语义关键词匹配

        终极清洗：移除所有空白字符后再匹配

        Args:
            widget_name: 目标控件名称
            parsed_widgets: 控件列表
            widget_type: 控件类型（TextView, EditText, Button 等）
            content_desc_hint: content_desc 提示（用于区分同 resource-id 的字段）
            target_x: 目标中心 X 坐标（视觉定位）
            target_y: 目标中心 Y 坐标（视觉定位）

        Returns:
            找到的控件字典，未找到返回 None
        """
        if not parsed_widgets:
            return None

        # ========== 策略0B：坐标匹配（NEW - 最高优先级 - 视觉定位）==========
        if target_x is not None and target_y is not None:
            print(f"[匹配策略0B] 使用 LLM 提供的视觉定位坐标: ({target_x}, {target_y})")

            closest_widget = None
            min_distance = float('inf')
            tolerance = 100  # 容差 100px

            for widget in parsed_widgets:
                cx, cy = self._calculate_center(widget)
                if cx is not None and cy is not None:
                    distance = abs(cx - target_x) + abs(cy - target_y)
                    widget_name_temp = widget.get("text", "") or widget.get("resource_id", "")
                    if "/" in widget_name_temp:
                        widget_name_temp = widget_name_temp.split("/")[-1]
                    print(f"  [距离计算] '{widget_name_temp}' center=({cx},{cy}), distance={distance}px")

                    if distance < min_distance and distance < tolerance:
                        min_distance = distance
                        closest_widget = widget

            if closest_widget:
                closest_name = closest_widget.get("text", "") or closest_widget.get("resource_id", "")
                if "/" in closest_name:
                    closest_name = closest_name.split("/")[-1]
                print(f"[匹配成功] 通过视觉定位坐标找到最近控件: '{closest_name}', distance={min_distance}px")
                return closest_widget
            else:
                print(f"[匹配警告] 视觉定位坐标 ({target_x}, {target_y}) 未找到容差范围内的控件，继续使用名称匹配...")

        if not widget_name:
            return None

        # ========== 终极字符串清洗辅助函数 ==========
        def clean_text(text: str) -> str:
            """移除所有空白字符，转小写"""
            if not text:
                return ""
            return re.sub(r'\s+', '', text.lower())

        widget_name_clean = clean_text(widget_name)
        widget_name_lower = widget_name.lower().strip('"\'')
        print(f"[匹配调试] 目标控件名: '{widget_name}' (clean: '{widget_name_clean}')")
        print(f"[匹配调试] 控件列表数量: {len(parsed_widgets)}")
        if widget_type:
            print(f"[匹配调试] 目标控件类型: '{widget_type}'")

        # 打印所有控件的标识信息（用于调试）- 显示所有控件
        print("[匹配调试] 控件列表详情:")
        for i, w in enumerate(parsed_widgets):
            text = w.get("text", "")
            rid = w.get("resource_id", "")
            cd = w.get("content_desc", "")
            cls = w.get("class", "")
            # 提取简单类名
            simple_class = cls.split(".")[-1] if cls else ""
            # 显示清洗前后对比
            text_clean = clean_text(text)
            print(f"  [{i}] text='{text}' (clean: '{text_clean}'), class='{simple_class}', id='{rid}', content_desc='{cd}'")

        # ========== 策略0：text + class 组合精确匹配（最高优先级）==========
        if widget_type:
            print(f"[匹配策略0] 尝试 text + class 组合匹配...")
            for widget in parsed_widgets:
                text = widget.get("text", "")
                class_name = widget.get("class", "")

                # 提取简单类名
                simple_class = class_name.split(".")[-1] if class_name else ""

                # 清洗后匹配 text
                text_clean = clean_text(text)
                if text_clean == widget_name_clean:
                    # 检查 class 是否匹配
                    if widget_type.lower() in simple_class.lower():
                        print(f"[匹配成功] 通过 text+class 组合精确匹配: text='{text}', class='{simple_class}' (target: {widget_type})")
                        return widget
                    else:
                        print(f"  [跳过] text 匹配但 class 不匹配: text='{text}', class='{simple_class}' (target: {widget_type})")

        # 策略1：精确匹配 text（包括 original_text）
        for widget in parsed_widgets:
            text = widget.get("text", "")
            original_text = widget.get("original_text", "")
            # 同时检查 text 和 original_text
            for txt in [text, original_text]:
                if txt and txt.strip():
                    # 清洗后精确匹配
                    txt_clean = clean_text(txt)
                    if txt_clean == widget_name_clean:
                        print(f"[匹配成功] 通过 text 清洗后精确匹配: '{txt}' -> '{txt_clean}'")
                        return widget

        # ========== 策略2A：resource-id + content_desc 组合匹配（新增）==========
        # 用于区分同 resource-id 的多个控件（如 AnkiDroid 中的 Front/Back 字段）
        if content_desc_hint:
            print(f"[匹配策略2A] 尝试 resource-id + content_desc 组合匹配，hint='{content_desc_hint}'...")
            for widget in parsed_widgets:
                resource_id = widget.get("resource_id", "")
                widget_cd = widget.get("content_desc", "")

                if resource_id:
                    id_name = resource_id.split("/")[-1] if "/" in resource_id else resource_id
                    id_clean = clean_text(id_name)

                    if id_clean == widget_name_clean:
                        # 匹配 content_desc
                        cd_clean = clean_text(widget_cd)
                        hint_clean = clean_text(content_desc_hint)

                        if cd_clean == hint_clean or hint_clean in cd_clean:
                            print(f"[匹配成功] resource-id + content_desc 组合匹配: id='{id_name}', cd='{widget_cd}'")
                            return widget
                        else:
                            print(f"  [跳过] resource-id 匹配但 content_desc 不匹配: id='{id_name}', cd='{widget_cd}' (期望: '{content_desc_hint}')")

        # ========== 策略2：精确匹配 resource-id 最后一部分（改进：警告多匹配）==========
        matching_widgets = []
        for widget in parsed_widgets:
            resource_id = widget.get("resource_id", "")
            if resource_id:
                id_name = resource_id.split("/")[-1] if "/" in resource_id else resource_id
                id_clean = clean_text(id_name)
                if id_clean == widget_name_clean:
                    matching_widgets.append(widget)

        if len(matching_widgets) == 1:
            widget = matching_widgets[0]
            rid = widget.get("resource_id", "")
            cd = widget.get("content_desc", "")
            print(f"[匹配成功] 通过 resource-id 精确匹配: '{rid.split('/')[-1]}' (content_desc='{cd}')")
            return widget

        # 如果有多个匹配的控件，发出警告并返回第一个（建议使用 content_desc）
        if len(matching_widgets) > 1:
            print(f"[匹配警告] 发现 {len(matching_widgets)} 个同名控件 resource-id='{widget_name}'：")
            for i, w in enumerate(matching_widgets):
                cd = w.get("content_desc", "")
                text = w.get("text", "")
                print(f"  [{i}] content_desc='{cd}', text='{text[:30] if text else ''}'")
            print(f"[匹配建议] 请在 JSON 中使用 ContentDesc 字段指定目标控件（如 'Front' 或 'Back'）")
            # 返回第一个（默认行为，但可能不准确）
            print(f"[匹配结果] 返回第一个同名控件（可能不准确）")
            return matching_widgets[0]

        # 策略3：精确匹配 content-desc
        for widget in parsed_widgets:
            content_desc = widget.get("content_desc", "")
            if content_desc and content_desc.strip():
                cd_clean = clean_text(content_desc)
                if cd_clean == widget_name_clean:
                    print(f"[匹配成功] 通过 content-desc 精确匹配: {content_desc}")
                    return widget

        # 策略4：模糊匹配（包含关系）- 也使用清洗后的字符串
        for widget in parsed_widgets:
            text = widget.get("text", "")
            original_text = widget.get("original_text", "")
            resource_id = widget.get("resource_id", "")
            content_desc = widget.get("content_desc", "")

            # 收集所有可能的文本标识并清洗
            all_texts = [text, original_text, content_desc]

            for txt in all_texts:
                if txt and txt.strip():
                    txt_clean = clean_text(txt)
                    # 清洗后模糊匹配（包含关系）
                    if widget_name_clean in txt_clean or txt_clean in widget_name_clean:
                        print(f"[匹配成功] 通过清洗后模糊匹配: '{txt}' -> '{txt_clean}'")
                        return widget

            # 检查 resource-id 是否包含 widget_name
            if resource_id:
                id_name = resource_id.split("/")[-1] if "/" in resource_id else resource_id
                id_clean = clean_text(id_name)
                if widget_name_clean in id_clean or id_clean in widget_name_clean:
                    print(f"[匹配成功] 通过 resource-id 模糊匹配: {id_name}")
                    return widget

        # 策略5：语义关键词匹配（处理 LLM 返回语义描述的情况）
        semantic_match = self._semantic_keyword_match(widget_name, widget_name_clean, parsed_widgets, widget_type)
        if semantic_match:
            return semantic_match

        print(f"[匹配失败] 未找到匹配控件: '{widget_name}' (clean: '{widget_name_clean}')")
        return None

    def _semantic_keyword_match(
        self,
        widget_name: str,
        widget_name_clean: str,
        parsed_widgets: List[Dict],
        widget_type: Optional[str] = None
    ) -> Optional[Dict]:
        """
        语义关键词匹配

        处理 LLM 返回语义描述的情况，例如：
        - "用户名输入框" -> 匹配 resource_id="username" 或 text="请输入用户名"
        - "登录按钮" -> 匹配 resource_id="login" 或 text="登录"
        - "密码" -> 匹配 resource_id="password"

        Args:
            widget_name: 原始控件名称
            widget_name_clean: 清洗后的控件名称
            parsed_widgets: 控件列表
            widget_type: 控件类型

        Returns:
            匹配到的控件，未找到返回 None
        """
        # 计算小写版本
        widget_name_lower = widget_name.lower().strip('"\'')

        # 语义关键词映射表
        SEMANTIC_KEYWORDS = {
            # 登录相关
            "登录": ["login", "signin", "log_in", "sign_in"],
            "注册": ["register", "signup", "sign_up", "create"],
            "提交": ["submit", "confirm", "ok", "done"],
            "取消": ["cancel", "close", "dismiss"],

            # 用户相关
            "用户名": ["username", "user", "account", "loginname", "name"],
            "密码": ["password", "pwd", "pass"],
            "邮箱": ["email", "mail"],
            "手机": ["phone", "mobile", "tel"],
            "验证码": ["code", "captcha", "verify"],

            # 操作相关
            "搜索": ["search", "find", "query"],
            "发送": ["send", "submit", "post"],
            "保存": ["save", "store"],
            "删除": ["delete", "remove", "clear"],
            "编辑": ["edit", "modify", "change"],
            "设置": ["setting", "config", "preference"],

            # 导航相关
            "返回": ["back", "return", "prev"],
            "下一步": ["next", "forward", "continue"],
            "完成": ["finish", "done", "complete"],

            # 英文关键词
            "username": ["username", "user", "account", "loginname", "用户名"],
            "password": ["password", "pwd", "密码"],
            "login": ["login", "signin", "登录"],
            "submit": ["submit", "confirm", "提交", "确定"],
            "cancel": ["cancel", "取消"],
            "search": ["search", "搜索", "查找"],
        }

        # 扩展关键词：提取 widget_name 中的关键词
        expanded_keywords = []
        for key, synonyms in SEMANTIC_KEYWORDS.items():
            if key in widget_name_lower or key in widget_name_clean:
                expanded_keywords.extend(synonyms)

        if not expanded_keywords:
            return None

        print(f"[语义匹配] 扩展关键词: {expanded_keywords}")

        # 在控件中搜索匹配
        for widget in parsed_widgets:
            text = widget.get("text", "").lower()
            original_text = widget.get("original_text", "").lower()
            resource_id = widget.get("resource_id", "").lower()
            content_desc = widget.get("content_desc", "").lower()
            class_name = widget.get("class", "")

            # 提取 resource-id 的最后一部分
            id_name = resource_id.split("/")[-1] if "/" in resource_id else resource_id

            # 收集所有文本
            all_texts = [text, original_text, content_desc, id_name]

            # 检查是否匹配任何扩展关键词
            for keyword in expanded_keywords:
                keyword_clean = re.sub(r'\s+', '', keyword.lower())
                for txt in all_texts:
                    txt_clean = re.sub(r'\s+', '', txt)
                    if keyword_clean in txt_clean:
                        # 如果指定了 widget_type，检查 class 是否匹配
                        if widget_type:
                            simple_class = class_name.split(".")[-1] if class_name else ""
                            if widget_type.lower() not in simple_class.lower():
                                continue
                        print(f"[匹配成功] 通过语义关键词匹配: '{keyword}' -> '{txt}'")
                        return widget

        return None

    def _calculate_center(self, widget: Dict) -> Tuple[Optional[int], Optional[int]]:
        """
        计算控件的中心坐标

        优先使用已计算好的 center_x/center_y，否则从 bounds 解析

        Args:
            widget: 控件信息字典

        Returns:
            元组 (center_x, center_y)，解析失败返回 (None, None)
        """
        # 优先使用已计算好的坐标
        if widget.get("center_x") and widget.get("center_y"):
            return widget["center_x"], widget["center_y"]

        # 从 bounds 解析
        bounds = widget.get("bounds", "")
        if not bounds:
            return None, None

        try:
            pattern = r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]'
            match = re.match(pattern, bounds)

            if not match:
                return None, None

            x1, y1, x2, y2 = map(int, match.groups())
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2

            return center_x, center_y

        except Exception as e:
            print(f"[坐标计算异常] {e}")
            return None, None

    def _perform_operation(
        self,
        operation: str,
        x: int,
        y: int,
        adb_controller: ADBController
    ) -> bool:
        """
        执行具体的操作

        根据操作类型调用相应的 ADB 命令

        Args:
            operation: 操作类型
            x: X 坐标
            y: Y 坐标
            adb_controller: ADB 控制器实例

        Returns:
            True 表示执行成功，False 表示失败
        """
        # 标准化操作名称
        operation = operation.lower().replace("-", " ").strip()

        if operation == "click":
            print(f"[执行操作] 点击坐标 ({x}, {y})")
            return adb_controller.click(x, y)

        elif operation == "double click":
            print(f"[执行操作] 双击坐标 ({x}, {y})")
            success1 = adb_controller.click(x, y)
            time.sleep(0.1)
            success2 = adb_controller.click(x, y)
            return success1 and success2

        elif operation == "long press":
            print(f"[执行操作] 长按坐标 ({x}, {y})")
            return adb_controller.swipe(x, y, x, y, duration=1000)

        elif operation == "scroll":
            print(f"[执行操作] 从 ({x}, {y}) 向上滑动")
            return adb_controller.swipe(x, y + 200, x, y - 200, duration=500)

        else:
            print(f"[执行失败] 不支持的操作类型: {operation}")
            return False


# 测试入口
if __name__ == "__main__":
    executor = ActionExecutor()

    # ========== 测试 ReAct JSON 格式解析 ==========
    json_test_responses = [
        # 标准 JSON 格式（带代码块）
        '''```json
{
  "Thought": "The Login button is visible and not yet explored. I should click it to navigate to the login page.",
  "Action_Type": "click",
  "Target_Widget": "Login",
  "Input_Content": null,
  "Status": "Testing login flow"
}
```''',
        # 纯 JSON 对象（无代码块）
        '{"Thought": "There is a search input field.", "Action_Type": "input", "Target_Widget": "SearchBox", "Input_Content": "test query", "Status": "Testing search"}',
        # back 操作
        '''```json
{
  "Thought": "I have explored all widgets on this page.",
  "Action_Type": "back",
  "Target_Widget": null,
  "Input_Content": null,
  "Status": "Navigating back"
}
```''',
        # scroll_down 操作
        '''```json
{
  "Thought": "There might be more content below.",
  "Action_Type": "scroll_down",
  "Target_Widget": null,
  "Input_Content": null,
  "Status": "Exploring hidden content"
}
```''',
        # 小写字段名测试
        '{"thought": "test", "action_type": "click", "target_widget": "Settings", "input_content": null}',
    ]

    print("=" * 60)
    print("ReAct JSON 格式解析测试")
    print("=" * 60)

    for response in json_test_responses:
        print(f"\n输入: {response[:80]}...")
        action = executor._parse_llm_response(response)
        print(f"输出: {action}")
        print(f"有效: {action.is_valid()}")
        if action.thought:
            print(f"Thought: {action.thought}")

    # ========== 测试旧格式解析（向后兼容）==========
    legacy_test_responses = [
        'Operation: "click" Widget: "Search"',
        'Sure! Based on the current page, I suggest you to:\nOperation: "click" Widget: "Login"',
        'Widget: "SearchBox" Input: "hello world"',
        'Widget: "search_src_text" Input: "test query"',
        'operation: long press widget: SubmitButton',
        'Operation: "back"',
        'Operation: "scroll_down"',
        'Operation: "go back"',  # 同义词测试
    ]

    print("\n" + "=" * 60)
    print("旧格式解析测试（向后兼容）")
    print("=" * 60)

    for response in legacy_test_responses:
        print(f"\n输入: {response[:60]}...")
        action = executor._parse_llm_response(response)
        print(f"输出: {action}")
        print(f"有效: {action.is_valid()}")

    print("\n" + "=" * 60)
    print("崩溃检测功能测试")
    print("=" * 60)
    print("提示: 实际崩溃检测需要连接真实设备")
    print("execute_action 方法返回: (success, action)")
    print("崩溃检测结果存储在: executor.last_crash_detected, executor.last_crash_log")