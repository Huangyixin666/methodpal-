import math
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import statsmodels.api as sm
from fastapi import HTTPException
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse, RegressionRow
from app.services.model_tools.shared import (
    dataset_to_dataframe,
    ensure_columns,
    is_binary_column,
    type_hint,
    build_design_matrix,
    common_warnings,
    regression_rows_from_statsmodels,
    round_float,
    diagnostics_with_vif,
    response,
)


def _class_counts(y: pd.Series) -> Dict[str, int]:
    counts = y.astype(int).value_counts(dropna=True).to_dict()
    return {str(k): int(v) for k, v in counts.items()}


def _drop_collinear_columns(X: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """Keep a greedily selected full-rank subset of columns.

    Statsmodels Logit uses maximum likelihood and is very sensitive to a singular
    design matrix. This function removes exact linear dependencies while keeping
    the original column order. A constant column is included in the rank test so
    columns that duplicate the intercept are also removed.
    """
    if X is None or X.empty:
        return X, []

    selected: List[str] = []
    dropped: List[str] = []
    base = np.ones((X.shape[0], 1), dtype=float)
    current = base
    current_rank = int(np.linalg.matrix_rank(current))

    for col in X.columns:
        values = pd.to_numeric(X[col], errors="coerce").fillna(0.0).astype(float).to_numpy().reshape(-1, 1)
        candidate = np.column_stack([current, values])
        candidate_rank = int(np.linalg.matrix_rank(candidate))
        if candidate_rank > current_rank:
            selected.append(col)
            current = candidate
            current_rank = candidate_rank
        else:
            dropped.append(col)

    if not selected:
        return pd.DataFrame(index=X.index), dropped
    return X[selected].copy(), dropped


def _regularized_rows(model: LogisticRegression, X: pd.DataFrame) -> List[RegressionRow]:
    rows: List[RegressionRow] = []
    intercept = float(model.intercept_[0]) if getattr(model, "intercept_", None) is not None else 0.0
    rows.append(RegressionRow(
        variable="截距",
        coefficient=round_float(intercept),
        odds_ratio=round_float(math.exp(intercept)),
        variable_type="intercept",
        original_variable=None,
    ))
    coefs = model.coef_[0].tolist()
    for term, coef in zip(X.columns.tolist(), coefs):
        coef = float(coef)
        rows.append(RegressionRow(
            variable=str(term),
            coefficient=round_float(coef),
            odds_ratio=round_float(math.exp(coef)),
            standardized_beta=round_float(coef),
            variable_type="predictor_regularized",
            original_variable=str(term).split("_")[0] if "_" in str(term) else str(term),
        ))
    return rows


def _fit_regularized_logistic(req: AnalysisRunRequest, y: pd.Series, X: pd.DataFrame, meta: Dict[str, Any], dataset: Dict[str, Any], warnings: List[str], reason: str) -> AnalysisResponse:
    if X.shape[1] == 0:
        raise HTTPException(status_code=400, detail="清理后没有可用预测变量，无法运行 Logistic 回归。")

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X.astype(float))
    try:
        model = LogisticRegression(
            penalty="l2",
            C=1.0,
            solver="lbfgs",
            max_iter=2000,
            class_weight=None,
        )
        model.fit(X_scaled, y.astype(int))
        pred_prob = model.predict_proba(X_scaled)[:, 1]
        pred_label = (pred_prob >= 0.5).astype(int)
        accuracy = accuracy_score(y.astype(int), pred_label)
        auc = roc_auc_score(y.astype(int), pred_prob) if len(set(y.astype(int).tolist())) == 2 else None
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Logistic 回归失败：{}。正则化兜底也未能完成，请减少变量、合并稀疏类别或检查完全分离。".format(exc))

    warnings = list(warnings or [])
    warnings.append("普通 Logistic 最大似然估计失败或不稳定，已自动切换为 L2 正则化 Logistic 回归。原因：{}".format(reason))
    warnings.append("正则化 Logistic 可用于稳定估计和预测，但当前结果不提供传统标准误、p 值和置信区间；系数主要用于方向和相对重要性参考。")
    warnings.append("正则化模型使用标准化后的设计矩阵；OR 可理解为标准化尺度上的近似 odds ratio，不等同于普通未正则化 Logistic 的原始 OR。")

    diagnostics: Dict[str, Any] = {
        "n_rows_original": int(meta.get("n_rows_original", len(y))),
        "n_rows_used": int(meta.get("n_rows_used", len(y))),
        "n_rows_dropped": int(meta.get("n_rows_dropped", 0)),
        "predictor_count": int(X.shape[1]),
        "design_matrix_columns": X.columns.tolist(),
        "y_meta": meta.get("y_meta", {}),
        "class_counts": _class_counts(y),
        "fit_mode": "regularized_l2_sklearn",
        "fallback_reason": str(reason),
        "standardized_design_matrix": True,
    }
    diagnostics, vif_warnings_list = diagnostics_with_vif(diagnostics, X)
    warnings.extend(vif_warnings_list)
    warnings.extend(common_warnings(dataset, int(meta.get("n_rows_used", len(y))), int(X.shape[1])))

    stats = {
        "fit_mode": "regularized_l2_sklearn",
        "accuracy_threshold_0_5": round_float(accuracy),
        "auc": round_float(auc),
    }
    return response(
        req,
        "二元 Logistic 回归（正则化兜底）",
        "Regularized Logistic Regression (L2)",
        int(meta.get("n_rows_used", len(y))),
        stats=stats,
        regression_table=_regularized_rows(model, X),
        diagnostics=diagnostics,
        warnings=warnings,
        summary="普通 Logistic 回归因矩阵奇异/分离/共线性等问题不稳定，已自动使用 L2 正则化 Logistic 回归完成分析。",
    )


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable:
        raise HTTPException(status_code=400, detail="Logistic 回归需要选择因变量。")
    x_cols = list(dict.fromkeys((req.independent_variables or []) + (req.control_variables or [])))
    if not x_cols:
        raise HTTPException(status_code=400, detail="Logistic 回归至少需要一个自变量。")
    ensure_columns(df, [req.dependent_variable], "因变量")
    ensure_columns(df, x_cols, "自变量/控制变量")
    if req.dependent_variable in x_cols:
        raise HTTPException(status_code=400, detail="因变量不能同时作为自变量或控制变量。")
    if not is_binary_column(df[req.dependent_variable], type_hint(req, req.dependent_variable)):
        raise HTTPException(status_code=400, detail="Logistic 回归要求因变量为二分类变量。请在变量设置中把该变量设为“二分类”，或先用 Agent 数据转换工具把多类别变量重编码为 0/1。")

    y, X, meta, build_warnings = build_design_matrix(df, req.dependent_variable, x_cols, req, binary=True)
    warnings = list(build_warnings or [])

    class_counts = _class_counts(y)
    if len(class_counts) != 2:
        raise HTTPException(status_code=400, detail="Logistic 回归要求因变量在有效样本中必须同时包含 0 和 1。当前类别分布：{}".format(class_counts))
    min_class = min(class_counts.values())
    if min_class < 10:
        warnings.append("因变量较少类别样本数只有 {}，Logistic 回归可能不稳定，建议谨慎解释。".format(min_class))

    X_ranked, dropped_collinear = _drop_collinear_columns(X)
    if dropped_collinear:
        warnings.append("检测到设计矩阵存在完全共线列，已自动移除：{}。".format("、".join(dropped_collinear[:30]) + (" 等" if len(dropped_collinear) > 30 else "")))
        meta = dict(meta)
        meta["encoded_columns_before_rank_check"] = X.columns.tolist()
        meta["encoded_columns"] = X_ranked.columns.tolist()
        meta["dropped_collinear_columns"] = dropped_collinear
        X = X_ranked

    if X.shape[1] == 0:
        raise HTTPException(status_code=400, detail="移除常量/共线变量后没有可用预测变量，无法运行 Logistic 回归。")

    # If the number of effective samples is too close to the number of predictors,
    # MLE is very likely to be singular. Go directly to the regularized fallback.
    if len(y) <= X.shape[1] + 1:
        return _fit_regularized_logistic(req, y, X, meta, dataset, warnings, "有效样本量不足以支撑当前预测变量数量")

    X_const = sm.add_constant(X, has_constant="add")
    try:
        model = sm.Logit(y, X_const).fit(disp=False, maxiter=300)
    except Exception as exc:
        return _fit_regularized_logistic(req, y, X, meta, dataset, warnings, str(exc))

    warnings.extend(common_warnings(dataset, int(meta["n_rows_used"]), int(X.shape[1])))
    try:
        pred_prob = model.predict(X_const)
        pred_label = (pred_prob >= 0.5).astype(int)
        accuracy = accuracy_score(y.astype(int), pred_label)
        auc = roc_auc_score(y.astype(int), pred_prob) if len(set(y.astype(int).tolist())) == 2 else None
    except Exception:
        accuracy, auc = None, None

    stats = {
        "fit_mode": "statsmodels_mle",
        "pseudo_r2_mcfadden": round_float(model.prsquared),
        "log_likelihood": round_float(model.llf),
        "ll_null": round_float(model.llnull),
        "aic": round_float(model.aic),
        "bic": round_float(model.bic),
        "accuracy_threshold_0_5": round_float(accuracy),
        "auc": round_float(auc),
    }
    diagnostics: Dict[str, Any] = {
        "n_rows_original": int(meta["n_rows_original"]),
        "n_rows_used": int(meta["n_rows_used"]),
        "n_rows_dropped": int(meta["n_rows_dropped"]),
        "predictor_count": int(X.shape[1]),
        "design_matrix_columns": X.columns.tolist(),
        "dropped_collinear_columns": meta.get("dropped_collinear_columns", []),
        "y_meta": meta.get("y_meta", {}),
        "class_counts": class_counts,
        "fit_mode": "statsmodels_mle",
    }
    diagnostics, vif_warnings_list = diagnostics_with_vif(diagnostics, X)
    warnings.extend(vif_warnings_list)
    return response(
        req,
        "二元 Logistic 回归",
        "Logit",
        int(meta["n_rows_used"]),
        stats=stats,
        regression_table=regression_rows_from_statsmodels(model, X, y, logistic=True),
        diagnostics=diagnostics,
        warnings=warnings,
        summary="已基于真实数据完成二元 Logistic 回归。",
    )
