"""
GPTDroid 测试入口 - Outer Loop 主程序
实现 LLM 全自主驱动的自动化测试探索

完整流程：
1. 清空日志缓冲区，准备崩溃检测
2. 循环执行探索步骤，每步由 LLM 完全自主决策：
   a. ADB 环境交互层 - 抓取 UI 布局
   b. GUI 上下文提取层 - 解析 UI 控件
   c. 获取当前 Activity 名称
   d. 大模型提示词构建层 - 生成 Test Prompt（含记忆）
   e. 大模型交互层 - 获取 LLM 决策
   f. 动作执行层 - 执行操作并检测崩溃
   g. 状态差分检测 - 验证动作是否有效
   h. 记忆更新 - 记录测试历史（含效果反馈）
3. 输出测试总结报告
"""

import time
import json
from typing import Dict
from pathlib import Path

from env_interactor import ADBController, ActionExecutor
from gui_extractor import GUIAnalyzer, ManifestParser
from llm_agent import PromptGenerator, LLMClient, TestingSequenceMemorizer, UserContext  # NEW: UserContext
from llm_agent.supervisor import SupervisorModel
from llm_agent.bug_analysis_engine import BugAnalysisEngine, BugReport, BugSeverity, BugCategory  # NEW: Bug 报告类型
from llm_agent.multimodal_llm_client import MultimodalLLMClient  # NEW: 多模态 LLM
from llm_agent.screenshot_manager import ScreenshotManager, ScreenshotData  # NEW: 截图管理
from llm_agent.exploration_cache import ExplorationCache
from llm_agent.test_logger import get_logger


# ==================== 配置参数 ====================
MAX_STEPS = 300           # 最大探索步数
STEP_WAIT_TIME = 1   # 每步操作后的等待时间（秒）


# ==================== 交互式输入函数 ====================

def get_user_input(default_app_name: str = "") -> UserContext:
    """
    获取用户输入的测试上下文信息

    Args:
        default_app_name: 默认应用名称（从 Manifest 解析）

    Returns:
        UserContext: 用户输入的上下文信息
    """
    print("\n" + "=" * 60)
    print("  测试配置")
    print("=" * 60)

    # 1. 应用名称
    default_display = default_app_name or "未知应用"
    app_name_input = input(f"应用名称 [{default_display}]: ").strip()
    app_name = app_name_input if app_name_input else default_app_name

    # 2. 用户自定义说明（一句话）
    print("\n请输入测试说明（可选，直接回车跳过）:")
    print("例如: 重点测试登录和支付功能")
    user_note = input("> ").strip()

    # 构建并返回用户上下文
    user_context = UserContext(
        app_name=app_name,
        user_note=user_note
    )

    # 显示配置摘要
    print("\n" + "-" * 60)
    print(f"应用: {user_context.app_name}")
    if user_context.user_note:
        print(f"说明: {user_context.user_note}")
    print("-" * 60)

    return user_context


def _handle_llm_bug_report(
    components,
    parsed_action,
    current_activity: str,
    screenshot_data,
    step_result: Dict,
    results: Dict,
    supervisor,
    memory_manager
) -> bool:
    """
    处理 LLM 报告的 Bug（集成监管者审查）

    当 parsed_action.bug_detected == True 时触发，暂停测试循环，
    由监管者审查 Bug 的真实性。

    Args:
        components: 测试组件集合（包含 bug_analysis_engine 等）
        parsed_action: 解析后的动作，包含 bug_description
        current_activity: 当前 Activity
        screenshot_data: 当前截图数据
        step_result: 步骤结果字典
        results: 测试结果汇总字典
        supervisor: 监管者模型实例
        memory_manager: 记忆管理器实例

    Returns:
        bool: True 表示应终止测试，False 表示继续测试
    """
    from datetime import datetime as dt

    bug_desc = parsed_action.bug_description or {}
    bug_type = bug_desc.get("type", "unknown")
    bug_severity = bug_desc.get("severity", "Error")
    bug_message = bug_desc.get("description", "Bug detected by LLM")

    print(f"\n{'!' * 60}")
    print(f"[Bug检测] LLM 发现 Bug!")
    print(f"   类型: {bug_type}, 严重程度: {bug_severity}")
    print(f"   描述: {bug_message[:80]}...")

    # 映射严重程度到 BugSeverity
    severity_map = {
        "Critical": BugSeverity.CRITICAL,
        "Error": BugSeverity.ERROR,
        "Warning": BugSeverity.WARNING,
        "Info": BugSeverity.INFO
    }
    mapped_severity = severity_map.get(bug_severity, BugSeverity.ERROR)

    # 映射 Bug 类型到 BugCategory
    category_map = {
        "crash": BugCategory.CRASH,
        "calculation_error": BugCategory.CALCULATION_ERROR,
        "data_inconsistency": BugCategory.DATA_INCONSISTENCY,
        "function_anomaly": BugCategory.FUNCTION_ANOMALY,
        "unknown": BugCategory.UNKNOWN
    }
    mapped_category = category_map.get(bug_type, BugCategory.UNKNOWN)

    # 构建 BugReport
    # 包含所有历史操作记录（用于完整复现路径）
    all_operation_history = list(memory_manager.operation_history)

    current_expected_result = parsed_action.expected_result or memory_manager.get_expected_result()

    bug_report = BugReport(
        bug_id=f"BUG-{dt.now().strftime('%Y%m%d')}-{memory_manager.get_step_count():04d}",
        timestamp=dt.now(),
        severity=mapped_severity,
        category=mapped_category,
        title=bug_message[:100],
        description=bug_message,
        activity=current_activity,
        operation=parsed_action.operation or "",
        widget=parsed_action.widget or "",
        screenshot_paths=[str(screenshot_data.path)] if screenshot_data else [],
        additional_info={
            "detected_by": "explorer_llm",
            "source": "explorer_bug_assertion",
            "expected_result": current_expected_result,
            "page_description": parsed_action.page_description,
            "llm_thought": parsed_action.thought,
            "function_name": parsed_action.function_name,
        },
        operation_history=all_operation_history  # 包含所有历史操作
    )

    # ==================== 监管者审查 ====================
    print("\n[监管者] 暂停测试，调用监管者审查 Bug 报告...")

    context = {
        'operation_history': list(memory_manager.operation_history),
        # Bug 断言发生在执行动作之前，此时当前 LLM 响应还没进入 memory。
        # 因此优先使用本次响应中的 Expected_Result，再回退到上一轮记忆。
        'last_expected_result': current_expected_result,
        'page_description': parsed_action.page_description,
        'current_activity': current_activity,
    }

    review_result = supervisor.check_false_positive(
        bug_report=bug_report,
        context=context,
        screenshots=[screenshot_data] if screenshot_data else None
    )

    # 处理审查结果
    if review_result.is_false_positive:
        print(f"[监管者] 判定为假阳性：{review_result.false_positive_reason}")
        print("[监管者] 跳过此 Bug 报告，继续测试")

        # 记录假阳性案例供学习
        memory_manager.record_false_positive_case(
            bug_description=bug_message,
            reason=review_result.false_positive_reason,
            confidence=review_result.confidence
        )

        return False  # 不终止测试

    print(f"[监管者] 确认真实 Bug: {review_result.reasoning}")

    # ========== 立即保存真实 Bug 报告（包含所有历史操作）==========
    print(f"\n[Bug报告] 立即保存真实 Bug 报告...")
    print(f"[Bug报告] 包含 {len(all_operation_history)} 条历史操作记录")

    # 加载截图为 base64
    bug_report.load_screenshots_as_base64()

    # 保存 Bug 报告
    report_dir = Path("bug_reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    # 保存 Markdown 格式
    md_path = report_dir / f"{bug_report.bug_id}.md"
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(bug_report.to_markdown())
    print(f"[Bug报告] Markdown 已保存: {md_path}")

    # 保存 JSON 格式（包含完整数据，便于程序读取）
    json_path = report_dir / f"{bug_report.bug_id}.json"
    with open(json_path, 'w', encoding='utf-8') as json_file:
        json.dump(bug_report.to_dict(), json_file, ensure_ascii=False, indent=2)
    print(f"[Bug报告] JSON 已保存: {json_path}")

    # 记录到测试日志
    from llm_agent.test_logger import get_logger
    bug_logger = get_logger()
    bug_logger.log(f"真实 Bug 已保存: {bug_report.bug_id}", "ERROR")
    bug_logger.log(f"  类型: {mapped_category.value}, 严重程度: {mapped_severity.value}", "ERROR")
    bug_logger.log(f"  位置: {current_activity} - {parsed_action.operation}", "ERROR")
    bug_logger.log(f"  描述: {bug_message[:100]}...", "ERROR")

    results["bug_count"] = results.get("bug_count", 0) + 1

    # 严重 Bug 终止测试
    if bug_report.severity == BugSeverity.CRITICAL:
        print("[Bug检测] 发现严重 Bug，终止测试循环")
        return True

    return False


def print_substep_header(substep_name: str) -> None:
    """打印子步骤标题"""
    print(f"\n>>> {substep_name}")
    print("-" * 50)
    # 同时写入日志文件
    logger = get_logger()
    logger.subsection(substep_name)


def compute_ui_state_fingerprint(widgets: list) -> str:
    """
    计算 UI 状态指纹

    基于控件列表生成唯一的字符串标识，用于状态差分比较

    Args:
        widgets: 控件列表

    Returns:
        UI 状态指纹字符串
    """
    if not widgets:
        return ""

    # 提取每个控件的关键属性并排序，确保顺序一致
    fingerprints = []
    for w in widgets:
        text = w.get("text", "") or ""
        rid = w.get("resource_id", "") or ""
        bounds = w.get("bounds", "") or ""
        # 组合关键属性
        fp = f"{text}|{rid}|{bounds}"
        fingerprints.append(fp)

    # 排序后拼接
    fingerprints.sort()
    return "||".join(fingerprints)


def run_outer_loop() -> Dict:
    """
    执行完整的测试 Outer Loop

    Returns:
        测试结果汇总字典
    """
    # ========== 初始化日志 ==========
    logger = get_logger(log_file=Path("temp_data/test_history.log"))
    logger.section("GPTDroid 测试开始")

    # ========== 初始化阶段 ==========
    logger.subsection("组件初始化")

    adb_controller = ADBController()
    gui_analyzer = GUIAnalyzer()

    adb_controller = ADBController()
    gui_analyzer = GUIAnalyzer()

    # 初始化全局探索缓存（每次启动清空）
    exploration_cache = ExplorationCache()
    exploration_cache.clear_cache()

    # 初始化记忆管理器（唯一数据源）
    memory_manager = TestingSequenceMemorizer()

    # 将记忆管理器传递给 PromptGenerator
    prompt_generator = PromptGenerator(
        memory_manager=memory_manager
    )

    llm_client = LLMClient()

    # 初始化截图管理器
    screenshot_manager = ScreenshotManager(adb_controller=adb_controller)

    # 统一 Bug 报告引擎：崩溃和逻辑 Bug 都使用 BugReport 的 JSON/Markdown 结构
    bug_analysis_engine = BugAnalysisEngine(
        adb_controller=adb_controller,
        screenshot_manager=screenshot_manager
    )

    # 动作执行器注入 BugAnalysisEngine，避免崩溃时只生成旧版 TXT 报告
    action_executor = ActionExecutor(
        bug_analysis_engine=bug_analysis_engine,
        screenshot_manager=screenshot_manager
    )

    # ========== NEW: 初始化监管者组件 ==========
    # 初始化多模态 LLM 客户端（监管者使用）
    multimodal_llm = MultimodalLLMClient()

    # 初始化监管者模型
    supervisor = SupervisorModel(
        multimodal_llm=multimodal_llm,
        screenshot_manager=screenshot_manager,
        review_interval=10,
        min_confidence=0.7
    )
    print("[监管者] Supervisor 初始化完成")

    # 获取当前应用信息
    print("\n[应用信息] 正在获取当前应用...")
    current_package = adb_controller.get_current_package()
    target_package = current_package if current_package and current_package != "unknown.package" else ""
    manifest_parser = ManifestParser()
    app_info = manifest_parser.get_or_parse(current_package)

    # 获取默认应用名称
    default_app_name = app_info.app_name if app_info else current_package.split('.')[-1]

    # ========== 获取用户输入的测试配置 ==========
    user_context = get_user_input(default_app_name=default_app_name)
    prompt_generator.set_user_context(user_context)

    if app_info:
        print(f"[应用信息] 包名: {app_info.package_name}")
        print(f"[应用信息] Activity 数量: {len(app_info.activities)}")

        # 注册所有 Activities 到 memory_manager（提取 name 属性）
        activity_names = [a.name for a in app_info.activities]
        memory_manager.register_activities(activity_names)
        print(f"[应用信息] 已注册 {len(activity_names)} 个 Activities")
    else:
        print(f"[应用信息] 无法解析 Manifest")

    print("[初始化] 组件初始化完成")

    # 清空日志缓冲区（准备崩溃检测）
    print("\n[日志管理] 清空 logcat 缓冲区...")
    adb_controller.clear_logcat()



    # ========== 主循环 ==========
    print("\n" + "=" * 70)
    print(f"  开始执行 LLM 自主探索 (最大步数: {MAX_STEPS})")
    print("=" * 70)

    # 测试结果统计
    results = {
        "total_steps": MAX_STEPS,
        "successful_steps": 0,
        "failed_steps": 0,
        "skipped_steps": 0,
        "crashed": False,
        "step_details": []
    }

    # 记录是否为第一步
    is_first_step = True

    for step in range(1, MAX_STEPS + 1):
        step_result = {
            "step_index": step,
            "status": "pending",
            "activity": "unknown",
            "operation": None,
            "widget": None,
            "error": None
        }

        # 打印当前探索步骤
        step_header = f"第 {step} 步"
        print("\n" + "=" * 70)
        print(f"  当前探索步骤: {step_header}")
        print("=" * 70)

        # 同时记录到日志
        logger.section(f"探索步骤: {step_header}")

        try:
            # ---------- a. 抓取 UI 布局 ----------
            print_substep_header("a. 抓取 UI 布局")

            ui_file = adb_controller.dump_ui()

            if not ui_file:
                print("[警告] UI 布局抓取失败，跳过当前步骤")
                step_result["status"] = "skipped"
                step_result["error"] = "UI dump failed"
                results["skipped_steps"] += 1
                results["step_details"].append(step_result)
                continue

            print(f"[成功] UI 布局已保存: {ui_file}")

            # ---------- b. 解析 UI 控件 ----------
            print_substep_header("b. 解析 UI 控件")

            widgets = gui_analyzer.parse_xml(ui_file, target_package=target_package)

            if not widgets:
                if gui_analyzer.is_system_page_detected():
                    detected_package = gui_analyzer.get_detected_package() or "unknown"
                    print(f"[外部页面] 检测到已跳转到非目标页面: {detected_package}")
                    print("[外部页面] 按 Back 返回被测应用，下一步继续探索")
                    adb_controller.go_back()
                    time.sleep(STEP_WAIT_TIME)
                    step_result["status"] = "skipped"
                    step_result["error"] = f"External/system page detected: {detected_package}"
                    results["skipped_steps"] += 1
                    results["step_details"].append(step_result)
                    continue

                print("[警告] 未提取到有效控件，跳过当前步骤")
                step_result["status"] = "skipped"
                step_result["error"] = "No widgets found"
                results["skipped_steps"] += 1
                results["step_details"].append(step_result)
                continue

            print(f"[成功] 提取到 {len(widgets)} 个有效控件")

            # 打印前 3 个控件概览
            print("[控件概览] 前 3 个控件:")
            for j, widget in enumerate(widgets[:3], 1):
                text = widget.get("text", "(无文本)")
                rid = widget.get("resource_id", "(无ID)")
                print(f"  {j}. 文本: {text}, ID: {rid}")

            # ---------- c. 获取当前 Activity ----------
            print_substep_header("c. 获取当前 Activity")

            current_activity = adb_controller.get_current_activity()
            step_result["activity"] = current_activity

            print(f"[成功] 当前 Activity: {current_activity}")

            # ---------- d. 生成 Prompt（根据阶段选择）----------
            print_substep_header("d. 生成 Prompt")

            if is_first_step:
                # 第一步：使用初始提示词（完整上下文）
                print("[提示词] 使用初始提示词（第一步）")
                test_prompt = prompt_generator.build_initial_prompt(
                    widgets, current_activity
                )
                is_first_step = False
            else:
                # 后续步骤：使用测试提示词（包含成功信息）
                print("[提示词] 使用测试提示词（后续步骤）")
                test_prompt = prompt_generator.build_test_prompt(
                    widgets, current_activity
                )

            print("[成功] Prompt 已生成")

            # ---------- e. 获取 LLM 决策 ----------
            print_substep_header("e. 获取 LLM 决策")

            # 获取系统提示词
            system_prompt = prompt_generator.build_system_prompt()

            llm_response = llm_client.get_decision(test_prompt, system_prompt)

            print(f"[成功] LLM 响应: {llm_response}")

            # 记录到日志
            logger.log(f"LLM 决策: {llm_response[:200]}...")

            # ========== NEW: 先解析 LLM 响应检查 Bug（不执行动作）==========
            # Bug 断言审查必须基于触发断言时的上下文快照，而不是执行后续动作后的新状态
            parsed_action_preview = action_executor.parse_action_only(llm_response)

            if parsed_action_preview and parsed_action_preview.bug_detected:
                # Bug 检测：立即在当前状态截图（触发断言时的上下文快照）
                print(f"\n{'!' * 60}")
                print(f"[Bug检测] LLM 发现 Bug!（在执行动作之前）")
                print(f"   类型: {parsed_action_preview.bug_description.get('type', 'unknown')}")
                print(f"   描述: {parsed_action_preview.bug_description.get('description', 'N/A')[:80]}...")
                logger.log(f"LLM 报告 Bug（审查前）: {parsed_action_preview.bug_description}", "WARNING")

                # 在当前状态截图（触发断言时的上下文快照，而非执行动作后的新状态）
                context_snapshot = screenshot_manager.capture(activity_name=current_activity)
                print(f"[监管者] 使用当前状态截图进行审查（触发断言时的上下文）")

                # 调用监管者审查
                should_terminate = _handle_llm_bug_report(
                    components=None,
                    parsed_action=parsed_action_preview,
                    current_activity=current_activity,
                    screenshot_data=context_snapshot,
                    step_result=step_result,
                    results=results,
                    supervisor=supervisor,
                    memory_manager=memory_manager
                )

                if should_terminate:
                    step_result["status"] = "bug_terminated"
                    step_result["error"] = "Critical bug detected, test terminated"
                    results["step_details"].append(step_result)
                    break

                # 如果监管者判定为假阳性，记录后继续执行动作
                # 如果监管者判定为真实 Bug，已记录，继续执行动作

            # ---------- f. 执行动作 ----------
            print_substep_header("f. 执行动作")

            # ========== 工业级崩溃检测：每步操作前清空 logcat 缓存 ==========
            # 确保只捕获当前动作产生的增量日志，从源头减少日志分析负担
            print("[日志管理] 清空 logcat 缓冲区，准备捕获增量日志...")
            adb_controller.clear_logcat()

            # ========== 记录前置状态（状态差分第一步）==========
            state_before = compute_ui_state_fingerprint(widgets)
            activity_before = current_activity

            try:
                # 安全接收返回值
                execute_result = action_executor.execute_action(
                    llm_response, widgets, adb_controller,
                    memory_manager=memory_manager,
                    activity_name=current_activity,
                    target_package=target_package
                )

                # 安全取值
                success = execute_result[0] if execute_result else False
                parsed_action = execute_result[1] if len(execute_result) > 1 else None

                # 记录解析结果
                if parsed_action:
                    step_result["operation"] = parsed_action.operation
                    step_result["widget"] = parsed_action.widget

                if not success:
                    print("[警告] 动作执行失败")
                    step_result["status"] = "failed"
                    step_result["error"] = "Action execution failed"
                    results["failed_steps"] += 1

                    # 检查是否为"控件未找到"错误，如果是则使用反馈提示词重试
                    failed_widget = parsed_action.widget if parsed_action else None

                    # 记录失败操作
                    memory_manager.update_step(
                        activity_name=current_activity,
                        operation=parsed_action.operation if parsed_action else "unknown",
                        widget_name=failed_widget or "unknown",
                        success=False
                    )
                    print(f"[记忆更新] 已记录失败操作: {failed_widget}")

                    # 如果是控件未找到，尝试使用反馈提示词让 LLM 重选
                    if failed_widget:
                        print("[重试] 控件未找到，使用反馈提示词让 LLM 重新选择...")
                        feedback_prompt = prompt_generator.build_feedback_prompt(
                            widgets, current_activity, failed_widget
                        )

                        # 获取 LLM 重新决策（使用相同的系统提示词）
                        retry_response = llm_client.get_decision(feedback_prompt, system_prompt)
                        print(f"[重试] LLM 响应: {retry_response}")

                        # 尝试执行重试决策
                        retry_result = action_executor.execute_action(
                            retry_response, widgets, adb_controller,
                            memory_manager=memory_manager,
                            activity_name=current_activity,
                            target_package=target_package
                        )

                        retry_success = retry_result[0] if retry_result else False
                        retry_action = retry_result[1] if retry_result and len(retry_result) > 1 else None

                        if retry_success:
                            print("[重试成功] LLM 重新选择的操作执行成功")
                            # 使用重试的操作信息
                            parsed_action = retry_action
                            success = True
                            step_result["status"] = "success"
                            results["successful_steps"] += 1
                            results["failed_steps"] -= 1  # 恢复计数
                        else:
                            print("[重试失败] LLM 重新选择仍然失败，跳过此步骤")

                    if not success:
                        results["step_details"].append(step_result)
                        continue

                print("[成功] 动作执行完成")

                # 记录到日志
                if parsed_action:
                    logger.log(f"执行动作: {parsed_action.operation} -> {parsed_action.widget}", "SUCCESS")

                # 检测是否发生崩溃
                if action_executor.last_crash_detected:
                    print("[崩溃检测] 发现应用崩溃！终止测试循环")
                    logger.log("应用崩溃！终止测试循环", "ERROR")
                    step_result["status"] = "crashed"
                    step_result["error"] = "Application crashed"
                    results["failed_steps"] += 1
                    results["crashed"] = True
                    results["step_details"].append(step_result)
                    break

                # 获取解析结果用于记忆更新
                operation = parsed_action.operation if parsed_action else None
                widget_name = parsed_action.widget if parsed_action else None

                # 更新 LLM 返回的功能信息（如果有）
                print(f"[DEBUG] parsed_action: {parsed_action}")
                if parsed_action:
                    print(f"[DEBUG] parsed_action.function_name: {parsed_action.function_name}")

                if parsed_action and parsed_action.function_name:
                    # 从 LLM 响应中获取功能信息
                    print(f"[DEBUG] 从 LLM 获取功能: {parsed_action.function_name}")
                    memory_manager.update_function(
                        parsed_action.function_name,
                        parsed_action.function_status or "testing"
                    )
                else:
                    # 后备机制：从 Activity 名称推断功能
                    print(f"[DEBUG] 从 Activity 推断功能: {current_activity}")
                    inferred_function = memory_manager.infer_function_from_activity(current_activity)
                    print(f"[DEBUG] 推断结果: {inferred_function}")
                    memory_manager.update_function(inferred_function, "testing")

                print(f"[DEBUG] explored_functions: {memory_manager.explored_functions}")

            except Exception as action_error:
                print(f"[异常] 动作执行层发生错误: {action_error}")
                import traceback
                traceback.print_exc()

                step_result["status"] = "failed"
                step_result["error"] = f"Action execution error: {action_error}"
                results["failed_steps"] += 1
                results["step_details"].append(step_result)
                continue

            # ---------- g. 状态差分检测 ----------
            print_substep_header("g. 状态差分检测")

            # 等待 UI 响应
            print(f"[状态检测] 等待 {STEP_WAIT_TIME} 秒让 UI 响应...")
            time.sleep(STEP_WAIT_TIME)

            # 静默抓取后置 UI 状态
            try:
                ui_file_after = adb_controller.dump_ui()
                if ui_file_after:
                    widgets_after = gui_analyzer.parse_xml(ui_file_after, target_package=target_package)
                    activity_after = adb_controller.get_current_activity()

                    state_after = compute_ui_state_fingerprint(widgets_after)

                    # 状态差分比较
                    if state_before == state_after and activity_before == activity_after:
                        print(f"[状态差分] ⚠️ UI 状态未发生变化")
                    else:
                        print(f"[状态差分] ✅ 操作有效：UI 状态已改变")
                        if activity_before != activity_after:
                            print(f"[状态差分] Activity 切换: {activity_before} -> {activity_after}")
                else:
                    print("[状态检测] 无法获取后置 UI，跳过状态差分")
            except Exception as diff_error:
                print(f"[状态检测] 差分检测异常: {diff_error}")

            # ---------- h. 更新记忆 ----------
            print_substep_header("h. 更新记忆")

            # 构建当前操作测试的 Widgets 列表
            # 只包含当前操作的目标 widget，而不是所有历史 widgets
            widgets_tested = []
            if parsed_action and parsed_action.widget:
                widgets_tested.append({
                    "name": parsed_action.widget,
                    "visits": memory_manager.get_widget_visits(current_activity).get(parsed_action.widget, 0)
                })

            # 如果是多输入操作，添加所有输入的 widgets
            if parsed_action and parsed_action.input_sequence:
                for item in parsed_action.input_sequence:
                    # input_sequence 是三元组: (widget_name, input_text, content_desc)
                    widget_name = item[0] if isinstance(item, tuple) else item
                    widgets_tested.append({
                        "name": widget_name,
                        "visits": memory_manager.get_widget_visits(current_activity).get(widget_name, 0)
                    })

            # 记录操作历史
            if parsed_action and parsed_action.expected_result:
                memory_manager.set_expected_result(parsed_action.expected_result)

            memory_manager.record_operation(
                activity_name=current_activity,
                widgets_tested=widgets_tested,
                operation=operation,
                target_widget=widget_name,
                success=True,
                expected_result=parsed_action.expected_result if parsed_action else None,
                page_description=parsed_action.page_description if parsed_action else None
            )

            # 记录到探索缓存
            if operation and widget_name:
                exploration_cache.record_exploration(current_activity, widget_name)

            print(f"[成功] 记忆已更新，当前步骤数: {memory_manager.get_step_count()}")

            # ========== NEW: 定期监管者漏检检测 ==========
            current_step = memory_manager.get_step_count()
            if supervisor.should_trigger_review(current_step):
                print(f"\n[监管者] 触发定期审查 (步骤 {current_step})")

                # 截取当前截图
                review_screenshot = screenshot_manager.capture(activity_name=current_activity)

                latest_history = memory_manager.get_operation_history()
                latest_operation = latest_history[0] if latest_history else None
                pending_verifications = (
                    [latest_operation]
                    if latest_operation and latest_operation.get("expected_result")
                    else []
                )

                review_context = {
                    'current_activity': current_activity,
                    'operation_history': list(memory_manager.operation_history),
                    'pending_verifications': pending_verifications
                }

                review_result = supervisor.check_missed_bugs(
                    context=review_context,
                    screenshots=[review_screenshot] if review_screenshot else None
                )

                # NEW: 传递建议给探索者模型
                if review_result.suggestions:
                    prompt_generator.set_supervisor_suggestions(review_result.suggestions)
                    print(f"[监管者] 已传递 {len(review_result.suggestions)} 条建议给探索者")

                # 处理发现的漏检 Bug
                if review_result.missed_bugs:
                    print(f"[监管者] 发现 {len(review_result.missed_bugs)} 个漏检 Bug")
                    results["missed_bug_count"] = results.get("missed_bug_count", 0) + len(review_result.missed_bugs)

                    # 记录漏检 Bug（可选：保存为 Bug 报告）
                    for bug in review_result.missed_bugs:
                        logger.log(f"漏检 Bug: {bug.get('type', 'unknown')} - {bug.get('description', '')}", "WARNING")
                else:
                    print("[监管者] 未发现漏检 Bug")

            # 标记步骤成功
            step_result["status"] = "success"
            results["successful_steps"] += 1
            results["step_details"].append(step_result)

        except Exception as e:
            print(f"[异常] 步骤 {step} 发生未捕获的异常: {e}")
            import traceback
            traceback.print_exc()

            step_result["status"] = "failed"
            step_result["error"] = str(e)
            results["failed_steps"] += 1
            results["step_details"].append(step_result)

            print("[恢复] 继续执行下一个步骤...")
            continue

    # ========== 测试总结 ==========
    print("\n" + "=" * 70)
    print("  测试完成 - 结果汇总")
    print("=" * 70)

    # 记录到日志
    logger.section("测试完成 - 结果汇总")
    logger.log(f"总步骤数: {results['total_steps']}")
    logger.log(f"成功步骤: {results['successful_steps']}")
    logger.log(f"失败步骤: {results['failed_steps']}")
    logger.log(f"跳过步骤: {results['skipped_steps']}")
    logger.log(f"是否崩溃: {'是' if results['crashed'] else '否'}")

    print(f"\n  总步骤数:   {results['total_steps']}")
    print(f"  成功步骤:   {results['successful_steps']}")
    print(f"  失败步骤:   {results['failed_steps']}")
    print(f"  跳过步骤:   {results['skipped_steps']}")
    print(f"  是否崩溃:   {'是' if results['crashed'] else '否'}")

    actual_steps = results['successful_steps'] + results['failed_steps'] + results['skipped_steps']
    success_rate = results['successful_steps'] / actual_steps * 100 if actual_steps > 0 else 0
    print(f"  成功率:     {success_rate:.1f}%")

    print("\n" + "-" * 70)
    print("  各步骤详情:")
    print("-" * 70)

    for detail in results["step_details"]:
        status_icon = {
            "success": "[OK]",
            "failed": "[FAIL]",
            "skipped": "[SKIP]",
            "crashed": "[CRASH]"
        }.get(detail["status"], "[?]")

        print(f"  步骤 {detail['step_index']}: {status_icon} "
              f"Activity={detail['activity']}, "
              f"操作={detail['operation'] or 'N/A'}, "
              f"控件={detail['widget'] or 'N/A'}")

        if detail["error"]:
            print(f"           错误: {detail['error']}")

    print("\n" + "=" * 70)
    print("  GPTDroid LLM 自主探索测试完成")
    print("=" * 70)

    # 输出日志文件路径
    log_path = logger.get_log_path()
    print(f"\n[日志文件] 测试历史已保存到: {log_path}")
    print(f"[监控提示] 可使用 Claude Code 监控此文件查看实时进度")

    # 关闭日志
    logger.close()

    return results


def main():
    """主函数入口"""
    try:
        run_outer_loop()
    except KeyboardInterrupt:
        print("\n[中断] 用户手动终止测试")
    except Exception as e:
        print(f"\n[致命错误] {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
