from fastapi import HTTPException

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.prediction_evaluation import evaluate_classifier, feature_importance_from_model, permutation_importance_rows
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, is_classification_column, type_hint, build_design_matrix, common_warnings, diagnostics_with_vif, response, class_count


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
        from catboost import CatBoostClassifier
    except Exception as exc:
        raise HTTPException(status_code=400, detail="CatBoost 未安装或无法导入：{}".format(exc))
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable:
        raise HTTPException(status_code=400, detail="CatBoost 分类需要选择因变量。")
    x_cols = list(dict.fromkeys((req.independent_variables or []) + (req.control_variables or [])))
    if not x_cols:
        raise HTTPException(status_code=400, detail="CatBoost 分类至少需要一个自变量。")
    ensure_columns(df, [req.dependent_variable] + x_cols, "CatBoost 变量")
    if not is_classification_column(df[req.dependent_variable], type_hint(req, req.dependent_variable)):
        raise HTTPException(status_code=400, detail="CatBoost 分类要求因变量为二分类或多分类变量。")
    dep_classes = class_count(df[req.dependent_variable])
    y, X, meta, build_warnings = build_design_matrix(df, req.dependent_variable, x_cols, req, multiclass=True)
    iterations = _opt_int(req, "iterations", _opt_int(req, "n_estimators", 300, 20, 3000), 20, 3000)
    depth = _opt_int(req, "max_depth", 6, 1, 16)
    learning_rate = _opt_float(req, "learning_rate", 0.05, 0.001, 1.0)
    estimator = CatBoostClassifier(iterations=iterations, depth=depth, learning_rate=learning_rate, loss_function="Logloss" if dep_classes == 2 else "MultiClass", random_seed=int((req.options or {}).get("random_state", 42)), verbose=False, allow_writing_files=False)
    try:
        fitted, pred_metrics, pred_rows, eval_warnings = evaluate_classifier(estimator, X, y, req)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="CatBoost 分类失败：{}".format(exc))
    importances = feature_importance_from_model(fitted, X.columns.tolist()) or permutation_importance_rows(fitted, X, y, "classification", random_state=int((req.options or {}).get("random_state", 42)))
    train = pred_metrics.get("train") or {}; test = pred_metrics.get("test") or {}; cvm = pred_metrics.get("cross_validation") or {}
    stats_out = {"task_type": pred_metrics.get("task_type"), "class_count": dep_classes, "test_accuracy": test.get("accuracy"), "test_auc": test.get("auc"), "test_f1": test.get("f1"), "cv_accuracy_mean": cvm.get("cv_accuracy_mean"), "train_accuracy": train.get("accuracy"), "iterations": iterations, "depth": depth, "learning_rate": learning_rate}
    warnings = build_warnings + eval_warnings + common_warnings(dataset, int(meta["n_rows_used"]), int(X.shape[1])) + ["CatBoost 分类属于非线性预测模型；变量重要性不等同于统计显著性或因果效应。"]
    diagnostics, vif_warnings_list = diagnostics_with_vif({"n_rows_original": int(meta["n_rows_original"]), "n_rows_used": int(meta["n_rows_used"]), "n_rows_dropped": int(meta["n_rows_dropped"]), "predictor_count": int(X.shape[1]), "design_matrix_columns": X.columns.tolist(), "feature_importances": importances, "y_meta": meta.get("y_meta", {}), "model_options": req.options or {}}, X)
    warnings.extend(vif_warnings_list)
    return response(req, "CatBoost 分类", "CatBoostClassifier", int(meta["n_rows_used"]), stats=stats_out, result_table=importances, diagnostics=diagnostics, prediction_metrics=pred_metrics, feature_importance=importances, prediction_table=pred_rows, warnings=warnings, summary="已完成 CatBoost 二分类/多分类、预测评估与变量重要性计算。")
