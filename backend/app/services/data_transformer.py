from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.services.data_profiler import clean_text, dataset_dict_to_response, dataset_response, get_dataset, to_float
from app.services.store import DATASETS


MISSING_TOKENS = {"", "缺失", "nan", "none", "null", "na", "n/a"}


def _full_rows(dataset: Dict[str, Any]) -> List[List[str]]:
    return [list(r or []) for r in (dataset.get("_full_rows") or dataset.get("rows") or [])]


def _headers(dataset: Dict[str, Any]) -> List[str]:
    return [str(h) for h in (dataset.get("headers") or [])]


def _col_idx(headers: List[str], column: str) -> int:
    if column in headers:
        return headers.index(column)
    # tolerant match: strip spaces and brackets.
    norm = _norm_name(column)
    for i, h in enumerate(headers):
        if _norm_name(h) == norm:
            return i
    raise ValueError("找不到变量：{}".format(column))


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "")).strip().lower()


def _norm_value(value: Any) -> str:
    text = clean_text(value)
    if text.endswith(".0"):
        try:
            num = float(text)
            if num.is_integer():
                return str(int(num))
        except Exception:
            pass
    return text


def _is_missing(value: Any) -> bool:
    return clean_text(value).lower() in MISSING_TOKENS


def _unique_non_missing(rows: List[List[str]], idx: int) -> List[str]:
    seen: Dict[str, None] = {}
    for row in rows:
        value = row[idx] if idx < len(row) else ""
        if _is_missing(value):
            continue
        seen.setdefault(_norm_value(value), None)
    return list(seen.keys())


def _numeric_columns(headers: List[str], rows: List[List[str]]) -> List[str]:
    cols: List[str] = []
    for ci, h in enumerate(headers):
        non_missing = 0
        nums = 0
        for row in rows:
            value = row[ci] if ci < len(row) else ""
            if _is_missing(value):
                continue
            non_missing += 1
            if to_float(value) is not None:
                nums += 1
        if non_missing > 0 and nums / max(1, non_missing) >= 0.8:
            cols.append(h)
    return cols


def _mentioned_columns(text: str, headers: List[str]) -> List[str]:
    compact = _norm_name(text)
    hits = []
    for h in sorted(headers, key=lambda x: len(str(x)), reverse=True):
        if h and (str(h) in text or _norm_name(h) in compact):
            hits.append(str(h))
    # Preserve header order and remove duplicates.
    return [h for h in headers if h in set(hits)]


def _find_column(text: str, headers: List[str], selected_variables: Optional[Dict[str, Any]] = None) -> Optional[str]:
    hits = _mentioned_columns(text, headers)
    if hits:
        # longest explicit mention usually reflects the target column.
        return sorted(hits, key=lambda x: len(str(x)), reverse=True)[0]
    selected_variables = selected_variables or {}
    dep = selected_variables.get("dependent_variable")
    if dep in headers:
        return dep
    return None


def _parse_percent(text: str, default: float = 1.0) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if not m:
        m = re.search(r"百分之\s*(\d+(?:\.\d+)?)", text)
    if not m:
        return default
    try:
        val = float(m.group(1))
        if val <= 0:
            return default
        return min(val, 49.0)
    except Exception:
        return default


def _default_binary_name(source: str) -> str:
    if "类型" in source:
        base = source.replace("类型", "")
        return "是否{}".format(base or "为1")
    if source.startswith("是否"):
        return source
    return "是否{}".format(source)


def _extract_new_column(text: str, default_name: str) -> str:
    patterns = [
        r"生成(?:一个)?(?:新)?变量[“\"']?([^”\"'，,。\s]+)[”\"']?",
        r"新建(?:一个)?(?:新)?变量[“\"']?([^”\"'，,。\s]+)[”\"']?",
        r"命名为[“\"']?([^”\"'，,。\s]+)[”\"']?",
        r"叫做[“\"']?([^”\"'，,。\s]+)[”\"']?",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            name = clean_text(m.group(1))
            if name:
                return name
    return default_name


def detect_transform_intent(message: str, dataset_id: Optional[str], selected_variables: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Parse common natural-language data-cleaning requests into safe operations.

    This is intentionally rule-based. The LLM can phrase the explanation, but the
    actual operation must be structured and inspectable before execution.
    """
    text = clean_text(message)
    if not text or not dataset_id:
        return None
    dataset = get_dataset(dataset_id)
    if not dataset:
        return None
    headers = _headers(dataset)
    rows = _full_rows(dataset)
    selected_variables = selected_variables or {}

    # Binary recoding: 把除0外的类别合并为1 / 非0变为1 / 是否更新
    binary_markers = ["除0", "除 0", "非0", "非 0", "不是0", "不为0", "以外", "其他", "其余"]
    binary_actions = ["合并为1", "合并成1", "变成1", "设为1", "编码为1", "转成1", "二分类", "二值", "是否"]
    if any(m in text for m in binary_markers) and any(a in text for a in binary_actions):
        source = _find_column(text, headers, selected_variables)
        if source:
            return {
                "op": "recode_binary",
                "source_column": source,
                "new_column": _extract_new_column(text, _default_binary_name(source)),
                "negative_values": ["0", "0.0"],
                "positive_rule": "all_other_non_missing_values",
                "missing_policy": "keep_missing",
                "suggest_as_dependent": bool(selected_variables.get("dependent_variable") == source or "因变量" in text or "解释" in text or "影响" in text),
                "description": "将「{}」重编码为 0/1 二分类新变量。".format(source),
            }

    # Missing value imputation.
    if "缺失" in text and any(k in text for k in ["填", "补", "替换", "处理"]):
        strategy = "median"
        if "均值" in text or "平均" in text:
            strategy = "mean"
        elif "众数" in text or "最多" in text:
            strategy = "mode"
        elif "中位" in text:
            strategy = "median"
        cols = _mentioned_columns(text, headers) or selected_variables.get("independent_variables") or _numeric_columns(headers, rows)
        return {
            "op": "impute_missing",
            "columns": cols,
            "strategy": strategy,
            "description": "按{}填补缺失值。".format({"mean": "均值", "median": "中位数", "mode": "众数"}.get(strategy, strategy)),
        }

    # Winsorize / cap extremes.
    if any(k in text.lower() for k in ["winsor", "winsorize", "缩尾", "截尾", "压缩极端"]):
        pct = _parse_percent(text, 1.0)
        mentioned = _mentioned_columns(text, headers)
        cols = mentioned or selected_variables.get("independent_variables") or _numeric_columns(headers, rows)
        return {
            "op": "winsorize",
            "columns": cols,
            "lower_percent": pct,
            "upper_percent": pct,
            "description": "对数值变量进行上下 {}% 缩尾处理。".format(pct),
        }

    # Drop extreme rows.
    if any(k in text for k in ["去掉", "删除", "剔除", "移除"] ) and any(k in text for k in ["极端", "异常", "离群", "outlier"]):
        pct = _parse_percent(text, 1.0)
        mentioned = _mentioned_columns(text, headers)
        cols = mentioned or selected_variables.get("independent_variables") or _numeric_columns(headers, rows)
        return {
            "op": "remove_extremes",
            "columns": cols,
            "lower_percent": pct,
            "upper_percent": pct,
            "description": "删除指定数值变量上下 {}% 极端值所在行。".format(pct),
        }

    # Drop columns.
    if any(k in text for k in ["删除列", "删掉列", "去掉变量", "删除变量"]):
        cols = _mentioned_columns(text, headers)
        if cols:
            return {"op": "drop_columns", "columns": cols, "description": "删除变量：{}。".format("、".join(cols))}

    # One-hot encode.
    if any(k in text for k in ["哑变量", "独热", "one-hot", "one hot", "dummy"]):
        cols = _mentioned_columns(text, headers)
        if not cols:
            dep = selected_variables.get("dependent_variable")
            cols = [dep] if dep in headers else []
        if cols:
            return {"op": "one_hot_encode", "columns": cols, "drop_original": False, "description": "为分类变量生成哑变量。"}

    return None


def _ensure_width(rows: List[List[str]], width: int) -> List[List[str]]:
    out: List[List[str]] = []
    for row in rows:
        r = list(row or []) + [""] * max(0, width - len(row or []))
        out.append([clean_text(v) for v in r[:width]])
    return out


def _operation_title(op: Dict[str, Any]) -> str:
    names = {
        "recode_binary": "二值化/类别合并",
        "remove_extremes": "删除极端值",
        "winsorize": "缩尾处理",
        "impute_missing": "缺失值填补",
        "drop_columns": "删除变量",
        "one_hot_encode": "生成哑变量",
    }
    return names.get(str(op.get("op") or ""), str(op.get("op") or "数据转换"))


def _series_for_col(headers: List[str], rows: List[List[str]], col: str) -> pd.Series:
    idx = _col_idx(headers, col)
    values = []
    for row in rows:
        value = row[idx] if idx < len(row) else ""
        values.append(to_float(value))
    return pd.Series(values, dtype="float64")


def _apply_operation(headers: List[str], rows: List[List[str]], operation: Dict[str, Any]) -> Tuple[List[str], List[List[str]], Dict[str, Any], List[str]]:
    op = str(operation.get("op") or "").strip()
    headers = list(headers)
    rows = _ensure_width(rows, len(headers))
    warnings: List[str] = []
    summary: Dict[str, Any] = {"operation": op, "before_rows": len(rows), "before_columns": len(headers)}

    if op == "recode_binary":
        source = clean_text(operation.get("source_column"))
        new_col = clean_text(operation.get("new_column")) or _default_binary_name(source)
        source_idx = _col_idx(headers, source)
        neg_values = set(_norm_value(v) for v in (operation.get("negative_values") or ["0", "0.0"]))
        new_headers = list(headers)
        if new_col in new_headers:
            new_idx = new_headers.index(new_col)
        else:
            new_headers.append(new_col)
            new_idx = len(new_headers) - 1
        new_rows: List[List[str]] = []
        counts = {"0": 0, "1": 0, "missing": 0}
        source_counts: Dict[str, int] = {}
        for row in rows:
            r = list(row) + [""] * max(0, len(new_headers) - len(row))
            raw = r[source_idx] if source_idx < len(r) else ""
            norm = _norm_value(raw)
            if _is_missing(norm):
                recoded = ""
                counts["missing"] += 1
            elif norm in neg_values:
                recoded = "0"
                counts["0"] += 1
            else:
                recoded = "1"
                counts["1"] += 1
            if not _is_missing(norm):
                source_counts[norm] = source_counts.get(norm, 0) + 1
            r[new_idx] = recoded
            new_rows.append(r)
        summary.update({
            "source_column": source,
            "new_column": new_col,
            "counts": counts,
            "source_top_values": sorted(source_counts.items(), key=lambda x: x[1], reverse=True)[:12],
            "changed_cells": len(rows),
            "added_columns": [new_col] if new_col not in headers else [],
        })
        if counts["1"] == 0 or counts["0"] == 0:
            warnings.append("转换后「{}」仍然只有一个有效类别，请检查原始取值或转换规则。".format(new_col))
        return new_headers, new_rows, summary, warnings

    if op == "remove_extremes":
        cols = [c for c in (operation.get("columns") or []) if c in headers]
        if not cols:
            cols = _numeric_columns(headers, rows)
        if not cols:
            raise ValueError("没有找到可用于删除极端值的数值变量。")
        lp = max(0.0, min(49.0, float(operation.get("lower_percent") or 1.0))) / 100.0
        up = 1.0 - max(0.0, min(49.0, float(operation.get("upper_percent") or 1.0))) / 100.0
        bounds: Dict[str, Dict[str, float]] = {}
        for c in cols:
            s = _series_for_col(headers, rows, c).dropna()
            if len(s) < 5:
                warnings.append("变量「{}」有效数值太少，已跳过极端值删除。".format(c))
                continue
            bounds[c] = {"lower": float(s.quantile(lp)), "upper": float(s.quantile(up))}
        keep_rows: List[List[str]] = []
        removed = 0
        for row in rows:
            remove = False
            for c, b in bounds.items():
                idx = _col_idx(headers, c)
                val = to_float(row[idx] if idx < len(row) else "")
                if val is None:
                    continue
                if val < b["lower"] or val > b["upper"]:
                    remove = True
                    break
            if remove:
                removed += 1
            else:
                keep_rows.append(row)
        summary.update({"columns": list(bounds.keys()), "bounds": bounds, "removed_rows": removed, "after_rows": len(keep_rows)})
        return headers, keep_rows, summary, warnings

    if op == "winsorize":
        cols = [c for c in (operation.get("columns") or []) if c in headers]
        if not cols:
            cols = _numeric_columns(headers, rows)
        if not cols:
            raise ValueError("没有找到可用于缩尾处理的数值变量。")
        lp = max(0.0, min(49.0, float(operation.get("lower_percent") or 1.0))) / 100.0
        up = 1.0 - max(0.0, min(49.0, float(operation.get("upper_percent") or 1.0))) / 100.0
        bounds: Dict[str, Dict[str, float]] = {}
        changed = 0
        for c in cols:
            s = _series_for_col(headers, rows, c).dropna()
            if len(s) < 5:
                warnings.append("变量「{}」有效数值太少，已跳过缩尾。".format(c))
                continue
            bounds[c] = {"lower": float(s.quantile(lp)), "upper": float(s.quantile(up))}
        new_rows = copy.deepcopy(rows)
        for row in new_rows:
            for c, b in bounds.items():
                idx = _col_idx(headers, c)
                val = to_float(row[idx] if idx < len(row) else "")
                if val is None:
                    continue
                capped = min(max(val, b["lower"]), b["upper"])
                if capped != val:
                    row[idx] = str(round(capped, 6)).rstrip("0").rstrip(".")
                    changed += 1
        summary.update({"columns": list(bounds.keys()), "bounds": bounds, "changed_cells": changed})
        return headers, new_rows, summary, warnings

    if op == "impute_missing":
        cols = [c for c in (operation.get("columns") or []) if c in headers]
        if not cols:
            cols = headers
        strategy = str(operation.get("strategy") or "median")
        new_rows = copy.deepcopy(rows)
        fills: Dict[str, str] = {}
        changed = 0
        for c in cols:
            idx = _col_idx(headers, c)
            values = [row[idx] if idx < len(row) else "" for row in rows]
            non_missing = [v for v in values if not _is_missing(v)]
            if not non_missing:
                warnings.append("变量「{}」全为空，无法填补。".format(c))
                continue
            nums = [to_float(v) for v in non_missing]
            numeric_values = [v for v in nums if v is not None]
            if strategy in {"mean", "median"} and len(numeric_values) / max(1, len(non_missing)) >= 0.8:
                s = pd.Series(numeric_values, dtype="float64")
                fill = float(s.mean()) if strategy == "mean" else float(s.median())
                fill_text = str(round(fill, 6)).rstrip("0").rstrip(".")
            else:
                fill_text = str(pd.Series(non_missing, dtype="object").mode().iloc[0])
            fills[c] = fill_text
            for row in new_rows:
                if idx < len(row) and _is_missing(row[idx]):
                    row[idx] = fill_text
                    changed += 1
        summary.update({"columns": cols, "strategy": strategy, "fills": fills, "changed_cells": changed})
        return headers, new_rows, summary, warnings

    if op == "drop_columns":
        cols = [c for c in (operation.get("columns") or []) if c in headers]
        if not cols:
            raise ValueError("没有指定需要删除的变量。")
        keep_indices = [i for i, h in enumerate(headers) if h not in set(cols)]
        new_headers = [headers[i] for i in keep_indices]
        new_rows = [[row[i] if i < len(row) else "" for i in keep_indices] for row in rows]
        summary.update({"dropped_columns": cols, "after_columns": len(new_headers)})
        return new_headers, new_rows, summary, warnings

    if op == "one_hot_encode":
        cols = [c for c in (operation.get("columns") or []) if c in headers]
        if not cols:
            raise ValueError("没有指定需要生成哑变量的分类变量。")
        new_headers = list(headers)
        new_rows = [list(row) for row in rows]
        added: List[str] = []
        for c in cols:
            idx = _col_idx(headers, c)
            uniques = _unique_non_missing(rows, idx)
            # protect UI from accidental huge expansion
            if len(uniques) > 20:
                warnings.append("变量「{}」类别超过20个，已跳过哑变量生成。".format(c))
                continue
            for val in uniques:
                col_name = "{}_{}".format(c, val)
                if col_name in new_headers:
                    continue
                new_headers.append(col_name)
                added.append(col_name)
                for ri, row in enumerate(new_rows):
                    raw = row[idx] if idx < len(row) else ""
                    row.append("1" if _norm_value(raw) == val else "0")
        summary.update({"columns": cols, "added_columns": added, "changed_cells": len(new_rows) * len(added)})
        return new_headers, new_rows, summary, warnings

    raise ValueError("暂不支持的数据转换操作：{}".format(op or "未指定"))


def _build_preview(old_headers: List[str], old_rows: List[List[str]], new_headers: List[str], new_rows: List[List[str]], limit: int = 12) -> List[Dict[str, Any]]:
    preview: List[Dict[str, Any]] = []
    max_rows = min(limit, max(len(old_rows), len(new_rows)))
    for ri in range(max_rows):
        before = {h: (old_rows[ri][ci] if ri < len(old_rows) and ci < len(old_rows[ri]) else "") for ci, h in enumerate(old_headers)}
        after = {h: (new_rows[ri][ci] if ri < len(new_rows) and ci < len(new_rows[ri]) else "") for ci, h in enumerate(new_headers)}
        changed = []
        for h in new_headers:
            if before.get(h, None) != after.get(h, None):
                changed.append(h)
        if changed or ri >= len(old_rows) or ri >= len(new_rows):
            preview.append({"row": ri + 1, "before": before, "after": after, "changed_columns": changed[:12]})
    return preview[:limit]


def _variable_patch(operation: Dict[str, Any], old_headers: List[str], new_headers: List[str], selected_variables: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not selected_variables:
        selected_variables = {}
    selection = copy.deepcopy(selected_variables)
    vtypes = dict(selection.get("variable_types") or {})
    op = operation.get("op")

    if op == "recode_binary":
        source = operation.get("source_column")
        new_col = operation.get("new_column")
        if new_col:
            vtypes[new_col] = "二分类"
            selection["variable_types"] = vtypes
            for key in ["independent_variables", "control_variables", "mediator_variables", "moderator_variables"]:
                vals = list(selection.get(key) or [])
                selection[key] = [v for v in vals if v != new_col]
            if operation.get("suggest_as_dependent") or selection.get("dependent_variable") == source:
                selection["dependent_variable"] = new_col
        return selection

    if op == "drop_columns":
        dropped = set(operation.get("columns") or [])
        if selection.get("dependent_variable") in dropped:
            selection["dependent_variable"] = ""
        for key in ["independent_variables", "control_variables", "mediator_variables", "moderator_variables"]:
            selection[key] = [v for v in (selection.get(key) or []) if v not in dropped]
        if selection.get("group_variable") in dropped:
            selection["group_variable"] = None
        for d in dropped:
            vtypes.pop(d, None)
        selection["variable_types"] = vtypes
        return selection

    if op == "one_hot_encode":
        for h in new_headers:
            if h not in old_headers:
                vtypes[h] = "二分类"
        selection["variable_types"] = vtypes
        return selection

    # For row-level or value-level transforms, keep the existing variable setup.
    if selected_variables:
        return selection
    return None


def preview_dataset_transform(dataset_id: str, operation: Dict[str, Any], selected_variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise ValueError("dataset not found")
    headers = _headers(dataset)
    rows = _full_rows(dataset)
    new_headers, new_rows, summary, warnings = _apply_operation(headers, rows, operation)
    summary.update({"after_rows": len(new_rows), "after_columns": len(new_headers)})
    patch = _variable_patch(operation, headers, new_headers, selected_variables)
    return {
        "ok": True,
        "operation": operation,
        "message": "已生成「{}」预览。确认后会生成一个新的数据版本，不会覆盖原始数据。".format(_operation_title(operation)),
        "summary": summary,
        "preview_rows": _build_preview(headers, rows, new_headers, new_rows),
        "warnings": warnings,
        "variable_selection_patch": patch,
    }


def apply_dataset_transform(dataset_id: str, operation: Dict[str, Any], selected_variables: Optional[Dict[str, Any]] = None, create_new_dataset: bool = True) -> Dict[str, Any]:
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise ValueError("dataset not found")
    headers = _headers(dataset)
    rows = _full_rows(dataset)
    new_headers, new_rows, summary, warnings = _apply_operation(headers, rows, operation)
    summary.update({"after_rows": len(new_rows), "after_columns": len(new_headers)})

    old_name = dataset.get("name") or "dataset"
    new_name = "{}__{}".format(old_name, _operation_title(operation)) if create_new_dataset else old_name
    ds_id = None if create_new_dataset else dataset_id
    response = dataset_response(new_headers, new_rows, name=new_name, dataset_id=ds_id)
    response.message = "已应用数据转换「{}」：{} 行 × {} 列。".format(_operation_title(operation), response.profile.row_count, response.profile.column_count)

    # Add lightweight transform history into stored payload.
    stored = DATASETS.get(response.dataset_id) or {}
    history = list(dataset.get("_transform_history") or [])
    history.append({"from_dataset_id": dataset_id, "operation": operation, "summary": summary})
    stored["_transform_history"] = history[-20:]
    stored["message"] = response.message
    DATASETS[response.dataset_id] = stored

    patch = _variable_patch(operation, headers, new_headers, selected_variables)
    response_obj = dataset_dict_to_response(stored)
    dataset_payload = response_obj.model_dump() if hasattr(response_obj, "model_dump") else response_obj.dict()
    return {
        "ok": True,
        "operation": operation,
        "message": response.message + " 已重新生成数据摘要和变量类型。",
        "dataset": dataset_payload,
        "summary": summary,
        "warnings": warnings,
        "variable_selection_patch": patch,
    }
