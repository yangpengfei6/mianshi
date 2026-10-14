# -*- coding: utf-8 -*-
"""
仓库完整性自检

为什么要存在这个脚本？
----------------------
Frappe App 里最常见的翻车点是"半成品 pair"：JSON 改了忘同步 .py、
Select 选项和 constants.py 不一致、field_order 里漏写字段名、
hooks 指向的模块被删了……这些在没起 Frappe 环境时**完全看不出来**，
一旦 install 就是一堆摸不着头脑的报错。

``python scripts/validate_repo.py`` 在 CI 里跑，把这些不一致提前拦掉。
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "growth_insight"

# constants.py 里的字典变量 -> 对应的 Select 字段应使用的取值
SELECT_SOURCES = {
    "region": "REGIONS",
    "channel": "CHANNELS",
    "plan": "PLANS",
    "device": "DEVICES",
    "status": "STATUSES",
    "churn_reason": "CHURN_REASONS",
    "activation_type": "ACTIVATION_TYPES",
    "event_type": "EVENT_TYPES",
}

errors: list[str] = []
warnings: list[str] = []


def fail(message: str) -> None:
    errors.append(message)


def warn(message: str) -> None:
    warnings.append(message)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"JSON 解析失败 {path.relative_to(ROOT)}: {exc}")
        return None


def load_constants() -> dict:
    """直接从 constants.py 里提取弟典取值，避免自增层面的循环依赖。"""
    source = (APP_DIR / "analytics" / "constants.py").read_text(encoding="utf-8")
    namespace: dict = {}
    exec(compile(source, "constants.py", "exec"), namespace)  # noqa: S102
    return namespace


CONSTANTS = load_constants()


# ---------------------------------------------------------------------------
def check_doctype_json(path: Path) -> None:
    data = load_json(path)
    if not data:
        return
    rel = path.relative_to(ROOT)
    name = data.get("name", "<unknown>")

    if not data.get("module"):
        fail(f"{rel}: 缺少 module")
    if data.get("doctype") != "DocType":
        fail(f"{rel}: doctype 字段应为 DocType")

    fields = data.get("fields") or []
    if not fields:
        fail(f"{rel}: 没有任何字段")

    fieldnames = [field.get("fieldname") for field in fields]
    if len(set(fieldnames)) != len(fieldnames):
        dupes = {f for f in fieldnames if fieldnames.count(f) > 1}
        fail(f"{rel}: 存在重复 fieldname {dupes}")

    # field_order 必须与 fields 完全一致（多了少了一律不匹配，Frappe 会静默丢渲染）
    declared = [field for field in (data.get("field_order") or [])]
    actual = [field for field in fieldnames]
    if declared != actual:
        missing = [field for field in actual if field not in declared]
        extra = [field for field in declared if field not in actual]
        fail(f"{rel}: field_order 与 fields 不一致 (缺少 {missing} / 多余 {extra})")

    # Select 选项需要留出首位空值给用户"不筛选"，这是 Frappe 的惯例
    for field in fields:
        fieldname = field.get("fieldname")
        options = field.get("options") or ""
        if field.get("fieldtype") == "Select" and fieldname in SELECT_SOURCES:
            source_key = SELECT_SOURCES[fieldname]
            expected = CONSTANTS.get(source_key) or []
            actual_options = [opt for opt in options.split("\n") if opt]
            if not set(expected).issubset(set(actual_options)):
                missing = set(expected) - set(actual_options)
                fail(
                    f"{rel}: 字段 {fieldname} 的 Select 选项与 constants.{source_key} 不一致，缺少 {missing}"
                )
        if field.get("fieldtype") == "Table":
            target = field.get("options") or ""
            # DocType 名可以带空格，目录名一律小写 + 下划线
            target_slug = frappe_scrub(target)
            if not list(APP_DIR.glob(f"**/doctype/{target_slug}/{target_slug}.json")):
                fail(f"{rel}: 子表字段 {fieldname} 指向的 {target} 不存在")

    if not data.get("permissions") and not data.get("istable"):
        warn(f"{rel}: 未配置 permissions，普通角色将无法访问")

    print(f"  ✓ DocType {name}")


# ---------------------------------------------------------------------------
def check_report_json(path: Path) -> None:
    data = load_json(path)
    if not data:
        return
    rel = path.relative_to(ROOT)
    for key in ("report_name", "report_type", "module", "ref_doctype"):
        if not data.get(key):
            fail(f"{rel}: 缺少必需字段 {key}")
    if data.get("is_standard") != "Yes":
        fail(f"{rel}: 随 App 发布的报表必须设置 is_standard=Yes")

    report_type = data.get("report_type")
    if report_type == "Script Report":
        py_file = path.parent / f"{path.parent.name}.py"
        if not py_file.exists():
            fail(f"{rel}: Script Report 缺少同名 Python 文件")
        else:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
            if "execute" not in functions:
                fail(f"{py_file.name}: Script Report 必须定义 execute() 函数")
    print(f"  ✓ Report {data.get('report_name')}")


def frappe_scrub(value: str) -> str:
    """模拟 Frappe 的 scrub()：空格/连字符转下划线并小写。"""
    return re.sub(r"[-\s]+", "_", (value or "").strip()).lower()


def check_page_json(path: Path) -> None:
    data = load_json(path)
    if not data:
        return
    rel = path.relative_to(ROOT)
    page_name = data.get("page_name")

    # page_name 用连字符、目录用下划线是 Frappe 的惯例（如 point-of-sale）
    if frappe_scrub(page_name) != path.parent.name:
        warn(
            f"{rel}: page_name({page_name}) 与目录名({path.parent.name}) 不一致，建议保持 scrub 后一致"
        )

    js_files = list(path.parent.glob("*.js"))
    if not js_files:
        fail(f"{rel}: 页面目录下没有任何 .js 文件")
    elif not (path.parent / f"{frappe_scrub(page_name)}.js").exists():
        fail(
            f"{rel}: 期望存在 {frappe_scrub(page_name)}.js，实际为 "
            f"{[p.name for p in js_files]}"
        )
    # 页面 JS 必须注册 frappe.pages[...]，否则打不开（白屏）
    for js_file in js_files:
        content = js_file.read_text(encoding="utf-8")
        if "frappe.pages[" not in content and page_name not in content:
            warn(f"{js_file.relative_to(ROOT)}: 未发现 frappe.pages 注册语句")
    print(f"  ✓ Page {data.get('title') or page_name}")


# ---------------------------------------------------------------------------
def check_python_ast(path: Path) -> None:
    """检查 Python 源码不得出现非 ASCII 标识符（手滑混进中文变量/函数名的护栏）。"""
    rel = path.relative_to(ROOT)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        fail(f"{rel}: Python 语法错误 {exc}")
        return

    offenders: list[str] = []

    def check_identifier(identifier: str, kind: str) -> None:
        if identifier and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
            offenders.append(f"{kind}:{identifier}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check_identifier(node.name, type(node).__name__)
            for arg in node.args.args:
                check_identifier(arg.arg, "arg")
        elif isinstance(node, ast.ClassDef):
            check_identifier(node.name, "ClassDef")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            check_identifier(node.id, "variable")
        elif isinstance(node, ast.arg):
            check_identifier(node.arg, "arg")

    if offenders:
        fail(f"{rel}: 发现非 ASCII 标识符 {offenders[:6]}")


# ---------------------------------------------------------------------------
def check_packages() -> None:
    """Frappe 要求每个 Python 目录都有 __init__.py，否则 import 会出各种诡异问题。"""
    for directory in APP_DIR.rglob("*"):
        if not directory.is_dir():
            continue
        if directory.name in {"__pycache__", ".git"}:
            continue
        if not (directory / "__init__.py").exists():
            warn(f"目录缺少 __init__.py: {directory.relative_to(ROOT)}")


def check_modules() -> None:
    modules_file = APP_DIR / "modules.txt"
    if not modules_file.exists():
        fail("缺少 modules.txt")
        return
    for line in modules_file.read_text(encoding="utf-8").splitlines():
        module = line.strip()
        if not module or module.startswith("#"):
            continue
        slug = module.lower().replace(" ", "_")
        if not (APP_DIR / slug).is_dir():
            fail(f"modules.txt 中的模块 {module} 没有对应目录 {slug}")
        else:
            print(f"  ✓ Module {module}")


def check_hooks() -> None:
    """hooks 里写的 dotted path 必须能解析到真实文件/函数。"""
    hooks_file = APP_DIR / "hooks.py"
    if not hooks_file.exists():
        fail("缺少 hooks.py")
        return
    print("  ✓ hooks.py 存在")

    tree = ast.parse(hooks_file.read_text(encoding="utf-8"))
    paths: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            value = node.value.value
            if isinstance(value, str) and value.startswith("growth_insight."):
                paths.append(value)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for key_node, value_node in zip(node.value.keys, node.value.values):
                if isinstance(value_node, ast.Constant) and isinstance(value_node.value, list):
                    paths.extend(
                        item.value
                        for item in value_node.value
                        if isinstance(item, ast.Constant)
                        and isinstance(item.value, str)
                        and item.value.startswith("growth_insight.")
                    )
                elif isinstance(value_node, ast.Constant) and isinstance(
                    value_node.value, str
                ) and value_node.value.startswith("growth_insight."):
                    paths.append(value_node.value)
                del key_node

    for dotted in paths:
        parts = dotted.split(".")
        # 点路径的第一个分量是包名本身（=APP_DIR），后面的才需要解析
        parts = parts[1:]
        resolved = _resolve_dotted(parts)
        if resolved is None:
            fail(f"hooks.py 中的 {dotted} 解析不到真实文件")
            continue

        target_file, attribute = resolved
        tree = ast.parse(target_file.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for assign_target in node.targets:
                    if isinstance(assign_target, ast.Name):
                        names.add(assign_target.id)
        if attribute is not None and attribute not in names:
            fail(
                f"hooks.py 中的 {dotted} 在 {target_file.relative_to(ROOT)} 找不到对象 {attribute}"
            )


def _resolve_dotted(parts: list[str]):
    """
    把一个 dotted path 解析成 ``(文件, 属性名|None)``。

    兼容 Frappe 里两种常见写法：
    * ``setup.install.before_install`` -> ``setup/install.py`` 里的 ``before_install``
    * ``patches.v1_0.something``       -> ``patches/v1_0/something.py``
    """
    # 1) 最后一段是属性，前面是模块
    for split in range(len(parts) - 1, 0, -1):
        module_parts = parts[:split]
        attribute = parts[split]
        module_file = APP_DIR / ("/".join(module_parts) + ".py")
        if module_file.exists():
            return module_file, attribute

    # 2) 整体就是一个包
    package_init = APP_DIR.joinpath(*parts) / "__init__.py"
    if package_init.exists():
        return package_init, None

    return None


# ---------------------------------------------------------------------------
def main() -> int:
    print("Growth Insight 仓库自检\n" + "-" * 60)

    print("DocType:")
    for path in APP_DIR.glob("**/doctype/*/*.json"):
        check_doctype_json(path)

    print("\nReport:")
    for path in APP_DIR.glob("**/report/*/*.json"):
        check_report_json(path)

    print("\nPage:")
    for path in APP_DIR.glob("**/page/*/*.json"):
        check_page_json(path)

    print("\n模块:")
    check_modules()

    print("\nPython 源码:")
    for path in APP_DIR.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        check_python_ast(path)
    print(f"  ✓ 已检查 {len(list(APP_DIR.rglob('*.py')))} 个 Python 文件")

    print("\n包结构:")
    check_packages()

    print("\nhooks:")
    check_hooks()

    print("\n" + "-" * 60)
    if warnings:
        print(f"警告 {len(warnings)} 项:")
        for item in warnings:
            print(f"  ! {item}")
    if errors:
        print(f"错误 {len(errors)} 项:")
        for item in errors:
            print(f"  ✗ {item}")
        return 1
    print("全部检查通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
