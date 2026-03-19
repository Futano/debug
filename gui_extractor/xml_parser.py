"""
XML 解析模块 - 有效动作空间剪枝版
用于解析 Android UI 层级 XML 文件，提取真正可交互的 GUI 控件信息

核心优化：
1. 基础物理过滤 - 剔除无效 bounds 占位符
2. 交互属性判定 - clickable/scrollable/checkable/long-clickable 及交互组件标识
3. 父级事件冒泡 - 向上穿透算法，捕获被父容器代理的交互
4. 布局容器剔除 - 拒绝无意义的空白 Layout
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, List, Dict, Set, Tuple
import re


class GUIAnalyzer:
    """
    GUI 分析器类
    解析 Android UI XML 文件，实施严格的有效动作空间剪枝
    """

    # 屏幕高度常量，用于判断控件位置（遵循 GPTDroid 论文设定）
    SCREEN_HEIGHT = 1920
    SCREEN_MIDLINE = SCREEN_HEIGHT // 2  # 960

    # 无意义的布局类名集合（幽灵控件黑名单）
    # 这些是纯排版容器，本身不具备交互语义
    GHOST_WIDGET_BLACKLIST: Set[str] = {
        'LinearLayout', 'FrameLayout', 'RelativeLayout',
        'ViewGroup', 'ScrollView', 'HorizontalScrollView',
        'ConstraintLayout', 'CoordinatorLayout', 'DrawerLayout',
        'AppBarLayout', 'NestedScrollView', 'RecyclerView',
        'GridLayout', 'TableLayout', 'TabLayout',
        'android.widget.LinearLayout', 'android.widget.FrameLayout',
        'android.widget.RelativeLayout', 'android.view.ViewGroup',
        'android.view.View', 'android.widget.ScrollView',
        'android.widget.GridLayout', 'android.widget.TableLayout',
    }

    # 明确的交互组件标识符（出现在 class 名称中）
    # 包含这些关键词的控件直接判定为可交互
    INTERACTIVE_CLASS_KEYWORDS: Set[str] = {
        'Button', 'EditText', 'CheckBox', 'Switch', 'Toggle',
        'RadioButton', 'RadioGroup', 'Spinner', 'SeekBar',
        'ImageButton', 'CompoundButton', 'CheckedTextView',
        'AutoCompleteTextView', 'MultiAutoCompleteTextView',
        'SearchView', 'RatingBar', 'Chronometer', 'DatePicker',
        'TimePicker', 'NumberPicker', 'CalendarView', 'ZoomButton',
        'QuickContactBadge', 'VideoView',
        # 'ImageView',  # ImageView 可能是可点击的图标
        # 'TextView',  # TextView 可能承载父级的点击事件
    }

    # 交互属性列表 - 具有这些属性之一即为潜在可交互
    INTERACTION_ATTRIBUTES: Set[str] = {
        'clickable', 'scrollable', 'checkable', 'long-clickable',
        'focusable', 'editable'
    }

    # 降级模式的最小控件数量阈值
    # 低于此阈值时触发降级解析
    FALLBACK_THRESHOLD = 2

    def __init__(self):
        """初始化 GUI 分析器"""
        self.nodes: List[Dict] = []  # 存储解析后的控件列表
        self._seen_bounds: Set[str] = set()  # 用于去重的 bounds 集合
        self._fallback_mode: bool = False  # 当前是否处于降级模式

    def parse_xml(self, xml_path: str | Path) -> List[Dict]:
        """
        解析 UI XML 文件，提取有效控件信息

        实施严格的有效动作空间剪枝，并支持优雅降级：
        1. 物理坐标有效性验证
        2. 交互属性判定
        3. 父级事件冒泡处理
        4. 无意义布局容器过滤
        5. 降级解析模式 - 当严格过滤导致控件过少时自动放宽标准

        Args:
            xml_path: XML 文件路径

        Returns:
            包含所有有效控件信息的字典列表
        """
        self.nodes = []
        self._seen_bounds = set()  # 重置去重集合
        self._fallback_mode = False  # 重置降级模式标记
        xml_path = Path(xml_path)

        try:
            # 解析 XML 文件
            tree = ET.parse(xml_path)
            root = tree.getroot()

            # ========== 第一轮：严格模式遍历 ==========
            self._traverse_node(root, parent_chain=[])

            print(f"[XML解析] 严格模式提取到 {len(self.nodes)} 个有效控件")

            # ========== 优雅降级检测 ==========
            # 如果控件数量低于阈值，触发降级模式重新解析
            if len(self.nodes) < self.FALLBACK_THRESHOLD:
                print(f"[XML解析] 控件数量不足（<{self.FALLBACK_THRESHOLD}），触发降级模式...")

                # 重置状态，准备降级解析
                self.nodes = []
                self._seen_bounds = set()
                self._fallback_mode = True

                # 第二轮：降级模式遍历（放宽标准）
                self._traverse_node_lenient(root)

                print(f"[XML解析] 降级模式提取到 {len(self.nodes)} 个控件")

            return self.nodes

        except ET.ParseError as e:
            print(f"[XML解析失败] XML 解析错误: {e}")
            return []
        except FileNotFoundError:
            print(f"[XML解析失败] 文件不存在: {xml_path}")
            return []
        except Exception as e:
            print(f"[XML解析失败] 异常: {e}")
            return []

    def _traverse_node(
        self,
        node: ET.Element,
        parent_chain: List[ET.Element],
        max_bubble_depth: int = 3
    ) -> None:
        """
        递归遍历 XML 节点树，实施有效动作空间剪枝

        Args:
            node: 当前 XML 元素节点
            parent_chain: 从根节点到当前节点的父节点链（用于事件冒泡）
            max_bubble_depth: 向上冒泡的最大深度（默认 3 层：父、爷、祖）
        """
        # 提取节点属性
        node_info = self._extract_node_info(node)

        # ========== 第一道防线：物理坐标有效性验证 ==========
        if not self._is_valid_bounds(node_info.get("bounds", "")):
            # 仍然遍历子节点（父节点无效不代表子节点无效）
            new_chain = parent_chain + [node]
            for child in node:
                self._traverse_node(child, new_chain, max_bubble_depth)
            return

        # ========== 第二道防线：布局容器判断 ==========
        class_name = node_info.get("class", "")
        is_ghost_container = self._is_ghost_container(class_name)

        # ========== 第三道防线：交互属性判定 ==========
        has_interaction_attr = self._has_interaction_attributes(node)
        has_interactive_class = self._has_interactive_class_keyword(class_name)

        # ========== 第四道防线：父级事件冒泡 ==========
        # 如果当前节点有文本但没有交互属性，尝试向上查找
        has_text = bool(node_info.get("original_text", "").strip() or
                       node_info.get("content_desc", "").strip())
        bubble_result = None

        if has_text and not has_interaction_attr and not has_interactive_class:
            bubble_result = self._bubble_find_interactive_parent(
                node, parent_chain, max_bubble_depth
            )

        # ========== 综合判定：是否为有效交互节点 ==========
        is_valid_node = False
        validation_reason = ""

        # 情况1：有交互属性且不是幽灵容器
        if has_interaction_attr and not is_ghost_container:
            is_valid_node = True
            validation_reason = "交互属性"

        # 情况2：类名包含明确的交互组件标识
        elif has_interactive_class and not is_ghost_container:
            is_valid_node = True
            validation_reason = "交互类名"

        # 情况3：有文本内容，且（自身有交互 或 父级有交互）
        elif has_text:
            if has_interaction_attr or has_interactive_class:
                is_valid_node = True
                validation_reason = "有文本+交互属性"
            elif bubble_result:
                is_valid_node = True
                validation_reason = f"事件冒泡(深度{bubble_result['depth']})"
                # 使用冒泡找到的父节点的交互属性
                node_info["clickable"] = True
                node_info["bubble_parent"] = bubble_result["parent_class"]

        # # 情况4：有有意义的 resource-id
        # elif self._has_meaningful_resource_id(node_info.get("resource_id", "")):
        #     if not is_ghost_container:
        #         is_valid_node = True
        #         validation_reason = "有意义的resource-id"

        # ========== 去重处理 ==========
        # 相同 bounds 的节点只保留一个（避免重复控件）
        if is_valid_node:
            bounds = node_info.get("bounds", "")
            if bounds in self._seen_bounds:
                is_valid_node = False
                validation_reason = "去重跳过"
            else:
                self._seen_bounds.add(bounds)

        # ========== 最终判定 ==========
        if is_valid_node:
            # 记录验证原因（用于调试）
            node_info["_validation_reason"] = validation_reason
            self.nodes.append(node_info)

        # 递归遍历子节点
        new_chain = parent_chain + [node]
        for child in node:
            self._traverse_node(child, new_chain, max_bubble_depth)

    def _traverse_node_lenient(self, node: ET.Element) -> None:
        """
        降级模式遍历 XML 节点树（放宽标准）

        降级模式判定规则：
        1. bounds 必须有效（物理坐标仍需验证）
        2. 只要节点有 text 或 content-desc，且不是幽灵布局容器，就视为有效
        3. 或有有意义的 resource-id

        此方法用于在严格模式提取控件不足时的兜底救援，防止 LLM 陷入死循环。

        Args:
            node: 当前 XML 元素节点
        """
        # 提取节点属性
        node_info = self._extract_node_info(node)

        # 物理坐标有效性验证（降级模式仍需验证）
        if not self._is_valid_bounds(node_info.get("bounds", "")):
            # 仍然遍历子节点
            for child in node:
                self._traverse_node_lenient(child)
            return

        # 布局容器判断
        class_name = node_info.get("class", "")
        is_ghost_container = self._is_ghost_container(class_name)

        # 文本内容检查
        has_text = bool(node_info.get("original_text", "").strip() or
                       node_info.get("content_desc", "").strip())

        # resource-id 检查
        has_resource_id = self._has_meaningful_resource_id(node_info.get("resource_id", ""))

        # 降级模式判定：有文本或有 resource-id，且不是幽灵容器
        is_valid_node = False
        validation_reason = ""

        if not is_ghost_container:
            if has_text:
                is_valid_node = True
                validation_reason = "Fallback Mode: 有文本内容"
            elif has_resource_id:
                is_valid_node = True
                validation_reason = "Fallback Mode: 有resource-id"

        # 去重处理
        if is_valid_node:
            bounds = node_info.get("bounds", "")
            if bounds in self._seen_bounds:
                is_valid_node = False
            else:
                self._seen_bounds.add(bounds)

        # 添加到控件列表
        if is_valid_node:
            node_info["_validation_reason"] = validation_reason
            node_info["_fallback_mode"] = True  # 标记为降级模式提取
            self.nodes.append(node_info)

        # 递归遍历子节点
        for child in node:
            self._traverse_node_lenient(child)

    def _extract_node_info(self, node: ET.Element) -> Dict:
        """
        提取节点的关键属性信息

        Args:
            node: XML 元素节点

        Returns:
            包含节点信息的字典
        """
        # 获取各类属性
        class_name = node.get("class", "")
        text = node.get("text", "")
        content_desc = node.get("content-desc", "")
        resource_id = node.get("resource-id", "")
        bounds = node.get("bounds", "")

        # 交互属性
        clickable = node.get("clickable", "false") == "true"
        scrollable = node.get("scrollable", "false") == "true"
        checkable = node.get("checkable", "false") == "true"
        long_clickable = node.get("long-clickable", "false") == "true"
        focusable = node.get("focusable", "false") == "true"
        editable = node.get("editable", "false") == "true"
        enabled = node.get("enabled", "true") == "true"  # 默认为 true

        # 解析 bounds 坐标
        center_x, center_y, position = self._parse_bounds(bounds)

        # 如果 text 为空，则使用 content-desc 作为显示文本
        display_text = text if text else content_desc

        return {
            "class": class_name,
            "text": display_text,  # text 为空时取 content-desc
            "original_text": text,  # 保留原始 text
            "content_desc": content_desc,
            "resource_id": resource_id,
            "bounds": bounds,
            "center_x": center_x,
            "center_y": center_y,
            "position": position,  # "upper" 或 "lower"
            "clickable": clickable,
            "scrollable": scrollable,
            "checkable": checkable,
            "long_clickable": long_clickable,
            "focusable": focusable,
            "editable": editable,
            "enabled": enabled,
        }

    def _parse_bounds(self, bounds: str) -> Tuple[Optional[int], Optional[int], str]:
        """
        解析 bounds 字符串，计算中心点坐标并判断位置

        bounds 格式示例: "[0,100][1080,200]"
        解析为左上角 (0, 100) 和右下角 (1080, 200)

        Args:
            bounds: bounds 属性字符串

        Returns:
            元组 (center_x, center_y, position)
            position 为 "upper" 或 "lower"
        """
        if not bounds:
            return None, None, "unknown"

        try:
            # 使用正则提取坐标值
            # 格式: [x1,y1][x2,y2]
            pattern = r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]'
            match = re.match(pattern, bounds)

            if not match:
                return None, None, "unknown"

            x1, y1, x2, y2 = map(int, match.groups())

            # 计算中心点坐标
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2

            # 判断位置：y < 960 为 upper，否则为 lower
            if center_y < self.SCREEN_MIDLINE:
                position = "upper"
            else:
                position = "lower"

            return center_x, center_y, position

        except Exception as e:
            print(f"[bounds解析失败] bounds={bounds}, 错误: {e}")
            return None, None, "unknown"

    def _is_valid_bounds(self, bounds: str) -> bool:
        """
        验证 bounds 是否有效

        无效条件：
        1. bounds 为空
        2. bounds 格式无法解析
        3. 宽度 <= 0 或高度 <= 0
        4. x1 == 0 且 y1 == 0 且 x2 == 0 且 y2 == 0（完全无坐标的幽灵占位符）

        Args:
            bounds: bounds 属性字符串，格式如 "[0,100][1080,200]"

        Returns:
            True 表示有效，False 表示无效
        """
        if not bounds:
            return False

        try:
            pattern = r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]'
            match = re.match(pattern, bounds)

            if not match:
                return False

            x1, y1, x2, y2 = map(int, match.groups())

            # 检查完全无坐标的幽灵占位符
            if x1 == 0 and y1 == 0 and x2 == 0 and y2 == 0:
                return False

            # 检查宽高是否有效
            width = x2 - x1
            height = y2 - y1

            if width <= 0 or height <= 0:
                return False

            return True

        except Exception:
            return False

    def _is_ghost_container(self, class_name: str) -> bool:
        """
        判断类名是否为幽灵布局容器

        Args:
            class_name: 类名字符串

        Returns:
            True 表示是幽灵容器，False 表示不是
        """
        if not class_name:
            return True  # 没有类名的节点也视为容器

        # 精确匹配
        if class_name in self.GHOST_WIDGET_BLACKLIST:
            return True

        # 后缀匹配（处理 android.widget.xxx 格式）
        simple_name = class_name.split('.')[-1] if '.' in class_name else class_name
        if simple_name in self.GHOST_WIDGET_BLACKLIST:
            return True

        return False

    def _has_interaction_attributes(self, node: ET.Element) -> bool:
        """
        检查节点是否具有交互属性

        检查的属性：clickable, scrollable, checkable, long-clickable

        Args:
            node: XML 元素节点

        Returns:
            True 表示具有交互属性
        """
        for attr in ['clickable', 'scrollable', 'checkable', 'long-clickable']:
            if node.get(attr, "false") == "true":
                return True
        return False

    def _has_interactive_class_keyword(self, class_name: str) -> bool:
        """
        检查类名是否包含交互组件标识

        Args:
            class_name: 类名字符串

        Returns:
            True 表示包含交互关键词
        """
        if not class_name:
            return False

        # 获取简单类名（去掉包名前缀）
        simple_name = class_name.split('.')[-1] if '.' in class_name else class_name

        # 检查是否在交互关键词集合中
        for keyword in self.INTERACTIVE_CLASS_KEYWORDS:
            if keyword in simple_name:
                return True

        return False

    def _has_meaningful_resource_id(self, resource_id: str) -> bool:
        """
        检查 resource-id 是否有意义

        有意义的 resource-id 包含 "/"，说明是开发者定义的 ID
        例如: "com.example.app:id/btn_submit"

        Args:
            resource_id: resource-id 属性值

        Returns:
            True 表示有意义的 resource-id
        """
        if not resource_id:
            return False

        # 必须包含 "/" 才是开发者定义的 ID
        return "/" in resource_id

    def _bubble_find_interactive_parent(
        self,
        node: ET.Element,
        parent_chain: List[ET.Element],
        max_depth: int
    ) -> Optional[Dict]:
        """
        向上冒泡查找具有交互属性的父节点

        算法说明：
        很多时候，文本节点本身 clickable="false"，但它的父节点（或爷爷节点）是
        clickable="true"。这种情况下，用户点击文本实际上触发的是父级的点击事件。

        本方法向上遍历父节点链，找到第一个具有交互属性的长辈节点。

        Args:
            node: 当前 XML 元素节点
            parent_chain: 父节点链（从根节点到当前节点的直接父节点）
            max_depth: 最大向上查找深度

        Returns:
            找到的交互父节点信息，包含 depth 和 parent_class；
            未找到返回 None
        """
        # parent_chain 是从根节点到当前节点的父节点链
        # 我们需要从最近的父节点开始向上查找
        if not parent_chain:
            return None

        # 反转父节点链，从最近的父节点开始
        reversed_chain = list(reversed(parent_chain))

        for depth, parent in enumerate(reversed_chain[:max_depth], start=1):
            # 检查父节点是否有交互属性
            clickable = parent.get("clickable", "false") == "true"
            scrollable = parent.get("scrollable", "false") == "true"
            checkable = parent.get("checkable", "false") == "true"
            long_clickable = parent.get("long-clickable", "false") == "true"

            if clickable or scrollable or checkable or long_clickable:
                parent_class = parent.get("class", "unknown")
                return {
                    "depth": depth,
                    "parent_class": parent_class,
                    "clickable": clickable,
                    "scrollable": scrollable,
                }

        return None

    def get_nodes_by_position(self, position: str) -> List[Dict]:
        """
        根据位置筛选控件

        Args:
            position: "upper" 或 "lower"

        Returns:
            符合条件的控件列表
        """
        return [node for node in self.nodes if node.get("position") == position]

    def get_clickable_nodes(self) -> List[Dict]:
        """
        获取所有可点击的控件

        Returns:
            可点击控件列表
        """
        return [node for node in self.nodes if node.get("clickable")]

    def get_nodes_with_text(self) -> List[Dict]:
        """
        获取所有有文本内容的控件

        Returns:
            有文本内容的控件列表
        """
        return [node for node in self.nodes if node.get("text", "").strip()]

    def get_interactive_summary(self) -> Dict:
        """
        获取控件交互属性统计摘要

        Returns:
            统计信息字典
        """
        total = len(self.nodes)
        if total == 0:
            return {"total": 0, "fallback_mode": self._fallback_mode}

        clickable = sum(1 for n in self.nodes if n.get("clickable"))
        scrollable = sum(1 for n in self.nodes if n.get("scrollable"))
        checkable = sum(1 for n in self.nodes if n.get("checkable"))
        long_clickable = sum(1 for n in self.nodes if n.get("long_clickable"))
        with_text = sum(1 for n in self.nodes if n.get("text", "").strip())
        with_bubble = sum(1 for n in self.nodes if n.get("bubble_parent"))
        fallback_count = sum(1 for n in self.nodes if n.get("_fallback_mode"))

        return {
            "total": total,
            "clickable": clickable,
            "scrollable": scrollable,
            "checkable": checkable,
            "long_clickable": long_clickable,
            "with_text": with_text,
            "bubble_parent": with_bubble,
            "fallback_mode": self._fallback_mode,
            "fallback_count": fallback_count,
        }


# 测试入口
if __name__ == "__main__":
    import sys

    # 获取测试文件路径
    test_file = sys.argv[1] if len(sys.argv) > 1 else "temp_data/current_ui.xml"

    print("=" * 60)
    print("XML 解析测试 - 有效动作空间剪枝 + 优雅降级")
    print("=" * 60)

    analyzer = GUIAnalyzer()
    nodes = analyzer.parse_xml(test_file)

    # 打印统计摘要
    summary = analyzer.get_interactive_summary()
    print("\n--- 控件统计摘要 ---")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    # 打印前 10 个有效控件
    print(f"\n--- 前 10 个有效控件 ---")
    for i, node in enumerate(nodes[:10]):
        print(f"\n[控件 {i + 1}]")
        print(f"  类别: {node['class']}")
        print(f"  文本: {node['text']}")
        print(f"  ID: {node['resource_id']}")
        print(f"  位置: {node['position']}")
        print(f"  坐标: ({node['center_x']}, {node['center_y']})")
        print(f"  交互属性: clickable={node['clickable']}, scrollable={node['scrollable']}")
        print(f"  验证原因: {node.get('_validation_reason', 'N/A')}")
        if node.get("bubble_parent"):
            print(f"  冒泡父级: {node['bubble_parent']}")
        if node.get("_fallback_mode"):
            print(f"  [降级模式提取]")