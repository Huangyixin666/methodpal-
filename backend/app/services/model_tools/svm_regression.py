from fastapi import HTTPException
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.prediction_evaluation import evaluate_regressor, permutation_importance_rows
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, is_numeric_column, type_hint, build_design_matrix, common_warnings, diagnostics_with_vif, response


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable:
        raise HTTPException(status_code=400, detail="SVM 回归需要选择因变量。")
    x_cols = list(dict.fromkeys((req.independent_variables or []) + (req.control_variables or [])))
    if not x_cols:
        raise HTTPException(status_code=400, detail="SVM 回归至少需要一个自变量。")
    ensure_columns(df, [req.dependent_variable] + x_cols, "SVM 变量")
    if not is_numeric_column(df[req.dependent_variable], type_hint(req, req.dependent_variable)):
        raise HTTPException(status_code=400, detail="SVM 回归要求因变量为连续型/数值型变量。")
    y, X, meta, build_warnings = build_design_matrix(df, req.dependent_variable, x_cols, req, binary=False)
    kernel = (req.options or {}).get("kernel", "rbf")
    estimator = Pipeline([("scaler", StandardScaler()), ("svr", SVR(kernel=kernel))])
    try:
        fitted, pred_metrics, pred_rows, eval_warnings = evaluate_regressor(estimator, X, y, req)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="SVM 回归失败：{}".format(exc))
    importances = permutation_importance_rows(fitted, X, y, "regression")
    train = pred_metrics.get("train") or {}; test = pred_metrics.get("test") or {}; cvm = pred_metrics.get("cross_validation") or {}
    stats_out = {"r2_in_sample": train.get("r2"), "rmse_in_sample": train.get("rmse"), "mae_in_sample": train.get("mae"), "test_r2": test.get("r2"), "test_rmse": test.get("rmse"), "test_mae": test.get("mae"), "cv_r2_mean": cvm.get("cv_r2_mean"), "kernel": kernel}
    warnings = build_warnings + eval_warnings + common_warnings(dataset, int(meta["n_rows_used"]), int(X.shape[1])) + ["SVM 属于预测模型；Permutation importance 表示预测贡献，不等同于统计显著性。"]
    diagnostics, vif_warnings_list = diagnostics_with_vif({"n_rows_original": int(meta["n_rows_original"]), "n_rows_used": int(meta["n_rows_used"]), "n_rows_dropped": int(meta["n_rows_dropped"]), "predictor_count": int(X.shape[1]), "design_matrix_columns": X.columns.tolist()}, X)
    warnings.extend(vif_warnings_list)
    return response(req, "SVM 回归", "SVR", int(meta["n_rows_used"]), stats=stats_out, result_table=importances, diagnostics=diagnostics, prediction_metrics=pred_metrics, feature_importance=importances, prediction_table=pred_rows, warnings=warnings, summary="已完成 SVM 回归与预测评估。")
