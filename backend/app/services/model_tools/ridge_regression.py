from typing import List

from fastapi import HTTPException
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse, RegressionRow
from app.services.model_tools.prediction_evaluation import evaluate_regressor, feature_importance_from_coefficients
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, is_numeric_column, type_hint, build_design_matrix, common_warnings, round_float, diagnostics_with_vif, response


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable:
        raise HTTPException(status_code=400, detail="Ridge 回归需要选择因变量。")
    x_cols = list(dict.fromkeys((req.independent_variables or []) + (req.control_variables or [])))
    if not x_cols:
        raise HTTPException(status_code=400, detail="Ridge 回归至少需要一个自变量。")
    ensure_columns(df, [req.dependent_variable] + x_cols, "Ridge 变量")
    if not is_numeric_column(df[req.dependent_variable], type_hint(req, req.dependent_variable)):
        raise HTTPException(status_code=400, detail="Ridge 回归要求因变量为连续型/数值型变量。")
    y, X, meta, build_warnings = build_design_matrix(df, req.dependent_variable, x_cols, req, binary=False)
    estimator = Pipeline([("scaler", StandardScaler()), ("ridge", RidgeCV(alphas=[0.01, 0.1, 1.0, 10.0, 100.0]))])
    try:
        fitted, pred_metrics, pred_rows, eval_warnings = evaluate_regressor(estimator, X, y, req)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Ridge 回归失败：{}".format(exc))
    ridge = fitted.named_steps["ridge"]
    rows: List[RegressionRow] = [RegressionRow(variable=str(name), coefficient=round_float(coef), variable_type="predictor", original_variable=str(name).split("_")[0]) for name, coef in zip(X.columns, ridge.coef_)]
    feature_importance = feature_importance_from_coefficients(X.columns.tolist(), ridge.coef_)
    train = pred_metrics.get("train") or {}; test = pred_metrics.get("test") or {}; cvm = pred_metrics.get("cross_validation") or {}
    stats_out = {"r2_in_sample": train.get("r2"), "rmse_in_sample": train.get("rmse"), "mae_in_sample": train.get("mae"), "test_r2": test.get("r2"), "test_rmse": test.get("rmse"), "test_mae": test.get("mae"), "cv_r2_mean": cvm.get("cv_r2_mean"), "alpha": round_float(ridge.alpha_)}
    warnings = build_warnings + eval_warnings + common_warnings(dataset, int(meta["n_rows_used"]), int(X.shape[1])) + ["Ridge 主要用于共线性较强时的稳定预测/估计，不提供传统 OLS p 值。"]
    diagnostics, vif_warnings_list = diagnostics_with_vif({"n_rows_original": int(meta["n_rows_original"]), "n_rows_used": int(meta["n_rows_used"]), "n_rows_dropped": int(meta["n_rows_dropped"]), "predictor_count": int(X.shape[1]), "design_matrix_columns": X.columns.tolist()}, X)
    warnings.extend(vif_warnings_list)
    return response(req, "Ridge 回归", "RidgeCV", int(meta["n_rows_used"]), stats=stats_out, regression_table=rows, diagnostics=diagnostics, prediction_metrics=pred_metrics, feature_importance=feature_importance, prediction_table=pred_rows, warnings=warnings, summary="已完成 Ridge 正则化回归与预测评估。")
