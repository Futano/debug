"""
监管者模型模块
独立审查 LLM 决策和 Bug 报告，提供质量监督

功能：
1. 假阳性检测 - 审查 LLM 报告的 Bug 是否真实
2. 漏检检测 - 主动发现未被报告的 Bug
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .bug_analysis_engine import BugReport
    from .screenshot_manager import ScreenshotData

from .multimodal_llm_client import MultimodalLLMClient
from .test_logger import get_logger  # NEW: 导入日志模块


@dataclass
class ReviewResult:
    """
    监管者审查结果

    Attributes:
        review_type: "false_positive_check" or "missed_bug_check"
        is_false_positive: True if the bug report is determined to be false positive
        false_positive_reason: Reason for false positive determination
        missed_bugs: List of missed bugs detected during periodic review
        suggestions: Dict of activity-based suggestions for the explorer model
        confidence: Confidence level of the review (0-1)
        reasoning: Detailed reasoning for the decision
        timestamp: When the review was performed
    """
    review_type: str  # "false_positive_check" or "missed_bug_check"
    is_false_positive: bool = False
    false_positive_reason: str = ""
    missed_bugs: List[Dict] = field(default_factory=list)
    suggestions: Dict[str, str] = field(default_factory=dict)  # NEW: {activity_name: suggestion_text}
    confidence: float = 0.0
    reasoning: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization"""
        return {
            "review_type": self.review_type,
            "is_false_positive": self.is_false_positive,
            "false_positive_reason": self.false_positive_reason,
            "missed_bugs": self.missed_bugs,
            "suggestions": self.suggestions,  # NEW
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "timestamp": self.timestamp.isoformat()
        }


class SupervisorModel:
    """
    监管者模型 - 独立审查 LLM 决策和 Bug 报告

    作为质量监督者，独立审查 LLM 的 Bug 报告，减少假阳性并检测漏检 Bug。

    Features:
    - False positive detection: Review reported bugs for accuracy
    - Missed bug detection: Periodic scan for bugs not reported by LLM
    - Confidence threshold: Only accept reviews above minimum confidence

    Usage:
        supervisor = SupervisorModel(
            multimodal_llm=multimodal_llm,
            screenshot_manager=screenshot_manager,
            review_interval=10
        )

        # False positive check
        result = supervisor.check_false_positive(bug_report, context, screenshots)

        # Periodic missed bug check
        if supervisor.should_trigger_review(step):
            result = supervisor.check_missed_bugs(context, screenshots)
    """

    def __init__(
        self,
        multimodal_llm: MultimodalLLMClient,
        screenshot_manager: Optional["ScreenshotManager"] = None,
        review_interval: int = 10,
        min_confidence: float = 0.7,
    ):
        """
        Initialize Supervisor Model

        Args:
            multimodal_llm: Multimodal LLM client for visual + text analysis
            screenshot_manager: Screenshot manager for visual evidence
            review_interval: Steps between periodic reviews (default: 10)
            min_confidence: Minimum confidence threshold for accepting reviews
        """
        self.multimodal_llm = multimodal_llm
        self.screenshot_manager = screenshot_manager
        self.review_interval = review_interval
        self.min_confidence = min_confidence

        # Review history tracking
        self._review_count = 0
        self._false_positive_count = 0
        self._missed_bug_count = 0

        print(f"[监管者] Supervisor 初始化完成")
        print(f"[监管者] 定期审查间隔: {review_interval} 步")
        print(f"[监管者] 最小置信度阈值: {min_confidence}")

    def set_screenshot_manager(self, screenshot_manager: "ScreenshotManager") -> None:
        """Set screenshot manager"""
        self.screenshot_manager = screenshot_manager

    def should_trigger_review(self, step: int) -> bool:
        """
        Determine if periodic review should be triggered

        Args:
            step: Current test step number

        Returns:
            True if review should be triggered
        """
        return step % self.review_interval == 0 and step > 0

    def check_false_positive(
        self,
        bug_report: "BugReport",
        context: Dict,
        screenshots: Optional[List["ScreenshotData"]] = None
    ) -> ReviewResult:
        """
        Review a bug report reported by LLM for false positive

        This method pauses the testing loop and invokes the supervisor to
        independently verify whether the reported bug is genuine or false positive.

        Args:
            bug_report: The bug report to review
            context: Context dict with operation_history, last_expected_result, etc.
            screenshots: Optional list of screenshots for visual evidence

        Returns:
            ReviewResult with false positive determination
        """
        from .prompt_templates import SupervisorPromptTemplate

        logger = get_logger()
        self._review_count += 1

        print(f"\n{'=' * 60}")
        print(f"[监管者] 开始假阳性审查 #{self._review_count}")
        print(f"{'=' * 60}")

        # 日志记录：审查开始
        logger.section(f"监管者假阳性审查 #{self._review_count}")
        logger.log(f"Bug报告类型: {bug_report.category.value if hasattr(bug_report.category, 'value') else bug_report.category}", "INFO")
        logger.log(f"Bug描述: {bug_report.description[:100] if bug_report.description else 'N/A'}...", "INFO")
        logger.log(f"Bug位置: {bug_report.activity} - {bug_report.operation}", "INFO")

        # Build review prompt
        review_prompt = SupervisorPromptTemplate.false_positive_review_prompt(
            bug_report=bug_report,
            context=context
        )

        # Get supervisor system prompt
        system_prompt = self._build_system_prompt()

        # Get supervisor decision with visual evidence
        review_response = self.multimodal_llm.get_decision(
            review_prompt,
            system_prompt,
            screenshots
        )

        # 日志记录：LLM响应
        logger.subsection("监管者LLM响应")
        logger.log(f"响应内容:\n{review_response}", "DEBUG")

        # Parse response
        result = self._parse_review_response(review_response)
        result.review_type = "false_positive_check"

        # Update statistics
        if result.is_false_positive:
            self._false_positive_count += 1
            print(f"[监管者] 判定为假阳性")
            print(f"[监管者] 原因: {result.false_positive_reason}")
        else:
            print(f"[监管者] 确认真实 Bug")
            print(f"[监管者] 置信度: {result.confidence}")

        # 日志记录：审查结果
        self._log_review_result(result, "假阳性审查")

        print(f"[监管者] 审查完成")
        print(f"{'=' * 60}\n")

        return result

    def check_missed_bugs(
        self,
        context: Dict,
        screenshots: Optional[List["ScreenshotData"]] = None
    ) -> ReviewResult:
        """
        Review for bugs that may have been missed by the LLM

        Periodically scans the current state to detect any bugs that
        the testing LLM might have overlooked.

        Args:
            context: Context dict with current_activity, operation_history, etc.
            screenshots: Optional list of screenshots for visual evidence

        Returns:
            ReviewResult with missed bugs if found
        """
        from .prompt_templates import SupervisorPromptTemplate

        logger = get_logger()
        self._review_count += 1

        activity_name = context.get('current_activity', 'Unknown')

        print(f"\n{'=' * 60}")
        print(f"[监管者] 开始漏检检测 #{self._review_count}")
        print(f"{'=' * 60}")

        # 日志记录：审查开始
        logger.section(f"监管者漏检检测 #{self._review_count}")
        logger.log(f"当前Activity: {activity_name}", "INFO")
        logger.log(f"操作历史长度: {len(context.get('operation_history', []))} 步", "INFO")

        # Build review prompt
        review_prompt = SupervisorPromptTemplate.missed_bug_detection_prompt(
            context=context
        )

        # Get supervisor system prompt
        system_prompt = self._build_system_prompt()

        # Get supervisor decision
        review_response = self.multimodal_llm.get_decision(
            review_prompt,
            system_prompt,
            screenshots
        )

        # 日志记录：LLM响应
        logger.subsection("监管者LLM响应")
        logger.log(f"响应内容:\n{review_response}", "DEBUG")

        # Parse response
        result = self._parse_review_response(review_response)
        result.review_type = "missed_bug_check"

        # Update statistics
        if result.missed_bugs:
            self._missed_bug_count += len(result.missed_bugs)
            print(f"[监管者] 发现 {len(result.missed_bugs)} 个漏检 Bug")
            for i, bug in enumerate(result.missed_bugs, 1):
                print(f"  {i}. {bug.get('type', 'unknown')}: {bug.get('description', '')[:50]}...")
        else:
            print(f"[监管者] 未发现漏检 Bug")

        # 日志记录：审查结果和建议
        self._log_review_result(result, "漏检检测")

        print(f"[监管者] 审查完成")
        print(f"{'=' * 60}\n")

        return result

    def _log_review_result(self, result: ReviewResult, review_name: str) -> None:
        """
        记录审查结果到日志

        Args:
            result: 审查结果对象
            review_name: 审查类型名称
        """
        logger = get_logger()

        # 记录审查结果摘要
        logger.subsection(f"{review_name}结果摘要")
        logger.log(f"审查类型: {result.review_type}", "INFO")
        logger.log(f"置信度: {result.confidence:.2f}", "INFO")

        # 假阳性审查结果
        if result.review_type == "false_positive_check":
            if result.is_false_positive:
                logger.log(f"判定结果: 假阳性 ✗", "WARNING")
                logger.log(f"假阳性原因: {result.false_positive_reason}", "INFO")
            else:
                logger.log(f"判定结果: 真实Bug ✓", "SUCCESS")

        # 漏检检测结果
        if result.review_type == "missed_bug_check":
            if result.missed_bugs:
                logger.log(f"发现漏检Bug: {len(result.missed_bugs)} 个", "WARNING")
                for i, bug in enumerate(result.missed_bugs, 1):
                    bug_type = bug.get('type', 'unknown')
                    bug_desc = bug.get('description', 'N/A')
                    bug_severity = bug.get('severity', 'Unknown')
                    logger.log(f"  Bug #{i}: [{bug_severity}] {bug_type} - {bug_desc[:80]}...", "WARNING")
            else:
                logger.log(f"未发现漏检Bug ✓", "SUCCESS")

        # NEW: 记录给探索者的建议
        if result.suggestions:
            logger.subsection("监管者建议（传递给探索者）")
            logger.log(f"建议数量: {len(result.suggestions)} 条", "INFO")
            logger.log(f"建议内容:", "INFO")
            for activity, suggestion in result.suggestions.items():
                logger.log(f"  📋 {activity}: {suggestion}", "INFO")
        else:
            logger.log(f"无建议传递给探索者", "INFO")

        # 记录详细推理过程
        if result.reasoning:
            logger.log(f"推理过程: {result.reasoning[:200]}...", "DEBUG")

    def _build_system_prompt(self) -> str:
        """
        Build system prompt for supervisor

        Returns:
            System prompt string defining supervisor role
        """
        return """You are a Quality Assurance Supervisor reviewing the work of an AI tester.

Your responsibilities:
1. Review bug reports for accuracy - identify false positives
2. Detect bugs that may have been missed

When analyzing:
- Be thorough but fair - don't reject valid bugs without solid evidence
- Consider UI context and visual evidence carefully
- Explain your reasoning clearly with specific evidence from screenshots

Output your analysis in JSON format as specified in the prompt.

IMPORTANT: Your output must be a single JSON code block with the exact format requested."""

    def _parse_review_response(self, response: str) -> ReviewResult:
        """
        Parse supervisor response into ReviewResult

        Args:
            response: Raw LLM response string

        Returns:
            ReviewResult with parsed data
        """
        # Default result for parsing failures
        default_result = ReviewResult(
            review_type="unknown",  # Placeholder - set later by calling method
            is_false_positive=False,
            confidence=0.5,
            reasoning=response[:200] if response else "No response"
        )

        if not response:
            return default_result

        try:
            # Extract JSON from response (handle markdown code blocks)
            json_match = re.search(r'```json\s*(.+?)\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try to find raw JSON object
                json_match = re.search(r'\{[\s\S]*\}', response)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    print("[监管者] 无法解析 JSON，使用默认结果")
                    return default_result

            # Parse JSON
            data = json.loads(json_str.strip())

            # Build result based on review type (determined later)
            return ReviewResult(
                review_type="unknown",  # Placeholder - set later by calling method
                is_false_positive=data.get('is_false_positive', False),
                false_positive_reason=data.get('reason', '') or data.get('false_positive_reason', ''),
                missed_bugs=data.get('missed_bugs', []),
                suggestions=data.get('suggestions', {}),  # NEW: 解析建议
                confidence=float(data.get('confidence', 0.5)),
                reasoning=data.get('reasoning', '') or data.get('analysis', '')
            )

        except json.JSONDecodeError as e:
            print(f"[监管者] JSON 解析错误: {e}")
            return default_result
        except Exception as e:
            print(f"[监管者] 解析异常: {e}")
            return default_result

    def get_statistics(self) -> Dict:
        """
        Get supervisor statistics

        Returns:
            Dict with review counts and findings
        """
        return {
            "total_reviews": self._review_count,
            "false_positives_detected": self._false_positive_count,
            "missed_bugs_detected": self._missed_bug_count,
            "review_interval": self.review_interval,
            "min_confidence": self.min_confidence
        }


# Test entry point
if __name__ == "__main__":
    print("=" * 60)
    print("SupervisorModel 测试")
    print("=" * 60)

    # Mock test (without actual LLM)
    print("\n[测试] ReviewResult 数据类:")
    result = ReviewResult(
        review_type="false_positive_check",
        is_false_positive=True,
        false_positive_reason="Temporary UI state, not a bug",
        confidence=0.85,
        reasoning="The error message appeared briefly and then disappeared"
    )
    print(result.to_dict())

    print("\n[测试] SupervisorModel 初始化:")
    # Note: Would need actual MultimodalLLMClient for real test
    print("SupervisorModel 需要 MultimodalLLMClient 实例")
    print("测试跳过（无实际 LLM 客户端）")