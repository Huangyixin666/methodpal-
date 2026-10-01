from __future__ import annotations

import math
import uuid
from typing import Any, Dict, List, Optional

import pandas as pd

from app.schemas.dataset import Cell, CellIssue, DatasetProfile, DatasetProfileColumn, DatasetResponse
from app.services.store import DATASETS

# The first response contains an initial page only. The frontend virtual grid requests
# later pages on demand, while backend storage and analysis always keep all rows.
DEFAULT_PREVIEW_LIMIT = 200


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def to_float(value: Any) -> Optional[float]:
    try:
        text = clean_text(value)
        if text == "":
            return None
        return float(text)
    except Exception:
        return None


def _round_float(value: Any, digits: int = 4) -> Optional[float]:
    try:
        if value is None:
            return None
        num = float(value)
        if math.isnan(num) or math.isinf(num):
            return None
        return round(num, digits)
    except Exception:
        return None


def _normalize_rows(headers: List[str], rows: List[List[Any]]) -> List[List[str]]:
    width = len(headers)
    normalized: List[List[str]] = []
    for row in rows or []:
        if isinstance(row, dict):
            values = [row.get(h, "") for h in headers]
        else:
            values = list(row or [])
        values = values + [""] * max(0, width - len(values))
        normalized.append([clean_text(v) for v in values[:width]])
    return normalized


def col_mean(rows: List[List[str]], col_idx: int) -> str:
    values: List[float] = []
    for row in rows:
        if col_idx < len(row):
            num = to_float(row[col_idx])
            if num is not None:
                values.append(num)
    if not values:
        return "—"
    return "{:.1f}".format(sum(values) / len(values))


def infer_type(values: List[str]) -> str:
    non_empty = [v for v in values if clean_text(v) not in {"", "缺失"}]
    if not non_empty:
        return "未知"

    unique_values = set(non_empty)
    nums = [to_float(v) for v in non_empty]
    numeric_rate = sum(v is not None for v in nums) / max(1, len(non_empty))

    if len(unique_values) == 2:
        return "二分类"

    if numeric_rate >= 0.9:
        numeric_values = [v for v in nums if v is not None]
        if numeric_values and all(float(v).is_integer() for v in numeric_values):
            if len(unique_values) <= max(10, len(non_empty) * 0.05):
                return "有序型" if len(unique_values) > 2 else "二分类"
            return "整数型"
        return "连续型"

    unique = len(unique_values)
    if unique <= max(20, len(non_empty) * 0.2):
        return "分类型"
    return "文本型"


def role_suggestion(name: str, var_type: str) -> str:
    low = name.lower()
    if name in {"编号", "id", "ID"} or "编号" in name or low.endswith("id") or "序号" in name:
        return "id"
    if "得分" in name or "分数" in name or "score" in low or "结果" in name or "类型" in name:
        return "dependent"
    if name in {"年龄", "性别", "年级", "教育", "收入", "城市", "地区"}:
        return "control"
    if var_type in {"连续型", "整数型", "计数型", "有序型", "二分类", "分类型"}:
        return "independent"
    return "ignore"


def build_cells(headers: List[str], rows: List[List[str]]) -> List[List[Cell]]:
    cells: List[List[Cell]] = []
    for row in rows:
        padded = list(row) + [""] * max(0, len(headers) - len(row))
        row_cells: List[Cell] = []
        for ci, raw in enumerate(padded[: len(headers)]):
            value = clean_text(raw)
            header = headers[ci] if ci < len(headers) else ""
            num = to_float(value)
            issue: Optional[CellIssue] = None

            if value == "" or value == "缺失":
                suggestion = col_mean(rows, ci)
                issue = CellIssue(
                    type="missing",
                    suggestion=suggestion,
                    label="缺失值，建议填入列均值 {}".format(suggestion),
                )
            elif "睡眠" in header and num is not None and num > 14:
                suggestion = col_mean(rows, ci)
                if suggestion == "—":
                    suggestion = "6.5"
                issue = CellIssue(
                    type="outlier_high",
                    suggestion=suggestion,
                    label="异常偏高（{} 小时），建议替换为列均值 {}".format(value, suggestion),
                )
            elif ("得分" in header or "分数" in header or "score" in header.lower()) and num is not None and num > 100:
                suggestion = col_mean(rows, ci)
                if suggestion == "—":
                    suggestion = "70"
                issue = CellIssue(
                    type="outlier_high",
                    suggestion=suggestion,
                    label="超出量表范围（最大100），建议替换为均值 {}".format(suggestion),
                )
            elif num is not None and ("比例" in header or "percent" in header.lower()) and num < 0:
                issue = CellIssue(
                    type="outlier_low",
                    suggestion="0",
                    label="比例变量出现负值（{}），建议复查或设为 0".format(value),
                )

            row_cells.append(Cell(value=value, issue=issue))
        cells.append(row_cells)
    return cells


def _column_profile(name: str, values: List[str]) -> DatasetProfileColumn:
    clean_values = [clean_text(v) for v in values]
    non_missing = [v for v in clean_values if v not in {"", "缺失"}]
    miss = len(clean_values) - len(non_missing)
    var_type = infer_type(clean_values)

    nums = [to_float(v) for v in non_missing]
    numeric_values = [v for v in nums if v is not None]
    numeric_rate = len(numeric_values) / max(1, len(non_missing))

    stats: Dict[str, Any] = {}
    if numeric_values and numeric_rate >= 0.8 and var_type not in {"ID型", "文本型"}:
        s = pd.Series(numeric_values, dtype="float64")
        stats.update({
            "mean": _round_float(s.mean()),
            "std": _round_float(s.std(ddof=1)) if len(s) > 1 else None,
            "min": _round_float(s.min()),
            "q1": _round_float(s.quantile(0.25)),
            "median": _round_float(s.median()),
            "q3": _round_float(s.quantile(0.75)),
            "max": _round_float(s.max()),
        })

    top_values = []
    try:
        counts = pd.Series(non_missing, dtype="object").value_counts().head(8)
        total = max(1, len(non_missing))
        top_values = [{"value": str(k), "count": int(v), "rate": round(float(v) / total, 4)} for k, v in counts.items()]
    except Exception:
        top_values = []

    return DatasetProfileColumn(
        name=name,
        type=var_type,
        missing_count=miss,
        missing_rate=round(miss / max(1, len(clean_values)), 4),
        unique_count=len(set(non_missing)),
        role_suggestion=role_suggestion(name, var_type),
        non_missing_count=len(non_missing),
        numeric_count=len(numeric_values),
        numeric_rate=round(numeric_rate, 4),
        top_values=top_values,
        **stats,
    )


def profile_dataset(headers: List[str], rows: List[List[str]], cells: List[List[Cell]]) -> DatasetProfile:
    issue_count = sum(1 for row in cells for cell in row if cell.issue is not None)
    missing_count = sum(1 for row in cells for cell in row if cell.issue and cell.issue.type == "missing")
    outlier_count = issue_count - missing_count
    columns: List[DatasetProfileColumn] = []

    for ci, name in enumerate(headers):
        values = [clean_text(row[ci]) if ci < len(row) else "" for row in rows]
        columns.append(_column_profile(name, values))

    if issue_count:
        summary = "检测到 {} 处缺失值和 {} 处异常值。".format(missing_count, outlier_count)
    else:
        summary = "未发现明显数据质量问题。"

    return DatasetProfile(
        row_count=len(rows),
        column_count=len(headers),
        issue_count=issue_count,
        missing_count=missing_count,
        outlier_count=outlier_count,
        columns=columns,
        summary=summary,
    )


def _store_dataset(response: DatasetResponse, full_rows: List[List[str]], full_cells: List[List[Cell]], preview_limit: int) -> None:
    existing = DATASETS.get(response.dataset_id) or {}
    payload = response.model_dump()
    payload["_full_rows"] = full_rows
    # Store values once. Sparse annotations avoid serializing hundreds of
    # thousands of redundant {value, issue: null, fixed: null} dictionaries.
    payload['_cell_annotations'] = {}
    for ri, row in enumerate(full_cells):
        for ci, cell in enumerate(row):
            annotation = cell.model_dump() if hasattr(cell, 'model_dump') else dict(cell)
            if annotation.get('issue') or annotation.get('fixed'):
                payload['_cell_annotations'][f'{ri}:{ci}'] = {k: v for k, v in annotation.items() if k != 'value' and v is not None}
    payload["_preview_limit"] = preview_limit
    # Preserve immutable source metadata across edits and structural changes.
    if isinstance(existing, dict) and existing.get("_source"):
        payload["_source"] = existing.get("_source")
        source = existing.get("_source") or {}
        payload["source_kind"] = payload.get("source_kind") or source.get("kind")
        payload["header_mode"] = payload.get("header_mode") or source.get("header_mode")
        payload["header_row"] = payload.get("header_row") or source.get("header_row")
        payload["can_reparse_header"] = bool(source.get("path"))
    DATASETS[response.dataset_id] = payload
    stored = DATASETS.get(response.dataset_id) or {}
    response.dataset_revision = stored.get('dataset_revision', 0)
    response.data_fingerprint = stored.get('data_fingerprint')


def dataset_response(
    headers: List[str],
    rows: List[List[Any]],
    name: str = "dataset",
    dataset_id: Optional[str] = None,
    preview_limit: int = DEFAULT_PREVIEW_LIMIT,
    source_kind: Optional[str] = None,
    header_mode: Optional[str] = None,
    header_row: Optional[int] = None,
    can_reparse_header: bool = False,
) -> DatasetResponse:
    cleaned_headers = [clean_text(h) or "变量{}".format(i + 1) for i, h in enumerate(headers)]
    full_rows = _normalize_rows(cleaned_headers, rows)
    full_cells = build_cells(cleaned_headers, full_rows)
    profile = profile_dataset(cleaned_headers, full_rows, full_cells)

    limit = max(1, int(preview_limit or DEFAULT_PREVIEW_LIMIT))
    preview_rows = full_rows[:limit]
    preview_cells = full_cells[:limit]
    is_preview = len(preview_rows) < len(full_rows)
    ds_id = dataset_id or "ds_{}".format(uuid.uuid4().hex[:8])

    if is_preview:
        msg = "后端已接收全量数据：{} 行 × {} 列，发现 {} 处可检查问题。编辑器将按需加载全部行，后续分析使用全量数据。".format(
            profile.row_count, profile.column_count, profile.issue_count
        )
    else:
        msg = "后端已接收数据：{} 行 × {} 列，发现 {} 处可检查问题。".format(
            profile.row_count, profile.column_count, profile.issue_count
        )

    response = DatasetResponse(
        dataset_id=ds_id,
        name=name,
        headers=cleaned_headers,
        rows=preview_rows,
        cells=preview_cells,
        profile=profile,
        message=msg,
        preview_row_count=len(preview_rows),
        full_row_count=len(full_rows),
        is_preview=is_preview,
        source_kind=source_kind,
        header_mode=header_mode if header_mode in {"row", "none"} else None,
        header_row=header_row,
        can_reparse_header=can_reparse_header,
    )
    _store_dataset(response, full_rows, full_cells, limit)
    return response


def dataframe_to_dataset(
    df: pd.DataFrame,
    name: str,
    dataset_id: Optional[str] = None,
    source_kind: Optional[str] = None,
    header_mode: Optional[str] = None,
    header_row: Optional[int] = None,
    can_reparse_header: bool = False,
) -> DatasetResponse:
    # Store the full dataframe for analysis; only the response may be previewed.
    df = df.copy()
    headers = [clean_text(c) or "变量{}".format(i + 1) for i, c in enumerate(df.columns.tolist())]
    rows = df.astype(object).where(pd.notnull(df), "").values.tolist()
    return dataset_response(
        headers,
        rows,
        name=name,
        dataset_id=dataset_id,
        source_kind=source_kind,
        header_mode=header_mode,
        header_row=header_row,
        can_reparse_header=can_reparse_header,
    )


def get_dataset(dataset_id: str) -> Optional[Dict[str, Any]]:
    return DATASETS.get(dataset_id)


def dataset_dict_to_response(dataset: Dict[str, Any]) -> DatasetResponse:
    return DatasetResponse(
        dataset_revision=dataset.get('dataset_revision', 0),
        data_fingerprint=dataset.get('data_fingerprint'),
        dataset_id=dataset.get("dataset_id"),
        name=dataset.get("name") or "dataset",
        headers=dataset.get("headers") or [],
        rows=dataset.get("rows") or [],
        cells=dataset.get("cells") or [],
        profile=dataset.get("profile") or {},
        message=dataset.get("message") or "数据集已载入。",
        preview_row_count=int(dataset.get("preview_row_count") or len(dataset.get("rows") or [])),
        full_row_count=int(dataset.get("full_row_count") or (dataset.get("profile") or {}).get("row_count") or len(dataset.get("_full_rows") or dataset.get("rows") or [])),
        is_preview=bool(dataset.get("is_preview")),
        source_kind=dataset.get("source_kind") or (dataset.get("_source") or {}).get("kind"),
        header_mode=dataset.get("header_mode") or (dataset.get("_source") or {}).get("header_mode"),
        header_row=dataset.get("header_row") or (dataset.get("_source") or {}).get("header_row"),
        can_reparse_header=bool(dataset.get("can_reparse_header") or (dataset.get("_source") or {}).get("path")),
    )


def update_dataset_from_client(dataset_id: str, headers: List[str], rows: List[List[Any]], name: str = "dataset") -> DatasetResponse:
    existing = get_dataset(dataset_id) or {}
    old_full_rows = existing.get("_full_rows") or existing.get("rows") or []
    old_headers = existing.get("headers") or []
    new_headers = [clean_text(h) or "变量{}".format(i + 1) for i, h in enumerate(headers or [])]
    client_rows = _normalize_rows(new_headers, rows or [])

    # If the frontend only sent a preview of a large dataset, preserve non-preview rows.
    old_full_count = len(old_full_rows)
    existing_preview_count = int(existing.get("preview_row_count") or len(existing.get("rows") or []))
    sent_is_preview = old_full_count > len(client_rows) and existing_preview_count >= len(client_rows)

    if sent_is_preview:
        # Map full rows to the current header layout, so column deletions are applied globally.
        mapped_full: List[List[str]] = []
        for old_row in old_full_rows:
            old_row_list = list(old_row or [])
            mapped_row: List[str] = []
            for h in new_headers:
                if h in old_headers:
                    idx = old_headers.index(h)
                    mapped_row.append(clean_text(old_row_list[idx]) if idx < len(old_row_list) else "")
                else:
                    mapped_row.append("")
            mapped_full.append(mapped_row)

        for i, row in enumerate(client_rows):
            if i < len(mapped_full):
                mapped_full[i] = row
            else:
                mapped_full.append(row)
        full_rows = mapped_full
    else:
        full_rows = client_rows

    response = dataset_response(new_headers, full_rows, name=name or existing.get("name") or "dataset", dataset_id=dataset_id)
    response.message = "已保存编辑并基于{}行全量数据重新诊断。".format(response.profile.row_count)
    _store_dataset(response, full_rows, build_cells(response.headers, full_rows), DEFAULT_PREVIEW_LIMIT)
    return response


def build_clean_suggestions(dataset: Dict[str, Any]) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []
    headers = dataset.get("headers") or []
    if '_cell_annotations' in dataset:
        rows = dataset.get('_full_rows') or dataset.get('rows') or []
        for key, annotation in dataset['_cell_annotations'].items():
            ri, ci = map(int, key.split(':'))
            issue = annotation.get('issue')
            if issue:
                suggestions.append({'row': ri, 'col': ci, 'column': headers[ci], 'issue_type': issue.get('type'),
                    'current_value': rows[ri][ci], 'suggestion': issue.get('suggestion'), 'message': issue.get('label')})
        return suggestions
    cells = dataset.get("_full_cells") or dataset.get("cells") or []
    for ri, row in enumerate(cells):
        for ci, cell in enumerate(row):
            issue = cell.get("issue") if isinstance(cell, dict) else getattr(cell, "issue", None)
            if issue:
                value = cell.get("value", "") if isinstance(cell, dict) else getattr(cell, "value", "")
                suggestions.append(
                    {
                        "row": ri,
                        "col": ci,
                        "column": headers[ci] if ci < len(headers) else "变量{}".format(ci + 1),
                        "issue_type": issue.get("type") if isinstance(issue, dict) else issue.type,
                        "current_value": value,
                        "suggestion": issue.get("suggestion") if isinstance(issue, dict) else issue.suggestion,
                        "message": issue.get("label") if isinstance(issue, dict) else issue.label,
                    }
                )
    return suggestions


def apply_suggestions(dataset: Dict[str, Any]) -> DatasetResponse:
    headers = dataset.get("headers") or []
    rows = [list(row) for row in (dataset.get("_full_rows") or dataset.get("rows") or [])]
    suggestions = build_clean_suggestions(dataset)
    for s in suggestions:
        if s["suggestion"] != "—" and s["row"] < len(rows) and s["col"] < len(rows[s["row"]]):
            rows[s["row"]][s["col"]] = str(s["suggestion"])
    cleaned = dataset_response(headers, rows, name="{}_cleaned".format(dataset.get("name", "dataset")))
    cleaned.message = "已应用 {} 条清洗建议，生成新的清洗后数据集。".format(len(suggestions))
    return cleaned


def dataset_rows_page(dataset: Dict[str, Any], offset: int = 0, limit: int = DEFAULT_PREVIEW_LIMIT) -> Dict[str, Any]:
    full_rows = dataset.get("_full_rows") or dataset.get("rows") or []
    full_cells = dataset.get("_full_cells") or dataset.get("cells") or []
    total = len(full_rows)
    start = min(max(0, int(offset)), total)
    page_limit = min(max(1, int(limit)), 1000)
    end = min(total, start + page_limit)
    page_cells = full_cells[start:end]
    if '_cell_annotations' in dataset:
        annotations = dataset['_cell_annotations']
        page_cells = [[{'value': value, **annotations.get(f'{ri}:{ci}', {})} for ci, value in enumerate(full_rows[ri])] for ri in range(start, end)]
    return {
        "dataset_id": dataset.get("dataset_id"),
        "offset": start,
        "limit": page_limit,
        "total_rows": total,
        "rows": full_rows[start:end],
        "cells": page_cells,
        "has_more": end < total,
    }


def patch_dataset_cells(dataset_id: str, changes: List[Any]) -> DatasetResponse:
    existing = get_dataset(dataset_id)
    if not existing:
        raise KeyError("dataset not found")
    headers = list(existing.get("headers") or [])
    full_rows = [list(row) for row in (existing.get("_full_rows") or existing.get("rows") or [])]
    applied = 0
    for change in changes or []:
        if isinstance(change, dict):
            row = int(change.get("row", -1))
            col = int(change.get("col", -1))
            value = change.get("value", "")
        else:
            row = int(getattr(change, "row", -1))
            col = int(getattr(change, "col", -1))
            value = getattr(change, "value", "")
        if row < 0 or row >= len(full_rows):
            raise ValueError("行号超出范围：{}".format(row + 1))
        if col < 0 or col >= len(headers):
            raise ValueError("列号超出范围：{}".format(col + 1))
        while len(full_rows[row]) < len(headers):
            full_rows[row].append("")
        full_rows[row][col] = clean_text(value)
        applied += 1
    response = dataset_response(
        headers,
        full_rows,
        name=existing.get("name") or "dataset",
        dataset_id=dataset_id,
        preview_limit=int(existing.get("_preview_limit") or DEFAULT_PREVIEW_LIMIT),
    )
    response.message = "已批量保存 {} 个单元格，并基于 {} 行全量数据重新诊断。".format(applied, response.profile.row_count)
    stored = get_dataset(dataset_id)
    if stored is not None:
        stored["message"] = response.message
    return response


def _unique_header_name(headers: List[str], requested: str) -> str:
    base = clean_text(requested) or "新变量"
    if base not in headers:
        return base
    suffix = 2
    while "{}{}".format(base, suffix) in headers:
        suffix += 1
    return "{}{}".format(base, suffix)


def insert_dataset_rows(dataset_id: str, index: Optional[int] = None, count: int = 1) -> DatasetResponse:
    existing = get_dataset(dataset_id)
    if not existing:
        raise KeyError("dataset not found")
    headers = list(existing.get("headers") or [])
    full_rows = [list(row) for row in (existing.get("_full_rows") or existing.get("rows") or [])]
    insert_at = len(full_rows) if index is None else min(max(0, int(index)), len(full_rows))
    safe_count = min(max(1, int(count)), 100)
    for offset in range(safe_count):
        full_rows.insert(insert_at + offset, [""] * len(headers))
    response = dataset_response(headers, full_rows, name=existing.get("name") or "dataset", dataset_id=dataset_id, preview_limit=int(existing.get("_preview_limit") or DEFAULT_PREVIEW_LIMIT))
    response.message = "已在第 {} 行位置新增 {} 行，并基于 {} 行全量数据重新诊断。".format(insert_at + 1, safe_count, response.profile.row_count)
    stored = get_dataset(dataset_id)
    if stored is not None:
        stored["message"] = response.message
        DATASETS[dataset_id] = stored
    return dataset_dict_to_response(get_dataset(dataset_id) or response.model_dump())


def delete_dataset_rows(dataset_id: str, indices: List[int]) -> DatasetResponse:
    existing = get_dataset(dataset_id)
    if not existing:
        raise KeyError("dataset not found")
    headers = list(existing.get("headers") or [])
    full_rows = [list(row) for row in (existing.get("_full_rows") or existing.get("rows") or [])]
    valid = sorted({int(index) for index in indices if 0 <= int(index) < len(full_rows)}, reverse=True)
    if not valid:
        raise ValueError("请选择要删除的有效行")
    for index in valid:
        full_rows.pop(index)
    response = dataset_response(headers, full_rows, name=existing.get("name") or "dataset", dataset_id=dataset_id, preview_limit=int(existing.get("_preview_limit") or DEFAULT_PREVIEW_LIMIT))
    response.message = "已删除 {} 行，当前剩余 {} 行数据。".format(len(valid), response.profile.row_count)
    stored = get_dataset(dataset_id)
    if stored is not None:
        stored["message"] = response.message
        DATASETS[dataset_id] = stored
    return dataset_dict_to_response(get_dataset(dataset_id) or response.model_dump())


def insert_dataset_column(dataset_id: str, name: str, index: Optional[int] = None, default_value: str = "") -> DatasetResponse:
    existing = get_dataset(dataset_id)
    if not existing:
        raise KeyError("dataset not found")
    headers = list(existing.get("headers") or [])
    full_rows = [list(row) for row in (existing.get("_full_rows") or existing.get("rows") or [])]
    final_name = _unique_header_name(headers, name)
    insert_at = len(headers) if index is None else min(max(0, int(index)), len(headers))
    headers.insert(insert_at, final_name)
    value = clean_text(default_value)
    for row in full_rows:
        while len(row) < len(headers) - 1:
            row.append("")
        row.insert(insert_at, value)
    response = dataset_response(headers, full_rows, name=existing.get("name") or "dataset", dataset_id=dataset_id, preview_limit=int(existing.get("_preview_limit") or DEFAULT_PREVIEW_LIMIT))
    response.message = "已新增变量“{}”，当前共 {} 列。".format(final_name, response.profile.column_count)
    stored = get_dataset(dataset_id)
    if stored is not None:
        stored["message"] = response.message
        DATASETS[dataset_id] = stored
    return dataset_dict_to_response(get_dataset(dataset_id) or response.model_dump())


def delete_dataset_columns(dataset_id: str, indices: List[int]) -> DatasetResponse:
    existing = get_dataset(dataset_id)
    if not existing:
        raise KeyError("dataset not found")
    headers = list(existing.get("headers") or [])
    if len(headers) <= 1:
        raise ValueError("至少需要保留 1 列")
    valid = sorted({int(index) for index in indices if 0 <= int(index) < len(headers)}, reverse=True)
    if not valid:
        raise ValueError("请选择要删除的有效列")
    if len(headers) - len(valid) < 1:
        raise ValueError("至少需要保留 1 列")
    removed = [headers[index] for index in reversed(valid)]
    full_rows = [list(row) for row in (existing.get("_full_rows") or existing.get("rows") or [])]
    for index in valid:
        headers.pop(index)
        for row in full_rows:
            if index < len(row):
                row.pop(index)
    response = dataset_response(headers, full_rows, name=existing.get("name") or "dataset", dataset_id=dataset_id, preview_limit=int(existing.get("_preview_limit") or DEFAULT_PREVIEW_LIMIT))
    response.message = "已删除变量 {}，当前剩余 {} 列。".format("、".join(removed), response.profile.column_count)
    stored = get_dataset(dataset_id)
    if stored is not None:
        stored["message"] = response.message
        DATASETS[dataset_id] = stored
    return dataset_dict_to_response(get_dataset(dataset_id) or response.model_dump())
