"""
Prompt Templates Module
Structured prompt templates for Android GUI testing
Supports GUIContext and FunctionMemory modules
"""

from typing import List, Dict, Optional


class UserContext:
    """
    用户输入的测试上下文信息

    Attributes:
        app_name: 应用名称
        user_note: 用户自定义测试说明（一句话）
    """
    def __init__(
        self,
        app_name: str = "",
        user_note: str = ""
    ):
        self.app_name = app_name
        self.user_note = user_note

    def has_custom_info(self) -> bool:
        """检查是否有用户自定义信息"""
        return bool(self.user_note)


class SystemPromptTemplate:
    """System prompt template defining LLM role and behavior"""

    @staticmethod
    def get_system_prompt(
        app_name: str = "the target app",
        user_context: Optional[UserContext] = None
    ) -> str:
        """
        Get the system prompt for Android GUI testing agent.

        Defines LLM as an expert Android GUI testing agent with ReAct paradigm.

        Args:
            app_name: The name of the app being tested
            user_context: Optional user-provided context (goals, features, notes)

        Returns:
            System prompt string with app-specific role definition
        """
        # 使用用户提供的应用名称（如果有）
        display_app_name = user_context.app_name if user_context and user_context.app_name else app_name

        # 构建用户自定义信息部分
        user_info_section = ""
        if user_context and user_context.has_custom_info():
            user_info_section = f"\n**User Note:** {user_context.user_note}\n"

        return f"""You are a professional software tester. You are testing **{display_app_name}**.

Your goal is to find bugs and ensure the app works correctly.
{user_info_section}
Analyze the screenshot and UI information, then decide the next action.

Key points:
- **Page_Description**: ALWAYS describe what you see on the current screen first
- Check if the previous action's expected result matches the current state
- Identify any bugs (calculation errors, data inconsistency, function anomalies)
- Choose the most appropriate action from VALID ACTION TYPES below

VALID ACTION TYPES:
- "click": Click on a widget (requires Widget)
- "double-click": Double click on a widget (requires Widget)
- "long press": Long press on a widget (requires Widget)
- "input": Input text into a text field (requires Inputs array)
- "back": Press the back button (no Widget needed)
- "scroll_down": Scroll down the screen (no Widget needed)
- "scroll_up": Scroll up the screen (no Widget needed)

MANDATORY OUTPUT FORMAT:
You MUST output ONLY a single JSON code block based on the action type:

For click/double-click/long press operations:
```json
{{
  "Page_Description": "Brief description of what you see on the current screen (UI elements, layout, state)",
  "Function": "<function_name>",
  "Status": "<Yes/No>",
  "Operation": "<click/double-click/long press>",
  "Widget": "<widget_name>",
  "WidgetType": "<TextView/EditText/Button/ImageView>",
  "TargetX": <center_x_coordinate>,   // Target widget center X coordinate (integer)
  "TargetY": <center_y_coordinate>,   // Target widget center Y coordinate (integer)
  "Expected_Result": "What should happen after this action (e.g., 'Navigate to search results page')",
  "Bug_Detected": false,
  "Bug_Description": null
}}
```

**IMPORTANT - Visual Positioning**: When multiple widgets have identical attributes (e.g., multiple Switch controls with the same resource-id):
1. **Use the screenshot** to visually identify the target widget's location
2. **Check the Position (bounds)** shown in the widget list (e.g., "[901,1058][1038,1184]")
3. **Calculate the center coordinates**: TargetX = (left + right) / 2, TargetY = (top + bottom) / 2
4. **Output TargetX and TargetY** for precise clicking
5. The executor will use these coordinates to locate and click the correct widget

Example: If a Switch has Position: "[901,1058][1038,1184]", the center is:
- TargetX = (901 + 1038) / 2 = 969
- TargetY = (1058 + 1184) / 2 = 1121

For text input operation:
```json
{{
  "Page_Description": "Brief description of what you see on the current screen (UI elements, layout, state)",
  "Function": "<function_name>",
  "Status": "<Yes/No>",
  "Inputs": [
    {{"Widget": "<widget_name_1>", "WidgetType": "<EditText>", "ContentDesc": "<field_identifier_1>", "Input": "<input_text_1>"}},
    {{"Widget": "<widget_name_2>", "WidgetType": "<EditText>", "ContentDesc": "<field_identifier_2>", "Input": "<input_text_2>"}}
  ],
  "Operation": "<click/double-click/long press>",
  "OperationWidget": "<widget_name>",
  "OperationWidgetType": "<Button/TextView>",
  "Expected_Result": "What should happen after this action",
  "Bug_Detected": false,
  "Bug_Description": null
}}
```

**IMPORTANT**: When multiple widgets share the same resource-id (e.g., edit_text for multiple input fields), use `ContentDesc` to specify the field identifier shown in the widget list (e.g., "Front", "Back"). This ensures the correct field is targeted.

For system navigation (back/scroll_down/scroll_up):
```json
{{
  "Page_Description": "Brief description of what you see on the current screen (UI elements, layout, state)",
  "Function": "<function_name>",
  "Status": "<Yes/No>",
  "Operation": "<back/scroll_down/scroll_up>",
  "Expected_Result": "What should happen after this action",
  "Bug_Detected": false,
  "Bug_Description": null
}}
```
Note: System navigation actions do NOT require a Widget field.

BUG DETECTION:
Before deciding your next action, CHECK the history for any "Expected_Result" from previous steps.
Compare expected results with the current screenshot to detect bugs:

1. **Calculation Error**:
   - Example: Input 200, expected balance to double (200*2=400), but actual balance shows 600

2. **Data Inconsistency**:
   - Example: Data entered on one page differs from what's displayed on another page
   - CRITICAL RULE FOR DIALOGS: Mobile UI dialogs (pop-ups) are volatile. If a dialog is closed (e.g., by clicking OK/Cancel/Back) and reopened later, fields maybe reset to their default values. Do NOT report this reset as a data_inconsistency bug. You must ONLY compare data within the SAME uninterrupted dialog lifecycle.

3. **Function Anomaly**:
   - Example: Clicked "Submit" but nothing happened, or wrong page displayed
   - Example: Clicked "OK" but nothing happened, or wrong page displayed

If you detect a bug:
```json
{{
  "": "Brief description of what you see on the current screen, noting the bug",
  "Function": "<function_name>",
  "Status": "<Yes/No>",
  "Operation": "<action>",
  "Widget": "<widget_name>",
  "Expected_Result": "<what should happen next>",
  "Bug_Detected": true,
  "Bug_Description": {{
    "type": "calculation_error|data_inconsistency|function_anomaly",
    "severity": "Critical|Error|Warning",
    "description": "Detailed description: what was expected vs what actually happened"
  }}
}}
```

IMPORTANT:
- "Status" indicates whether this is a new function never encountered before. Use "Yes" if it's new, "No" if it has been tested.
- "Inputs" is an array that may contain one or multiple input fields.
- "Operation" and "OperationWidget" are optional fields for input operations (e.g., submit button).
- "Expected_Result" describes what you expect to happen after the action.
- "Bug_Detected" should be true if you observe a bug, false otherwise.
- "Bug_Description" is required when Bug_Detected is true.
- Output ONLY the JSON block. No additional text, no explanations outside the JSON.

⚠️ CRITICAL WIDGET SELECTION RULES:
1. The "Widget" and "OperationWidget" values MUST be selected from the provided widget list.
2. DO NOT invent, guess, or modify widget names. Use EXACT names as shown in the widget list.
3. If no suitable widget is found in the list, use "back" operation to navigate away.
4. Widget names are case-sensitive. Match them exactly as displayed.

Example of CORRECT widget selection:
- Widget list shows: "Login", "Cancel", "Username", "Password"
- CORRECT: "Widget": "Login"
- WRONG: "Widget": "login button" (not in list)
- WRONG: "Widget": "Submit" (not in list)
"""


class GUIContextTemplate:
    """GUI Context prompt templates for App, Page, and Widget information"""

    @staticmethod
    def app_info(app_name: str, activities_info: List[Dict]) -> str:
        """
        [1] App Information - From Manifest

        Args:
            app_name: Application name
            activities_info: List of activity info dicts with keys:
                - name: Activity name
                - visit_time: Number of visits
                - status: "visited" or "unvisited"

        Returns:
            Formatted app information string
            Format: App Name: <Name>
                    Activities: <ActivityName> + <VisitTime> + <Status>, ...
        """
        if not activities_info:
            return f"App Name: {app_name}\nActivities: None"

        activity_parts = []
        for activity in activities_info:
            name = activity.get("name", "Unknown")
            visit_time = activity.get("visit_time", 0)
            status = activity.get("status", "unvisited")
            activity_parts.append(f"{name} + {visit_time} + {status}")

        activities_str = ", ".join(activity_parts)
        return f"App Name: {app_name}\nActivities: {activities_str}"

    @staticmethod
    def page_info(activity_name: str) -> str:
        """
        [2] Page GUI Information

        Args:
            activity_name: Current Activity name

        Returns:
            Formatted page information string
            Format: Current Activity: <ActivityName>
        """
        return f"Current Activity: {activity_name}"

    @staticmethod
    def widget_info(widgets: List[Dict], widget_visits: Optional[Dict[str, int]] = None, screen_width: int = 1080, screen_height: int = 1920, is_first_visit: bool = True) -> str:
        """
        [3] Widget Information - 支持渐进式披露

        Args:
            widgets: List of widget dicts with keys:
                - text or resource_id: Widget identifier
                - category: Widget type (Button, EditText, etc.)
                - original_text: For EditText, the current/hint text content
                - nearby_label: For CheckBox/Switch, the associated text label
                - bounds: Widget position bounds (e.g., "[901,1058][1038,1184]")
            widget_visits: Optional dict mapping widget_identifier to visit count
            screen_width: Screen width in pixels (default 1080)
            screen_height: Screen height in pixels (default 1920)
            is_first_visit: 是否首次访问该 Activity
                - True: 不显示 Visits 信息，引导自由探索
                - False: 显示已测试 Widget 信息，引导探索未测试控件

        Returns:
            Formatted widget information string with progressive disclosure
        """
        if not widgets:
            return "The widgets which can be operated are: none"

        lines = [f"Screen Size: {screen_width} x {screen_height}",
                 "All coordinates are based on this resolution.",
                 ""]

        # 渐进式披露：根据首次访问选择不同的标题和引导语
        if is_first_visit:
            lines.append("The widgets which can be operated are:")
        else:
            # 返回访问：添加引导语
            tested_widgets = [k for k, v in widget_visits.items() if v > 0] if widget_visits else []
            if tested_widgets:
                lines.append(f"📌 You have already tested these widgets: {', '.join(tested_widgets[:10])}")
                lines.append("Please explore other untested widgets on this page.")
                lines.append("")
            lines.append("All widgets on this page:")

        # 收集所有控件名称用于后面的约束提示
        widget_names = []

        for widget in widgets:
            # Get widget identifier (prefer text, then resource_id)
            widget_id = widget.get("text", "") or widget.get("resource_id", "")
            if "/" in widget_id:
                widget_id = widget_id.split("/")[-1]

            widget_names.append(widget_id)

            # Get category
            category = widget.get("category", "Widget")

            # Get original_text for EditText content display
            original_text = widget.get("original_text", "")

            # Get nearby_label for CheckBox/Switch
            nearby_label = widget.get("nearby_label", "")

            # Get bounds position info (NEW: for visual positioning)
            bounds = widget.get("bounds", "")
            position_info = ""
            if bounds:
                # Simplify: show bounds directly for LLM visual positioning
                position_info = f', Position: {bounds}'

            # Build field part for EditText (using content_desc for field differentiation)
            # 例如 AnkiDroid 中 Front/Back 字段有相同 resource-id，通过 content_desc 区分
            field_part = ""
            content_desc = widget.get("content_desc", "")
            if category == "EditText" and content_desc and content_desc.strip():
                field_part = f', Field: "{content_desc}"'

            # Build content part for EditText
            content_part = ""
            if category == "EditText" and original_text:
                content_part = f', Current Input: "{original_text}"'

            # Build label part for CheckBox/Switch/RadioButton
            label_part = ""
            if nearby_label and category in ["CheckBox", "Switch", "RadioButton", "ToggleButton"]:
                label_part = f', Label: "{nearby_label}"'

            # 渐进式披露：首次访问不显示 Visits
            if is_first_visit:
                visits_info = ""  # 首次访问：不显示
            else:
                visits = widget_visits.get(widget_id, 0) if widget_visits else 0
                visits_info = f', Visits: {visits}'

            # Format line (order: category -> field -> label -> content -> position -> visits)
            lines.append(f"  - {widget_id} ({category}{field_part}{label_part}{content_part}{position_info}{visits_info})")

        # 添加约束提示
        lines.append("")
        lines.append("⚠️ IMPORTANT: You MUST select Widget/OperationWidget names from the list above.")
        lines.append("DO NOT invent or guess widget names. Use EXACT names as shown above.")
        lines.append(f"Available widgets: {', '.join(widget_names[:10])}{'...' if len(widget_names) > 10 else ''}")

        return "\n".join(lines)

    @staticmethod
    def action_operation_question() -> str:
        """
        [4] Action Operation Question - Non-input operations

        Returns:
            Operation question string
        """
        return "What operation is required? (Operation [click/double-click/long press/scroll] + <Widget Name>)\n⚠️ Widget Name MUST be from the widget list above."

    @staticmethod
    def input_operation_question() -> str:
        """
        [5] Input Operation Question

        Returns:
            Input operation question string
        """
        return "Please generate the input text in sequence, and the operation after input. (<Widget name> + <Input Content>, ...)\n⚠️ Widget Name MUST be from the widget list above."

    @staticmethod
    def testing_feedback(widget_identifier: str) -> str:
        """
        [6] Testing Feedback - Widget Not Found

        Args:
            widget_identifier: The widget that was not found

        Returns:
            Widget not found feedback string
        """
        return (
            f"❌ ERROR: Widget '{widget_identifier}' was NOT FOUND on the current page.\n"
            "This usually means you used a widget name that is NOT in the widget list.\n"
            "You MUST select a widget name from the provided list above.\n"
            "Please reselect a valid widget from the list."
        )

    @staticmethod
    def operation_questions() -> str:
        """
        Combined operation questions ([4] and [5])

        Returns:
            Combined operation questions string
        """
        return (
            "What operation is required? (Operation [click/double-click/long press/scroll] + <Widget Name>)\n"
            "⚠️ Widget Name MUST be from the widget list above.\n\n"
            "Please generate the input text in sequence, and the operation after input. (<Widget name> + <Input Content>, ...)\n"
            "⚠️ Widget Name MUST be from the widget list above."
        )


class FunctionMemoryTemplate:
    """Function Memory prompt templates for Explored Functions, Covered Activities, History, and Function Query"""

    @staticmethod
    def explored_function(function_visits: Dict[str, Dict]) -> str:
        """
        [1] Explored Function - From LLM summarization

        Args:
            function_visits: Dict mapping function_name to {visits: int, status: str}
                status can be "tested" or "testing"

        Returns:
            Formatted explored function string
            Format: List of tested functions: "Function: <FunctionName>. Visits: <Visits>. Status: <Status>", ...
        """
        if not function_visits:
            return 'List of tested functions: None'

        parts = []
        for name, info in function_visits.items():
            visits = info.get("visits", 0)
            status = info.get("status", "unvisited")
            # Format: "Function: <Name>. Visits: <Visits>. Status: <Status>"
            parts.append(f'"Function: {name}. Visits: {visits}. Status: {status}"')

        functions_str = ", ".join(parts)
        return f"List of tested functions: {functions_str}"

    @staticmethod
    def covered_activities(activities: List[Dict]) -> str:
        """
        [2] Activity List - From Manifest (ALL activities with visits)

        Args:
            activities: List of activity dicts with:
                - name: Activity name
                - visit_time: Number of visits (0 = unvisited)
                - status: "visited" or "unvisited"

        Returns:
            Formatted activity list string
            Format: Activity List (try to cover ALL activities):
                    "Activity: <ActivityName>. Visits: <VisitTime>", ...
        """
        if not activities:
            return 'Activity List: None'

        parts = []
        for activity in activities:
            name = activity.get("name", "Unknown")
            visit_time = activity.get("visit_time", 0)
            status = activity.get("status", "unvisited")

            # 标记未访问的 Activity
            # 只需要检查 status，因为 visit_time > 0 时 status 必然是 "visited"
            if status == "unvisited":
                parts.append(f'"Activity: {name}. Visits: {visit_time} (UNVISITED)"')
            else:
                parts.append(f'"Activity: {name}. Visits: {visit_time}"')

        activities_str = ", ".join(parts)

        return f'''Activity List (Try to cover ALL activities):
{activities_str}.'''

    @staticmethod
    def latest_tested_history(history: List[Dict]) -> str:
        """
        [3] History of Latest Tested Pages and Operations

        Args:
            history: List of history entries (most recent first), each with:
                - activity_name: The Activity tested
                - operation: The operation performed (e.g., "Click")
                - target_widget: The widget operated on
                - expected_result: Expected result of the operation
                - page_description: Description of the page before operation
                - visual_description: Visual description (legacy, for compatibility)

        Returns:
            Formatted history string for LLM to judge operation effectiveness
        """
        if not history:
            return "History: None"

        lines = ["## Recent Test History", ""]

        for i, entry in enumerate(history[:10], 1):
            activity_name = entry.get("activity_name", "Unknown")
            operation = entry.get("operation", "click")
            target_widget = entry.get("target_widget", "")
            expected_result = entry.get("expected_result", "")
            page_description = entry.get("page_description", "") or entry.get("visual_description", "")
            success = entry.get("success", True)

            status_str = "✓" if success else "✗"
            lines.append(f"### Step {i} [{status_str}]")
            lines.append(f"**Activity**: {activity_name}")
            lines.append(f"**Action**: {operation} → {target_widget}")

            # Page description - important for context
            if page_description:
                lines.append(f"**Page Before**: {page_description}")

            # Expected result for verification
            if expected_result:
                lines.append(f"**Expected**: {expected_result}")

            lines.append("")  # Separator between steps

        return "\n".join(lines)

    @staticmethod
    def function_query(current_function: Optional[str] = None, current_status: Optional[str] = None) -> str:
        """
        [4] Function Query - Ask LLM to summarize current function

        Args:
            current_function: Currently testing function name (optional)
            current_status: Current function status (optional)

        Returns:
            Formatted function query string (JSON format defined in System Prompt)
        """
        current_info = ""
        if current_function and current_status:
            current_info = f" (Current: {current_function}, Status: {current_status})"

        return (
            f"What is the function currently being tested? Are we testing a new function?{current_info}\n"
            "Output the JSON action following the MANDATORY OUTPUT FORMAT defined in the system prompt."
        )

    @staticmethod
    def verification_prompt(
        expected_result: str,
        actual_result: Optional[str] = None
    ) -> str:
        """
        [5] Verification Prompt - Ask LLM to verify expected vs actual result

        Args:
            expected_result: Expected result from previous action
            actual_result: Actual result observed (optional, will be inferred from screenshot)

        Returns:
            Formatted verification prompt string
        """
        prompt_parts = [
            "## Previous Action Verification",
            "",
            f"**Expected Result**: {expected_result}",
        ]

        if actual_result:
            prompt_parts.append(f"**Actual Result**: {actual_result}")

        prompt_parts.extend([
            "",
            "Please verify:",
            "1. Did the previous action produce the expected result?",
            "2. If not, is there a bug? Describe the bug type:",
            "   - calculation_error: Wrong numerical results",
            "   - data_inconsistency: Cross-page data mismatch",
            "   - function_anomaly: Feature not working as expected",
            ""
        ])

        return "\n".join(prompt_parts)


class MultimodalPromptTemplate:
    """Multimodal prompt templates for visual context and bug analysis"""

    @staticmethod
    def visual_context_intro() -> str:
        """
        Introduction for multimodal visual context

        Returns:
            Visual context introduction string
        """
        return """## Visual Context

I have attached screenshot(s) of the current Android app screen. Please use both the visual information from the screenshot(s) and the textual UI structure information below to make your decision.

When analyzing the screenshot:
1. Identify visible UI elements (buttons, text fields, icons, etc.)
2. Note any visual cues (colors, icons, layouts)
3. Compare visual state with the textual widget information
4. Look for any visual anomalies or unexpected states

"""

    @staticmethod
    def bug_analysis_prompt(
        bug_type: str,
        description: str,
        activity_name: str = "",
        operation: str = "",
        widget: str = "",
        crash_log: str = ""
    ) -> str:
        """
        Build bug analysis prompt

        Args:
            bug_type: Type of bug (crash, logic_error, ui_error, etc.)
            description: Human-readable description
            activity_name: Current activity
            operation: Operation that triggered the bug
            widget: Widget involved
            crash_log: Crash log if applicable

        Returns:
            Bug analysis prompt string
        """
        parts = [
            "# Bug Analysis Request",
            "",
            "Analyze the following bug detected during automated testing. I have attached screenshot(s) for visual context.",
            "",
            "## Bug Information",
            f"- **Type**: {bug_type}",
            f"- **Description**: {description}",
        ]

        if activity_name:
            parts.append(f"- **Activity**: {activity_name}")
        if operation:
            parts.append(f"- **Trigger Operation**: {operation}")
        if widget:
            parts.append(f"- **Widget Involved**: {widget}")

        if crash_log:
            # Truncate long crash logs
            truncated_log = crash_log[:2000] if len(crash_log) > 2000 else crash_log
            parts.append(f"\n## Crash Log\n```\n{truncated_log}\n```\n")

        parts.append("""
## Required Analysis

Please provide a comprehensive analysis including:

1. **Root Cause Analysis** 
   - What is the underlying technical cause?
   - Why did this bug occur?
   - What conditions trigger it?

2. **Severity Assessment**
   - Critical: App crash, data loss, security vulnerability
   - Error: Feature malfunction, incorrect output
   - Warning: UX issues, potential problems
   - Info: Minor issues, suggestions

3. **Category Classification**
   - crash: Application crash
   - calculation_error: Wrong numerical results
   - data_inconsistency: Cross-page data mismatch
   - function_anomaly: Feature not working as expected

4. **Fix Suggestion**
   - What code changes are needed?
   - Any configuration changes required?

5. **Reproduction Steps**
   - Clear step-by-step instructions to reproduce

## Output Format

Please output your analysis in JSON format:

```json
{
  "root_cause": "Detailed analysis of the root cause",
  "severity": "Critical|Error|Warning|Info",
  "category": "bug_category",
  "fix_suggestion": "Suggested fix",
  "reproduction_steps": [
    "Step 1: ...",
    "Step 2: ...",
    "Step 3: ..."
  ],
  "confidence": 0.8
}
```
""")
        return "\n".join(parts)

    @staticmethod
    def logic_error_detection_prompt(
        error_type: str,
        expected_value: str,
        actual_value: str,
        context: str = ""
    ) -> str:
        """
        Build logic error detection prompt

        Args:
            error_type: Type of logic error
            expected_value: Expected value/result
            actual_value: Actual value/result
            context: Additional context

        Returns:
            Logic error detection prompt string
        """
        return f"""# Logic Error Detection Request

I have attached screenshot(s) showing the current application state. Please analyze for potential logic errors.

## Error Information

- **Error Type**: {error_type}
- **Expected Value/Behavior**: {expected_value}
- **Actual Value/Behavior**: {actual_value}
{f"- **Context**: {context}" if context else ""}

## Analysis Tasks

1. **Verify the Error**
   - Confirm whether this is a genuine bug
   - Identify what went wrong

2. **Determine Severity**
   - How critical is this error?
   - Does it affect core functionality?

3. **Suggest Fix**
   - What needs to be changed?
   - Any workarounds?

## Output Format

```json
{{
  "is_genuine_error": true,
  "root_cause": "Explanation",
  "severity": "Critical|Error|Warning|Info",
  "fix_suggestion": "How to fix",
  "confidence": 0.8
}}
```
"""

    @staticmethod
    def visual_anomaly_prompt() -> str:
        """
        Prompt for detecting visual anomalies

        Returns:
            Visual anomaly detection prompt
        """
        return """# Visual Anomaly Detection

Please analyze the attached screenshot(s) for any visual anomalies:

## Check for:

1. **Rendering Issues**
   - Overlapping elements
   - Cut-off text
   - Missing icons/images
   - Incorrect colors

2. **Layout Problems**
   - Elements out of place
   - Broken responsive design
   - Hidden/obscured elements

3. **UI State Issues**
   - Incorrect button states (disabled when should be enabled)
   - Wrong text displayed
   - Missing labels

4. **Unexpected Elements**
   - Error dialogs
   - Toast messages
   - Unexpected popups

## Output Format

If any issues found:
```json
{
  "issues_found": true,
  "anomalies": [
    {
      "type": "rendering|layout|state|unexpected",
      "description": "What's wrong",
      "location": "Where on screen",
      "severity": "Critical|Error|Warning|Info"
    }
  ]
}
```

If no issues:
```json
{
  "issues_found": false,
  "notes": "Any observations"
}
```
"""

    @staticmethod
    def comparison_prompt(
        description: str = "Compare the two screenshots"
    ) -> str:
        """
        Prompt for comparing two screenshots

        Args:
            description: Description of what to compare

        Returns:
            Comparison prompt string
        """
        return f"""# Screenshot Comparison Request

{description}

Please analyze both screenshots and identify:

1. **Differences**
   - What changed between the two states?
   - Are the changes expected or unexpected?

2. **State Transition**
   - Did the action have the intended effect?
   - Any unintended side effects?

3. **Anomalies**
   - Any unexpected visual changes?
   - Any errors or warnings appeared?

## Output Format

```json
{{
  "differences": [
    {{"element": "...", "before": "...", "after": "..."}}
  ],
  "transition_successful": true,
  "anomalies": [],
  "summary": "Brief summary of the comparison"
}}
```
"""


class SupervisorPromptTemplate:
    """监管者提示词模板 - 用于假阳性审查和漏检检测"""

    @staticmethod
    def false_positive_review_prompt(bug_report, context: Dict) -> str:
        """
        假阳性审查提示词

        Args:
            bug_report: BugReport 对象（需导入 BugReport 类型）
            context: 上下文信息，包含 operation_history, last_expected_result 等

        Returns:
            格式化的假阳性审查提示词
        """
        # 导入检查：bug_report 应有 category, severity, description, activity, operation, widget 属性
        operation_history = context.get('operation_history', [])
        last_expected = context.get('last_expected_result', 'Unknown')
        page_description = context.get('page_description') or 'Unknown'

        history_str = SupervisorPromptTemplate._format_history(operation_history)

        # 安全获取 bug_report 属性
        try:
            category = bug_report.category.value if hasattr(bug_report.category, 'value') else str(bug_report.category)
            severity = bug_report.severity.value if hasattr(bug_report.severity, 'value') else str(bug_report.severity)
            description = bug_report.description or "No description"
            activity = bug_report.activity or "Unknown"
            operation = bug_report.operation or "Unknown"
            widget = bug_report.widget or "Unknown"
        except Exception:
            category = "unknown"
            severity = "Error"
            description = str(bug_report)
            activity = "Unknown"
            operation = "Unknown"
            widget = "Unknown"

        return f"""# False Positive Review Request

You are reviewing a bug report generated by the AI tester. Determine if this is a genuine bug or a false positive.

## Bug Report Under Review

- **Type**: {category}
- **Severity**: {severity}
- **Description**: {description}
- **Activity**: {activity}
- **Trigger Operation**: {operation} on {widget}

## Context

### Recent Operations
{history_str}

### Bug Assertion Context
- **Page Before Assertion**: {page_description}
- **Expected Result Claimed by Explorer**: {last_expected}

### Expected Result To Validate
{last_expected}

## Visual Evidence
[Attached: Current screenshot(s). These screenshots represent the UI state at the moment the bug was asserted.]

## Review Checklist

1. **Visual Evidence Check** - Does the screenshot support the bug claim?
2. **Expected Result Validation** - Is the expected result reasonable, and does the current UI violate it?
3. **False Positive Indicators**:
   - Temporary UI state (loading, transition)
   - Wrong context comparison
   - Expected result itself is incorrect
   - Bug already fixed or self-resolved

## Output Format

Output ONLY a single JSON code block:

```json
{{
  "is_false_positive": false,
  "reason": "Detailed explanation for your decision",
  "confidence": 0.85,
  "reasoning": "Step-by-step reasoning process"
}}
```

Set `is_false_positive` to `true` if this is a false positive, `false` if genuine bug.
Provide detailed explanation in `reason` field."""

    @staticmethod
    def missed_bug_detection_prompt(context: Dict) -> str:
        """
        漏检检测提示词

        Args:
            context: 上下文信息，包含 current_activity, operation_history, pending_verifications

        Returns:
            格式化的漏检检测提示词
        """
        activity_name = context.get('current_activity', 'Unknown')
        operation_history = context.get('operation_history', [])
        pending_verifications = context.get('pending_verifications', [])

        history_str = SupervisorPromptTemplate._format_history(operation_history)
        verif_str = SupervisorPromptTemplate._format_verifications(pending_verifications)

        return f"""# Missed Bug Detection Review

Review the current application state to identify any bugs that may have been missed by the tester.

## Current State
- **Activity**: {activity_name}
- **Recent Operations**: {len(operation_history)} steps

## Recent Test History
{history_str}

## Pending Verifications
{verif_str}

## Detection Checklist

Check for the following bug types that may have been missed:

1. **Visual Anomalies**
   - Error messages visible on screen
   - UI rendering issues (overlapping, cut-off, missing)
   - Incorrect colors or icons

2. **State Inconsistencies**
   - Data mismatch between what was entered and what's displayed
   - Unexpected page state
   - Wrong data values
   - Current UI does not match the Expected_Result of a recent operation

3. **Functional Issues**
   - Unresponsive elements
   - Incorrect feedback after action
   - Feature not working as expected
   - Operation was marked successful but the expected result is not visible in the current screenshot

## Output Format

Output ONLY a single JSON code block:

```json
{{
  "bugs_found": false,
  "missed_bugs": [
    {{
      "type": "ui_error|data_inconsistency|function_anomaly",
      "severity": "Critical|Error|Warning",
      "description": "Detailed description of the bug",
      "evidence": "Visual or contextual evidence"
    }}
  ],
  "suggestions": {{
    "MainActivity": "Suggestion text for this activity",
    "SearchActivity": "Another suggestion for different activity"
  }},
  "confidence": 0.8,
  "reasoning": "Analysis process explaining why these bugs were detected"
}}
```

If no bugs found, set `bugs_found` to `false` and `missed_bugs` to empty array.
If bugs found, set `bugs_found` to `true` and list each bug in `missed_bugs`.

## Suggestion Types

Provide testing suggestions in the `suggestions` field to guide the tester:

- **False Positive Bug**: `"ActivityName": "The reported bug is not real, it's a loading state"`
- **Repeated Testing**: `"ActivityName": "Already tested 5 times, try other features"`
- **Logic Issue**: `"ActivityName": "Expected result was incorrect, review test logic"`
- **Coverage Guidance**: `"ActivityName": "Focus on edge cases for this feature"`
- **Strategy Adjustment**: `"ActivityName": "Consider switching to other activities"`

These suggestions will be passed to the tester to improve future decisions."""

    @staticmethod
    def _format_history(operation_history: List[Dict]) -> str:
        """格式化操作历史"""
        if not operation_history:
            return "No recent operations."

        lines = []
        # 取最近 5 条记录
        recent_ops = operation_history[-5:] if len(operation_history) > 5 else operation_history

        for i, entry in enumerate(reversed(recent_ops), 1):
            op = entry.get('operation', 'unknown')
            widget = entry.get('target_widget', 'unknown')
            activity = entry.get('activity_name', 'unknown')
            success = "✓" if entry.get('success', True) else "✗"
            lines.append(f"{i}. [{success}] [{activity}] {op} -> {widget}")

        return "\n".join(lines)

    @staticmethod
    def _format_verifications(pending_verifications: List[Dict]) -> str:
        """格式化待验证列表"""
        if not pending_verifications:
            return "No pending verifications."

        lines = []
        for i, v in enumerate(pending_verifications, 1):
            expected = v.get('expected_result', 'Unknown')
            activity = v.get('activity_name', 'unknown')
            operation = v.get('operation', 'unknown')
            widget = v.get('target_widget', 'unknown')
            page_description = v.get('page_description') or v.get('visual_description') or 'Unknown'
            success = "success" if v.get('success', True) else "failed"
            lines.append(
                f"{i}. [{success}] [{activity}] {operation} -> {widget}\n"
                f"   Page Before: {page_description}\n"
                f"   Expected: {expected}\n"
                f"   Verify whether the current screenshot/UI satisfies this expected result."
            )

        return "\n".join(lines)


# Backward compatibility alias
TestHistoryTemplate = FunctionMemoryTemplate


# Convenience function for building complete prompts
def build_initial_prompt(
    app_name: str,
    activities_info: List[Dict],
    activity_name: str,
    widgets: List[Dict],
    widget_visits: Optional[Dict[str, int]],
    function_visits: Dict[str, Dict],
    covered_activities: List[Dict],
    operation_history: List[Dict],
    current_function: Optional[str] = None,
    current_status: Optional[str] = None,
    screen_width: int = 1080,  # NEW: 屏幕宽度
    screen_height: int = 1920  # NEW: 屏幕高度
) -> str:
    """
    Build complete initial phase prompt

    Combination: GUIContext[1,2,3,4,5] + FunctionMemory[1,2,3,4]

    Returns:
        Complete prompt string
    """
    parts = [
        GUIContextTemplate.app_info(app_name, activities_info),
        GUIContextTemplate.page_info(activity_name),
        GUIContextTemplate.widget_info(widgets, widget_visits, screen_width, screen_height),
        GUIContextTemplate.action_operation_question(),
        GUIContextTemplate.input_operation_question(),
        FunctionMemoryTemplate.explored_function(function_visits),
        FunctionMemoryTemplate.covered_activities(covered_activities),
        FunctionMemoryTemplate.latest_tested_history(operation_history),
        FunctionMemoryTemplate.function_query(current_function, current_status)
    ]

    return "\n\n".join(parts)


def build_test_prompt(
    activity_name: str,
    widgets: List[Dict],
    widget_visits: Optional[Dict[str, int]],
    function_visits: Dict[str, Dict],
    operation_history: List[Dict],
    current_function: Optional[str] = None,
    current_status: Optional[str] = None,
    screen_width: int = 1080,  # NEW: 屏幕宽度
    screen_height: int = 1920  # NEW: 屏幕高度
) -> str:
    """
    Build test phase prompt (after successful operation)

    Combination: "We successfully did the above operation." + GUIContext[2,3,4,5] + FunctionMemory[1,2,3,4]

    Returns:
        Complete prompt string
    """
    parts = [
        GUIContextTemplate.page_info(activity_name),
        GUIContextTemplate.widget_info(widgets, widget_visits, screen_width, screen_height),
        GUIContextTemplate.action_operation_question(),
        GUIContextTemplate.input_operation_question(),
        FunctionMemoryTemplate.explored_function(function_visits),
        FunctionMemoryTemplate.latest_tested_history(operation_history),
        FunctionMemoryTemplate.function_query(current_function, current_status)
    ]

    return "\n\n".join(parts)


def build_feedback_prompt(
    failed_widget: str,
    widgets: List[Dict],
    widget_visits: Optional[Dict[str, int]],
    function_visits: Dict[str, Dict],
    operation_history: List[Dict],
    current_function: Optional[str] = None,
    current_status: Optional[str] = None,
    screen_width: int = 1080,  # NEW: 屏幕宽度
    screen_height: int = 1920  # NEW: 屏幕高度
) -> str:
    """
    Build feedback phase prompt (after failed operation)

    Combination: GUIContext[6] + GUIContext[3,4,5] + FunctionMemory[3,4]

    Returns:
        Complete prompt stringFunction
    """
    parts = [
        GUIContextTemplate.testing_feedback(failed_widget),
        GUIContextTemplate.widget_info(widgets, widget_visits, screen_width, screen_height),
        GUIContextTemplate.action_operation_question(),
        GUIContextTemplate.input_operation_question(),
        FunctionMemoryTemplate.latest_tested_history(operation_history),
        FunctionMemoryTemplate.function_query(current_function, current_status)
    ]

    return "\n\n".join(parts)


# Test entry point
if __name__ == "__main__":
    # Test GUIContextTemplate
    print("=" * 60)
    print("Testing GUIContextTemplate")
    print("=" * 60)

    app_info = GUIContextTemplate.app_info(
        "Wikipedia",
        [
            {"name": "MainActivity", "visit_time": 2, "status": "visited"},
            {"name": "SearchActivity", "visit_time": 1, "status": "visited"},
            {"name": "SettingsActivity", "visit_time": 0, "status": "unvisited"}
        ]
    )
    print("[1] App Information:")
    print(app_info)

    print()
    print("[2] Page Information:")
    page_info = GUIContextTemplate.page_info("SearchActivity")
    print(page_info)

    print()
    print("[3] Widget Information:")
    widget_info = GUIContextTemplate.widget_info(
        [
            {"text": "search_input", "category": "EditText", "original_text": "android testing"},
            {"text": "Search", "category": "Button"},
            {"text": "Cancel", "category": "Button"}
        ],
        {"search_input": 1, "Search": 2, "Cancel": 0}
    )
    print(widget_info)

    print()
    print("[4] Action Operation Question:")
    print(GUIContextTemplate.action_operation_question())

    print()
    print("[5] Input Operation Question:")
    print(GUIContextTemplate.input_operation_question())

    print()
    print("[6] Testing Feedback:")
    print(GUIContextTemplate.testing_feedback("SubmitButton"))

    # Test FunctionMemoryTemplate
    print("\n" + "=" * 60)
    print("Testing FunctionMemoryTemplate")
    print("=" * 60)

    print("[1] Explored Function:")
    func_list = FunctionMemoryTemplate.explored_function({
        "Login": {"visits": 1, "status": "tested"},
        "Search": {"visits": 2, "status": "testing"}
    })
    print(func_list)

    print()
    print("[2] Covered Activities:")
    covered = FunctionMemoryTemplate.covered_activities([
        {"name": "MainActivity", "visit_time": 2, "status": "visited"},
        {"name": "SearchActivity", "visit_time": 1, "status": "visited"},
        {"name": "SettingsActivity", "visit_time": 0, "status": "unvisited"}
    ])
    print(covered)

    print()
    print("[3] Latest Tested History:")
    latest_ops = FunctionMemoryTemplate.latest_tested_history([
        {"activity_name": "MainActivity", "widgets_tested": [{"name": "Search", "visits": 1}, {"name": "Settings", "visits": 1}], "operation": "Click", "target_widget": "Search"},
        {"activity_name": "MainActivity", "widgets_tested": [{"name": "Login", "visits": 1}], "operation": "Click", "target_widget": "Login"}
    ])
    print(latest_ops)

    print()
    print("[4] Function Query:")
    print(FunctionMemoryTemplate.function_query())

    # Test complete prompt building
    print("\n" + "=" * 60)
    print("Testing Complete Initial Prompt")
    print("=" * 60)

    initial_prompt = build_initial_prompt(
        app_name="Wikipedia",
        activities_info=[
            {"name": "MainActivity", "visit_time": 2, "status": "visited"},
            {"name": "SearchActivity", "visit_time": 1, "status": "visited"},
            {"name": "SettingsActivity", "visit_time": 0, "status": "unvisited"}
        ],
        activity_name="SearchActivity",
        widgets=[
            {"text": "SearchBox", "category": "EditText"},
            {"text": "Search", "category": "Button"},
            {"text": "Cancel", "category": "Button"}
        ],
        widget_visits={"SearchBox": 1, "Search": 2, "Cancel": 0},
        function_visits={
            "Login": {"visits": 1, "status": "tested"},
            "Search": {"visits": 2, "status": "testing"}
        },
        covered_activities=[
            {"name": "MainActivity", "visit_time": 2, "status": "visited"},
            {"name": "SearchActivity", "visit_time": 1, "status": "visited"},
            {"name": "SettingsActivity", "visit_time": 0, "status": "unvisited"}
        ],
        operation_history=[
            {"activity_name": "MainActivity", "widgets_tested": [{"name": "Search", "visits": 1}], "operation": "Click", "target_widget": "Search"},
            {"activity_name": "MainActivity", "widgets_tested": [{"name": "Login", "visits": 1}], "operation": "Click", "target_widget": "Login"}
        ]
    )
    print(initial_prompt)
