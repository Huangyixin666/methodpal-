from fastapi import HTTPException

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.prediction_evaluation import evaluate_regressor, feature_importance_from_model, permutation_importance_rows
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, is_numeric_column, type_hint, build_design_matrix, common_warnings, diagnostics_with_vif, response


def _opt_int(req: AnalysisRunRequest, key: str, default: int, lower: int, upper: int) -> int:
    try:
        return max(lower, min(upper, int((req.options or {}).get(key, default))))
    except Exception:
        return default


def _opt_float(req: AnalysisRunRequest, key: str, default: float, lower: float, upper: float) -> float:
    try:
        return max(lower, min(upper, float((req.options or {}).get(key, default))))
    except Exception:
        return default


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    try:
        from xgboost import XGBRegressor
    except Exception as exc:
        raise HTTPException(status_code=400, detail="XGBoost 未安装或无法导入：{}".format(exc))

    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable:
        raise HTTPException(status_code=400, detail="XGBoost 回归需要选择因变量。")
    x_cols = list(dict.fromkeys((req.independent_variables or []) + (req.control_variables or [])))
    if not x_cols:
        raise HTTPException(status_code=400, detail="XGBoost 回归至少需要一个自变量。")
    ensure_columns(df, [req.dependent_variable] + x_cols, "XGBoost 变量")
    if not is_numeric_column(df[req.dependent_variable], type_hint(req, req.dependent_variable)):
        raise HTTPException(status_code=400, detail="XGBoost 回归要求因变量为连续型/数值型变量。")

    y, X, meta, build_warnings = build_design_matrix(df, req.dependent_variable, x_cols, req, binary=False)
    n_estimators = _opt_int(req, "n_estimators", 300, 20, 3000)
    max_depth = _opt_int(req, "max_depth", 4, 1, 20)
    learning_rate = _opt_float(req, "learning_rate", 0.05, 0.001, 1.0)
    subsample = _opt_float(req, "subsample", 0.9, 0.3, 1.0)
    colsample_bytree = _opt_float(req, "colsample_bytree", 0.9, 0.3, 1.0)
    estimator = XGBRegressor(
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        subsample=subsample,
        colsample_bytree=colsample_bytree,
        objective="reg:squarederror",
        random_state=int((req.options or {}).get("random_state", 42)),
        n_jobs=-1,
        verbosity=0,
    )
    try:
        fitted, pred_metrics, pred_rows, eval_warnings = evaluate_regressor(estimator, X, y, req)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="XGBoost 回归失败：{}".format(exc))
    importances = feature_importance_from_model(fitted, X.columns.tolist()) or permutation_importance_rows(fitted, X, y, "regression", random_state=int((req.options or {}).get("random_state", 42)))
    train = pred_metrics.get("train") or {}; test = pred_metrics.get("test") or {}; cvm = pred_metrics.get("cross_validation") or {}
    stats_out = {"test_r2": test.get("r2"), "test_rmse": test.get("rmse"), "test_mae": test.get("mae"), "cv_r2_mean": cvm.get("cv_r2_mean"), "train_r2": train.get("r2"), "n_estimators": n_estimators, "max_depth": max_depth, "learning_rate": learning_rate}
    warnings = build_warnings + eval_warnings + common_warnings(dataset, int(meta["n_rows_used"]), int(X.shape[1])) + ["XGBoost 属于非线性预测模型；变量重要性/SHAP 类解释不等同于显著性或因果效应。"]
    diagnostics, vif_warnings_list = diagnostics_with_vif({"n_rows_original": int(meta["n_rows_original"]), "n_rows_used": int(meta["n_rows_used"]), "n_rows_dropped": int(meta["n_rows_dropped"]), "predictor_count": int(X.shape[1]), "design_matrix_columns": X.columns.tolist(), "feature_importances": importances, "model_options": req.options or {}}, X)
    warnings.extend(vif_warnings_list)
    return response(req, "XGBoost 回归", "XGBRegressor", int(meta["n_rows_used"]), stats=stats_out, result_table=importances, diagnostics=diagnostics, prediction_metrics=pred_metrics, feature_importance=importances, prediction_table=pred_rows, warnings=warnings, summary="已完成 XGBoost 非线性回归、预测评估与变量重要性计算。")
