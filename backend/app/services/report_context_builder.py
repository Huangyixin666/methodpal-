from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

from app.services import store
from app.services.artifact_versions import chart_is_current, analysis_is_current
from fastapi import HTTPException

ANALYSES = store.ANALYSES
PROJECTS = store.PROJECTS
CHARTS = getattr(store, "CHARTS", {})
DATASETS = getattr(store, "DATASETS", {})


def as_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return value.dict()
        except Exception:
            pass
    if isinstance(value, dict):
        return value
    return {}


def safe_text(value: Any, fallback: str = "") -> str:
    text = str(value if value is not None else "").strip()
    return text or fallback


def fmt_number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    try:
        num = float(value)
        if math.isnan(num) or math.isinf(num):
            return "—"
        text = ("{:0." + str(digits) + "f}").format(num)
        return text.rstrip("0").rstrip(".")
    except Exception:
        return str(value)


def compact_json(obj: Any, max_chars: int = 16000) -> str:
    try:
        raw = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        raw = str(obj)
    if len(raw) > max_chars:
        raw = raw[:max_chars] + "\n...<已截断：报告写作只能使用已展示的统计事实>"
    return raw


def _to_list(value: Any) -> List[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _clean_list(value: Any) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in _to_list(value):
        text = safe_text(item)
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _first_present(mapping: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) is not None and mapping.get(key) != "":
            return mapping.get(key)
    return None


def load_project(project_id: Optional[str], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if project_id:
        item = PROJECTS.get(project_id)
        if item:
            return as_dict(item)
    ctx = context or {}
    return {
        "project_id": project_id,
        "name": ctx.get("project_name"),
        "research_goal": ctx.get("research_goal"),
    }


def _merge_dicts(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow merge helper that keeps current workspace values authoritative.

    The report page may still have an older project-level analysis_id after the
    user changes variables/model. When a fresh analysis_result is supplied in
    context, it is the source of truth; stored analysis records only fill missing
    fields when they refer to the same analysis.
    """
    merged = dict(base or {})
    for k, v in (overlay or {}).items():
        if v is not None and v != "":
            merged[k] = v
    return merged


def load_analysis(analysis_id: Optional[str], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ctx = context or {}
    ctx_analysis = as_dict(ctx.get("analysis_result") or ctx.get("analysis") or ctx.get("result"))
    ctx_analysis_id = safe_text(ctx_analysis.get("analysis_id"))

    if ctx_analysis:
        base: Dict[str, Any] = {}
        # Only enrich from storage if the IDs match the current workspace result.
        # Do not let activeProject.analysis_id revive a stale previous model.
        if ctx_analysis_id and ANALYSES.get(ctx_analysis_id):
            base = as_dict(ANALYSES.get(ctx_analysis_id))
        elif analysis_id and ctx_analysis_id and str(analysis_id) == ctx_analysis_id and ANALYSES.get(str(analysis_id)):
            base = as_dict(ANALYSES.get(str(analysis_id)))
        return _merge_dicts(base, ctx_analysis)

    if analysis_id:
        item = ANALYSES.get(str(analysis_id))
        if item:
            return as_dict(item)
    return {}


def load_dataset(dataset_id: Optional[str], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ctx = context or {}
    ctx_dataset = as_dict(ctx.get("dataset") or {})
    base: Dict[str, Any] = {}
    if dataset_id:
        item = DATASETS.get(str(dataset_id))
        if item:
            base = as_dict(item)
    # Current workspace dataset/profile should override stale storage fields, but
    # storage can still supply full rows/headers if the frontend context is compact.
    return _merge_dicts(base, ctx_dataset) if ctx_dataset else base


def _chart_analysis_id(chart: Dict[str, Any]) -> str:
    meta = as_dict(chart.get("meta"))
    return safe_text(chart.get("analysis_id") or meta.get("analysis_id"))


def _chart_dataset_id(chart: Dict[str, Any]) -> str:
    meta = as_dict(chart.get("meta"))
    aid = _chart_analysis_id(chart)
    if aid.startswith("dataset:"):
        return aid.split(":", 1)[1]
    return safe_text(chart.get("dataset_id") or meta.get("dataset_id"))


def _chart_is_current(chart: Dict[str, Any], current_analysis_id: str, dataset_id: Optional[str]) -> bool:
    """Reject charts generated for a previous analysis/model.

    Project-local chart cache is intentionally broad, so report generation must
    filter stale model-level charts after the user changes target variables or
    switches from classification to regression. Dataset-level charts, such as
    correlation heatmaps, remain valid when the dataset matches.
    """
    return chart_is_current(chart, current_analysis_id, DATASETS.get(dataset_id) or {})


def collect_charts(
    chart_ids: Optional[List[str]],
    charts: Optional[List[Dict[str, Any]]],
    current_analysis_id: str = "",
    dataset_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    out: List[Dict[str, Any]] = []
    stale: List[str] = []
    seen = set()

    def add_chart(item: Dict[str, Any]) -> None:
        if not item:
            return
        cid = str(item.get("chart_id") or "")
        if cid and CHARTS.get(cid):
            item = as_dict(CHARTS.get(cid))
        key = cid or "{}:{}:{}".format(item.get("chart_type"), _chart_analysis_id(item), len(out))
        if key in seen:
            return
        if not _chart_is_current(item, current_analysis_id, dataset_id):
            stale.append(cid or safe_text(item.get("chart_type"), "unknown_chart"))
            return
        out.append(item)
        seen.add(key)

    for chart in charts or []:
        add_chart(as_dict(chart))
    for cid in chart_ids or []:
        if not cid or cid in seen:
            continue
        add_chart(as_dict(CHARTS.get(cid)))
    return out, stale


def _numeric(value: Any) -> Optional[float]:
    try:
        num = float(value)
        if math.isnan(num) or math.isinf(num):
            return None
        return num
    except Exception:
        return None


def _extract_metrics(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten common metrics from stats and nested prediction_metrics.

    Model tools often store values in stats, prediction_metrics.test/train,
    and cross_validation. The report writer should see those actual numbers,
    not only top-level fields.
    """
    metrics: List[Dict[str, Any]] = []
    seen = set()

    def add(key: str, label: str, value: Any) -> None:
        if value is None or value == "":
            return
        skey = "{}:{}".format(key, label)
        if skey in seen:
            return
        seen.add(skey)
        metrics.append({"key": key, "label": label, "value": value, "formatted": fmt_number(value)})

    stats = as_dict(analysis.get("stats"))
    pm = as_dict(analysis.get("prediction_metrics") or analysis.get("metrics"))
    train = as_dict(pm.get("train"))
    test = as_dict(pm.get("test"))
    cv = as_dict(pm.get("cross_validation"))

    # Prefer test/generalization metrics first.
    for key, label in [
        ("auc", "测试 AUC"),
        ("accuracy", "测试准确率"),
        ("precision", "测试 Precision"),
        ("recall", "测试 Recall"),
        ("f1", "测试 F1"),
        ("macro_f1", "测试 Macro F1"),
        ("r2", "测试 R²"),
        ("rmse", "测试 RMSE"),
        ("mae", "测试 MAE"),
        ("log_loss", "测试 Log loss"),
    ]:
        add("test_" + key, label, test.get(key))

    for key, label in [
        ("accuracy", "训练准确率"),
        ("auc", "训练 AUC"),
        ("f1", "训练 F1"),
        ("r2", "训练 R²"),
        ("rmse", "训练 RMSE"),
        ("mae", "训练 MAE"),
    ]:
        add("train_" + key, label, train.get(key))

    for key, label in [
        ("cv_accuracy_mean", "交叉验证 Accuracy 均值"),
        ("cv_roc_auc_mean", "交叉验证 AUC 均值"),
        ("cv_f1_mean", "交叉验证 F1 均值"),
        ("cv_r2_mean", "交叉验证 R² 均值"),
        ("cv_rmse_mean", "交叉验证 RMSE 均值"),
        ("folds", "交叉验证折数"),
    ]:
        add(key, label, cv.get(key))

    # stats_out from individual model runners.
    for key, label in [
        ("test_auc", "测试 AUC"),
        ("test_accuracy", "测试准确率"),
        ("test_f1", "测试 F1"),
        ("test_r2", "测试 R²"),
        ("test_rmse", "测试 RMSE"),
        ("test_mae", "测试 MAE"),
        ("cv_accuracy_mean", "交叉验证 Accuracy 均值"),
        ("cv_auc_mean", "交叉验证 AUC 均值"),
        ("cv_roc_auc_mean", "交叉验证 AUC 均值"),
        ("cv_f1_mean", "交叉验证 F1 均值"),
        ("train_accuracy", "训练准确率"),
        ("train_auc", "训练 AUC"),
        ("r_squared", "R²"),
        ("adj_r_squared", "调整 R²"),
        ("aic", "AIC"),
        ("bic", "BIC"),
        ("class_count", "类别数"),
    ]:
        add(key, label, stats.get(key, analysis.get(key)))

    # Some old results put flat values under prediction_metrics itself.
    for key, label in [
        ("auc", "AUC"),
        ("accuracy", "准确率"),
        ("precision", "Precision"),
        ("recall", "Recall"),
        ("f1", "F1"),
        ("macro_f1", "Macro F1"),
        ("r2", "R²"),
        ("rmse", "RMSE"),
        ("mae", "MAE"),
    ]:
        add(key, label, pm.get(key, analysis.get(key)))

    return metrics


def _normalize_ranked_rows(value: Any, name_keys: List[str], value_keys: List[str], max_rows: int = 30) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            rows.append({"name": str(k), "value": v, "formatted": fmt_number(v)})
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                name = _first_present(item, name_keys) or _first_present(item, ["variable", "feature", "term", "name", "column", "model"])
                val = _first_present(item, value_keys) or _first_present(item, ["importance", "value", "score", "metric", "mean", "coefficient", "coef", "odds_ratio", "or"])
                if name is not None and val is not None:
                    copied = dict(item)
                    copied["name"] = str(name)
                    copied["value"] = val
                    copied["formatted"] = fmt_number(val)
                    rows.append(copied)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                rows.append({"name": str(item[0]), "value": item[1], "formatted": fmt_number(item[1])})
    try:
        rows.sort(key=lambda r: abs(float(r.get("value", 0))), reverse=True)
    except Exception:
        pass
    return rows[:max_rows]


def _extract_feature_importance(analysis: Dict[str, Any], charts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates = [
        analysis.get("feature_importance"),
        analysis.get("feature_importances"),
        analysis.get("variable_importance"),
        analysis.get("importances"),
        analysis.get("result_table"),
    ]
    for chart in charts:
        if "feature_importance" in str(chart.get("chart_type") or ""):
            candidates.append(chart.get("data") or [])
    for value in candidates:
        rows = _normalize_ranked_rows(value, ["feature", "variable", "original_variable", "name"], ["importance", "value", "score"])
        if rows:
            return rows
    return []


def _extract_coefficients(analysis: Dict[str, Any], charts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates = [analysis.get("regression_table"), analysis.get("coefficients"), analysis.get("coef_table"), analysis.get("params"), analysis.get("terms")]
    for chart in charts:
        ctype = str(chart.get("chart_type") or "")
        if "coef" in ctype or "odds" in ctype:
            candidates.append(chart.get("data") or [])
    for value in candidates:
        rows = _normalize_ranked_rows(value, ["term", "variable", "feature", "name"], ["coefficient", "coef", "odds_ratio", "or", "value"])
        if rows:
            return rows
    return []


def _extract_confusion_matrix(analysis: Dict[str, Any], charts: List[Dict[str, Any]]) -> Dict[str, Any]:
    pm = as_dict(analysis.get("prediction_metrics") or analysis.get("metrics"))
    cm = analysis.get("confusion_matrix") or pm.get("confusion_matrix") or as_dict(analysis.get("classification_report")).get("confusion_matrix")
    diagnostics = as_dict(analysis.get("diagnostics") or analysis.get("model_diagnostics"))
    y_meta = as_dict(diagnostics.get("y_meta"))
    labels = analysis.get("class_labels") or analysis.get("labels") or y_meta.get("classes") or []
    if cm is not None:
        return {"matrix": cm, "labels": labels, "source": "analysis"}
    for chart in charts:
        if "confusion" in str(chart.get("chart_type") or ""):
            return {
                "matrix": chart.get("matrix") or chart.get("data"),
                "labels": chart.get("row_labels") or chart.get("column_labels") or [],
                "source": "chart",
            }
    return {}


def _dataset_context(dataset_id: Optional[str], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    ctx = context or {}
    dataset_obj = load_dataset(dataset_id, context)
    summary = as_dict(ctx.get("dataset_summary") or ctx.get("profile") or dataset_obj.get("profile"))
    headers = dataset_obj.get("headers") or ctx.get("headers") or summary.get("headers") or []
    rows = dataset_obj.get("_full_rows") or dataset_obj.get("rows") or ctx.get("rows") or []
    row_count = summary.get("row_count") or summary.get("n_rows") or ctx.get("row_count") or dataset_obj.get("full_row_count") or dataset_obj.get("row_count")
    col_count = summary.get("column_count") or summary.get("n_columns") or ctx.get("column_count") or len(headers or []) or dataset_obj.get("column_count")
    if not row_count and isinstance(rows, list):
        row_count = len(rows)
    missing = summary.get("missing") or summary.get("missing_values") or summary.get("missing_summary") or summary.get("column_missing") or {}
    outliers = summary.get("outliers") or summary.get("outlier_summary") or summary.get("issues") or []
    numeric = summary.get("numeric") or summary.get("numeric_stats") or summary.get("numeric_summary") or []
    categorical = summary.get("categorical") or summary.get("categorical_summary") or summary.get("categorical_stats") or []
    return {
        "dataset_id": dataset_id,
        "name": ctx.get("dataset_name") or dataset_obj.get("name") or ctx.get("name") or "当前数据集",
        "row_count": row_count,
        "column_count": col_count,
        "headers_preview": [str(x) for x in (headers or [])[:40]],
        "missing_summary": missing,
        "outlier_summary": outliers,
        "numeric_summary": numeric,
        "categorical_summary": categorical,
        "raw_profile": summary,
    }


def _role_map_variables(selection: Dict[str, Any]) -> Dict[str, List[str]]:
    roles = as_dict(selection.get("variable_roles") or selection.get("roles") or selection.get("role_map"))
    out = {"dependent": [], "independent": [], "control": [], "mediator": [], "moderator": [], "excluded": []}
    for var, role_raw in roles.items():
        var_name = safe_text(var)
        role = safe_text(role_raw).lower()
        if not var_name:
            continue
        if any(k in role for k in ["dependent", "target", "因变量", "结果变量", "y"]):
            out["dependent"].append(var_name)
        elif any(k in role for k in ["control", "控制"]):
            out["control"].append(var_name)
        elif any(k in role for k in ["mediator", "中介"]):
            out["mediator"].append(var_name)
        elif any(k in role for k in ["moderator", "调节"]):
            out["moderator"].append(var_name)
        elif any(k in role for k in ["exclude", "excluded", "unused", "ignore", "不用", "不使用", "无", "none"]):
            out["excluded"].append(var_name)
        elif any(k in role for k in ["independent", "predictor", "自变量", "解释变量", "x"]):
            out["independent"].append(var_name)
    return out


def _normalize_variables(selection: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize actual saved variable roles, preferring workspace state.

    Do not use earlier conversational suggestions as variable truth. The report
    should follow the saved variable selection from the Variables page; analysis
    fields are used only as fallback.
    """
    role_map = _role_map_variables(selection)
    dep_value = _first_present(selection, [
        "dependent_variable", "dependent_var", "target", "target_variable", "y", "因变量"
    ])
    dep_list = _first_present(selection, ["dependent_variables", "dependent_vars", "targets", "因变量列表"])
    dep_candidates = _clean_list(dep_list)
    dep = dep_value if dep_value is not None else (dep_candidates[0] if dep_candidates else (role_map["dependent"][0] if role_map["dependent"] else analysis.get("dependent_variable")))

    indeps = _first_present(selection, [
        "independent_variables", "independent_vars", "x_variables", "predictors", "features", "自变量"
    ])
    controls = _first_present(selection, ["control_variables", "control_vars", "controls", "控制变量"])
    mediators = _first_present(selection, ["mediator_variables", "mediator_vars", "mediators", "中介变量"])
    moderators = _first_present(selection, ["moderator_variables", "moderator_vars", "moderators", "调节变量"])
    excluded = _first_present(selection, [
        "excluded_variables", "excluded_vars", "ignore_variables", "unused_variables", "none_variables", "not_used_variables", "不使用变量", "不用变量"
    ])

    variable_types = as_dict(selection.get("variable_types") or selection.get("types") or selection.get("variable_type_map") or analysis.get("variable_types"))
    actual_source = "workspace_selected_variables" if selection else "analysis_fallback"
    return {
        "dependent_variable": safe_text(dep),
        "independent_variables": _clean_list(indeps) or role_map["independent"] or _clean_list(analysis.get("independent_variables")),
        "control_variables": _clean_list(controls) or role_map["control"] or _clean_list(analysis.get("control_variables")),
        "mediator_variables": _clean_list(mediators) or role_map["mediator"],
        "moderator_variables": _clean_list(moderators) or role_map["moderator"],
        "excluded_variables": _clean_list(excluded) or role_map["excluded"],
        "group_variable": safe_text(selection.get("group_variable") or analysis.get("group_variable")),
        "variable_types": variable_types,
        "source": actual_source,
    }

def _infer_model_family(model_name: str, analysis: Dict[str, Any]) -> str:
    text = "{} {} {}".format(model_name, safe_text(analysis.get("method")), safe_text(analysis.get("model"))).lower()
    if any(k in text for k in ["xgb", "xgboost", "catboost", "random forest", "随机森林", "tree", "forest"]):
        return "tree_ml"
    if any(k in text for k in ["logistic", "logit", "二元 logistic", "逻辑回归"]):
        return "logistic"
    if any(k in text for k in ["ols", "linear", "线性回归", "ridge", "lasso", "elastic"]):
        return "linear_regression"
    if any(k in text for k in ["svm", "sgd"]):
        return "ml_classifier"
    if any(k in text for k in ["poisson", "泊松"]):
        return "count_model"
    return "unknown"


def _guardrails(model_family: str, analysis: Dict[str, Any], metrics: List[Dict[str, Any]]) -> List[str]:
    warnings: List[str] = []
    raw_warnings = analysis.get("warnings") or analysis.get("diagnostic_warnings") or []
    if isinstance(raw_warnings, list):
        warnings.extend([safe_text(x) for x in raw_warnings if safe_text(x)])
    elif raw_warnings:
        warnings.append(safe_text(raw_warnings))

    if model_family in ["tree_ml", "ml_classifier"]:
        warnings.append("机器学习模型的变量重要性主要表示预测贡献，不等同于传统统计显著性，也不能直接解释为因果效应。")
    if model_family == "logistic":
        warnings.append("Logistic 回归可用于解释关联方向和优势比，但因果解释仍依赖研究设计、变量控制和识别假设。")
    if model_family == "linear_regression":
        warnings.append("线性回归系数应结合标准误、置信区间、p 值、共线性诊断和稳健性检验解释。")
    for metric in metrics:
        key = str(metric.get("key") or "")
        try:
            value = float(metric.get("value"))
        except Exception:
            continue
        if key in ["auc", "test_auc", "accuracy", "test_accuracy", "train_accuracy", "train_auc"] and value >= 0.98:
            warnings.append("模型指标接近 1.0，报告时建议检查过拟合、数据泄露或训练/测试划分是否合理。")
            break
    seen = set()
    out = []
    for item in warnings:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _matrix_summary(matrix: Any, row_labels: Any, column_labels: Any, chart_type: str) -> Dict[str, Any]:
    if not isinstance(matrix, list):
        return {}
    rows = [list(r) if isinstance(r, (list, tuple)) else [] for r in matrix]
    rlabels = [safe_text(x) for x in _to_list(row_labels)]
    clabels = [safe_text(x) for x in _to_list(column_labels)]
    if not rlabels:
        rlabels = [str(i) for i in range(len(rows))]
    if not clabels and rows:
        clabels = [str(i) for i in range(max(len(r) for r in rows))]

    cells: List[Dict[str, Any]] = []
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            cells.append({
                "row": rlabels[i] if i < len(rlabels) else str(i),
                "column": clabels[j] if j < len(clabels) else str(j),
                "value": value,
                "formatted": fmt_number(value),
            })

    summary: Dict[str, Any] = {"matrix": rows, "row_labels": rlabels, "column_labels": clabels, "cells": cells[:80]}
    ctype = chart_type.lower()
    if "confusion" in ctype:
        diag = 0.0
        total = 0.0
        off_diag: List[Dict[str, Any]] = []
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                num = _numeric(value) or 0.0
                total += num
                if i == j:
                    diag += num
                elif num:
                    off_diag.append({
                        "true_label": rlabels[i] if i < len(rlabels) else str(i),
                        "predicted_label": clabels[j] if j < len(clabels) else str(j),
                        "count": num,
                    })
        off_diag.sort(key=lambda x: abs(float(x.get("count") or 0)), reverse=True)
        summary.update({
            "total_cases_in_matrix": int(total) if total.is_integer() else total,
            "correct_diagonal_total": int(diag) if diag.is_integer() else diag,
            "off_diagonal_total": int(total - diag) if (total - diag).is_integer() else total - diag,
            "largest_confusions": off_diag[:10],
        })
    if "correlation" in ctype:
        pairs: List[Dict[str, Any]] = []
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                if j <= i:
                    continue
                num = _numeric(value)
                if num is None:
                    continue
                pairs.append({
                    "variable_1": rlabels[i] if i < len(rlabels) else str(i),
                    "variable_2": clabels[j] if j < len(clabels) else str(j),
                    "correlation": round(num, 4),
                })
        pairs.sort(key=lambda x: abs(float(x.get("correlation") or 0)), reverse=True)
        summary["strongest_abs_correlations"] = pairs[:12]
    return summary


def _prediction_scatter_summary(data: List[Dict[str, Any]]) -> Dict[str, Any]:
    pairs: List[Tuple[float, float]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        yt = _numeric(row.get("true") if "true" in row else row.get("y_true"))
        yp = _numeric(row.get("predicted") if "predicted" in row else row.get("y_pred"))
        if yt is not None and yp is not None:
            pairs.append((yt, yp))
    if not pairs:
        return {"data_preview": data[:20]}
    errs = [p[1] - p[0] for p in pairs]
    mae = sum(abs(e) for e in errs) / len(errs)
    rmse = (sum(e * e for e in errs) / len(errs)) ** 0.5
    return {
        "n_points": len(pairs),
        "mae_from_chart_points": round(mae, 4),
        "rmse_from_chart_points": round(rmse, 4),
        "points_preview": [{"true": p[0], "predicted": p[1]} for p in pairs[:20]],
    }


def _chart_facts(charts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    facts: List[Dict[str, Any]] = []
    for idx, chart in enumerate(charts, start=1):
        ctype = safe_text(chart.get("chart_type"), "chart")
        data = chart.get("data") or []
        fact: Dict[str, Any] = {
            "figure_no": idx,
            "chart_id": chart.get("chart_id"),
            "chart_type": ctype,
            "title": chart.get("title") or ctype or "结果图表",
            "subtitle": chart.get("subtitle"),
            "notes": chart.get("notes") or [],
        }
        if isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]
            if "feature_importance" in ctype:
                fact["ranked_feature_importance"] = _normalize_ranked_rows(rows, ["feature", "variable", "name"], ["importance", "value", "score"], max_rows=25)
            elif "model_comparison" in ctype:
                fact["model_comparison_values"] = rows[:25]
            elif "prediction" in ctype or "scatter" in ctype:
                fact["prediction_scatter_summary"] = _prediction_scatter_summary(rows)
            elif "descriptive" in ctype or "mean" in ctype:
                fact["descriptive_values"] = _normalize_ranked_rows(rows, ["variable", "name"], ["mean", "value"], max_rows=25)
            elif "coef" in ctype or "odds" in ctype:
                fact["coefficient_or_or_values"] = _normalize_ranked_rows(rows, ["variable", "term", "name"], ["value", "coefficient", "odds_ratio", "or"], max_rows=25)
            else:
                fact["data_preview"] = rows[:25]
        matrix = chart.get("matrix")
        if matrix is not None:
            fact["matrix_facts"] = _matrix_summary(matrix, chart.get("row_labels"), chart.get("column_labels"), ctype)
        facts.append(fact)
    return facts


def _ai_explanation_context(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    ctx = context or {}
    raw = ctx.get("ai_explanation") or ctx.get("explanation") or {}
    item = as_dict(raw)
    text = ctx.get("ai_explanation_text") or item.get("text") or item.get("content") or ""
    findings = item.get("key_findings") or []
    warnings = item.get("warnings") or []
    return {
        "text": safe_text(text)[:6000],
        "key_findings": findings[:20] if isinstance(findings, list) else [],
        "warnings": warnings[:20] if isinstance(warnings, list) else [],
    }


def _analysis_prediction_summary(analysis: Dict[str, Any]) -> Dict[str, Any]:
    table = [r for r in (analysis.get("prediction_table") or []) if isinstance(r, dict)]
    if not table:
        return {}
    return _prediction_scatter_summary(table)


def build_report_context(
    project_id: Optional[str],
    analysis_id: Optional[str],
    dataset_id: Optional[str],
    chart_ids: Optional[List[str]],
    charts: Optional[List[Dict[str, Any]]],
    include_charts: bool,
    title: Optional[str],
    style: str,
    context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    project = load_project(project_id, context)
    analysis = load_analysis(analysis_id, context)
    ctx = context or {}
    current_analysis_id = safe_text(analysis.get("analysis_id") or analysis_id)
    if current_analysis_id and ANALYSES.get(current_analysis_id):
        analysis = as_dict(ANALYSES.get(current_analysis_id))
    if analysis:
        analysis_dataset = DATASETS.get(dataset_id or analysis.get('dataset_id')) or {}
        if not analysis_is_current(analysis, analysis_dataset):
            raise HTTPException(409, '报告引用的分析已过期，请重新分析后生成当前报告。历史报告仍可下载。')
    selected_charts, stale_chart_ids = collect_charts(chart_ids, charts, current_analysis_id=current_analysis_id, dataset_id=dataset_id) if include_charts else ([], [])
    variables = _normalize_variables(as_dict(ctx.get("selected_variables") or ctx.get("variables")), analysis)
    dataset = _dataset_context(dataset_id, context)

    model_name = safe_text(analysis.get("model_name") or analysis.get("method") or analysis.get("model"), "当前模型")
    model_family = _infer_model_family(model_name, analysis)
    metrics = _extract_metrics(analysis)
    feature_importance = _extract_feature_importance(analysis, selected_charts)
    coefficients = _extract_coefficients(analysis, selected_charts)
    confusion_matrix = _extract_confusion_matrix(analysis, selected_charts)
    guardrails = _guardrails(model_family, analysis, metrics)
    if stale_chart_ids:
        guardrails.append("已自动过滤与当前分析/数据集不匹配的旧图表：{}。".format("、".join(stale_chart_ids[:8])))
    chart_facts = _chart_facts(selected_charts)
    ai_explanation = _ai_explanation_context(context)

    report_title = safe_text(title or project.get("name") or ctx.get("project_name"), "MethodPal 分析报告")
    diagnostics = as_dict(analysis.get("diagnostics") or analysis.get("model_diagnostics"))
    stats = as_dict(analysis.get("stats"))

    fact_pack: Dict[str, Any] = {
        "report": {"title": report_title, "style": style},
        "project": {
            "project_id": project_id,
            "name": project.get("name") or ctx.get("project_name"),
            "research_goal": project.get("research_goal") or ctx.get("research_goal"),
        },
        "dataset": dataset,
        "variables": variables,
        "analysis": {
            "analysis_id": current_analysis_id or analysis.get("analysis_id"),
            "model_key": analysis.get("model"),
            "model_name": model_name,
            "method": analysis.get("method"),
            "task_type": analysis.get("task_type") or analysis.get("problem_type") or stats.get("task_type"),
            "model_family": model_family,
            "sample_size": analysis.get("sample_size") or stats.get("n_rows_used") or diagnostics.get("n_rows_used"),
            "dependent_variable_from_analysis": analysis.get("dependent_variable"),
            "independent_variables_from_analysis": analysis.get("independent_variables") or [],
            "control_variables_from_analysis": analysis.get("control_variables") or [],
            "model_parameters_or_options": diagnostics.get("model_options") or analysis.get("options") or {},
            "stats": stats,
            "prediction_metrics_raw": analysis.get("prediction_metrics") or {},
            "metrics": metrics,
            "top_feature_importance": feature_importance[:20],
            "top_coefficients_or_terms": coefficients[:20],
            "confusion_matrix": confusion_matrix,
            "model_comparison": analysis.get("model_comparison") or [],
            "prediction_summary": _analysis_prediction_summary(analysis),
            "diagnostics": diagnostics,
            "summary": analysis.get("summary"),
            "warnings": analysis.get("warnings") or [],
        },
        "charts": chart_facts,
        "ai_explanation_available": bool(ai_explanation.get("text") or ai_explanation.get("key_findings")),
        "ai_explanation": ai_explanation,
        "interpretation_guardrails": guardrails,
        "raw_available_flags": {
            "has_analysis": bool(analysis),
            "has_model_info": bool(model_name and model_name != "当前模型"),
            "has_metrics": bool(metrics),
            "has_feature_importance": bool(feature_importance),
            "has_coefficients": bool(coefficients),
            "has_charts": bool(selected_charts),
            "chart_count": len(selected_charts),
            "has_ai_explanation": bool(ai_explanation.get("text")),
            "stale_chart_count_filtered": len(stale_chart_ids),
        },
        "filtered_stale_chart_ids": stale_chart_ids,
    }
    return {
        "title": report_title,
        "project": project,
        "analysis": analysis,
        "charts": selected_charts,
        "fact_pack": fact_pack,
        "warnings": guardrails,
    }


def metric_markdown(metrics: List[Dict[str, Any]]) -> str:
    if not metrics:
        return "- 当前分析结果未提供常见预测/拟合指标。"
    return "\n".join(["- {}：{}".format(m.get("label"), m.get("formatted")) for m in metrics])


def variable_sentence(variables: Dict[str, Any]) -> str:
    y = safe_text(variables.get("dependent_variable"), "未设置")
    xs = variables.get("independent_variables") or []
    cs = variables.get("control_variables") or []
    ms = variables.get("mediator_variables") or []
    mods = variables.get("moderator_variables") or []
    excluded = variables.get("excluded_variables") or []
    return "因变量为{}；核心自变量包括{}；控制变量包括{}{}{}{}。".format(
        y,
        "、".join([str(x) for x in xs]) if xs else "未设置",
        "、".join([str(x) for x in cs]) if cs else "无",
        "；中介变量包括{}".format("、".join([str(x) for x in ms])) if ms else "",
        "；调节变量包括{}".format("、".join([str(x) for x in mods])) if mods else "",
        "；不纳入建模的变量包括{}".format("、".join([str(x) for x in excluded])) if excluded else "",
    )
