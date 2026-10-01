from typing import Callable, Dict

from fastapi import HTTPException

from app.schemas.analysis import AnalysisResponse, AnalysisRunRequest
from app.services.model_tools import descriptive_statistics
from app.services.model_tools import correlation_analysis
from app.services.model_tools import linear_regression
from app.services.model_tools import logistic_regression
from app.services.model_tools import t_test
from app.services.model_tools import one_way_anova
from app.services.model_tools import chi_square_test
from app.services.model_tools import mediation_analysis
from app.services.model_tools import moderation_analysis
from app.services.model_tools import lasso_regression
from app.services.model_tools import ridge_regression
from app.services.model_tools import elastic_net_regression
from app.services.model_tools import random_forest_regression
from app.services.model_tools import random_forest_classification
from app.services.model_tools import svm_regression
from app.services.model_tools import svm_classification
from app.services.model_tools import poisson_regression
from app.services.model_tools import ordinal_logistic_regression
from app.services.model_tools import model_comparison
from app.services.model_tools import sgd_classification
from app.services.model_tools import sgd_regression
from app.services.model_tools import catboost_classification
from app.services.model_tools import catboost_regression
from app.services.model_tools import xgboost_classification
from app.services.model_tools import xgboost_regression
from app.services.store import ANALYSES, DATASETS
from app.services.artifact_versions import source_snapshot, dataset_snapshot


# v0.5.1 tool registry: one model id maps to one callable tool.
# This keeps the backend close to the future agent architecture: an agent can choose a model_id and call the same tool.
MODEL_TOOLS: Dict[str, Callable[[AnalysisRunRequest], AnalysisResponse]] = {
    "descriptive_statistics": descriptive_statistics.run,
    "correlation_analysis": correlation_analysis.run,
    "linear_regression": linear_regression.run,
    "ols": linear_regression.run,
    "logistic_regression": logistic_regression.run,
    "t_test": t_test.run,
    "one_way_anova": one_way_anova.run,
    "chi_square_test": chi_square_test.run,
    "mediation_analysis": mediation_analysis.run,
    "moderation_analysis": moderation_analysis.run,
    "lasso_regression": lasso_regression.run,
    "lasso": lasso_regression.run,
    "ridge_regression": ridge_regression.run,
    "elastic_net_regression": elastic_net_regression.run,
    "poisson_regression": poisson_regression.run,
    "ordinal_logistic": ordinal_logistic_regression.run,
    "ordinal_logistic_regression": ordinal_logistic_regression.run,
    "random_forest_regression": random_forest_regression.run,
    "random_forest_classifier": random_forest_classification.run,
    "random_forest_classification": random_forest_classification.run,
    "svm_regression": svm_regression.run,
    "svm_classifier": svm_classification.run,
    "svm_classification": svm_classification.run,
    "model_comparison": model_comparison.run,
    "prediction_model_comparison": model_comparison.run,

    "xgboost_regression": xgboost_regression.run,
    "xgb_regression": xgboost_regression.run,
    "xgboost_classification": xgboost_classification.run,
    "xgb_classification": xgboost_classification.run,
    "xgboost_multiclass": xgboost_classification.run,
    "catboost_regression": catboost_regression.run,
    "catboost_classification": catboost_classification.run,
    "catboost_multiclass": catboost_classification.run,
    "sgd_regression": sgd_regression.run,
    "sgd_classification": sgd_classification.run,
    "sgd_classifier": sgd_classification.run,
}


def list_model_tools() -> Dict[str, str]:
    return {key: getattr(func, "__module__", "") for key, func in MODEL_TOOLS.items()}


def run_analysis(req: AnalysisRunRequest) -> AnalysisResponse:
    model_id = (req.model or "").strip()
    tool = MODEL_TOOLS.get(model_id)
    if not tool:
        raise HTTPException(status_code=400, detail="当前模型尚未实现或模型 ID 无效：{}".format(model_id))
    dataset = DATASETS.get(req.dataset_id)
    if not dataset:
        raise HTTPException(404, '数据集不存在')
    provenance = source_snapshot(dataset, model_id, req.model_dump(), req.options)
    result = tool(req)
    result.project_id = req.project_id
    if not result.project_id:
        from app.services.job_progress import job_id_context
        from app.services.store import JOBS
        job = JOBS.get(job_id_context.get()) if job_id_context.get() else None
        result.project_id = (job or {}).get('project_id')
    result.source_snapshot = provenance
    current = DATASETS.get(req.dataset_id) or {}
    if dataset_snapshot(current)['data_fingerprint'] != provenance['data_fingerprint']:
        result.artifact_status = 'stale'
        result.warnings.append('运行期间数据发生变化，此结果仅作为历史版本，请重新分析。')
    ANALYSES[result.analysis_id] = result.model_dump()
    return result


def get_analysis(analysis_id: str) -> AnalysisResponse:
    item = ANALYSES.get(analysis_id)
    if not item:
        raise HTTPException(status_code=404, detail="analysis not found")
    return AnalysisResponse(**item)
