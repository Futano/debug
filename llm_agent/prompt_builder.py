"""
大模型提示词构建模块
按照 GPTDroid 论文 Table 2 的格式构建 Test Prompt
支持功能感知记忆机制和全局探索缓存
"""

from typing import List, Dict, Optional, TYPE_CHECKING
from .memory_manager import TestingSequenceMemorizer

if TYPE_CHECKING:
    from .exploration_cache import ExplorationCache


class PromptGenerator:
    """
    提示词生成器类
    将提取的 GUI 控件信息组装成 LLM 可理解的提示词
    严格遵循 GPTDroid 论文 Table 2 格式
    支持功能感知记忆机制和全局探索缓存

    动作剪枝机制：
    - 过滤无效操作黑名单中的控件，逼迫 LLM 探索新控件
    - 黑名单由状态差分检测结果维护
    """

    def __init__(self, exploration_cache: Optional["ExplorationCache"] = None):
        """
        初始化提示词生成器

        Args:
            exploration_cache: 全局探索缓存实例（可选）
        """
        self.exploration_cache = exploration_cache

    def _filter_blacklisted_widgets(self, activity_name: str, widgets: List[Dict]) -> List[Dict]:
        """
        过滤无效操作黑名单中的控件（动作剪枝）

        遍历控件列表，移除在当前 Activity 中被标记为无效操作的控件

        Args:
            activity_name: 当前 Activity 名称
            widgets: 原始控件列表

        Returns:
            过滤后的控件列表
        """
        if not self.exploration_cache:
            return widgets

        filtered_widgets = []
        blacklisted_count = 0

        for widget in widgets:
            # 获取控件标识（优先 text，其次 resource_id）
            widget_identifier = self._get_widget_identifier(widget)

            # 检查是否在黑名单中
            if widget_identifier and self.exploration_cache.is_blacklisted(activity_name, widget_identifier):
                blacklisted_count += 1
                print(f"[动作剪枝] 过滤无效控件: {widget_identifier}")
                continue

            filtered_widgets.append(widget)

        if blacklisted_count > 0:
            print(f"[动作剪枝] 共过滤 {blacklisted_count} 个无效控件，剩余 {len(filtered_widgets)} 个可操作控件")

        return filtered_widgets

    def _get_widget_identifier(self, widget: Dict) -> Optional[str]:
        """
        获取控件的唯一标识符

        优先级：text > resource_id 最后部分

        Args:
            widget: 控件字典

        Returns:
            控件标识符，如果没有则返回 None
        """
        # 优先使用 text
        text = widget.get("text", "")
        if text and text.strip():
            return text.strip()

        # 其次使用 resource-id 的最后一部分
        resource_id = widget.get("resource_id", "")
        if resource_id:
            return resource_id.split("/")[-1] if "/" in resource_id else resource_id

        return None

    def build_test_prompt(
        self,
        activity_name: str,
        parsed_widgets: List[Dict],
        memorizer: Optional[TestingSequenceMemorizer] = None
    ) -> str:
        """
        构建 Test Prompt（支持记忆机制和探索缓存标记）

        动作剪枝：在构建 Prompt 前，先过滤掉无效操作黑名单中的控件

        完整提示词结构（遵循 GPTDroid 论文 Table 2）：
        1. 句子1：页面信息 - 控件概览
        2. 句子2：页面信息 - 区域分布
        3. 句子3：可操作控件信息（带探索标记）
        4. 句子4：操作提问
        5. 记忆部分：历史页面和操作（如果有记忆器）
        6. 功能查询：当前测试功能状态（如果有记忆器）

        Args:
            activity_name: 当前 Activity 名称
            parsed_widgets: 解析后的控件列表
            memorizer: 测试序列记忆器（可选）

        Returns:
            完整的 Test Prompt 字符串
        """
        # ========== 动作剪枝：过滤无效操作黑名单中的控件 ==========
        filtered_widgets = self._filter_blacklisted_widgets(activity_name, parsed_widgets)

        # 如果过滤后没有可用控件，给出警告
        if not filtered_widgets and parsed_widgets:
            print("[警告] 所有控件都被过滤！保留原始列表以避免 LLM 无选择")
            filtered_widgets = parsed_widgets

        # 分类控件：upper 区域和 lower 区域
        upper_widgets = [w for w in filtered_widgets if w.get("position") == "upper"]
        lower_widgets = [w for w in filtered_widgets if w.get("position") == "lower"]

        # 句子1：页面信息 - 控件概览
        sentence1 = self._build_sentence1(activity_name, filtered_widgets)

        # 句子2：页面信息 - 区域分布
        sentence2 = self._build_sentence2(upper_widgets, lower_widgets)

        # 句子3：可操作控件信息（带探索标记）
        sentence3 = self._build_sentence3(activity_name, filtered_widgets)

        # 句子4：操作提问
        sentence4 = self._build_sentence4()

        # 拼接基础提示词（换行分隔）
        base_prompt = f"{sentence1}\n{sentence2}\n{sentence3}\n{sentence4}"

        # 如果提供了记忆器，追加记忆部分和功能查询
        if memorizer:
            # 获取记忆提示词
            memory_prompt = memorizer.get_memory_prompt()

            # 获取功能查询问题
            function_query = memorizer.get_function_query()

            # 拼接完整提示词
            full_prompt = f"{base_prompt}\n{memory_prompt}\n{function_query}"
            return full_prompt

        return base_prompt

    def _build_sentence1(self, activity_name: str, all_widgets: List[Dict]) -> str:
        """
        构建句子1：页面信息 - 控件概览

        格式："The current page is {activity_name}, it has {所有控件的文本拼接，逗号分隔}."

        Args:
            activity_name: Activity 名称
            all_widgets: 所有控件列表

        Returns:
            句子1 字符串
        """
        # 提取所有控件的文本
        all_texts = self._extract_texts(all_widgets)
        texts_str = ", ".join(all_texts) if all_texts else "no widgets"

        sentence1 = f"The current page is {activity_name}, it has {texts_str}."
        return sentence1

    def _build_sentence2(self, upper_widgets: List[Dict], lower_widgets: List[Dict]) -> str:
        """
        构建句子2：页面信息 - 区域分布

        格式："The upper part of the app is {upper区域控件文本拼接}, the lower part is {lower区域控件文本拼接}."

        Args:
            upper_widgets: upper 区域控件列表
            lower_widgets: lower 区域控件列表

        Returns:
            句子2 字符串
        """
        # 提取 upper 区域控件文本
        upper_texts = self._extract_texts(upper_widgets)
        upper_str = ", ".join(upper_texts) if upper_texts else "no widgets"

        # 提取 lower 区域控件文本
        lower_texts = self._extract_texts(lower_widgets)
        lower_str = ", ".join(lower_texts) if lower_texts else "no widgets"

        sentence2 = f"The upper part of the app is {upper_str}, the lower part is {lower_str}."
        return sentence2

    def _build_sentence3(self, activity_name: str, all_widgets: List[Dict]) -> str:
        """
        构建句子3：可操作控件信息（带探索标记）

        格式："The widgets which can be operated are {所有控件的文本或ID拼接}."

        如果控件已在探索缓存中，会在名称后添加 "[ALREADY EXPLORED]" 标记

        Args:
            activity_name: 当前 Activity 名称
            all_widgets: 所有控件列表

        Returns:
            句子3 字符串
        """
        # 提取控件名称（文本或ID），带探索标记
        widget_names = self._extract_texts_or_ids_with_exploration_mark(activity_name, all_widgets)
        names_str = ", ".join(widget_names) if widget_names else "no widgets"

        sentence3 = f"The widgets which can be operated are {names_str}."
        return sentence3

    def _build_sentence4(self) -> str:
        """
        构建句子4：操作提问

        格式："What operation is required? (<Operation>[click/double-click/long press/scroll]+<Widget Name>)"

        Returns:
            句子4 字符串
        """
        sentence4 = "What operation is required? (<Operation>[click/double-click/long press/scroll]+<Widget Name>)"
        return sentence4

    def _extract_texts(self, widgets: List[Dict]) -> List[str]:
        """
        从控件列表中提取文本

        只提取 text 字段，不包含 ID

        Args:
            widgets: 控件列表

        Returns:
            控件文本列表
        """
        texts = []
        for widget in widgets:
            text = widget.get("text", "")
            if text and text.strip():
                texts.append(text.strip())
        return texts

    def _extract_texts_or_ids(self, widgets: List[Dict]) -> List[str]:
        """
        从控件列表中提取文本或ID

        优先级：text > resource_id > class 简名

        Args:
            widgets: 控件列表

        Returns:
            控件名称列表（文本或ID）
        """
        names = []
        for widget in widgets:
            # 优先使用 text
            text = widget.get("text", "")
            if text and text.strip():
                names.append(text.strip())
                continue

            # 其次使用 resource-id 的最后一部分
            resource_id = widget.get("resource_id", "")
            if resource_id:
                # 提取 ID 的最后部分，如 "com.app:id/btn_ok" -> "btn_ok"
                id_name = resource_id.split("/")[-1] if "/" in resource_id else resource_id
                if id_name:
                    names.append(id_name)
                    continue

            # 最后使用 class 的简名
            class_name = widget.get("class", "")
            if class_name:
                # 提取类名的最后一部分，如 "android.widget.Button" -> "Button"
                simple_name = class_name.split(".")[-1]
                names.append(simple_name)

        return names

    def _extract_texts_or_ids_with_exploration_mark(
        self,
        activity_name: str,
        widgets: List[Dict]
    ) -> List[str]:
        """
        从控件列表中提取文本或ID，并标记已探索的控件

        优先级：text > resource_id > class 简名

        如果控件已在探索缓存中，会在名称后添加 "[ALREADY EXPLORED]" 标记

        Args:
            activity_name: 当前 Activity 名称
            widgets: 控件列表

        Returns:
            控件名称列表（带探索标记）
        """
        names = []
        for widget in widgets:
            # 优先使用 text
            text = widget.get("text", "")
            if text and text.strip():
                widget_name = text.strip()
                # 检查是否已探索并添加标记
                if self.exploration_cache and self.exploration_cache.is_explored(activity_name, widget_name):
                    widget_name = f"{widget_name} [ALREADY EXPLORED]"
                names.append(widget_name)
                continue

            # 其次使用 resource-id 的最后一部分
            resource_id = widget.get("resource_id", "")
            if resource_id:
                # 提取 ID 的最后部分，如 "com.app:id/btn_ok" -> "btn_ok"
                id_name = resource_id.split("/")[-1] if "/" in resource_id else resource_id
                if id_name:
                    # 检查是否已探索并添加标记
                    if self.exploration_cache and self.exploration_cache.is_explored(activity_name, id_name):
                        id_name = f"{id_name} [ALREADY EXPLORED]"
                    names.append(id_name)
                    continue

            # 最后使用 class 的简名
            class_name = widget.get("class", "")
            if class_name:
                # 提取类名的最后一部分，如 "android.widget.Button" -> "Button"
                simple_name = class_name.split(".")[-1]
                # 检查是否已探索并添加标记
                if self.exploration_cache and self.exploration_cache.is_explored(activity_name, simple_name):
                    simple_name = f"{simple_name} [ALREADY EXPLORED]"
                names.append(simple_name)

        return names


# 测试入口
if __name__ == "__main__":
    # 模拟控件数据
    mock_widgets = [
        {
            "class": "android.widget.Button",
            "text": "Login",
            "resource_id": "com.example:id/btn_login",
            "position": "upper",
            "clickable": True
        },
        {
            "class": "android.widget.EditText",
            "text": "",
            "resource_id": "com.example:id/et_username",
            "position": "upper",
            "clickable": True
        },
        {
            "class": "android.widget.Button",
            "text": "Register",
            "resource_id": "com.example:id/btn_register",
            "position": "lower",
            "clickable": True
        }
    ]

    # 创建记忆器并记录历史
    memorizer = TestingSequenceMemorizer()
    memorizer.update_step("MainActivity", "click", "Search", "Search")
    memorizer.update_step("SearchActivity", "input", "SearchBox")

    # 生成带记忆的提示词
    generator = PromptGenerator()
    prompt = generator.build_test_prompt("SearchActivity", mock_widgets, memorizer)

    print("=" * 60)
    print("生成的 Test Prompt (带记忆机制):")
    print("=" * 60)
    print(prompt)