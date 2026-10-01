import math
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import statsmodels.api as sm
from fastapi import HTTPException
from statsmodels.stats.outliers_influence import variance_inflation_factor

from app.schemas.analysis import AnalysisResponse, AnalysisRunRequest, RegressionRow
from app.services.data_profiler import clean_text, get_dataset, to_float
from app.services.store import ANALYSES

try:
    from app.services.sample_data import SAMPLE_HEADERS, SAMPLE_ROWS
except Exception:  # pragma: no cover
    SAMPLE_HEADERS, SAMPLE_ROWS = [], []

NUMERIC_TYPES = {"连续型", "整数型", "有序型", "计数型", "numeric", "continuous", "integer", "ordinal", "count"}
CATEGORICAL_TYPES = {"分类型", "二分类", "文本型", "categorical", "binary", "text"}
BINARY_TYPES = {"二分类", "binary"}
ID_TYPES = {"ID型", "id", "identifier"}
MISSING_TOKENS = {"", "缺失", "nan", "NaN", "NA", "N/A", "null", "None", "无"}


def is_missing(value: Any) -> bool:
    return clean_text(value) in MISSING_TOKENS


def round_float(value: Any, digits: int = 4) -> Optional[float]:
    try:
        if value is None:
            return None
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, digits)
    except Exception:
        return None


def p_value(value: Any) -> Optional[float]:
    return round_float(value, 6)


def dataset_to_dataframe(dataset_id: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    dataset = get_dataset(dataset_id)
    if not dataset and dataset_id in {"ds_mock_001", "sample", "sample_dataset"} and SAMPLE_HEADERS:
        dataset = {
            "dataset_id": dataset_id,
            "name": "sample_dataset",
            "headers": SAMPLE_HEADERS,
            "_full_rows": SAMPLE_ROWS,
            "rows": SAMPLE_ROWS,
            "profile": {},
        }
    if not dataset:
        raise HTTPException(status_code=404, detail="dataset not found: {}".format(dataset_id))

    headers = dataset.get("headers") or []
    rows = dataset.get("_full_rows") or dataset.get("rows") or []
    if not headers:
        raise HTTPException(status_code=400, detail="数据集没有字段名，无法分析。")
    df = pd.DataFrame(rows, columns=headers)
    for col in df.columns:
        df[col] = df[col].map(clean_text)
    return df, dataset


def ensure_columns(df: pd.DataFrame, columns: List[str], role: str) -> None:
    missing = [c for c in columns if c and c not in df.columns]
    if missing:
        raise HTTPException(status_code=400, detail="{} 中存在数据集没有的字段：{}".format(role, "、".join(missing)))


def type_hint(req: AnalysisRunRequest, col: str) -> Optional[str]:
    value = (req.variable_types or {}).get(col)
    return clean_text(value) if value is not None else None


def numeric_series(series: pd.Series) -> pd.Series:
    return series.map(to_float).astype("float64")


def clean_series(series: pd.Series) -> pd.Series:
    return series.map(clean_text).map(lambda x: np.nan if is_missing(x) else x)


def numeric_rate(series: pd.Series) -> float:
    raw = series.map(clean_text)
    non_missing = raw[~raw.map(is_missing)]
    if len(non_missing) == 0:
        return 0.0
    numeric = non_missing.map(to_float).notna().sum()
    return float(numeric) / float(len(non_missing))


def is_numeric_column(series: pd.Series, hint: Optional[str] = None) -> bool:
    if hint in ID_TYPES or hint in CATEGORICAL_TYPES:
        return False
    if hint in NUMERIC_TYPES:
        return True
    return numeric_rate(series) >= 0.9


def is_binary_column(series: pd.Series, hint: Optional[str] = None) -> bool:
    raw = clean_series(series).dropna().astype(str)
    values = sorted(raw.unique().tolist())
    if len(values) != 2:
        return False
    if hint in BINARY_TYPES:
        return True
    canonical = set(values)
    return canonical in [
        {"0", "1"}, {"是", "否"}, {"男", "女"}, {"yes", "no"}, {"Yes", "No"}, {"true", "false"}, {"True", "False"},
    ]



def multiclass_y(series: pd.Series) -> Tuple[pd.Series, Dict[str, Any]]:
    """Encode a categorical outcome with two or more classes into 0..K-1.

    This is used by tree/XGBoost/CatBoost/SGD classifiers. It deliberately does
    not imply an ordinal interpretation; class ids are only internal labels.
    """
    raw = clean_series(series)
    values = sorted([str(v) for v in raw.dropna().unique().tolist()])
    if len(values) < 2:
        raise HTTPException(status_code=400, detail="分类模型的因变量至少需要两个有效类别。")
    mapping = {label: i for i, label in enumerate(values)}
    y = raw.astype(str).map(mapping).astype("float64")
    return y, {"classes": values, "mapping": mapping, "class_count": len(values)}


def class_count(series: pd.Series) -> int:
    raw = clean_series(series).dropna().astype(str)
    return int(raw.nunique())


def is_classification_column(series: pd.Series, hint: Optional[str] = None) -> bool:
    """Return True for binary or multi-class categorical outcomes.

    We keep a conservative cap to avoid treating arbitrary free text as a
    classification target.
    """
    if hint in ID_TYPES or hint == "文本型":
        return False
    if hint in {"二分类", "分类型", "有序型", "categorical", "binary", "ordinal"}:
        return class_count(series) >= 2
    n = class_count(series)
    return 2 <= n <= 50 and not is_numeric_column(series, hint)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "是", "启用"}


def _as_int(value: Any, default: int, lower: Optional[int] = None, upper: Optional[int] = None) -> int:
    try:
        out = int(value)
    except Exception:
        out = default
    if lower is not None:
        out = max(lower, out)
    if upper is not None:
        out = min(upper, out)
    return out


def _selected_numeric_terms(req: AnalysisRunRequest) -> Dict[str, Any]:
    opts = req.options or {}
    return {
        "polynomial_degree": _as_int(opts.get("polynomial_degree", opts.get("degree", 1)), 1, 1, 3),
        "add_log_terms": _as_bool(opts.get("add_log_terms", opts.get("log_terms", False))),
        "add_exp_terms": _as_bool(opts.get("add_exp_terms", opts.get("exp_terms", False))),
    }


def add_nonlinear_feature_terms(
    X: pd.DataFrame,
    df: pd.DataFrame,
    numeric_vars: List[str],
    req: AnalysisRunRequest,
) -> Tuple[pd.DataFrame, List[str], List[str]]:
    """Add optional quadratic/cubic/log/exp terms for numeric original variables.

    These terms are controlled from the model-selection UI through req.options.
    They are useful for linear/regularized models and can also be passed to ML
    models when the user wants to explicitly expose simple nonlinear features.
    """
    if X is None or X.empty:
        return X, [], []
    opts = _selected_numeric_terms(req)
    degree = int(opts["polynomial_degree"])
    add_log = bool(opts["add_log_terms"])
    add_exp = bool(opts["add_exp_terms"])
    if degree <= 1 and not add_log and not add_exp:
        return X, [], []

    out = X.copy()
    warnings: List[str] = []
    added: List[str] = []
    for col in numeric_vars:
        if col not in df.columns:
            continue
        base = numeric_series(df[col])
        if degree >= 2:
            name = "{}^2".format(col)
            if name not in out.columns:
                out[name] = base ** 2
                added.append(name)
        if degree >= 3:
            name = "{}^3".format(col)
            if name not in out.columns:
                out[name] = base ** 3
                added.append(name)
        if add_log:
            name = "log1p({})".format(col)
            if name not in out.columns:
                # log1p is only defined for x > -1. Invalid values become NaN and
                # are handled by the shared complete-case cleaning below.
                out[name] = base.map(lambda v: np.log1p(v) if pd.notna(v) and float(v) > -1 else np.nan)
                added.append(name)
        if add_exp:
            name = "exp_z({})".format(col)
            if name not in out.columns:
                mean = base.mean(skipna=True)
                std = base.std(skipna=True)
                if pd.isna(std) or float(std) == 0.0:
                    warnings.append("变量「{}」无法构造指数项，因为标准差为 0 或不可计算。".format(col))
                    continue
                z = (base - mean) / std
                out[name] = np.exp(np.clip(z.astype(float), -8, 8))
                added.append(name)
    if added:
        warnings.append("已根据模型参数加入非线性特征项：{}。".format("、".join(added[:20]) + (" 等" if len(added) > 20 else "")))
    return out, warnings, added

def binary_y(series: pd.Series, hint: Optional[str] = None) -> Tuple[pd.Series, Dict[str, Any]]:
    raw = clean_series(series)
    values = sorted([str(v) for v in raw.dropna().unique().tolist()])
    if len(values) != 2:
        raise HTTPException(status_code=400, detail="二分类模型的因变量必须只有两个有效取值。")
    if set(values) == {"0", "1"}:
        mapping = {"0": 0, "1": 1}
    elif set(values) == {"否", "是"}:
        mapping = {"否": 0, "是": 1}
    else:
        mapping = {values[0]: 0, values[1]: 1}
    y = raw.astype(str).map(mapping).astype("float64")
    return y, {"classes": values, "mapping": mapping, "event_class": values[1]}


def selected_columns(req: AnalysisRunRequest, df: pd.DataFrame) -> List[str]:
    cols = list(dict.fromkeys(
        ([req.dependent_variable] if req.dependent_variable else [])
        + (req.independent_variables or [])
        + (req.control_variables or [])
        + (req.mediator_variables or [])
        + (req.moderator_variables or [])
        + ([req.group_variable] if req.group_variable else [])
    ))
    return [c for c in cols if c in df.columns] or df.columns.tolist()


def prepare_predictor(series: pd.Series, col: str, req: AnalysisRunRequest) -> Tuple[pd.DataFrame, List[str], str]:
    hint = type_hint(req, col)
    warnings: List[str] = []
    non_missing_unique = sorted(set([clean_text(v) for v in series.tolist() if not is_missing(clean_text(v))]))
    if len(non_missing_unique) <= 1:
        warnings.append("变量「{}」只有一个有效取值，已从模型中移除。".format(col))
        return pd.DataFrame(index=series.index), warnings, "constant"
    if hint in ID_TYPES:
        warnings.append("变量「{}」被标记为 ID 型，已从模型中移除。".format(col))
        return pd.DataFrame(index=series.index), warnings, "id"
    if hint == "文本型":
        warnings.append("变量「{}」被标记为文本型，当前版本暂不纳入统计模型。".format(col))
        return pd.DataFrame(index=series.index), warnings, "text"
    if is_numeric_column(series, hint):
        return pd.DataFrame({col: numeric_series(series)}), warnings, "numeric"
    safe = clean_series(series)
    dummies = pd.get_dummies(safe, prefix=col, drop_first=True, dtype=float)
    if dummies.shape[1] == 0:
        warnings.append("分类变量「{}」没有可用哑变量，已从模型中移除。".format(col))
    return dummies, warnings, "categorical"


def build_design_matrix(
    df: pd.DataFrame,
    y_col: str,
    x_cols: List[str],
    req: AnalysisRunRequest,
    binary: bool = False,
    multiclass: bool = False,
    add_interactions: Optional[List[Tuple[str, str]]] = None,
) -> Tuple[pd.Series, pd.DataFrame, Dict[str, Any], List[str]]:
    warnings: List[str] = []
    y_hint = type_hint(req, y_col)
    if multiclass:
        y, y_meta = multiclass_y(df[y_col])
    elif binary:
        y, y_meta = binary_y(df[y_col], y_hint)
    else:
        y = numeric_series(df[y_col])
        y_meta = {}
    if y.notna().sum() < 3:
        raise HTTPException(status_code=400, detail="因变量可用样本少于 3，无法进行模型分析。")

    x_parts: List[pd.DataFrame] = []
    numeric_vars: List[str] = []
    categorical_vars: List[str] = []
    for col in x_cols:
        part, part_warnings, kind = prepare_predictor(df[col], col, req)
        warnings.extend(part_warnings)
        if part.shape[1] == 0:
            continue
        if kind == "numeric":
            numeric_vars.append(col)
        elif kind == "categorical":
            categorical_vars.append(col)
        x_parts.append(part)

    if not x_parts:
        raise HTTPException(status_code=400, detail="没有可用预测变量，无法进行模型分析。")
    X = pd.concat(x_parts, axis=1)
    X, nonlinear_warnings, nonlinear_terms = add_nonlinear_feature_terms(X, df, numeric_vars, req)
    warnings.extend(nonlinear_warnings)

    if add_interactions:
        for a, b in add_interactions:
            if a not in X.columns and a in df.columns:
                X[a] = numeric_series(df[a])
            if b not in X.columns and b in df.columns:
                X[b] = numeric_series(df[b])
            if a in X.columns and b in X.columns:
                X["{} × {}".format(a, b)] = X[a] * X[b]
            else:
                warnings.append("交互项 {} × {} 无法构造，已跳过。".format(a, b))

    model_df = pd.concat([y.rename(y_col), X], axis=1)
    n_original = len(model_df)
    model_df = model_df.replace([np.inf, -np.inf], np.nan).dropna(axis=0, how="any")
    n_used = len(model_df)
    if n_used < 3:
        raise HTTPException(status_code=400, detail="删除缺失值后可用样本少于 3，无法分析。")

    y_clean = model_df[y_col].astype(float)
    X_clean = model_df.drop(columns=[y_col]).astype(float)
    zero_var_cols = [c for c in X_clean.columns if X_clean[c].nunique(dropna=True) <= 1]
    if zero_var_cols:
        warnings.append("以下变量在有效样本中没有变化，已从模型中移除：{}".format("、".join(zero_var_cols)))
        X_clean = X_clean.drop(columns=zero_var_cols)
    if X_clean.shape[1] == 0:
        raise HTTPException(status_code=400, detail="清理后没有可用预测变量，无法分析。")

    meta = {
        "n_rows_original": int(n_original),
        "n_rows_used": int(n_used),
        "n_rows_dropped": int(n_original - n_used),
        "numeric_variables": numeric_vars,
        "categorical_variables": categorical_vars,
        "encoded_columns": X_clean.columns.tolist(),
        "nonlinear_terms": nonlinear_terms,
        "y_meta": y_meta,
    }
    return y_clean, X_clean, meta, warnings


def common_warnings(dataset: Dict[str, Any], n_used: int, predictor_count: int) -> List[str]:
    warnings: List[str] = []
    if n_used < 30:
        warnings.append("当前有效样本量少于 30，结果更适合功能测试或探索分析，不建议直接作为正式推断结论。")
    if predictor_count and n_used <= predictor_count + 1:
        warnings.append("有效样本量不足以稳定估计所有预测变量，自由度不足，p 值和置信区间可能不可解释。")
    profile = dataset.get("profile") or {}
    if profile.get("issue_count", 0) > 0:
        warnings.append("当前数据集仍有 {} 处数据质量问题，建议清洗后再解释模型。".format(profile.get("issue_count")))
    return warnings


def vif_status(value: Any) -> Tuple[str, str]:
    """Return a conservative interpretation for VIF values."""
    try:
        v = float(value)
    except Exception:
        return "无法判断", "VIF 无法计算，通常是样本量不足、变量完全共线或设计矩阵奇异。"
    if math.isnan(v) or math.isinf(v):
        return "无法判断", "VIF 无法计算，通常是样本量不足、变量完全共线或设计矩阵奇异。"
    if v < 5:
        return "正常", "通常没有明显多重共线性问题。"
    if v < 10:
        return "需要注意", "可能存在一定多重共线性，建议结合理论和相关矩阵判断是否保留。"
    return "严重风险", "存在较强多重共线性风险，建议检查变量冗余、合成指标或使用正则化模型。"


def vif_table(X: pd.DataFrame) -> List[Dict[str, Any]]:
    """Compute variance inflation factors for an already encoded design matrix.

    VIF is a predictor-side diagnostic. It is useful for OLS/logistic/Poisson and
    also informative for ML models when the user wants to understand redundant
    predictors, but it should not be interpreted as a model performance metric.
    """
    if X is None or X.empty:
        return []
    if X.shape[0] <= X.shape[1] + 1:
        return [{
            "variable": "整体设计矩阵",
            "vif": None,
            "status": "无法计算",
            "interpretation": "有效样本量不足以稳定计算 VIF。通常需要样本量明显大于预测变量数量。",
        }]

    values = sm.add_constant(X, has_constant="add")
    result: List[Dict[str, Any]] = []
    for i, col in enumerate(values.columns):
        if col == "const":
            continue
        try:
            vif = float(variance_inflation_factor(values.values, i))
        except Exception:
            vif = np.nan
        status, interpretation = vif_status(vif)
        result.append({
            "variable": str(col),
            "vif": round_float(vif),
            "status": status,
            "interpretation": interpretation,
        })
    return result


def vif_warnings(vif_rows: List[Dict[str, Any]]) -> List[str]:
    warnings: List[str] = []
    severe = [str(r.get("variable")) for r in vif_rows if r.get("status") == "严重风险"]
    moderate = [str(r.get("variable")) for r in vif_rows if r.get("status") == "需要注意"]
    unable = [str(r.get("variable")) for r in vif_rows if r.get("status") == "无法计算"]
    if severe:
        warnings.append("VIF 诊断显示以下变量存在严重多重共线性风险：{}。".format("、".join(severe)))
    elif moderate:
        warnings.append("VIF 诊断显示以下变量可能存在多重共线性，需要结合理论判断：{}。".format("、".join(moderate)))
    elif unable:
        warnings.append("VIF 当前无法稳定计算，通常是样本量过小或设计矩阵接近奇异。")
    return warnings


def diagnostics_with_vif(base: Dict[str, Any], X: pd.DataFrame) -> Tuple[Dict[str, Any], List[str]]:
    diagnostics = dict(base or {})
    rows = vif_table(X)
    diagnostics["vif"] = rows
    return diagnostics, vif_warnings(rows)


def regression_rows_from_statsmodels(model: Any, X: pd.DataFrame, y: pd.Series, logistic: bool = False) -> List[RegressionRow]:
    rows: List[RegressionRow] = []
    try:
        conf = model.conf_int(alpha=0.05)
    except Exception:
        conf = pd.DataFrame(index=model.params.index, data={0: np.nan, 1: np.nan})
    y_std = float(y.std(ddof=1)) if len(y) > 1 else 0.0
    for term in model.params.index:
        if term == "const":
            display_name, variable_type, original_variable, beta = "截距", "intercept", None, None
        else:
            display_name = str(term)
            variable_type = "predictor"
            original_variable = str(term).split("_")[0] if "_" in str(term) else str(term)
            x_std = float(X[term].std(ddof=1)) if term in X.columns and len(X[term]) > 1 else 0.0
            beta = round_float(float(model.params[term]) * x_std / y_std) if y_std > 0 and x_std > 0 else None
        rows.append(RegressionRow(
            variable=display_name,
            coefficient=round_float(model.params.get(term)),
            std_error=round_float(model.bse.get(term)) if hasattr(model, "bse") else None,
            t_value=round_float(model.tvalues.get(term)) if hasattr(model, "tvalues") and not logistic else None,
            z_value=round_float(model.tvalues.get(term)) if logistic and hasattr(model, "tvalues") else None,
            p_value=p_value(model.pvalues.get(term)) if hasattr(model, "pvalues") else None,
            ci_lower=round_float(conf.loc[term, 0]) if term in conf.index else None,
            ci_upper=round_float(conf.loc[term, 1]) if term in conf.index else None,
            standardized_beta=beta,
            odds_ratio=round_float(math.exp(float(model.params.get(term)))) if logistic and term != "const" else None,
            variable_type=variable_type,
            original_variable=original_variable,
        ))
    return rows


def response(
    req: AnalysisRunRequest,
    model_name: str,
    method: str,
    sample_size: int = 0,
    stats: Optional[Dict[str, Any]] = None,
    regression_table: Optional[List[RegressionRow]] = None,
    descriptive_statistics: Optional[List[Dict[str, Any]]] = None,
    correlation_matrix: Optional[List[Dict[str, Any]]] = None,
    result_table: Optional[List[Dict[str, Any]]] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
    prediction_metrics: Optional[Dict[str, Any]] = None,
    model_comparison: Optional[List[Dict[str, Any]]] = None,
    feature_importance: Optional[List[Dict[str, Any]]] = None,
    prediction_table: Optional[List[Dict[str, Any]]] = None,
    warnings: Optional[List[str]] = None,
    summary: str = "",
) -> AnalysisResponse:
    analysis_id = "ana_{}".format(uuid.uuid4().hex[:8])
    res = AnalysisResponse(
        analysis_id=analysis_id,
        dataset_id=req.dataset_id,
        model=req.model,
        model_name=model_name,
        method=method,
        dependent_variable=req.dependent_variable,
        independent_variables=req.independent_variables,
        control_variables=req.control_variables,
        mediator_variables=req.mediator_variables,
        moderator_variables=req.moderator_variables,
        group_variable=req.group_variable,
        variable_types=req.variable_types,
        sample_size=int(sample_size or 0),
        stats=stats or {},
        regression_table=regression_table or [],
        descriptive_statistics=descriptive_statistics or [],
        correlation_matrix=correlation_matrix or [],
        result_table=result_table or [],
        diagnostics=diagnostics or {},
        prediction_metrics=prediction_metrics or {},
        model_comparison=model_comparison or [],
        feature_importance=feature_importance or [],
        prediction_table=prediction_table or [],
        warnings=warnings or [],
        summary=summary,
    )
    ANALYSES[res.analysis_id] = res.model_dump()
    return res
