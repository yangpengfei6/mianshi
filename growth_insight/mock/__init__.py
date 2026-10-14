"""Growth Insight Mock 数据层。

* ``generator.py`` —— **零 Frappe 依赖**的数据生成器（离散时间 hazard 模型）；
* ``loader.py``    —— 把生成结果写入数据库。

把生成器做成无依赖是有意为之：这样可以在 CI 里用 pytest 断言"数据分布是否符合预期"，
而不必先拉起一整套 Frappe + MariaDB 环境。
"""
