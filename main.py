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
from typing import Dict

from env_interactor import ADBController, ActionExecutor
from gui_extractor import GUIAnalyzer
from llm_agent import PromptGenerator, LLMClient, TestingSequenceMemorizer
from llm_agent.exploration_cache import ExplorationCache


# ==================== 配置参数 ====================
MAX_STEPS = 30           # 最大探索步数
STEP_WAIT_TIME = 2      # 每步操作后的等待时间（秒）
TARGET_PACKAGE = ""      # 目标应用包名（空字符串表示不启动特定应用）


def print_substep_header(substep_name: str) -> None:
    """打印子步骤标题"""
    print(f"\n>>> {substep_name}")
    print("-" * 50)


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
    # ========== 初始化阶段 ==========
    print("\n" + "=" * 70)
    print("  GPTDroid - LLM 驱动的移动端 GUI 自动化测试")
    print("  全自主探索模式 - 功能感知记忆机制")
    print("=" * 70)

    # 初始化各组件
    print("\n[初始化] 正在初始化测试组件...")

    adb_controller = ADBController()
    gui_analyzer = GUIAnalyzer()

    # 初始化全局探索缓存
    exploration_cache = ExplorationCache()

    # 将探索缓存传递给 PromptGenerator
    prompt_generator = PromptGenerator(exploration_cache=exploration_cache)

    llm_client = LLMClient()
    action_executor = ActionExecutor()
    memory_manager = TestingSequenceMemorizer()

    print("[初始化] 组件初始化完成")

    # 清空日志缓冲区（准备崩溃检测）
    print("\n[日志管理] 清空 logcat 缓冲区...")
    adb_controller.clear_logcat()

    # 启动目标应用（如果配置了）
    if TARGET_PACKAGE:
        print(f"\n[应用启动] 目标应用: {TARGET_PACKAGE}")
        launch_success = adb_controller.launch_app(TARGET_PACKAGE)
        if not launch_success:
            print("[警告] 应用启动失败，将在当前界面继续测试")
    else:
        print("\n[探索模式] 未指定目标应用，将在当前界面进行自主探索")

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
        print("\n" + "=" * 70)
        print(f"  当前探索步骤: 第 {step} 步")
        print("=" * 70)

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

            widgets = gui_analyzer.parse_xml(ui_file)

            if not widgets:
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

            # ---------- d. 生成 Test Prompt ----------
            print_substep_header("d. 生成 Test Prompt")

            test_prompt = prompt_generator.build_test_prompt(
                current_activity, widgets, memory_manager
            )

            print("[成功] Test Prompt 已生成")

            # ---------- e. 获取 LLM 决策 ----------
            print_substep_header("e. 获取 LLM 决策")

            llm_response = llm_client.get_decision(test_prompt)

            print(f"[成功] LLM 响应: {llm_response}")

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
                    activity_name=current_activity
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

                    # ========== 关键修复：失败也要更新记忆 ==========
                    # 让 LLM 知道上一次操作失败了，避免重复选择同样的控件
                    if parsed_action:
                        memory_manager.update_step(
                            activity_name=current_activity,
                            operation=parsed_action.operation,
                            widget_name=parsed_action.widget,
                            success=False,  # 标记为失败
                            effect_status="FAILED"  # 执行失败
                        )
                        print(f"[记忆更新] 已记录失败操作: {parsed_action.widget}")

                    results["step_details"].append(step_result)
                    continue

                print("[成功] 动作执行完成")

                # 检测是否发生崩溃
                if action_executor.last_crash_detected:
                    print("[崩溃检测] 发现应用崩溃！终止测试循环")
                    step_result["status"] = "crashed"
                    step_result["error"] = "Application crashed"
                    results["failed_steps"] += 1
                    results["crashed"] = True
                    results["step_details"].append(step_result)
                    break

                # 获取解析结果用于记忆更新
                operation = parsed_action.operation if parsed_action else None
                widget_name = parsed_action.widget if parsed_action else None

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
            effect_status = "OK"  # 默认成功

            try:
                ui_file_after = adb_controller.dump_ui()
                if ui_file_after:
                    widgets_after = gui_analyzer.parse_xml(ui_file_after)
                    activity_after = adb_controller.get_current_activity()

                    state_after = compute_ui_state_fingerprint(widgets_after)

                    # 状态差分比较
                    if state_before == state_after and activity_before == activity_after:
                        # 状态完全相同，说明动作无效
                        effect_status = "NO_EFFECT"
                        print(f"[状态差分] ⚠️ 检测到无效操作：UI 状态未发生变化")
                        print(f"[状态差分] 控件 '{widget_name}' 可能是不可点击的假按钮")
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

            memory_manager.update_step(
                activity_name=current_activity,
                operation=operation,
                widget_name=widget_name,
                success=True,  # 执行成功
                effect_status=effect_status  # 效果状态：OK 或 NO_EFFECT
            )

            # ========== 全局探索缓存记录 + 无效操作黑名单管理 ==========
            if effect_status == "OK" and operation and widget_name:
                # 操作有效：记录到探索缓存
                exploration_cache.record_exploration(current_activity, widget_name)
                # 状态刷新法则：操作有效时，清空当前页面的无效操作黑名单
                exploration_cache.clear_blacklist(current_activity)

            elif effect_status == "NO_EFFECT" and widget_name:
                # 操作无效：将控件添加到黑名单，下次不再让 LLM 选择
                exploration_cache.add_to_blacklist(current_activity, widget_name)

            print(f"[成功] 记忆已更新，当前步骤数: {memory_manager.get_step_count()}")

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