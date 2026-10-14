"""
conftest —— 在收集测试模块之前，先把 frappe 桩装进 sys.modules。

pytest 会在导入本目录下的 test_*.py 之前先导入此 conftest，
因此这里在模块顶层安装桩，可保证：
  * 被测的 Frappe 集成模块（controller / report / service / api / tasks）
    在 import 时就能拿到假的 frappe，不会因本机无 bench 而 ImportError；
  * 纯 Python 的 analytics 测试不受任何影响（桩是"可选依赖"）。
"""

from growth_insight.tests._frappe_stub import install

# 在 import 时立即安装，早于任何 test_*.py 的模块级 import
install()
