from typing import Any, Dict, List

import statsmodels.api as sm
from fastapi import HTTPException

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, is_numeric_column, type_hint, build_design_matrix, common_warnings, regression_rows_from_statsmodels, round_float, p_value, response, diagnostics_with_vif


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable or not req.independent_variables or not req.moderator_variables:
        raise HTTPException(status_code=400, detail="调节效应分析需要设置 Y、至少一个 X 和至少一个调节变量 W。")
    y = req.dependent_variable
    x = req.independent_variables[0]
    w = req.moderator_variables[0]
    controls = req.control_variables or []
    ensure_columns(df, [y, x, w] + controls, "调节模型变量")
    if not is_numeric_column(df[y], type_hint(req, y)):
        raise HTTPException(status_code=400, detail="当前调节分析要求因变量 Y 为连续型/数值型。")
    # v0.5.1 MVP: interaction requires numeric X and W to keep interpretation clean.
    if not is_numeric_column(df[x], type_hint(req, x)) or not is_numeric_column(df[w], type_hint(req, w)):
        raise HTTPException(status_code=400, detail="当前调节分析 MVP 要求 X 和 W 均为连续型/数值型。分类调节变量将在后续版本扩展。")
    x_cols = list(dict.fromkeys([x, w] + controls))
    y_vec, X, meta, build_warnings = build_design_matrix(df, y, x_cols, req, binary=False, add_interactions=[(x, w)])
    X_const = sm.add_constant(X, has_constant="add")
    try:
        model = sm.OLS(y_vec, X_const).fit()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="调节效应模型失败：{}".format(exc))
    warnings = build_warnings + common_warnings(dataset, int(meta["n_rows_used"]), int(X.shape[1]))
    interaction_name = "{} × {}".format(x, w)
    interaction_p = None
    if interaction_name in model.pvalues.index:
        interaction_p = p_value(model.pvalues[interaction_name])
    stats_out = {"r2": round_float(model.rsquared), "adjusted_r2": round_float(model.rsquared_adj), "f_statistic": round_float(model.fvalue), "p_value": p_value(model.f_pvalue), "interaction": interaction_name, "interaction_p_value": interaction_p}
    diagnostics = {"n_rows_original": int(meta["n_rows_original"]), "n_rows_used": int(meta["n_rows_used"]), "n_rows_dropped": int(meta["n_rows_dropped"]), "predictor_count": int(X.shape[1]), "design_matrix_columns": X.columns.tolist()}
    diagnostics, vif_warnings_list = diagnostics_with_vif(diagnostics, X)
    warnings.extend(vif_warnings_list)
    if interaction_p is not None and interaction_p < 0.05:
        warnings.append("交互项达到常用显著性阈值；建议进一步绘制 simple slopes / Johnson-Neyman 区间。")
    else:
        warnings.append("当前仅返回交互项检验；simple slopes 图和 Johnson-Neyman 分析将在后续版本补充。")
    return response(req, "调节效应分析", "OLS with interaction term", int(meta["n_rows_used"]), stats=stats_out, regression_table=regression_rows_from_statsmodels(model, X, y_vec), diagnostics=diagnostics, warnings=warnings, summary="已完成调节效应分析。")
