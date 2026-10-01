from typing import Any, Dict, List

import numpy as np
import statsmodels.api as sm
from fastapi import HTTPException
from scipy import stats as scipy_stats
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.stats.stattools import durbin_watson

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.shared import (
    dataset_to_dataframe, ensure_columns, is_numeric_column, type_hint, build_design_matrix,
    common_warnings, regression_rows_from_statsmodels, round_float, p_value, response, diagnostics_with_vif,
)


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable:
        raise HTTPException(status_code=400, detail="线性回归需要选择因变量。")
    x_cols = list(dict.fromkeys((req.independent_variables or []) + (req.control_variables or [])))
    if not x_cols:
        raise HTTPException(status_code=400, detail="线性回归至少需要一个自变量。")
    ensure_columns(df, [req.dependent_variable], "因变量")
    ensure_columns(df, x_cols, "自变量/控制变量")
    if req.dependent_variable in x_cols:
        raise HTTPException(status_code=400, detail="因变量不能同时作为自变量或控制变量。")
    if not is_numeric_column(df[req.dependent_variable], type_hint(req, req.dependent_variable)):
        raise HTTPException(status_code=400, detail="线性回归要求因变量为连续型/数值型变量。")

    y, X, meta, build_warnings = build_design_matrix(df, req.dependent_variable, x_cols, req, binary=False)
    X_const = sm.add_constant(X, has_constant="add")
    try:
        model = sm.OLS(y, X_const).fit()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="OLS 回归失败：{}".format(exc))
    warnings = build_warnings + common_warnings(dataset, int(meta["n_rows_used"]), int(X.shape[1]))
    residuals = model.resid
    diagnostics: Dict[str, Any] = {
        "n_rows_original": int(meta["n_rows_original"]),
        "n_rows_used": int(meta["n_rows_used"]),
        "n_rows_dropped": int(meta["n_rows_dropped"]),
        "predictor_count": int(X.shape[1]),
        "design_matrix_columns": meta.get("encoded_columns", []),
        "numeric_variables": meta.get("numeric_variables", []),
        "categorical_variables": meta.get("categorical_variables", []),
        "df_residual": round_float(model.df_resid),
        "condition_number": round_float(model.condition_number),
        "durbin_watson": round_float(durbin_watson(residuals)) if len(residuals) >= 3 else None,
    }
    diagnostics, vif_warnings_list = diagnostics_with_vif(diagnostics, X)
    warnings.extend(vif_warnings_list)
    if len(residuals) >= 3 and len(residuals) <= 5000:
        try:
            shapiro_stat, shapiro_p = scipy_stats.shapiro(residuals)
            diagnostics["shapiro_wilk"] = {"statistic": round_float(shapiro_stat), "p_value": p_value(shapiro_p)}
        except Exception:
            diagnostics["shapiro_wilk"] = None
    if int(meta["n_rows_used"]) > int(X.shape[1]) + 2:
        try:
            bp = het_breuschpagan(residuals, X_const)
            diagnostics["breusch_pagan"] = {"lm_statistic": round_float(bp[0]), "lm_p_value": p_value(bp[1]), "f_statistic": round_float(bp[2]), "f_p_value": p_value(bp[3])}
        except Exception:
            diagnostics["breusch_pagan"] = None
    try:
        influence = model.get_influence()
        cooks = influence.cooks_distance[0]
        threshold = 4.0 / max(1, int(meta["n_rows_used"]))
        diagnostics["cooks_distance"] = {"max": round_float(np.nanmax(cooks)), "threshold": round_float(threshold), "influential_count": int(np.sum(cooks > threshold))}
    except Exception:
        diagnostics["cooks_distance"] = None
    stats = {"r2": round_float(model.rsquared), "adjusted_r2": round_float(model.rsquared_adj), "f_statistic": round_float(model.fvalue), "p_value": p_value(model.f_pvalue), "aic": round_float(model.aic), "bic": round_float(model.bic), "log_likelihood": round_float(model.llf), "df_model": round_float(model.df_model), "df_residual": round_float(model.df_resid)}
    return response(req, "多元线性回归", "OLS", int(meta["n_rows_used"]), stats=stats, regression_table=regression_rows_from_statsmodels(model, X, y), diagnostics=diagnostics, warnings=warnings, summary="已基于真实数据完成 OLS 多元线性回归。")
