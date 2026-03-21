"""
Prompt Builder Module
Generates structured prompts for LLM-based Android GUI testing
Supports three-phase prompt building: Initial, Test, and Feedback
"""

from typing import List, Dict, Optional, TYPE_CHECKING
from collections import deque
from .prompt_templates import (
    GUIContextTemplate,
    TestHistoryTemplate,
    OperationQuestionTemplate
)

if TYPE_CHECKING:
    from .exploration_cache import ExplorationCache


class PromptGenerator:
    """
    Prompt Generator for Android GUI Testing

    Generates structured prompts following the new template system:
    - GUIContext[1,2,3]: App, Page, and Widget information
    - TestHistory[1,2,3,4]: Function list, Test path, Operations, Function query
    - OperationQuestion[4,5,6]: Operation questions and feedback

    Supports three-phase prompt building:
    - Initial phase: Full context + all history
    - Test phase (success): Success message + context + history
    - Feedback phase (failure): Sorry + error + context + history

    Features:
    - Action pruning: Filters blacklisted widgets
    - Visit tracking: Tracks activity and widget visits
    - Operation history: Maintains recent operation history (max 5)
    """

    MAX_HISTORY_STEPS = 5

    def __init__(self, exploration_cache: Optional["ExplorationCache"] = None):
        """
        Initialize prompt generator

        Args:
            exploration_cache: Global exploration cache instance (optional)
        """
        self.exploration_cache = exploration_cache

        # App information
        self.app_name: str = ""

        # Activity tracking: {activity_name: {visits: int, status: str}}
        self.activity_info: Dict[str, Dict] = {}

        # Widget tracking: {activity_name: {widget_id: visits}}
        self.widget_visits: Dict[str, Dict[str, int]] = {}

        # Activity visit sequence (for test path)
        self.activity_sequence: List[str] = []

        # Operation history (max 5 entries)
        self.operation_history: deque = deque(maxlen=self.MAX_HISTORY_STEPS)

        # Current testing function
        self.current_function: Optional[str] = None
        self.current_function_status: Optional[str] = None

        # Step counter
        self.step_counter: int = 0

    # ==================== Configuration ====================

    def set_app_name(self, app_name: str) -> None:
        """Set application name"""
        self.app_name = app_name

    def register_activity(self, activity_name: str, status: str = "unvisited") -> None:
        """
        Register an activity

        Args:
            activity_name: Activity name
            status: Initial status ("visited" or "unvisited")
        """
        if activity_name not in self.activity_info:
            self.activity_info[activity_name] = {"visits": 0, "status": status}

    # ==================== Visit Recording ====================

    def record_activity_visit(self, activity_name: str) -> None:
        """
        Record an activity visit

        Args:
            activity_name: Activity name
        """
        # Ensure activity exists
        self.register_activity(activity_name)

        # Increment visit count
        self.activity_info[activity_name]["visits"] += 1
        self.activity_info[activity_name]["status"] = "visited"

        # Add to sequence (if different from last)
        if not self.activity_sequence or self.activity_sequence[-1] != activity_name:
            self.activity_sequence.append(activity_name)

    def record_widget_visit(self, activity_name: str, widget_identifier: str) -> None:
        """
        Record a widget visit

        Args:
            activity_name: Activity name
            widget_identifier: Widget identifier (text or resource_id)
        """
        if activity_name not in self.widget_visits:
            self.widget_visits[activity_name] = {}

        self.widget_visits[activity_name][widget_identifier] = \
            self.widget_visits[activity_name].get(widget_identifier, 0) + 1

    def record_operation(
        self,
        activity_name: str,
        widgets_tested: List[Dict],
        operation: str,
        target_widget: str,
        success: bool = True
    ) -> None:
        """
        Record an operation for history

        Args:
            activity_name: Activity name where operation occurred
            widgets_tested: List of tested widgets with visits: [{name, visits}]
            operation: Operation type (Click, Input, etc.)
            target_widget: Target widget name
            success: Whether operation succeeded
        """
        self.step_counter += 1

        entry = {
            "activity_name": activity_name,
            "widgets_tested": widgets_tested,
            "operation": operation,
            "target_widget": target_widget,
            "success": success
        }

        self.operation_history.appendleft(entry)

        # Also record activity and widget visits
        self.record_activity_visit(activity_name)
        self.record_widget_visit(activity_name, target_widget)

    def set_current_function(self, function_name: str, status: str = "testing") -> None:
        """Set current testing function"""
        self.current_function = function_name
        self.current_function_status = status

    # ==================== Widget Filtering ====================

    def _filter_blacklisted_widgets(self, activity_name: str, widgets: List[Dict]) -> List[Dict]:
        """
        Filter widgets that are in the blacklist (action pruning)

        Args:
            activity_name: Current Activity name
            widgets: Original widget list

        Returns:
            Filtered widget list
        """
        if not self.exploration_cache:
            return widgets

        filtered_widgets = []
        blacklisted_count = 0

        for widget in widgets:
            widget_identifier = self._get_widget_identifier(widget)

            if widget_identifier and self.exploration_cache.is_blacklisted(activity_name, widget_identifier):
                blacklisted_count += 1
                print(f"[Action Pruning] Filtered invalid widget: {widget_identifier}")
                continue

            filtered_widgets.append(widget)

        if blacklisted_count > 0:
            print(f"[Action Pruning] Filtered {blacklisted_count} invalid widgets, {len(filtered_widgets)} remaining")

        return filtered_widgets

    def _get_widget_identifier(self, widget: Dict) -> Optional[str]:
        """
        Get widget identifier (prefer text, then resource_id)

        Args:
            widget: Widget dict

        Returns:
            Widget identifier or None
        """
        text = widget.get("text", "")
        if text and text.strip():
            return text.strip()

        resource_id = widget.get("resource_id", "")
        if resource_id:
            return resource_id.split("/")[-1] if "/" in resource_id else resource_id

        return None

    # ==================== Widget Processing ====================

    def _process_widgets_for_template(
        self,
        activity_name: str,
        widgets: List[Dict]
    ) -> tuple[List[str], List[str], List[Dict]]:
        """
        Process widgets for template rendering

        Args:
            activity_name: Current Activity name
            widgets: Raw widget list

        Returns:
            Tuple of (upper_widget_names, lower_widget_names, processed_widgets)
        """
        # Filter blacklisted widgets
        filtered_widgets = self._filter_blacklisted_widgets(activity_name, widgets)

        if not filtered_widgets and widgets:
            print("[Warning] All widgets filtered! Using original list")
            filtered_widgets = widgets

        # Separate upper and lower widgets
        upper_widgets = [w for w in filtered_widgets if w.get("position") == "upper"]
        lower_widgets = [w for w in filtered_widgets if w.get("position") == "lower"]

        # Extract widget names
        upper_names = [self._get_widget_identifier(w) for w in upper_widgets]
        upper_names = [n for n in upper_names if n]

        lower_names = [self._get_widget_identifier(w) for w in lower_widgets]
        lower_names = [n for n in lower_names if n]

        # Process widgets for template (with nearby info)
        processed_widgets = self._add_nearby_info(filtered_widgets)

        return upper_names, lower_names, processed_widgets

    def _add_nearby_info(self, widgets: List[Dict]) -> List[Dict]:
        """
        Add nearby widget information to each widget

        Args:
            widgets: Widget list

        Returns:
            Widget list with nearby info
        """
        processed = []

        for i, widget in enumerate(widgets):
            # Get widget identifier
            widget_id = self._get_widget_identifier(widget)
            if not widget_id:
                continue

            # Get category (simplified class name)
            class_name = widget.get("class", "")
            category = class_name.split(".")[-1] if class_name else "Widget"

            # Get nearby widgets (previous and next)
            nearby = []
            if i > 0:
                prev_widget = widgets[i - 1]
                prev_id = self._get_widget_identifier(prev_widget)
                prev_class = prev_widget.get("class", "").split(".")[-1] if prev_widget.get("class") else "Widget"
                if prev_id:
                    nearby.append(f"[{prev_class}: {prev_id}]")

            if i < len(widgets) - 1:
                next_widget = widgets[i + 1]
                next_id = self._get_widget_identifier(next_widget)
                next_class = next_widget.get("class", "").split(".")[-1] if next_widget.get("class") else "Widget"
                if next_id:
                    nearby.append(f"[{next_class}: {next_id}]")

            # Get visit count for this widget in current activity
            # (Will be set from widget_visits in template)
            activity_name = widget.get("activity", "")

            processed.append({
                "text": widget_id,
                "resource_id": widget.get("resource_id", ""),
                "category": category,
                "nearby": nearby,
                "activity": activity_name
            })

        return processed

    def _get_widget_visits_for_activity(self, activity_name: str) -> Dict[str, int]:
        """Get widget visits for an activity"""
        return self.widget_visits.get(activity_name, {})

    # ==================== Data Preparation ====================

    def _get_activities_info(self) -> List[Dict]:
        """Get activities info list for templates"""
        activities_info = []
        for name, info in self.activity_info.items():
            activities_info.append({
                "name": name,
                "visit_time": info.get("visits", 0),
                "status": info.get("status", "unvisited")
            })
        return activities_info

    def _get_function_visits(self) -> Dict[str, Dict]:
        """Get function visits dict (alias for activity_info)"""
        return self.activity_info.copy()

    def _get_operation_history_list(self) -> List[Dict]:
        """Get operation history as list"""
        return list(self.operation_history)

    def _get_widgets_tested_for_activity(self, activity_name: str) -> List[Dict]:
        """Get widgets tested for an activity with visit counts"""
        widget_visits = self._get_widget_visits_for_activity(activity_name)
        return [{"name": name, "visits": visits} for name, visits in widget_visits.items()]

    # ==================== Prompt Building ====================

    def build_test_prompt(
        self,
        activity_or_widgets,
        widgets_or_activity=None,
        memorizer=None
    ) -> str:
        """
        Build test prompt - supports both new and legacy signatures

        New signature: build_test_prompt(widgets, activity_name)
        Legacy signature: build_test_prompt(activity_name, widgets, memorizer)

        Args:
            activity_or_widgets: Either activity_name (legacy) or widgets list (new)
            widgets_or_activity: Either widgets list (legacy) or activity_name (new)
            memorizer: Legacy parameter (ignored)

        Returns:
            Complete prompt string
        """
        # Detect signature based on types
        if isinstance(activity_or_widgets, str):
            # Legacy signature: build_test_prompt(activity_name, widgets, memorizer)
            activity_name = activity_or_widgets
            widgets = widgets_or_activity
            return self._build_initial_prompt_internal(widgets, activity_name)
        else:
            # New signature: build_test_prompt(widgets, activity_name)
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
        Build initial phase prompt (new signature)

        Combination: GUIContext[1,2,3] + TestHistory[1,2,3,4] + OperationQuestion[4,5]
        """
        if app_name:
            self.app_name = app_name
        return self._build_initial_prompt_internal(widgets, activity_name)

    def _build_initial_prompt_internal(
        self,
        widgets: List[Dict],
        activity_name: str
    ) -> str:
        """
        Internal: Build initial phase prompt
        """
        # Register activity if not exists
        self.register_activity(activity_name)

        # Process widgets
        upper_names, lower_names, processed_widgets = self._process_widgets_for_template(
            activity_name, widgets
        )

        # Get widget visits
        widget_visits = self._get_widget_visits_for_activity(activity_name)

        # Build prompt parts
        parts = [
            GUIContextTemplate.app_info(self.app_name, self._get_activities_info()),
            GUIContextTemplate.page_info(activity_name, upper_names, lower_names),
            GUIContextTemplate.widget_info(processed_widgets, widget_visits),
            TestHistoryTemplate.function_list(self._get_function_visits()),
            TestHistoryTemplate.test_path(self.activity_sequence),
            TestHistoryTemplate.latest_operations(self._get_operation_history_list()),
            TestHistoryTemplate.function_query(self.current_function, self.current_function_status),
            OperationQuestionTemplate.all_operations()
        ]

        return "\n\n".join(parts)

    def _build_test_prompt_internal(
        self,
        widgets: List[Dict],
        activity_name: str
    ) -> str:
        """
        Internal: Build test phase prompt (after successful operation)

        Combination: "We successfully did the above operation." + GUIContext[2,3] + TestHistory[3,4] + OperationQuestion[4,5]
        """
        # Register activity
        self.register_activity(activity_name)

        # Process widgets
        upper_names, lower_names, processed_widgets = self._process_widgets_for_template(
            activity_name, widgets
        )

        # Get widget visits
        widget_visits = self._get_widget_visits_for_activity(activity_name)

        # Build prompt parts
        parts = [
            "We successfully did the above operation.",
            GUIContextTemplate.page_info(activity_name, upper_names, lower_names),
            GUIContextTemplate.widget_info(processed_widgets, widget_visits),
            TestHistoryTemplate.latest_operations(self._get_operation_history_list()),
            TestHistoryTemplate.function_query(self.current_function, self.current_function_status),
            OperationQuestionTemplate.all_operations()
        ]

        return "\n\n".join(parts)

    def build_feedback_prompt(
        self,
        widgets: List[Dict],
        activity_name: str,
        failed_widget: str
    ) -> str:
        """
        Build feedback phase prompt (after failed operation)

        Combination: "Sorry," + OperationQuestion[6] + GUIContext[3] + TestHistory[3,4] + OperationQuestion[4,5]

        Args:
            widgets: Parsed widget list
            activity_name: Current Activity name
            failed_widget: The widget that was not found

        Returns:
            Complete prompt string
        """
        # Register activity
        self.register_activity(activity_name)

        # Process widgets (only need widget info, not page layout)
        _, _, processed_widgets = self._process_widgets_for_template(activity_name, widgets)

        # Get widget visits
        widget_visits = self._get_widget_visits_for_activity(activity_name)

        # Build prompt parts
        parts = [
            "Sorry,",
            OperationQuestionTemplate.widget_not_found(failed_widget),
            GUIContextTemplate.widget_info(processed_widgets, widget_visits),
            TestHistoryTemplate.latest_operations(self._get_operation_history_list()),
            TestHistoryTemplate.function_query(self.current_function, self.current_function_status),
            OperationQuestionTemplate.all_operations()
        ]

        return "\n\n".join(parts)

    # ==================== Legacy Compatibility ====================

    def build_test_prompt_legacy(
        self,
        activity_name: str,
        parsed_widgets: List[Dict],
        memorizer=None
    ) -> str:
        """
        Legacy method for backward compatibility

        Uses the new initial prompt building logic internally

        Args:
            activity_name: Current Activity name
            parsed_widgets: Parsed widget list
            memorizer: Ignored (legacy parameter)

        Returns:
            Complete prompt string
        """
        return self.build_initial_prompt(parsed_widgets, activity_name)

    # ==================== Utility Methods ====================

    def clear_history(self) -> None:
        """Clear all history and tracking data"""
        self.activity_info.clear()
        self.widget_visits.clear()
        self.activity_sequence.clear()
        self.operation_history.clear()
        self.current_function = None
        self.current_function_status = None
        self.step_counter = 0
        print("[PromptGenerator] All history cleared")

    def get_stats(self) -> Dict:
        """Get current statistics"""
        return {
            "app_name": self.app_name,
            "total_activities": len(self.activity_info),
            "visited_activities": sum(1 for a in self.activity_info.values() if a.get("status") == "visited"),
            "total_steps": self.step_counter,
            "current_function": self.current_function
        }


# Test entry point
if __name__ == "__main__":
    # Create generator
    generator = PromptGenerator()
    generator.set_app_name("MyTestApp")

    # Simulate some visits
    generator.record_activity_visit("SplashActivity")
    generator.record_activity_visit("MainActivity")
    generator.record_operation(
        "MainActivity",
        [{"name": "Search", "visits": 1}],
        "Click",
        "Login"
    )

    # Mock widgets
    mock_widgets = [
        {"text": "Username", "class": "android.widget.EditText", "resource_id": "com.app:id/username", "position": "upper"},
        {"text": "Password", "class": "android.widget.EditText", "resource_id": "com.app:id/password", "position": "upper"},
        {"text": "Login", "class": "android.widget.Button", "resource_id": "com.app:id/login", "position": "lower"},
        {"text": "Register", "class": "android.widget.Button", "resource_id": "com.app:id/register", "position": "lower"}
    ]

    # Test initial prompt
    print("=" * 60)
    print("Initial Prompt:")
    print("=" * 60)
    print(generator.build_initial_prompt(mock_widgets, "LoginActivity"))

    # Record another operation
    generator.record_operation(
        "LoginActivity",
        [{"name": "Username", "visits": 1}, {"name": "Login", "visits": 1}],
        "Click",
        "Login"
    )

    # Test test prompt
    print("\n" + "=" * 60)
    print("Test Prompt (Success):")
    print("=" * 60)
    print(generator.build_test_prompt(mock_widgets, "LoginActivity"))

    # Test feedback prompt
    print("\n" + "=" * 60)
    print("Feedback Prompt (Failure):")
    print("=" * 60)
    print(generator.build_feedback_prompt(mock_widgets, "LoginActivity", "SubmitButton"))

    # Stats
    print("\n" + "=" * 60)
    print("Statistics:")
    print("=" * 60)
    print(generator.get_stats())