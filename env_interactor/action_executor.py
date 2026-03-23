"""
动作执行器模块
解析 LLM 响应并执行相应的 GUI 操作
集成崩溃检测机制，实现 GPTDroid 论文的 Bug Oracle 功能

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

    JSON 格式示例:
    - 非输入操作:
      {"Thought": "...", "Function": "...", "Status": "Yes/No", "Operation": "click", "Widget": "Button"}
    - 输入操作:
      {"Thought": "...", "Function": "...", "Status": "Yes/No", "Widget": "InputField", "Input": "text", "Operation": "click", "OperationWidget": "Submit"}
    """
    def __init__(
        self,
        operation: Optional[str] = None,
        widget: Optional[str] = None,
        input_text: Optional[str] = None,
        thought: Optional[str] = None,
        status: Optional[str] = None,
        function_name: Optional[str] = None,
        function_status: Optional[str] = None,
        input_sequence: Optional[List[Tuple[str, str]]] = None,
        operation_widget: Optional[str] = None
    ):
        self.operation = operation
        self.widget = widget
        self.input_text = input_text
        self.thought = thought  # ReAct: 推理过程
        self.status = status    # ReAct: 状态信息
        self.function_name = function_name  # Function Query: 功能名称
        self.function_status = function_status  # Function Query: 功能状态 (tested/testing)
        self.input_sequence = input_sequence  # 多输入序列: [(widget, input_text), ...]
        self.operation_widget = operation_widget  # 输入后的操作目标控件（如 Submit 按钮）

    def is_valid(self) -> bool:
        """检查是否为有效的动作"""
        # 必须有操作类型
        if not self.operation:
            return False

        # 系统级动作（back, home, scroll_down, scroll_up）不需要 widget
        system_actions = {"back", "home", "scroll_down", "scroll_up"}
        if self.operation in system_actions:
            return True

        # 普通滚动操作也不需要 widget
        if self.operation == "scroll":
            return True

        # click 类操作必须有 widget
        if not self.widget:
            return False

        return True

    def __repr__(self) -> str:
        parts = [f"operation='{self.operation}'"]
        if self.widget:
            parts.append(f"widget='{self.widget}'")
        if self.input_text:
            parts.append(f"input='{self.input_text[:30]}...'" if len(self.input_text) > 30 else f"input='{self.input_text}'")
        if self.operation_widget:
            parts.append(f"op_widget='{self.operation_widget}'")
        if self.input_sequence:
            parts.append(f"inputs={len(self.input_sequence)}")
        if self.thought:
            parts.append(f"thought='{self.thought[:50]}...'" if len(self.thought or '') > 50 else f"thought='{self.thought}'")
        if self.function_name:
            parts.append(f"function='{self.function_name}'")
            if self.function_status:
                parts.append(f"status='{self.function_status}'")
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
    - home: Home键返回桌面
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
        "back", "home", "scroll_down", "scroll_up",
    }

    # 系统级动作列表（不需要 Widget）
    SYSTEM_LEVEL_ACTIONS = {"back", "home", "scroll_down", "scroll_up"}

    def __init__(self):
        """初始化动作执行器"""
        # 最后一次崩溃检测结果（供外部查询）
        self.last_crash_detected: bool = False
        self.last_crash_log: str = ""

    def execute_action(
        self,
        llm_response: str,
        parsed_widgets: List[Dict],
        adb_controller: ADBController,
        memory_manager: Optional["TestingSequenceMemorizer"] = None,
        activity_name: str = "UnknownActivity"
    ) -> Tuple[bool, Optional[ParsedAction]]:
        """
        执行 LLM 决策的动作，并在执行后检测崩溃

        完整流程：
        1. 解析 LLM 响应，提取操作类型、目标控件名和输入文本
        2. 在控件列表中查找匹配的控件
        3. 计算目标控件的中心坐标
        4. 调用 ADB 执行相应的操作
        5. 检测是否发生崩溃，如有崩溃则保存 Bug 报告

        Args:
            llm_response: LLM 的响应字符串
            parsed_widgets: 解析后的控件列表
            adb_controller: ADB 控制器实例
            memory_manager: 记忆管理器实例，用于生成复现路径
            activity_name: 当前 Activity 名称，用于 Bug 报告

        Returns:
            元组 (success, action):
            - success: True 表示执行成功，False 表示执行失败
            - action: 解析后的 ParsedAction 对象，解析失败时为 None

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
                adb_controller, memory_manager, activity_name, action
            )

            return success, action

        # 处理 home 操作（返回桌面）
        if action.operation == "home":
            print("[系统动作] 按下 Home 键返回桌面")
            success = adb_controller.go_home()
            time.sleep(1)  # 等待返回桌面

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action
            )

            return success, action

        # 处理 scroll_down 操作（向下滚动屏幕）
        if action.operation == "scroll_down":
            print("[系统动作] 向下滚动屏幕（查看下方内容）")
            success = adb_controller.scroll_down()
            time.sleep(1)  # 等待滚动完成

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action
            )

            return success, action

        # 处理 scroll_up 操作（向上滚动屏幕）
        if action.operation == "scroll_up":
            print("[系统动作] 向上滚动屏幕（查看上方内容）")
            success = adb_controller.scroll_up()
            time.sleep(1)  # 等待滚动完成

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action
            )

            return success, action

        # ========== 处理控件级操作（需要匹配控件）==========

        # 处理多输入操作（多个 Widget/Input 对）
        if action.has_multiple_inputs():
            success = self._execute_multiple_inputs_action(action, parsed_widgets, adb_controller)

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action
            )

            return success, action

        # 处理输入+操作组合（Widget + Input + Operation + OperationWidget）
        # 例如：输入文本到输入框，然后点击 Submit 按钮
        if action.has_input_with_operation():
            success = self._execute_input_then_operation(action, parsed_widgets, adb_controller)

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action
            )

            return success, action

        # 处理输入操作（operation == 'input' 或有 input_text）
        if action.operation == "input" or (action.input_text and action.widget):
            success = self._execute_input_action(action, parsed_widgets, adb_controller)

            # 执行后检测崩溃
            self._check_crash_after_action(
                adb_controller, memory_manager, activity_name, action
            )

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
                adb_controller, memory_manager, activity_name, action
            )

            return success, action

        # 步骤2：查找匹配的控件
        target_widget = self._find_target_widget(action.widget, parsed_widgets)
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
            adb_controller, memory_manager, activity_name, action
        )

        return success, action

    def _check_crash_after_action(
        self,
        adb_controller: ADBController,
        memory_manager: Optional["TestingSequenceMemorizer"],
        activity_name: str,
        action: ParsedAction
    ) -> None:
        """
        在执行动作后检测崩溃，并保存 Bug 报告

        工业级崩溃检测流程：
        1. 等待 2 秒，确保崩溃日志已写入系统缓冲区
        2. 调用 ADB 检测崩溃（精准关键字 + 日志切片）

        注意：logcat 缓存清空应在动作执行前完成（由调用方负责）

        检测结果存储在 self.last_crash_detected 和 self.last_crash_log 中

        Args:
            adb_controller: ADB 控制器实例
            memory_manager: 记忆管理器实例
            activity_name: 当前 Activity 名称
            action: 刚执行的动作
        """
        print("\n[Bug Oracle] 正在检测崩溃...")

        # 工业级标准：等待 2 秒，确保崩溃日志已写入系统缓冲区
        print("[Bug Oracle] 等待 2 秒确保崩溃日志已写入...")
        time.sleep(2)

        # 检测崩溃（使用精准关键字和日志切片）
        crash_log = adb_controller.check_for_crash()

        if crash_log:
            print("=" * 60)
            print("🚨 [崩溃检测] 发现应用崩溃！")
            print("=" * 60)

            # 更新崩溃状态
            self.last_crash_detected = True
            self.last_crash_log = crash_log

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
            print("[Bug Oracle] 未检测到崩溃，继续测试...")

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

            # ========== 提取 Input ==========
            # 新格式: Inputs 数组 (多输入支持)
            # 旧格式: Input 字段 (单输入)
            # 更旧格式: Input_Content 字段
            inputs_array = data.get('Inputs') or data.get('inputs')
            if inputs_array and isinstance(inputs_array, list):
                # 处理多输入数组格式
                input_sequence = []
                for item in inputs_array:
                    if isinstance(item, dict):
                        widget_name = item.get('Widget') or item.get('widget')
                        input_text = item.get('Input') or item.get('input')
                        if widget_name and input_text:
                            input_sequence.append((str(widget_name).strip(), str(input_text).strip()))
                if input_sequence:
                    action.input_sequence = input_sequence
                    # 设置第一个输入作为主 widget 和 input_text（向后兼容）
                    action.widget = input_sequence[0][0]
                    action.input_text = input_sequence[0][1]
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

            # ========== 后处理 ==========
            # 如果有 Input 但没有 Operation，默认为 input 操作
            if action.input_text and not action.operation:
                action.operation = "input"

            # 打印解析结果
            print(f"[JSON解析] Thought: {action.thought[:50] if action.thought else 'N/A'}...")
            print(f"[JSON解析] Operation: {action.operation}, Widget: {action.widget}, Input: {action.input_text}")
            if action.operation_widget:
                print(f"[JSON解析] OperationWidget: {action.operation_widget}")
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
                        input_sequence.append((closest_widget, input_val))

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

        Args:
            action: 解析后的动作，包含 input_sequence
            parsed_widgets: 控件列表
            adb_controller: ADB 控制器

        Returns:
            是否执行成功
        """
        print(f"[多输入操作] 共 {len(action.input_sequence)} 个输入")

        for i, (widget_name, input_text) in enumerate(action.input_sequence, 1):
            print(f"\n[输入 {i}/{len(action.input_sequence)}] 控件: {widget_name}, 文本: {input_text}")

            # 查找输入框控件
            target_widget = self._find_target_widget(widget_name, parsed_widgets)
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
            if not adb_controller.clear_and_input_text(target_widget, input_text):
                print(f"[执行失败] 文本输入失败: {input_text}")
                return False

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

        JSON 格式示例:
        {"Widget": "InputField", "Input": "text", "Operation": "click", "OperationWidget": "Submit"}

        Args:
            action: 解析后的动作，包含 widget, input_text, operation, operation_widget
            parsed_widgets: 控件列表
            adb_controller: ADB 控制器

        Returns:
            是否执行成功
        """
        print(f"[输入+操作] 输入框: {action.widget}, 文本: {action.input_text}")
        print(f"[输入+操作] 后续操作: {action.operation} -> {action.operation_widget}")

        # 步骤1：输入文本
        # 查找输入框控件
        input_widget = self._find_target_widget(action.widget, parsed_widgets)
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
        if not adb_controller.clear_and_input_text(input_widget, action.input_text):
            print("[执行失败] 文本输入失败")
            return False

        # 短暂等待
        time.sleep(0.3)

        # 步骤3：执行后续操作
        print(f"[步骤3] 执行操作: {action.operation} -> {action.operation_widget}")

        # 查找操作目标控件
        target_widget = self._find_target_widget(action.operation_widget, parsed_widgets)
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

        Args:
            action: 解析后的动作
            parsed_widgets: 控件列表
            adb_controller: ADB 控制器

        Returns:
            是否执行成功
        """
        print(f"[输入操作] 目标控件: {action.widget}, 输入文本: {action.input_text}")

        # 查找输入框控件
        target_widget = self._find_target_widget(action.widget, parsed_widgets)
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
        if not adb_controller.clear_and_input_text(target_widget, action.input_text):
            print("[执行失败] 文本输入失败")
            return False

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
        parsed_widgets: List[Dict]
    ) -> Optional[Dict]:
        """
        在控件列表中查找名称匹配的控件

        匹配策略：
        1. 精确匹配 text 字段
        2. 精确匹配 resource-id 的最后一部分
        3. 部分匹配 text 或 resource-id（模糊匹配）
        4. 匹配 content-desc

        终极清洗：移除所有空白字符（空格、换行、制表符）后再匹配

        Args:
            widget_name: 目标控件名称
            parsed_widgets: 控件列表

        Returns:
            找到的控件字典，未找到返回 None
        """
        if not widget_name or not parsed_widgets:
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

        # 打印所有控件的标识信息（用于调试）
        print("[匹配调试] 控件列表详情:")
        for i, w in enumerate(parsed_widgets[:10]):  # 只打印前10个
            text = w.get("text", "")
            original_text = w.get("original_text", "")
            rid = w.get("resource_id", "")
            cd = w.get("content_desc", "")
            cls = w.get("class", "")
            # 显示清洗前后对比
            text_clean = clean_text(text)
            print(f"  [{i}] text='{text}' (clean: '{text_clean}'), id='{rid}', content_desc='{cd}'")

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

        # 策略2：精确匹配 resource-id 最后一部分
        for widget in parsed_widgets:
            resource_id = widget.get("resource_id", "")
            if resource_id:
                id_name = resource_id.split("/")[-1] if "/" in resource_id else resource_id
                id_clean = clean_text(id_name)
                if id_clean == widget_name_clean:
                    print(f"[匹配成功] 通过 resource-id 精确匹配: {id_name}")
                    return widget

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

        print(f"[匹配失败] 未找到匹配控件: '{widget_name}' (clean: '{widget_name_clean}')")
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