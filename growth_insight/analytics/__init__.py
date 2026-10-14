"""Growth Insight 分析层。

目录约定
--------
* ``constants.py`` —— 领域字典，是 DocType JSON 的 Select 选项、前端脚本、
  后端聚合共同的数据契约；
* ``core.py``      —— **零 Frappe 依赖**的聚合引擎，可独立单测；
* ``service.py``   —— 唯一的 Frappe ORM 出入口。

铁律：聚合公式只允许出现在 ``core.py``。报表、大屏、API 三条出口都必须经过 service
调用 core，这样"报表和大屏数字不一致"这类问题在架构层面就不可能发生。
"""
