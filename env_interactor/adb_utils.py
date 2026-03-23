"""
ADB 工具模块
封装 ADB 命令用于移动端 GUI 自动化测试
支持 UIAutomator2 进行高级控件操作
"""

import subprocess
import time
import re
from pathlib import Path
from typing import Optional, Dict, Any

# 尝试导入 uiautomator2，如果未安装则给出提示
try:
    import uiautomator2 as u2
    UIAUTOMATOR2_AVAILABLE = True
except ImportError:
    UIAUTOMATOR2_AVAILABLE = False
    print("[警告] uiautomator2 未安装，某些高级功能将不可用")
    print("[提示] 请运行: pip install uiautomator2")



class ADBController:
    """
    ADB 控制器类
    封装常用的 ADB 命令，用于与 Android 设备进行交互
    同时支持 UIAutomator2 进行高级控件操作（如 clear_text, send_keys）
    """

    def __init__(self, device_id: Optional[str] = None):
        """
        初始化 ADB 控制器

        Args:
            device_id: 设备 ID，如果有多台设备连接时需要指定。
                       可通过 `adb devices` 命令查看
        """
        self.device_id = device_id
        self._u2_device = None  # UIAutomator2 设备实例 (类型: Optional[u2.Device])

    def _get_u2_device(self):  # -> Optional[u2.Device]
        """
        获取 UIAutomator2 设备实例（延迟初始化）

        Returns:
            UIAutomator2 Device 实例，如果不可用则返回 None
        """
        if not UIAUTOMATOR2_AVAILABLE:
            return None

        if self._u2_device is None:
            try:
                if self.device_id:
                    self._u2_device = u2.connect(self.device_id)
                else:
                    self._u2_device = u2.connect()
                print(f"[UIAutomator2] 成功连接到设备")
            except Exception as e:
                print(f"[UIAutomator2] 连接失败: {e}")
                self._u2_device = None

        return self._u2_device

    def _build_adb_command(self, cmd: str) -> list[str]:
        """
        构建 ADB shell 命令列表

        Args:
            cmd: ADB shell 命令字符串

        Returns:
            完整的命令列表，可直接传递给 subprocess
        """
        if self.device_id:
            return ["adb", "-s", self.device_id, "shell", cmd]
        return ["adb", "shell", cmd]

    def _execute_shell(self, cmd: str, timeout: int = 30) -> subprocess.CompletedProcess:
        """
        执行 ADB shell 命令

        Args:
            cmd: 要执行的 shell 命令
            timeout: 命令执行超时时间（秒）

        Returns:
            subprocess.CompletedProcess 对象，包含执行结果
        """
        full_cmd = self._build_adb_command(cmd)
        return subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',  # 忽略无法解码的字符，防止 Windows GBK 编码崩溃
            timeout=timeout
        )

    def _execute_adb(self, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
        """
        执行 ADB 命令（非 shell）

        Args:
            args: ADB 命令参数列表
            timeout: 命令执行超时时间（秒）

        Returns:
            subprocess.CompletedProcess 对象
        """
        if self.device_id:
            cmd = ["adb", "-s", self.device_id] + args
        else:
            cmd = ["adb"] + args
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',  # 忽略无法解码的字符，防止 Windows GBK 编码崩溃
            timeout=timeout
        )

    def click(self, x: int, y: int) -> bool:
        """
        在指定坐标执行点击操作

        Args:
            x: 点击位置的 X 坐标
            y: 点击位置的 Y 坐标

        Returns:
            True 表示命令执行成功，False 表示失败
        """
        try:
            result = self._execute_shell(f"input tap {x} {y}")
            if result.returncode == 0:
                print(f"[点击成功] 坐标: ({x}, {y})")
                return True
            else:
                print(f"[点击失败] 错误信息: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print("[点击失败] 命令执行超时")
            return False
        except Exception as e:
            print(f"[点击失败] 异常: {e}")
            return False

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> bool:
        """
        执行滑动操作

        Args:
            x1: 起始点 X 坐标
            y1: 起始点 Y 坐标
            x2: 终点 X 坐标
            y2: 终点 Y 坐标
            duration: 滑动持续时间（毫秒），默认 300ms

        Returns:
            True 表示命令执行成功，False 表示失败
        """
        try:
            result = self._execute_shell(
                f"input swipe {x1} {y1} {x2} {y2} {duration}"
            )
            if result.returncode == 0:
                print(f"[滑动成功] 从 ({x1}, {y1}) 滑动到 ({x2}, {y2})")
                return True
            else:
                print(f"[滑动失败] 错误信息: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print("[滑动失败] 命令执行超时")
            return False
        except Exception as e:
            print(f"[滑动失败] 异常: {e}")
            return False

    def go_back(self) -> bool:
        """
        按下系统返回键

        使用 ADB input keyevent 命令发送 KEYCODE_BACK (4)

        Returns:
            True 表示命令执行成功，False 表示失败
        """
        try:
            # KEYCODE_BACK = 4
            result = self._execute_shell("input keyevent 4")
            if result.returncode == 0:
                print("[返回键成功] 已按下系统返回键")
                return True
            else:
                print(f"[返回键失败] 错误信息: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print("[返回键失败] 命令执行超时")
            return False
        except Exception as e:
            print(f"[返回键失败] 异常: {e}")
            return False

    def go_home(self) -> bool:
        """
        按下系统 Home 键

        使用 ADB input keyevent 命令发送 KEYCODE_HOME (3)

        Returns:
            True 表示命令执行成功，False 表示失败
        """
        try:
            # KEYCODE_HOME = 3
            result = self._execute_shell("input keyevent 3")
            if result.returncode == 0:
                print("[Home键成功] 已按下系统 Home 键，返回桌面")
                return True
            else:
                print(f"[Home键失败] 错误信息: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print("[Home键失败] 命令执行超时")
            return False
        except Exception as e:
            print(f"[Home键失败] 异常: {e}")
            return False

    def scroll_down(self) -> bool:
        """
        向下滚动屏幕（查看下方内容）

        手势：从屏幕下部向上滑动，使内容向下滚动

        Returns:
            True 表示命令执行成功，False 表示失败
        """
        try:
            # 从 (500, 1500) 滑动到 (500, 500)，模拟向上滑动手势
            result = self._execute_shell("input swipe 500 1500 500 500 500")
            if result.returncode == 0:
                print("[滚动成功] 已向下滚动屏幕")
                return True
            else:
                print(f"[滚动失败] 错误信息: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print("[滚动失败] 命令执行超时")
            return False
        except Exception as e:
            print(f"[滚动失败] 异常: {e}")
            return False

    def scroll_up(self) -> bool:
        """
        向上滚动屏幕（查看上方内容）

        手势：从屏幕上部向下滑动，使内容向上滚动

        Returns:
            True 表示命令执行成功，False 表示失败
        """
        try:
            # 从 (500, 500) 滑动到 (500, 1500)，模拟向下滑动手势
            result = self._execute_shell("input swipe 500 500 500 1500 500")
            if result.returncode == 0:
                print("[滚动成功] 已向上滚动屏幕")
                return True
            else:
                print(f"[滚动失败] 错误信息: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print("[滚动失败] 命令执行超时")
            return False
        except Exception as e:
            print(f"[滚动失败] 异常: {e}")
            return False

    def dump_ui(self, save_dir: str = "temp_data") -> Optional[Path]:
        """
        导出当前界面的 UI 层级结构

        执行流程：
        1. 优先使用 UIAutomator2 的 dump_hierarchy() 方法（如果可用）
        2. 否则使用 ADB uiautomator dump 命令

        Args:
            save_dir: 本地保存目录，默认为 temp_data

        Returns:
            成功时返回本地文件的 Path 对象，失败时返回 None
        """
        # 设备上的 dump 文件路径（ADB 方式使用）
        device_ui_path = "/sdcard/window_dump.xml"

        # 确保本地目录存在
        local_dir = Path(save_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        local_output_path = local_dir / "current_ui.xml"

        # 优先尝试使用 UIAutomator2 的 dump_hierarchy()
        if UIAUTOMATOR2_AVAILABLE:
            try:
                print("[UI导出] 尝试使用 UIAutomator2 dump_hierarchy()...")
                device = self._get_u2_device()
                if device:
                    # 使用 UIAutomator2 获取 UI 层次结构
                    xml_content = device.dump_hierarchy()
                    if xml_content:
                        # 保存到本地文件
                        with open(local_output_path, 'w', encoding='utf-8') as f:
                            f.write(xml_content)
                        print(f"[UI导出成功] 使用 UIAutomator2，文件已保存到: {local_output_path}")
                        return local_output_path
            except Exception as e:
                print(f"[UIAutomator2] dump_hierarchy 失败: {e}，尝试 ADB 方式...")

        # 回退到 ADB uiautomator dump 方式
        try:
            # 步骤1: 在设备上执行 uiautomator dump
            print("[UI导出] 正在执行 uiautomator dump...")
            dump_result = self._execute_shell(
                f"uiautomator dump {device_ui_path}",
                timeout=60
            )

            if dump_result.returncode != 0:
                print(f"[UI导出失败] 错误信息: {dump_result.stderr}")
                return None

            # 步骤2: 等待 XML 文件生成完成
            print("[UI导出] 等待 XML 文件生成...")
            time.sleep(1.5)

            # 步骤3: 将文件从设备拉取到本地
            print(f"[UI导出] 正在拉取文件到本地: {local_output_path}")
            pull_result = self._execute_adb(["pull", device_ui_path, str(local_output_path)])

            if pull_result.returncode == 0 and local_output_path.exists():
                print(f"[UI导出成功] 文件已保存到: {local_output_path}")
                return local_output_path
            else:
                print(f"[UI导出失败] 拉取文件失败: {pull_result.stderr}")
                return None

        except subprocess.TimeoutExpired:
            print("[UI导出失败] 命令执行超时")
            return None
        except Exception as e:
            print(f"[UI导出失败] 异常: {e}")
            return None

    def get_current_activity(self) -> str:
        """
        获取当前处于焦点的应用包名和 Activity 名称

        采用多命令回退策略，依次尝试以下三条命令，直到成功为止：
        1. dumpsys activity activities | grep mResumedActivity  (推荐，Android 10+)
        2. dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp'
        3. dumpsys activity top | grep ACTIVITY

        Returns:
            Activity 名称字符串，格式示例：'Settings'
            如果获取失败或无法解析，返回 'UnknownActivity'
        """
        # 定义三种获取策略
        strategies = [
            {
                "name": "mResumedActivity",
                "cmd": "dumpsys activity activities",
                "keywords": ["mResumedActivity"],
                "description": "dumpsys activity activities (Android 10+)"
            },
            {
                "name": "mCurrentFocus/mFocusedApp",
                "cmd": "dumpsys window windows",
                "keywords": ["mCurrentFocus", "mFocusedApp"],
                "description": "dumpsys window windows"
            },
            {
                "name": "ACTIVITY",
                "cmd": "dumpsys activity top",
                "keywords": ["ACTIVITY"],
                "description": "dumpsys activity top"
            }
        ]

        # 依次尝试每种策略
        for strategy in strategies:
            try:
                print(f"[Activity获取] 尝试策略: {strategy['description']}")

                result = self._execute_shell(strategy["cmd"], timeout=10)

                if result.returncode != 0:
                    print(f"[Activity获取] 命令执行失败，尝试下一策略")
                    continue

                # 从输出中查找目标行
                output = result.stdout
                matched_line = None
                matched_keyword = None

                for line in output.split("\n"):
                    line_stripped = line.strip()
                    for keyword in strategy["keywords"]:
                        if keyword in line_stripped:
                            matched_line = line_stripped
                            matched_keyword = keyword
                            break
                    if matched_line:
                        break

                if not matched_line:
                    print(f"[Activity获取] 未找到 {strategy['keywords']} 信息，尝试下一策略")
                    continue

                print(f"[Activity获取] 匹配到关键字 '{matched_keyword}': {matched_line}")

                # 提取 Activity 名称
                activity_name = self._extract_activity_from_line(matched_line)

                if activity_name:
                    print(f"[Activity获取成功] 当前 Activity: {activity_name}")
                    return activity_name
                else:
                    print(f"[Activity解析失败] 无法从行中提取: {matched_line}")
                    continue

            except subprocess.TimeoutExpired:
                print(f"[Activity获取] 命令超时，尝试下一策略")
                continue
            except Exception as e:
                print(f"[Activity获取] 异常: {e}，尝试下一策略")
                continue

        # 所有策略都失败
        print("[Activity获取失败] 所有策略均未成功")
        return "UnknownActivity"

    def get_current_package(self) -> str:
        """
        获取当前处于焦点的应用包名

        Returns:
            包名字符串，如 'com.android.settings'
            获取失败返回 'unknown.package'
        """
        # 使用与 get_current_activity 相同的策略
        strategies = [
            {
                "name": "mResumedActivity",
                "cmd": "dumpsys activity activities",
                "keyword": "mResumedActivity"
            },
            {
                "name": "mCurrentFocus",
                "cmd": "dumpsys window windows",
                "keyword": "mCurrentFocus"
            }
        ]

        for strategy in strategies:
            try:
                print(f"[包名获取] 尝试策略: {strategy['name']}")
                result = self._execute_shell(strategy["cmd"], timeout=10)

                if result.returncode != 0:
                    continue

                output = result.stdout
                # 在 Python 中搜索，而不是用 grep
                for line in output.split("\n"):
                    if strategy["keyword"] in line:
                        # 从行中提取包名
                        # 格式: mCurrentFocus=Window{xxx com.example.app/...}
                        # 或: mResumedActivity: ActivityRecord{... com.example.app/...}
                        match = re.search(r'([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)/', line)
                        if match:
                            package_name = match.group(1)
                            print(f"[包名获取成功] {package_name}")
                            return package_name

            except Exception as e:
                print(f"[包名获取] 策略 {strategy['name']} 异常: {e}")
                continue

        print("[包名获取失败] 所有策略均未成功")
        return "unknown.package"

    def _extract_activity_from_line(self, line: str) -> Optional[str]:
        """
        从 dumpsys 输出行中提取 Activity 名称

        支持多种格式：
        1. mResumedActivity: ActivityRecord{... com.android.settings/.Settings}
        2. mCurrentFocus=Window{... com.android.settings/.Settings}
        3. mCurrentFocus=Window{... com.android.settings/com.android.settings.Settings}
        4. ACTIVITY com.android.settings/.Settings pid=1234
        5. mFocusedApp=AppWindowToken{... ActivityRecord{... com.example.app/.MainActivity}}

        Args:
            line: dumpsys 输出的一行内容

        Returns:
            提取的 Activity 名称（如 'Settings'），解析失败返回 None
        """
        if not line or "null" in line.lower():
            return None

        try:
            # ========== 策略1：匹配标准组件名格式 ==========
            # 格式：包名/.简短Activity名 或 包名/完整类名
            # 例如：com.android.settings/.Settings
            #      com.example.app/com.example.app.MainActivity

            # 匹配：包名/.Activity名（简写形式，如 .Settings）
            short_form_pattern = r'([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)/\.([A-Z][a-zA-Z0-9]*)'
            short_match = re.search(short_form_pattern, line)
            if short_match:
                activity_name = short_match.group(2)
                print(f"[解析成功] 短格式匹配: {activity_name}")
                return activity_name

            # 匹配：包名/完整类名（如 com.example.app/com.example.app.MainActivity）
            full_form_pattern = r'([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)/([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\.[A-Z][a-zA-Z0-9]*)'
            full_match = re.search(full_form_pattern, line)
            if full_match:
                activity_full = full_match.group(2)
                # 提取最后一个 '.' 之后的部分
                activity_name = activity_full.split(".")[-1]
                print(f"[解析成功] 完整格式匹配: {activity_name}")
                return activity_name

            # ========== 策略2：匹配 Window{...} 中的内容 ==========
            # 格式：Window{hash state uid component}
            window_pattern = r'Window\{[^}]*\s+([^\s}]+)\s*\}'
            window_match = re.search(window_pattern, line)
            if window_match:
                component = window_match.group(1)
                activity_name = self._parse_component_name(component)
                if activity_name:
                    print(f"[解析成功] Window 格式匹配: {activity_name}")
                    return activity_name

            # ========== 策略3：匹配 ActivityRecord{...} 中的内容 ==========
            # 格式：ActivityRecord{... component}
            record_pattern = r'ActivityRecord\{[^}]*\s+([^\s}]+)\s*\}'
            record_match = re.search(record_pattern, line)
            if record_match:
                component = record_match.group(1)
                activity_name = self._parse_component_name(component)
                if activity_name:
                    print(f"[解析成功] ActivityRecord 格式匹配: {activity_name}")
                    return activity_name

            # ========== 策略4：直接查找可能的 Activity 名称 ==========
            # 作为最后的回退，查找以大写字母开头的类名
            # 匹配 .ActivityName 或 /ActivityName 模式
            fallback_pattern = r'[/.]([A-Z][a-zA-Z0-9]*(?:Activity|Fragment|Screen|Page|Dialog|View))?'
            fallback_matches = re.findall(fallback_pattern, line)
            for match in fallback_matches:
                if match:  # 非空匹配
                    print(f"[解析成功] 回退匹配: {match}")
                    return match

            # 如果以上都没有，尝试找任何以大写字母开头的单词
            word_pattern = r'\b([A-Z][a-zA-Z0-9]{2,})\b'
            word_matches = re.findall(word_pattern, line)
            # 过滤掉一些常见的非 Activity 关键字
            exclude_words = {'Window', 'ActivityRecord', 'AppWindowToken', 'null', 'Build', 'VERSION'}
            for word in word_matches:
                if word not in exclude_words:
                    print(f"[解析成功] 通用匹配: {word}")
                    return word

            return None

        except Exception as e:
            print(f"[Activity解析异常] {e}")
            return None

    def _parse_component_name(self, component: str) -> Optional[str]:
        """
        解析组件名，提取 Activity 名称

        支持格式：
        - com.example.app/.MainActivity -> MainActivity
        - com.example.app/com.example.app.MainActivity -> MainActivity
        - .Settings -> Settings
        - com.example.app.MainActivity -> MainActivity

        Args:
            component: 组件名字符串

        Returns:
            Activity 名称，解析失败返回 None
        """
        if not component:
            return None

        try:
            # 如果包含 '/'，取后面的部分
            if "/" in component:
                activity_part = component.split("/")[-1]
            else:
                activity_part = component

            # 如果以 '.' 开头（如 .Settings），去掉开头的点
            if activity_part.startswith("."):
                activity_part = activity_part[1:]

            # 如果包含 '.'，取最后一部分
            if "." in activity_part:
                activity_name = activity_part.split(".")[-1]
            else:
                activity_name = activity_part

            # 确保首字母是大写（Activity 名称约定）
            if activity_name and activity_name[0].isupper():
                return activity_name

            return None

        except Exception as e:
            print(f"[组件名解析异常] {e}")
            return None

    def input_text(self, text: str, clear_first: bool = False) -> bool:
        """
        使用 ADB input text 命令输入文本

        注意：ADB 的 input text 命令不支持直接输入空格，
        需要将空格替换为 %s 才能正确输入。
        同时需要转义一些特殊字符如 &、(、) 等。

        Args:
            text: 要输入的文本字符串
            clear_first: 是否先清除输入框内容

        Returns:
            True 表示输入成功，False 表示失败
        """
        if not text:
            print("[输入失败] 文本为空")
            return False

        try:
            # 如果需要，先清除输入框
            if clear_first:
                print("[ADB输入] 先清除输入框内容...")
                # 发送 Ctrl+A 选择全部，然后 Del 删除
                # KEYCODE_CTRL_LEFT = 113, KEYCODE_A = 29, KEYCODE_DEL = 67
                self._execute_shell("input keyevent 113")  # CTRL
                time.sleep(0.05)
                self._execute_shell("input keyevent 29")   # A (全选)
                time.sleep(0.05)
                self._execute_shell("input keyevent 67")   # DEL (删除)
                time.sleep(0.1)
                # 再发送几次 DEL 确保清除
                for _ in range(3):
                    self._execute_shell("input keyevent 67")
                    time.sleep(0.05)
                print("[ADB输入] 清除完成")

            # 转义特殊字符
            escaped_text = text
            # 1. 空格替换为 %s（ADB input text 的特殊语法）
            escaped_text = escaped_text.replace(" ", "%s")
            # 2. 转义其他特殊字符
            escaped_text = escaped_text.replace("&", "\\&")
            escaped_text = escaped_text.replace("(", "\\(")
            escaped_text = escaped_text.replace(")", "\\)")

            print(f"[文本输入] 原始文本: '{text}'")
            print(f"[文本输入] 转义后: '{escaped_text}'")

            # 执行 input text 命令
            result = self._execute_shell(f'input text "{escaped_text}"')

            if result.returncode == 0:
                print(f"[输入成功] 已输入文本: {text}")
                return True
            else:
                print(f"[输入失败] 错误信息: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            print("[输入失败] 命令执行超时")
            return False
        except Exception as e:
            print(f"[输入失败] 异常: {e}")
            return False

    def clear_and_input_text(self, widget_info: Dict[str, Any], text: str) -> bool:
        """
        使用 UIAutomator2 清除输入框并输入文本

        优先使用 UIAutomator2 的 clear_text() 和 send_keys() 方法，
        如果 UIAutomator2 不可用则回退到 ADB 方式。

        Args:
            widget_info: 控件信息字典，包含 text, resource_id, bounds 等
            text: 要输入的文本字符串

        Returns:
            True 表示输入成功，False 表示失败
        """
        if not text:
            print("[输入失败] 文本为空")
            return False

        # 尝试使用 UIAutomator2
        device = self._get_u2_device()
        if device is None:
            print("[UIAutomator2] 不可用，回退到 ADB 输入方式（带清除）")
            return self.input_text(text, clear_first=True)

        try:
            # 构建选择器来查找元素
            selector = self._build_u2_selector(widget_info)
            if selector is None:
                print("[UIAutomator2] 无法构建选择器，回退到 ADB 输入方式（带清除）")
                print(f"[UIAutomator2-调试] widget_info: {widget_info}")
                return self.input_text(text, clear_first=True)

            # 查找元素
            print(f"[UIAutomator2] 尝试查找元素，选择器: {selector}")
            element = device(**selector)

            # 检查元素是否存在
            print(f"[UIAutomator2] 检查元素是否存在...")
            exists = element.exists
            print(f"[UIAutomator2] 元素 exists: {exists}")

            if not exists:
                print(f"[UIAutomator2] 未找到元素，回退到 ADB 输入方式（带清除）")
                # 尝试打印当前页面所有 EditText 用于调试
                try:
                    all_edit_texts = device(className="android.widget.EditText")
                    if all_edit_texts.exists:
                        print(f"[UIAutomator2-调试] 当前页面所有 EditText:")
                        for i, e in enumerate(all_edit_texts, 1):
                            print(f"  [{i}] resourceId: {e.info.get('resourceName', 'N/A')}, text: {e.info.get('text', 'N/A')[:30]}")
                except Exception as debug_e:
                    print(f"[UIAutomator2-调试] 获取调试信息失败: {debug_e}")
                return self.input_text(text, clear_first=True)

            # 点击元素获取焦点
            print(f"[UIAutomator2] 点击输入框获取焦点...")
            element.click()
            time.sleep(0.3)

            # 清除旧文本
            print(f"[UIAutomator2] 清除输入框中的旧文本...")
            element.clear_text()
            time.sleep(0.2)

            # 输入新文本
            print(f"[UIAutomator2] 输入文本: '{text}'")
            element.send_keys(text)

            print(f"[UIAutomator2] 输入成功")
            return True

        except Exception as e:
            print(f"[UIAutomator2] 操作异常: {e}，回退到 ADB 输入方式（带清除）")
            return self.input_text(text, clear_first=True)

    def _build_u2_selector(self, widget_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        根据控件信息构建 UIAutomator2 选择器

        Args:
            widget_info: 控件信息字典

        Returns:
            UIAutomator2 选择器字典，如果无法构建则返回 None
        """
        if not widget_info:
            print("[UIAutomator2-选择器] widget_info 为空，无法构建选择器")
            return None

        selector = {}

        # 优先使用 resource-id（支持部分匹配）
        resource_id = widget_info.get("resource_id", "")
        if resource_id:
            # 提取 id 名称（兼容完整格式和截断格式）
            if ":id/" in resource_id:
                id_name = resource_id.split(":id/")[-1]
            elif "/" in resource_id:
                id_name = resource_id.split("/")[-1]
            else:
                id_name = resource_id

            # 使用 resourceIdMatches 支持正则匹配，兼容不同包名前缀
            # 例如：org.wikipedia:id/search_src_text 或 search_src_text 都能匹配
            selector["resourceIdMatches"] = f".*[:/]{id_name}$"
            print(f"[UIAutomator2-选择器] 使用 resourceIdMatches: .*[:/]{id_name}$")
            print(f"[UIAutomator2-选择器] 原始 resource_id: {resource_id}")
            return selector

        # 其次使用 text
        text = widget_info.get("text", "")
        if text and text.strip():
            selector["text"] = text.strip()
            print(f"[UIAutomator2-选择器] 使用 text: {text.strip()}")
            return selector

        # 最后使用 class 和 clickable 组合
        class_name = widget_info.get("class", "")
        if class_name and "EditText" in class_name:
            selector["className"] = "android.widget.EditText"
            print(f"[UIAutomator2-选择器] 使用 className: android.widget.EditText")
            return selector

        # 如果以上都无法匹配，尝试使用 description
        description = widget_info.get("content_desc", "")
        if description:
            selector["description"] = description
            print(f"[UIAutomator2-选择器] 使用 description: {description}")
            return selector

        print(f"[UIAutomator2-选择器] 无法构建有效选择器，widget_info: {widget_info}")
        return None

    def clear_logcat(self) -> bool:
        """
        清空现有的系统日志缓冲区

        在每次测试开始前调用，确保后续的崩溃检测只会捕获当前测试期间产生的日志。

        Returns:
            True 表示清空成功，False 表示失败
        """
        try:
            # 执行 logcat -c 清空日志缓冲区
            result = self._execute_adb(["logcat", "-c"])

            if result.returncode == 0:
                print("[日志清空] logcat 缓冲区已清空")
                return True
            else:
                print(f"[日志清空失败] 错误信息: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            print("[日志清空失败] 命令执行超时")
            return False
        except Exception as e:
            print(f"[日志清空失败] 异常: {e}")
            return False

    def check_for_crash(self) -> str:
        """
        检查系统日志中是否存在崩溃信息（工业级精准检测）

        执行 logcat -d 导出当前日志，检测是否包含真正的崩溃关键字：
        - FATAL EXCEPTION: 致命异常（Java 崩溃）
        - beginning of crash: Android 崩溃日志块的标准起始标志
        - CRASH: 原生崩溃标记
        - Force Close: 应用强制关闭
        - has died: 进程异常终止

        注意：不使用 "AndroidRuntime" 关键字，因为它在正常启动时也会出现。

        日志截取策略：一旦定位到崩溃关键字，只截取该位置前 5 行和后 100 行，
        避免写入大量无关日志。

        Returns:
            崩溃日志字符串（精简后的核心堆栈信息），如果发现崩溃；
            如果没有检测到崩溃，返回空字符串
        """
        try:
            # 执行 logcat -d 导出日志（非阻塞，只导出当前缓冲区内容）
            result = self._execute_adb(["logcat", "-d"], timeout=10)

            if result.returncode != 0:
                print(f"[崩溃检测] logcat 执行失败: {result.stderr}")
                return ""

            # 安全解码日志内容（处理可能的编码问题）
            log_content = result.stdout
            if not log_content:
                log_content = ""
                # 尝试从 stderr 获取
                if result.stderr:
                    log_content = result.stderr

            # 将日志分割成行列表，用于精准切片
            log_lines = log_content.split('\n')

            # 严格的崩溃关键字列表（工业级精准检测，避免误报）
            # 按优先级排序，最可靠的放在前面
            crash_keywords = [
                "FATAL EXCEPTION",      # Java 致命异常（最可靠）
                "beginning of crash",   # Android 崩溃日志块的标准起始标志
                "CRASH:",               # 原生崩溃标记（NDK）
                "Force Close",          # 应用强制关闭
                "has died",             # 进程异常终止
            ]

            # 查找崩溃关键字的位置
            crash_keyword_found = None
            crash_line_index = -1

            for i, line in enumerate(log_lines):
                for keyword in crash_keywords:
                    if keyword in line:
                        crash_keyword_found = keyword
                        crash_line_index = i
                        print(f"[崩溃检测] 匹配到关键字: '{keyword}'，位于第 {i+1} 行")
                        break
                if crash_keyword_found:
                    break

            # 如果找到崩溃关键字，执行精准切片
            if crash_keyword_found and crash_line_index >= 0:
                # 计算切片范围：前 5 行 + 后 100 行
                start_index = max(0, crash_line_index - 5)
                end_index = min(len(log_lines), crash_line_index + 101)  # +101 因为切片是左闭右开

                # 执行切片
                crash_slice = log_lines[start_index:end_index]
                crash_log = '\n'.join(crash_slice)

                print(f"[崩溃检测] 发现真实崩溃！关键字: '{crash_keyword_found}'")
                print(f"[崩溃检测] 日志切片: 第 {start_index+1} 行到第 {end_index} 行，共 {len(crash_slice)} 行")
                print(f"[崩溃检测] 崩溃日志长度: {len(crash_log)} 字符（已精简）")

                return crash_log
            else:
                # 没有检测到崩溃
                return ""

        except subprocess.TimeoutExpired:
            print("[崩溃检测] logcat 命令执行超时")
            return ""
        except UnicodeDecodeError as e:
            # 处理编码错误，尝试安全解码
            print(f"[崩溃检测] 日志解码异常: {e}，尝试安全解码")
            try:
                # 重新执行，使用二进制模式读取
                if self.device_id:
                    cmd = ["adb", "-s", self.device_id, "logcat", "-d"]
                else:
                    cmd = ["adb", "logcat", "-d"]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    encoding='utf-8',
                    errors='ignore',  # 显式指定编码，防止 GBK 崩溃
                    timeout=10
                )
                # 获取日志内容
                log_content = result.stdout if result.stdout else ""
                log_lines = log_content.split('\n')

                # 严格的崩溃关键字检测（带精准切片）
                crash_keywords = ["FATAL EXCEPTION", "beginning of crash", "CRASH:", "Force Close", "has died"]

                for i, line in enumerate(log_lines):
                    for keyword in crash_keywords:
                        if keyword in line:
                            print(f"[崩溃检测] 发现真实崩溃（安全解码模式），关键字: '{keyword}'")
                            # 精准切片：前 5 行 + 后 100 行
                            start_index = max(0, i - 5)
                            end_index = min(len(log_lines), i + 101)
                            crash_slice = log_lines[start_index:end_index]
                            return '\n'.join(crash_slice)
                return ""
            except Exception:
                return ""
        except Exception as e:
            print(f"[崩溃检测] 异常: {e}")
            return ""

    def launch_app(self, package_name: str, activity_name: Optional[str] = None) -> bool:
        """
        启动指定的应用

        Args:
            package_name: 应用包名，例如 "com.android.settings"
            activity_name: 可选的 Activity 名称，如果不指定则启动应用主 Activity

        Returns:
            True 表示启动成功，False 表示失败
        """
        try:
            if activity_name:
                # 启动指定的 Activity
                cmd = f"am start -n {package_name}/{activity_name}"
            else:
                # 使用 monkey 启动应用主 Activity
                cmd = f"monkey -p {package_name} -c android.intent.category.LAUNCHER 1"

            print(f"[应用启动] 正在启动: {package_name}")
            result = self._execute_shell(cmd, timeout=10)

            if result.returncode == 0:
                print(f"[应用启动成功] {package_name}")
                # 等待应用启动
                time.sleep(2)
                return True
            else:
                print(f"[应用启动失败] {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            print("[应用启动失败] 命令执行超时")
            return False
        except Exception as e:
            print(f"[应用启动失败] 异常: {e}")
            return False

    def launch_app_by_intent(self, action: str, data_uri: Optional[str] = None) -> bool:
        """
        通过 Intent 启动应用（支持搜索等特定功能）

        Args:
            action: Intent action，例如 "android.intent.action.SEARCH"
            data_uri: 可选的 Data URI

        Returns:
            True 表示启动成功，False 表示失败
        """
        try:
            cmd = f"am start -a {action}"
            if data_uri:
                cmd += f" -d {data_uri}"

            print(f"[Intent启动] action={action}")
            result = self._execute_shell(cmd, timeout=10)

            if result.returncode == 0:
                print(f"[Intent启动成功]")
                time.sleep(2)
                return True
            else:
                print(f"[Intent启动失败] {result.stderr}")
                return False

        except Exception as e:
            print(f"[Intent启动失败] 异常: {e}")
            return False