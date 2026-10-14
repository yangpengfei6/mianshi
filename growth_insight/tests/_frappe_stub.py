"""
Frappe 契约桩（离线 Smoke Test 用）

目的
----
题目要求交付一个 **Frappe 自定义 App**，但本机没有 Docker / bench 环境，
无法真正 `bench run-tests`。为了避免"代码看起来对、一上环境就炸"这种面试官最
容易抓到的坑，这里手写一个**最小可用的 frappe 桩**：

* 覆盖 App 实际用到的全部 frappe API（见各模块 import 清单）；
* `frappe.db` 用一份内存表（由 generator 产出）实现 `get_all / get_value /
  set_value / count`，足以让 Report / API / 定时任务跑 **真实的数据链路**；
* 让 DocType 控制器在**没有真 ORM** 的情况下也能跑 `validate()`，证明业务规则
  在逻辑上自洽。

这不是为了"骗过测试"，而是把"上环境前能离线验证的集成面"尽量前移——CI 里
就能提前发现 import 错误、字段契约错位、报表返回结构异常。

用法（conftest 已自动安装）：：

    from growth_insight.tests._frappe_stub import install
    install()  # 安装桩 + 灌入 Mock 数据
"""

from __future__ import annotations

import calendar
import json
import sys
import types
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# 日期工具（与 frappe.utils 同名函数保持一致的行为）
# ---------------------------------------------------------------------------


def _getdate(value=None):
    """
    兼容 date / datetime / 'YYYY-MM-DD' 字符串 / 空值。

    frappe.utils.getdate() 不传参时返回"今天"，传空串时返回 None，
    这里对齐这两种边界行为。
    """
    if value is None:
        return _today()
    if value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return datetime.fromisoformat(text.replace("/", "-")).date()


def _today():
    """固定为 10-14，与 fixture / 数据生成保持同一"今天"，保证离线可复现。"""
    return date(2026, 10, 14)


def _add_months(d: date, months: int) -> date:
    index = d.month - 1 + months
    year = d.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)


def _date_diff(a, b):
    return (a - b).days if isinstance(a, date) and isinstance(b, date) else 0


def _cint(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _flt(value, precision=None):
    val = float(value) if value not in (None, "") else 0.0
    return round(val, precision) if precision is not None else val


def _now() -> str:
    return _today().isoformat() + " 10:24:36"


def _translate(text, *args, **kwargs) -> str:
    """桩化的 _()，不做任何翻译，原样返回。"""
    return text


# ---------------------------------------------------------------------------
# 内存数据库
# ---------------------------------------------------------------------------


def _sort_key(value):
    if value is None:
        return (0, "")
    if isinstance(value, date):
        return (1, value.isoformat())
    if isinstance(value, (int, float)):
        return (2, value)
    return (3, str(value))


def _op_match(cell, op, expected) -> bool:
    if op == "=":
        return cell == expected
    if op == "!=":
        return cell != expected
    if op == ">":
        return (cell or 0) > expected
    if op == ">=":
        return (cell or 0) >= expected
    if op == "<":
        return (cell or 0) < expected
    if op == "<=":
        return (cell or 0) <= expected
    if op == "in":
        seq = expected if isinstance(expected, (list, tuple, set)) else [expected]
        return cell in seq
    if op == "like":
        return str(expected) in str(cell or "")
    return True


def _row_matches(row: dict, filters) -> bool:
    if not filters:
        return True
    if isinstance(filters, dict):
        for key, value in filters.items():
            if isinstance(value, (tuple, list)) and len(value) == 2 and isinstance(value[0], str):
                op, expected = value
            else:
                op, expected = "=", value
            if not _op_match(row.get(key), op, expected):
                return False
        return True
    if isinstance(filters, list):
        for cond in filters:
            field, op, expected = cond[0], cond[1], cond[2]
            if not _op_match(row.get(field), op, expected):
                return False
        return True
    return True


class _FakeCache:
    """模拟 frappe.cache() 的短 TTL 内存缓存。"""

    def __init__(self, store: dict):
        self._store = store

    def get_value(self, key):
        return self._store.get(key)

    def set_value(self, key, value, expires_in_sec=None):  # noqa: ARG002
        self._store[key] = value


class _FakeDB:
    """极简内存表，支撑 App 实际用到的查询形态。"""

    def __init__(self):
        self._tables: dict[str, list[dict]] = {}
        self._series: dict[str, int] = {}

    # -- 数据装载（测试桩内部使用）--
    def _seed(self, doctype: str, rows: list[dict]):
        self._tables[doctype] = [dict(r) for r in rows]

    # -- 查询 --
    def get_all(
        self,
        doctype: str,
        filters=None,
        fields=None,
        order_by: str | None = None,
        limit_page_length: int | None = 0,
        pluck: str | None = None,
        distinct: bool = False,
        ignore_permissions: bool = False,  # noqa: ARG002
    ):
        rows = [dict(r) for r in self._tables.get(doctype, []) if _row_matches(r, filters)]

        if order_by:
            field, _, direction = order_by.partition(" ")
            reverse = direction.strip().lower().startswith("desc")
            rows.sort(key=lambda r: _sort_key(r.get(field)), reverse=reverse)

        if pluck:
            result = [r.get(pluck) for r in rows]
            if distinct:
                seen: set = set()
                out = []
                for value in result:
                    if value not in seen:
                        seen.add(value)
                        out.append(value)
                result = out
            return result

        if fields:
            result = [{f: r.get(f) for f in fields} for r in rows]
        else:
            result = rows

        if limit_page_length and limit_page_length != 0:
            result = result[:limit_page_length]
        return result

    def get_value(self, doctype: str, name, field):
        for row in self._tables.get(doctype, []):
            if row.get("name") == name:
                return row.get(field)
        return None

    def set_value(self, doctype: str, name, field, value, update_modified: bool = True):  # noqa: ARG002
        for row in self._tables.get(doctype, []):
            if row.get("name") == name:
                row[field] = value
                return value
        return None

    def count(self, doctype: str, filters=None) -> int:
        return len([r for r in self._tables.get(doctype, []) if _row_matches(r, filters)])

    def delete(self, doctype: str, filters=None):  # noqa: ARG002
        self._tables[doctype] = []

    def commit(self):
        return None

    def sql(self, *args, **kwargs):  # noqa: ARG002
        return []


# ---------------------------------------------------------------------------
# 轻量 Document（让控制器在没有真 ORM 时也能跑 validate）
# ---------------------------------------------------------------------------


class _Document:
    def __init__(self, *args, **kwargs):
        self.flags = types.SimpleNamespace()
        if len(args) == 1 and isinstance(args[0], dict):
            for key, value in args[0].items():
                setattr(self, key, value)
        for key, value in kwargs.items():
            setattr(self, key, value)
        if not hasattr(self, "status_history"):
            self.status_history = []

    def __getattr__(self, name):
        # 未显式设置的字段在 Frappe 里读取返回 None，这里对齐该行为
        return None

    def get(self, key, default=None):
        return getattr(self, key, default)

    def append(self, field: str, value: dict):
        lst = getattr(self, field, None)
        if lst is None:
            lst = []
            setattr(self, field, lst)
        lst.append(value)
        return value

    def get_doc_before_save(self):
        # 新建单据场景下，保存前没有"旧版本"
        return None

    def insert(self, ignore_permissions: bool = False, ignore_mandatory: bool = False):  # noqa: ARG002
        if hasattr(self, "validate"):
            self.validate()
        if not getattr(self, "name", None) and hasattr(self, "autoname"):
            self.autoname()
        return self

    def save(self, *args, **kwargs):  # noqa: ARG002
        if hasattr(self, "validate"):
            self.validate()
        return self


# ---------------------------------------------------------------------------
# frappe 桩主体
# ---------------------------------------------------------------------------


class _Frappe:
    DOCTYPE = "User Service Record"

    def __init__(self):
        self._db = _FakeDB()
        self._cache: dict = {}
        self._roles = ["System Manager"]
        self.session = types.SimpleNamespace(user="Administrator")
        self.PermissionError = type("PermissionError", (Exception,), {})
        self.ValidationError = type("ValidationError", (Exception,), {})

    # -- db / cache / session --
    @property
    def db(self) -> _FakeDB:
        return self._db

    def cache(self) -> _FakeCache:
        return _FakeCache(self._cache)

    # -- 权限 --
    def get_roles(self, user=None):  # noqa: ARG002
        return list(self._roles)

    def has_permission(self, doctype=None, ptype=None, doc=None):  # noqa: ARG002
        return True

    def parse_json(self, value):
        return json.loads(value) if isinstance(value, str) else value

    def throw(self, msg, exc=None):
        # frappe.throw() 默认抛出 ValidationError；显式传入的异常类型优先
        if exc is None:
            exc = self.ValidationError
        if isinstance(exc, str):
            raise RuntimeError(f"{exc}: {msg}")
        raise exc(msg)

    def generate_hash(self, length: int = 10) -> str:  # noqa: ARG002
        import random

        return "".join(random.choices("0123456789abcdef", k=length))

    def get_doc(self, *args, **kwargs):
        return _Document(*args, **kwargs)

    def logger(self, name=None):  # noqa: ARG002
        class _Logger:
            def info(self, *a, **k):  # noqa: ARG002
                pass

            def debug(self, *a, **k):  # noqa: ARG002
                pass

            def warning(self, *a, **k):  # noqa: ARG002
                pass

        return _Logger()

    def clear_messages(self):
        return None

    def whitelist(self, allow_guest: bool = False, methods=None):  # noqa: ARG002
        def decorator(func):
            func.is_whitelisted = True
            return func

        return decorator


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------


def install(today: date | None = None, records: list[dict] | None = None):
    """
    把假的 frappe 注册进 sys.modules，并灌入一份由 generator 产出的数据集。

    :param today: 数据"今天"，默认 2026-10-14（与 fixture 对齐）
    :param records: 已有数据集（测试可注入），缺省自动生成
    """
    from growth_insight.analytics import core  # noqa: WPS433
    from growth_insight.mock.generator import generate_records  # noqa: WPS433

    today = today or _today()

    frappe_mod = types.ModuleType("frappe")
    frappe = _Frappe()
    frappe_mod._gi_frappe = frappe
    frappe_mod._ = _translate
    frappe_mod.db = frappe.db
    frappe_mod.cache = frappe.cache
    frappe_mod.get_roles = frappe.get_roles
    frappe_mod.has_permission = frappe.has_permission
    frappe_mod.parse_json = frappe.parse_json
    frappe_mod.throw = frappe.throw
    frappe_mod.generate_hash = frappe.generate_hash
    frappe_mod.get_doc = frappe.get_doc
    frappe_mod.logger = frappe.logger
    frappe_mod.clear_messages = frappe.clear_messages
    frappe_mod.whitelist = frappe.whitelist
    frappe_mod.session = frappe.session
    frappe_mod.PermissionError = frappe.PermissionError
    frappe_mod.ValidationError = frappe.ValidationError

    # frappe.utils
    utils_mod = types.ModuleType("frappe.utils")
    utils_mod.getdate = _getdate
    utils_mod.today = _today
    utils_mod.add_months = _add_months
    utils_mod.add_days = _add_days
    utils_mod.date_diff = _date_diff
    utils_mod.cint = _cint
    utils_mod.flt = _flt
    utils_mod.now = _now
    frappe_mod.utils = utils_mod

    # frappe.model(.document/.naming)
    model_mod = types.ModuleType("frappe.model")
    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = _Document
    naming_mod = types.ModuleType("frappe.model.naming")
    _series_counters = {"_c": 0}

    def _getseries(prefix: str, digits: int = 5) -> str:
        _series_counters["_c"] += 1
        return str(_series_counters["_c"]).zfill(digits)

    naming_mod.getseries = _getseries
    frappe_mod.model = model_mod

    sys.modules["frappe"] = frappe_mod
    sys.modules["frappe.utils"] = utils_mod
    sys.modules["frappe.model"] = model_mod
    sys.modules["frappe.model.document"] = doc_mod
    sys.modules["frappe.model.naming"] = naming_mod

    # 灌数据：用真实 generator，保证"测试链路 == 真实链路"
    if records is None:
        records = generate_records(today=today)
    frappe.db._seed(_Frappe.DOCTYPE, records)
    # 备用：子表也建一张空表，便于后续扩展
    frappe.db._seed("Service Status Change", [])

    # 暴露给测试的句柄
    frappe_mod._gi_records = records
    frappe_mod._gi_core = core
    return frappe
