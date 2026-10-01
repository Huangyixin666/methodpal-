from fastapi import HTTPException
from sklearn.ensemble import RandomForestRegressor

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.prediction_evaluation import evaluate_regressor, feature_importance_from_model, permutation_importance_rows
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, is_numeric_column, type_hint, build_design_matrix, common_warnings, diagnostics_with_vif, response


def _opt_int(req: AnalysisRunRequest, key: str, default: int, lower: int = 1, upper: int = 10000) -> int:
    try:
        value = int((req.options or {}).get(key, default))
    except Exception:
        value = default
    return max(lower, min(upper, value))


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable:
        raise HTTPException(status_code=400, detail="随机森林回归需要选择因变量。")
    x_cols = list(dict.fromkeys((req.independent_variables or []) + (req.control_variables or [])))
    if not x_cols:
        raise HTTPException(status_code=400, detail="随机森林回归至少需要一个自变量。")
    ensure_columns(df, [req.dependent_variable] + x_cols, "随机森林变量")
    if not is_numeric_column(df[req.dependent_variable], type_hint(req, req.dependent_variable)):
        raise HTTPException(status_code=400, detail="随机森林回归要求因变量为连续型/数值型变量。")
    y, X, meta, build_warnings = build_design_matrix(df, req.dependent_variable, x_cols, req, binary=False)
    n_estimators = _opt_int(req, "n_estimators", 300, 10, 3000)
    max_depth_raw = (req.options or {}).get("max_depth", None)
    try:
        max_depth = int(max_depth_raw) if max_depth_raw not in [None, "", "none", "None"] else None
    except Exception:
        max_depth = None
    min_samples_leaf = _opt_int(req, "min_samples_leaf", 1, 1, 100)
    estimator = RandomForestRegressor(
        n_estimators=n_estimators,
        random_state=int((req.options or {}).get("random_state", 42)),
        max_depth=max_depth,
        min_samples_leaf=min_samples_leaf,
        n_jobs=-1,
    )
    try:
        fitted, pred_metrics, pred_rows, eval_warnings = evaluate_regressor(estimator, X, y, req)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="随机森林回归失败：{}".format(exc))
    importances = feature_importance_from_model(fitted, X.columns.tolist()) or permutation_importance_rows(fitted, X, y, "regression", random_state=int((req.options or {}).get("random_state", 42)))
    train = pred_metrics.get("train") or {}; test = pred_metrics.get("test") or {}; cvm = pred_metrics.get("cross_validation") or {}
    stats_out = {"r2_in_sample": train.get("r2"), "rmse_in_sample": train.get("rmse"), "mae_in_sample": train.get("mae"), "test_r2": test.get("r2"), "test_rmse": test.get("rmse"), "test_mae": test.get("mae"), "cv_r2_mean": cvm.get("cv_r2_mean"), "n_estimators": n_estimators, "max_depth": max_depth, "min_samples_leaf": min_samples_leaf}
    warnings = build_warnings + eval_warnings + common_warnings(dataset, int(meta["n_rows_used"]), int(X.shape[1])) + ["随机森林属于预测模型；变量重要性表示预测贡献，不等同于统计显著性或因果效应。"]
    diagnostics, vif_warnings_list = diagnostics_with_vif({"n_rows_original": int(meta["n_rows_original"]), "n_rows_used": int(meta["n_rows_used"]), "n_rows_dropped": int(meta["n_rows_dropped"]), "predictor_count": int(X.shape[1]), "design_matrix_columns": X.columns.tolist(), "feature_importances": importances, "model_options": req.options or {}}, X)
    warnings.extend(vif_warnings_list)
    return response(req, "随机森林回归", "RandomForestRegressor", int(meta["n_rows_used"]), stats=stats_out, result_table=importances, diagnostics=diagnostics, prediction_metrics=pred_metrics, feature_importance=importances, prediction_table=pred_rows, warnings=warnings, summary="已完成随机森林回归、预测评估与变量重要性计算。")
