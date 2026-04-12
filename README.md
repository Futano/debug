# GPTDroid - LLM-Driven Android GUI Automated Testing Tool

An LLM-based automated testing framework for Android applications, enabling fully autonomous GUI exploration testing.

**Supports multimodal bug detection: Application crashes + Logic errors (calculation errors, cross-page data inconsistency, functional anomalies)**

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Core Architecture](#core-architecture)
3. [Supervisor System](#supervisor-system)
4. [Bug Detection System](#bug-detection-system)
5. [Module Details](#module-details)
6. [Prompt System](#prompt-system)
7. [Main Program Flow](#main-program-flow)
8. [Quick Start](#quick-start)
9. [Configuration](#configuration)
10. [Output Files](#output-files)

---

## Project Structure

```
GPTDroid/
├── main.py                         # Main program entry (Outer Loop)

├── env_interactor/                 # Environment interaction layer
│   ├── __init__.py
│   ├── adb_utils.py               # ADB command wrapper
│   └── action_executor.py         # Action executor + LLM response parser

├── gui_extractor/                  # GUI parsing layer
│   ├── __init__.py
│   ├── xml_parser.py              # UI XML parser
│   └── manifest_parser.py         # APK Manifest parser

├── llm_agent/                      # LLM agent layer
│   ├── __init__.py
│   ├── prompt_templates.py        # Prompt template definitions
│   ├── prompt_builder.py          # Prompt generator (core entry)
│   ├── llm_client.py              # LLM client (text only)
│   ├── multimodal_llm_client.py   # Multimodal LLM client
│   ├── screenshot_manager.py      # Screenshot manager
│   ├── bug_analysis_engine.py     # Bug analysis engine
│   ├── memory_manager.py          # Test memory management
│   ├── supervisor.py              # Supervisor model (NEW)
│   ├── test_logger.py             # Test logger
│   ├── exploration_cache.py       # Exploration cache
│   └── token_monitor.py           # Token consumption monitor

├── bug_reports/                    # Bug report storage
├── temp_data/                      # Runtime data
│   ├── screenshots/               # Screenshots storage
│   └── current_ui.xml             # Latest UI layout

└── README.md
```

---

## Core Architecture

### Overall Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          GPTDroid Multimodal Architecture                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                     Dual-Agent Architecture                           │  │
│  ├──────────────────────────────────────────────────────────────────────┤  │
│  │                                                                       │  │
│  │   ┌─────────────────────┐         ┌─────────────────────────────┐    │  │
│  │   │   Explorer Agent    │         │     Supervisor Agent        │    │  │
│  │   │   (探索者模型)        │         │      (监管者模型)            │    │  │
│  │   ├─────────────────────┤         ├─────────────────────────────┤    │  │
│  │   │ - GUI exploration   │         │ - False positive review     │    │  │
│  │   │ - Action decision   │◄───────►│ - Missed bug detection      │    │  │
│  │   │ - Bug assertion     │  Review │ - Suggestions to explorer   │    │  │
│  │   └─────────────────────┘         └─────────────────────────────┘    │  │
│  │                                                                       │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌──────────────┐    ┌──────────────────┐    ┌────────────────────────┐    │
│  │ Screenshot   │    │  MultimodalLLM   │    │   Bug Analysis Engine  │    │
│  │   Manager    │───▶│    Client        │───▶│  ┌──────────────────┐  │    │
│  │              │    │                  │    │  │ CrashDetector    │  │    │
│  └──────────────┘    └──────────────────┘    │  ├──────────────────┤  │    │
│                                              │  │ BugReportGenerator│  │    │
│                                              │  └──────────────────┘  │    │
│                                              └────────────────────────┘    │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                        Data Flow                                      │  │
│  │                                                                       │  │
│  │  main.py ──▶ ADBController ──▶ GUIAnalyzer ──▶ PromptGenerator       │  │
│  │      │            │                │                │                │  │
│  │      │            │                │                ▼                │  │
│  │      │            │                │         LLMClient               │  │
│  │      │            │                │                │                │  │
│  │      │            ▼                │                ▼                │  │
│  │      └──▶ ScreenshotManager ───────┼───▶ ActionExecutor              │  │
│  │                                     │               │                │  │
│  │                                     │               ▼                │  │
│  │                                     └──────▶ SupervisorModel         │  │
│  │                                                   (审查)            │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Supervisor System

The Supervisor acts as an independent quality assurance agent, reviewing the Explorer's decisions and providing guidance.

### Two Review Scenarios

#### 1. False Positive Review

Triggered when the Explorer reports a bug (`Bug_Detected: true`).

**Critical: Review happens BEFORE executing the action, using the context snapshot at the moment of bug assertion.**

```
┌─────────────────────────────────────────────────────────────────┐
│              False Positive Review Flow                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Get LLM JSON response                                       │
│  2. Parse response: check if Bug_Detected=true                  │
│  3. if BUG_DETECTED:                                            │
│     ├─► Capture screenshot NOW (T0 state) ← Context Snapshot    │
│     ├─► Call Supervisor immediately                             │
│     │   Input: BugReport + Context + Screenshot                 │
│     ├─► Supervisor reviews:                                     │
│     │   - Is this a genuine bug or false positive?              │
│     │   - Does screenshot support the bug claim?                │
│     │   - Is the expected result reasonable?                    │
│     ├─► Result:                                                 │
│     │   - False Positive → Log, continue testing                │
│     │   - True Bug → Save Bug Report immediately                │
│  4. Execute the action (click/input)                            │
│  5. Continue to next cycle                                      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Information passed to Supervisor:**

| Field | Source | Description |
|-------|--------|-------------|
| `bug_report.category` | LLM output | Bug type (crash/calculation_error/data_inconsistency/function_anomaly) |
| `bug_report.severity` | LLM output | Bug severity (Critical/Error/Warning/Info) |
| `bug_report.description` | LLM output | Bug description |
| `bug_report.activity` | Current state | Activity where bug was detected |
| `bug_report.operation` | LLM decision | Operation that would trigger the bug |
| `bug_report.widget` | LLM decision | Widget involved |
| `bug_report.screenshot_paths` | T0 snapshot | Screenshot at bug assertion moment |
| `bug_report.operation_history` | Memory manager | ALL historical operations |
| `context.last_expected_result` | Memory manager | Previous expected result |
| `context.current_activity` | Current state | Current Activity |

#### 2. Missed Bug Detection (Periodic Review)

Triggered every 10 steps to detect bugs that the Explorer might have missed.

```
┌─────────────────────────────────────────────────────────────────┐
│              Missed Bug Detection Flow                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Every 10 steps:                                                │
│  ├─► Capture current screenshot                                 │
│  ├─► Build review context:                                      │
│  │   - current_activity                                         │
│  │   - operation_history (all steps)                            │
│  ├─► Supervisor scans for:                                      │
│  │   - Visual anomalies (error messages, UI rendering issues)   │
│  │   - State inconsistencies (data mismatch)                    │
│  │   - Functional issues (unresponsive elements)                │
│  ├─► Output:                                                    │
│  │   - missed_bugs: List of detected bugs                       │
│  │   - suggestions: Activity-based guidance for Explorer        │
│  ├─► Suggestions passed to PromptGenerator                      │
│     → Included in next Explorer prompt                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Supervisor Suggestions

Suggestions are passed to the Explorer to guide future decisions:

```
┌─────────────────────────────────────────────────────────────────┐
│  ## ⚠️ Supervisor's Suggestions                                  │
│                                                                 │
│  **You MUST consider these suggestions before making decision:**│
│                                                                 │
│  👉 **MainActivity**: Already tested basic functions, try       │
│     advanced features                                           │
│     **SearchActivity**: Search works correctly, focus on        │
│     edge cases                                                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### ReviewResult Data Structure

```python
@dataclass
class ReviewResult:
    review_type: str                  # "false_positive_check" or "missed_bug_check"
    is_false_positive: bool = False   # False positive determination
    false_positive_reason: str = ""   # Reason for false positive
    missed_bugs: List[Dict] = []      # Missed bugs detected
    suggestions: Dict[str, str] = {}  # {activity_name: suggestion_text}
    confidence: float = 0.0           # Confidence level (0-1)
    reasoning: str = ""               # Detailed reasoning
    timestamp: datetime               # Review timestamp
```

---

## Bug Detection System

### Bug Assertion Review Principle

**Bug assertions must be reviewed based on the context snapshot at the moment of assertion, NOT the new state after executing subsequent actions.**

```
WRONG Flow (Before Fix):
─────────────────────────────────────────────
Get LLM JSON → Execute Operation → Capture screenshot (NEW state)
→ if BUG_DETECTED → Call Supervisor (with NEW screenshot)
❌ Problem: Bug context is lost

CORRECT Flow (After Fix):
─────────────────────────────────────────────
Get LLM JSON → if BUG_DETECTED:
  ├─► Capture screenshot NOW (T0 state - Context Snapshot)
  ├─► Call Supervisor with T0 screenshot
  ├─► Review result: True Bug → Save Bug Report
  │                   False Positive → Log
→ Execute Operation → Continue to next cycle
✅ Correct: Bug context is preserved
```

### Bug Categories

| Category | Description | Detection Method |
|----------|-------------|------------------|
| `crash` | Application crash | logcat keyword detection |
| `calculation_error` | Numerical calculation error | LLM judgment + Supervisor review |
| `data_inconsistency` | Cross-page data mismatch | LLM judgment + Supervisor review |
| `function_anomaly` | Functional logic abnormality | LLM judgment + Supervisor review |

### Bug Severity

| Level | Description | Example |
|-------|-------------|---------|
| **Critical** | App crash, data loss, security vulnerability | NullPointerException, ANR |
| **Error** | Feature malfunction, incorrect output | Calculation error, data inconsistency |
| **Warning** | UX issues, potential problems | UI rendering issues |
| **Info** | Minor issues, suggestions | Text errors, layout adjustments |

### Bug Report Format

Bug reports are saved in two formats:

**1. Markdown Format (`bug_reports/BUG-YYYYMMDD-XXXX.md`)**
```markdown
# Bug Report: BUG-20260412-0015

**Timestamp**: 2026-04-12 10:30:45
**Severity**: Error
**Category**: calculation_error

## Summary
Balance calculation error: expected 400, actual 600

## Bug Details
- **Activity**: RechargeActivity
- **Operation**: click
- **Widget**: submit_button

## Operation History (All 15 Steps)
| Step | Status | Activity | Operation | Widget |
|------|--------|----------|-----------|--------|
| 1 | ✅ | MainActivity | click | menu |
| 2 | ✅ | MainActivity | click | recharge |
...
```

**2. JSON Format (`bug_reports/BUG-YYYYMMDD-XXXX.json`)**
```json
{
  "bug_id": "BUG-20260412-0015",
  "timestamp": "2026-04-12T10:30:45",
  "severity": "Error",
  "category": "calculation_error",
  "title": "Balance calculation error",
  "description": "Expected 400, actual 600",
  "activity": "RechargeActivity",
  "operation": "click",
  "widget": "submit_button",
  "screenshot_paths": ["..."],
  "screenshot_base64": ["..."],
  "operation_history": [...]
}
```

---

## Module Details

### 1. Environment Interaction Layer (`env_interactor/`)

#### ADBController (`adb_utils.py`)

| Method | Function | Return |
|--------|----------|--------|
| `dump_ui()` | Capture UI layout XML | `str` (XML file path) |
| `screenshot()` | Capture screen | `str` (image path) |
| `get_current_activity()` | Get current Activity | `str` |
| `get_current_package()` | Get current package | `str` |
| `check_for_crash()` | Detect crash logs | `str` (crash log) |
| `clear_logcat()` | Clear log buffer | `None` |
| `tap(x, y)` | Tap coordinates | `bool` |
| `input_text(text)` | Input text | `bool` |
| `go_back()` | Press back button | `bool` |
| `scroll(direction)` | Scroll screen | `bool` |

#### ActionExecutor (`action_executor.py`)

| Method | Function |
|--------|----------|
| `execute_action(llm_response, widgets, adb, ...)` | Parse LLM response and execute action |
| `parse_action_only(llm_response)` | Parse LLM response WITHOUT executing (NEW) |
| `_parse_llm_response(llm_response)` | Parse LLM JSON response |

**ParsedAction Structure:**
```python
class ParsedAction:
    operation: str              # click, input, back, scroll...
    widget: str                 # Target widget name
    input_text: str             # Input text (for input operation)
    thought: str                # LLM reasoning process
    page_description: str       # Page description from LLM
    function_name: str          # Current function name
    function_status: str        # Function status: testing / tested
    input_sequence: List        # Multiple input sequence
    operation_widget: str       # Post-input operation target
    expected_result: str        # Expected result (bug detection)
    bug_detected: bool          # Bug detected flag
    bug_description: Dict       # Bug description
    target_x: int               # Target center X coordinate
    target_y: int               # Target center Y coordinate
```

---

### 2. GUI Parsing Layer (`gui_extractor/`)

#### GUIAnalyzer (`xml_parser.py`)

| Method | Function |
|--------|----------|
| `parse_xml(xml_path, target_package)` | Parse UI XML, return widget list |
| `_extract_widget_info(node)` | Extract single widget info |
| `_filter_widget(widget, target_package)` | Filter valid widgets |

**Widget Info Structure:**
```python
{
    "text": "Login",                      # Display text
    "resource_id": "com.app:id/btn_login",# Resource ID
    "class": "android.widget.Button",     # Widget type
    "bounds": "[100,200][300,250]",       # Coordinate range
    "clickable": True,                    # Clickable flag
    "content_desc": "",                   # Content description
    "activity": "LoginActivity"           # Activity
}
```

---

### 3. LLM Agent Layer (`llm_agent/`)

#### PromptGenerator (`prompt_builder.py`)

Core entry point for building all prompts:

```python
class PromptGenerator:
    def __init__(self, memory_manager):
        self.memory = memory_manager
        self.supervisor_suggestions: Dict[str, str] = {}  # NEW

    def set_supervisor_suggestions(self, suggestions):
        """Set supervisor suggestions (merge mode)"""

    def build_system_prompt(self) -> str:
        """Build system prompt (LLM role definition)"""

    def build_initial_prompt(self, widgets, activity_name) -> str:
        """Build initial phase prompt (first step)"""

    def build_test_prompt(self, widgets, activity_name) -> str:
        """Build test phase prompt (after success)"""

    def build_feedback_prompt(self, widgets, activity_name, failed_widget) -> str:
        """Build feedback prompt (after failure)"""

    def _build_supervisor_suggestion_section(self, activity_name) -> str:
        """Build supervisor suggestion section (NEW)"""
```

#### TestingSequenceMemorizer (`memory_manager.py`)

**Core Data Structures:**
```python
class TestingSequenceMemorizer:
    app_name: str                              # App name
    activity_info: Dict[str, Dict]             # {activity: {visits, status}}
    widget_visits: Dict[str, Dict[str, int]]   # {activity: {widget: visits}}
    explored_functions: Dict[str, Dict]        # {function: {visits, status}}
    operation_history: deque                   # Operation history
    last_expected_result: str                  # Expected result (bug detection)
```

#### SupervisorModel (`supervisor.py`)

```python
class SupervisorModel:
    def __init__(self, multimodal_llm, screenshot_manager, review_interval=10):
        self.multimodal_llm = multimodal_llm
        self.screenshot_manager = screenshot_manager
        self.review_interval = review_interval  # Review every N steps

    def check_false_positive(self, bug_report, context, screenshots) -> ReviewResult:
        """Review bug report for false positive"""

    def check_missed_bugs(self, context, screenshots) -> ReviewResult:
        """Detect bugs that may have been missed"""

    def should_trigger_review(self, current_step) -> bool:
        """Check if periodic review should trigger"""

    def _log_review_result(self, result, review_name):
        """Log review process and results"""
```

---

## Prompt System

### Complete Prompt Structure

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         LLM Prompt Complete Structure                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    System Prompt                                 │   │
│  ├─────────────────────────────────────────────────────────────────┤   │
│  │ - LLM Role: Professional software tester                         │   │
│  │ - Action types: click, double-click, long press, input, back... │   │
│  │ - Mandatory JSON output format                                   │   │
│  │ - Bug detection fields: Expected_Result, Bug_Detected            │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    User Prompt                                   │   │
│  ├─────────────────────────────────────────────────────────────────┤   │
│  │                                                                  │   │
│  │  [NEW] Supervisor's Suggestions (if any)                         │   │
│  │  ## ⚠️ Supervisor's Suggestions                                  │   │
│  │  **You MUST consider these suggestions before making decision:** │   │
│  │  👉 **MainActivity**: Suggestion text                            │   │
│  │                                                                  │   │
│  │  ┌───────────────────────────────────────────────────────────┐  │   │
│  │  │                 GUIContext                                 │  │   │
│  │  ├───────────────────────────────────────────────────────────┤  │   │
│  │  │ [1] App Information                                        │  │   │
│  │  │ [2] Page Information (Current Activity)                    │  │   │
│  │  │ [3] Widget Information (with positions)                    │  │   │
│  │  │ [4] Action Operation Question                              │  │   │
│  │  │ [5] Input Operation Question                               │  │   │
│  │  └───────────────────────────────────────────────────────────┘  │   │
│  │                                                                  │   │
│  │  ┌───────────────────────────────────────────────────────────┐  │   │
│  │  │                 FunctionMemory                              │  │   │
│  │  ├───────────────────────────────────────────────────────────┤  │   │
│  │  │ [1] Explored Functions                                     │  │   │
│  │  │ [2] Covered Activities                                     │  │   │
│  │  │ [3] Latest Test History                                    │  │   │
│  │  │ [4] Function Query                                         │  │   │
│  │  └───────────────────────────────────────────────────────────┘  │   │
│  │                                                                  │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### LLM Output Format

**Normal Operation:**
```json
{
  "Page_Description": "Current screen shows login form with username and password fields",
  "Function": "Login",
  "Status": "Yes",
  "Operation": "click",
  "Widget": "Login",
  "WidgetType": "Button",
  "TargetX": 540,
  "TargetY": 800,
  "Expected_Result": "Navigate to home page",
  "Bug_Detected": false,
  "Bug_Description": null
}
```

**Bug Detected:**
```json
{
  "Page_Description": "Balance shows 600 instead of expected 400",
  "Function": "Recharge",
  "Status": "No",
  "Operation": "click",
  "Widget": "submit_button",
  "Expected_Result": "Balance should double to 400",
  "Bug_Detected": true,
  "Bug_Description": {
    "type": "calculation_error",
    "severity": "Error",
    "description": "Balance calculation error: expected 200*2=400, actual 600"
  }
}
```

---

## Main Program Flow

### Outer Loop

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           Outer Loop Main Cycle                           │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         │                          │                          │
         ▼                          ▼                          ▼
   ┌──────────┐              ┌──────────┐              ┌──────────┐
   │  Step 1  │              │  Step 2  │    ...       │  Step N  │
   └──────────┘              └──────────┘              └──────────┘
         │
         ├─── a. Capture UI layout          (ADBController.dump_ui)
         │
         ├─── b. Parse UI widgets           (GUIAnalyzer.parse_xml)
         │
         ├─── c. Get current Activity       (ADBController.get_current_activity)
         │
         ├─── d. Generate prompt            (PromptGenerator.build_*_prompt)
         │        │
         │        └─── Include supervisor suggestions (if any)
         │
         ├─── e. Get LLM decision           (LLMClient.get_decision)
         │
         ├─── [NEW] Parse response, check Bug_Detected
         │        │
         │        └─── if Bug_Detected:
         │             ├─► Capture screenshot (T0 state)
         │             ├─► Call Supervisor.check_false_positive()
         │             ├─► if True Bug: Save Bug Report
         │             └─► if False Positive: Log, continue
         │
         ├─── f. Execute action             (ActionExecutor.execute_action)
         │        │
         │        ├─── Parse LLM response
         │        ├─── Find widget coordinates
         │        ├─── Execute operation
         │        └─── Check for crash
         │
         ├─── g. State differential check   (UI fingerprint comparison)
         │
         ├─── h. Update memory              (TestingSequenceMemorizer)
         │
         ├─── [NEW] Periodic Supervisor review (every 10 steps)
         │        │
         │        └─── Supervisor.check_missed_bugs()
         │             ├─► Pass suggestions to PromptGenerator
         │             └─► Log missed bugs if any
         │
         └─── i. Continue or terminate
                  │
                  ├─── Normal: Continue to next step
                  ├─── Crash: Terminate loop
                  └─── Critical Bug: Terminate loop
```

---

## Quick Start

### 1. Install Dependencies

```bash
pip install openai uiautomator2
```

### 2. Configure Environment Variables

```bash
# Basic LLM configuration
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="gpt-4o"

# Multimodal LLM configuration (for Supervisor)
export MULTIMODAL_API_KEY="your-api-key"
export MULTIMODAL_BASE_URL="https://api.openai.com/v1"
export MULTIMODAL_MODEL="gpt-4o"
```

### 3. Connect Android Device

```bash
adb devices  # Ensure device is connected
```

### 4. Run Test

```bash
python main.py
```

---

## Configuration

Adjustable in `main.py`:

```python
MAX_STEPS = 300           # Maximum exploration steps
STEP_WAIT_TIME = 2        # Wait time after each operation (seconds)

# Supervisor configuration (in supervisor initialization)
review_interval = 10      # Periodic review every N steps
min_confidence = 0.7      # Minimum confidence threshold
```

---

## Output Files

| File/Directory | Description |
|----------------|-------------|
| `temp_data/current_ui.xml` | Latest captured UI layout |
| `temp_data/screenshots/` | Screenshots storage directory |
| `temp_data/test_history.log` | Test history log (includes Supervisor reviews) |
| `bug_reports/*.json` | Bug reports (JSON format) |
| `bug_reports/*.md` | Bug reports (Markdown format) |

---

## Technical Stack

- **Python 3.10+**
- **ADB (Android Debug Bridge)**
- **UIAutomator2** - Advanced widget operations
- **OpenAI API** - Compatible with DeepSeek, Claude, etc.
- **ReAct Architecture** (Reasoning and Acting)
- **Multimodal LLM** - Image + text analysis
- **Dual-Agent Architecture** - Explorer + Supervisor

---

## Architecture Design Reference

This project follows the core design of the GPTDroid paper:

1. **ReAct Paradigm**: Reasoning → Acting
2. **Three-Phase Prompts**: Initial → Test → Feedback
3. **Memory System**: TestingSequenceMemorizer (operation history, function tracking, expected results)
4. **Bug Oracle**: Crash detection + Prompt-based bug detection + Supervisor review
5. **Dual-Agent Architecture**: Independent Supervisor for quality assurance

---

## License

MIT