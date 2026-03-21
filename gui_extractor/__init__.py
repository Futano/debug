"""
GUI 上下文提取器模块
用于解析和提取 Android GUI 信息
"""

from .xml_parser import GUIAnalyzer
from .manifest_parser import ManifestParser, AppInfo, ActivityInfo

__all__ = ["GUIAnalyzer", "ManifestParser", "AppInfo", "ActivityInfo"]