from typing import Any, Dict, List, Tuple

from fastapi import HTTPException
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import ElasticNetCV, LassoCV, LinearRegression, LogisticRegression, RidgeCV, SGDClassifier, SGDRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC, SVR

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.prediction_evaluation import evaluate_classifier, evaluate_regressor, flatten_classification_metrics, flatten_regression_metrics
from app.services.model_tools.shared import (
    build_design_matrix,
    class_count,
    common_warnings,
    dataset_to_dataframe,
    diagnostics_with_vif,
    ensure_columns,
    is_classification_column,
    is_numeric_column,
    response,
    type_hint,
)


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


def _try_xgb_reg(req: AnalysisRunRequest):
    try:
        from xgboost import XGBRegressor
        return XGBRegressor(n_estimators=_opt_int(req, "n_estimators", 250, 20, 1000), max_depth=_opt_int(req, "max_depth", 4, 1, 20), learning_rate=_opt_float(req, "learning_rate", 0.05, 0.001, 1.0), subsample=_opt_float(req, "subsample", 0.9, 0.3, 1.0), colsample_bytree=_opt_float(req, "colsample_bytree", 0.9, 0.3, 1.0), objective="reg:squarederror", random_state=int((req.options or {}).get("random_state", 42)), n_jobs=-1, verbosity=0)
    except Exception:
        return None


def _try_xgb_cls(req: AnalysisRunRequest, class_count_value: int):
    try:
        from xgboost import XGBClassifier
        params = dict(n_estimators=_opt_int(req, "n_estimators", 250, 20, 1000), max_depth=_opt_int(req, "max_depth", 4, 1, 20), learning_rate=_opt_float(req, "learning_rate", 0.05, 0.001, 1.0), subsample=_opt_float(req, "subsample", 0.9, 0.3, 1.0), colsample_bytree=_opt_float(req, "colsample_bytree", 0.9, 0.3, 1.0), random_state=int((req.options or {}).get("random_state", 42)), n_jobs=-1, verbosity=0, eval_metric="logloss" if class_count_value == 2 else "mlogloss")
        if class_count_value > 2:
            params.update({"objective": "multi:softprob", "num_class": class_count_value})
        else:
            params.update({"objective": "binary:logistic"})
        return XGBClassifier(**params)
    except Exception:
        return None


def _try_cat_reg(req: AnalysisRunRequest):
    try:
        from catboost import CatBoostRegressor
        return CatBoostRegressor(iterations=_opt_int(req, "iterations", _opt_int(req, "n_estimators", 250, 20, 1000), 20, 1000), depth=_opt_int(req, "max_depth", 6, 1, 16), learning_rate=_opt_float(req, "learning_rate", 0.05, 0.001, 1.0), loss_function="RMSE", random_seed=int((req.options or {}).get("random_state", 42)), verbose=False, allow_writing_files=False)
    except Exception:
        return None


def _try_cat_cls(req: AnalysisRunRequest, class_count_value: int):
    try:
        from catboost import CatBoostClassifier
        return CatBoostClassifier(iterations=_opt_int(req, "iterations", _opt_int(req, "n_estimators", 250, 20, 1000), 20, 1000), depth=_opt_int(req, "max_depth", 6, 1, 16), learning_rate=_opt_float(req, "learning_rate", 0.05, 0.001, 1.0), loss_function="Logloss" if class_count_value == 2 else "MultiClass", random_seed=int((req.options or {}).get("random_state", 42)), verbose=False, allow_writing_files=False)
    except Exception:
        return None


def _regression_models(req: AnalysisRunRequest) -> List[Tuple[str, Any]]:
    models: List[Tuple[str, Any]] = [
        ("线性回归", LinearRegression()),
        ("Ridge", Pipeline([("scaler", StandardScaler()), ("ridge", RidgeCV(alphas=[0.01, 0.1, 1.0, 10.0, 100.0]))])),
        ("LASSO", Pipeline([("scaler", StandardScaler()), ("lasso", LassoCV(cv=3, random_state=42, max_iter=20000))])),
        ("Elastic Net", Pipeline([("scaler", StandardScaler()), ("enet", ElasticNetCV(cv=3, random_state=42, max_iter=20000, l1_ratio=[0.1, 0.5, 0.9]))])),
        ("随机森林回归", RandomForestRegressor(n_estimators=_opt_int(req, "n_estimators", 250, 20, 1000), random_state=42, min_samples_leaf=1, n_jobs=-1)),
        ("SVM 回归", Pipeline([("scaler", StandardScaler()), ("svr", SVR(kernel=str((req.options or {}).get("kernel", "rbf"))))])),
        ("SGD 回归", Pipeline([("scaler", StandardScaler()), ("sgd", SGDRegressor(loss="squared_error", penalty="elasticnet", max_iter=2000, random_state=42))])),
    ]
    xgb = _try_xgb_reg(req)
    if xgb is not None:
        models.append(("XGBoost 回归", xgb))
    cat = _try_cat_reg(req)
    if cat is not None:
        models.append(("CatBoost 回归", cat))
    return models


def _classification_models(req: AnalysisRunRequest, class_count_value: int) -> List[Tuple[str, Any]]:
    models: List[Tuple[str, Any]] = [
        ("Logistic 回归", Pipeline([("scaler", StandardScaler()), ("logit", LogisticRegression(max_iter=2000, multi_class="auto"))])),
        ("随机森林分类", RandomForestClassifier(n_estimators=_opt_int(req, "n_estimators", 250, 20, 1000), random_state=42, min_samples_leaf=1, class_weight="balanced" if class_count_value == 2 else None, n_jobs=-1)),
        ("SVM 分类", Pipeline([("scaler", StandardScaler()), ("svc", SVC(kernel=str((req.options or {}).get("kernel", "rbf")), probability=True, random_state=42))])),
        ("SGD 分类", Pipeline([("scaler", StandardScaler()), ("sgd", SGDClassifier(loss="log_loss", penalty="elasticnet", max_iter=2000, random_state=42, class_weight="balanced" if class_count_value == 2 else None))])),
    ]
    xgb = _try_xgb_cls(req, class_count_value)
    if xgb is not None:
        models.append(("XGBoost 分类", xgb))
    cat = _try_cat_cls(req, class_count_value)
    if cat is not None:
        models.append(("CatBoost 分类", cat))
    return models


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable:
        raise HTTPException(status_code=400, detail="模型对比需要选择因变量。")
    x_cols = list(dict.fromkeys((req.independent_variables or []) + (req.control_variables or [])))
    if not x_cols:
        raise HTTPException(status_code=400, detail="模型对比至少需要一个自变量。")
    ensure_columns(df, [req.dependent_variable] + x_cols, "模型对比变量")

    dep_hint = type_hint(req, req.dependent_variable)
    dep_class_count = class_count(df[req.dependent_variable])
    if is_classification_column(df[req.dependent_variable], dep_hint):
        task_type = "classification" if dep_class_count == 2 else "multiclass_classification"
        y, X, meta, build_warnings = build_design_matrix(df, req.dependent_variable, x_cols, req, multiclass=True)
        model_defs = _classification_models(req, dep_class_count)
    elif is_numeric_column(df[req.dependent_variable], dep_hint):
        task_type = "regression"
        y, X, meta, build_warnings = build_design_matrix(df, req.dependent_variable, x_cols, req, binary=False)
        model_defs = _regression_models(req)
    else:
        raise HTTPException(status_code=400, detail="模型对比当前支持连续型回归任务、二分类任务或多分类任务。请先检查因变量类型。")

    comparison_rows: List[Dict[str, Any]] = []
    warnings = list(build_warnings)
    for model_name, estimator in model_defs:
        try:
            if task_type == "regression":
                _, metrics, _, eval_warnings = evaluate_regressor(estimator, X, y, req)
                row = flatten_regression_metrics(model_name, metrics)
            else:
                _, metrics, _, eval_warnings = evaluate_classifier(estimator, X, y, req)
                row = flatten_classification_metrics(model_name, metrics)
            comparison_rows.append(row)
            warnings.extend(["{}：{}".format(model_name, w) for w in eval_warnings])
        except Exception as exc:
            comparison_rows.append({"model": model_name, "task_type": task_type, "error": str(exc)})

    def score(row: Dict[str, Any]) -> float:
        if row.get("error"):
            return -999999.0
        keys = ["test_r2", "cv_r2_mean", "test_auc", "test_f1", "test_accuracy", "cv_accuracy_mean", "train_accuracy"]
        for key in keys:
            value = row.get(key)
            try:
                if value is not None:
                    return float(value)
            except Exception:
                pass
        return -999999.0

    comparison_rows = sorted(comparison_rows, key=score, reverse=True)
    best_model = comparison_rows[0].get("model") if comparison_rows and not comparison_rows[0].get("error") else None
    diagnostics, vif_warnings_list = diagnostics_with_vif({
        "n_rows_original": int(meta["n_rows_original"]),
        "n_rows_used": int(meta["n_rows_used"]),
        "n_rows_dropped": int(meta["n_rows_dropped"]),
        "predictor_count": int(X.shape[1]),
        "design_matrix_columns": X.columns.tolist(),
        "task_type": task_type,
        "class_count": dep_class_count if task_type != "regression" else None,
        "best_model": best_model,
        "model_options": req.options or {},
    }, X)
    warnings.extend(vif_warnings_list)
    warnings.extend(common_warnings(dataset, int(meta["n_rows_used"]), int(X.shape[1])))
    warnings.append("模型对比用于预测性能比较，不应用来直接替代理论驱动的因果或显著性解释。")
    stats = {"task_type": task_type, "best_model": best_model, "model_count": len(comparison_rows)}
    return response(req, "模型对比", "Prediction model comparison", int(meta["n_rows_used"]), stats=stats, result_table=comparison_rows, model_comparison=comparison_rows, diagnostics=diagnostics, warnings=warnings, summary="已完成多个预测模型的统一训练/测试/交叉验证对比。最佳模型仅按当前评估指标排序，需结合研究目标判断。")
