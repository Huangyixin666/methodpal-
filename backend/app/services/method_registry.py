"""Single source of truth for MethodPal model metadata and role requirements.

Both the REST API, the React model page and the Agent consume this registry.  A
method can be selected before all variable roles are configured; availability
is evaluated separately by ``method_recommender``.
"""
from typing import Any, Dict, List, Optional


NUMERIC_TYPES = ["连续型", "整数型", "计数型"]
ORDERED_NUMERIC_TYPES = ["连续型", "整数型", "计数型", "有序型"]
CLASS_TYPES = ["分类型", "二分类", "有序型"]
BINARY_TYPES = ["二分类"]
COUNT_TYPES = ["整数型", "计数型"]
ANY_ANALYTIC_TYPES = ["连续型", "整数型", "计数型", "分类型", "二分类", "有序型"]


def role(
    role_id: str,
    label: str,
    allowed_types: Optional[List[str]] = None,
    min_count: int = 1,
    max_count: Optional[int] = None,
    description: str = "",
) -> Dict[str, Any]:
    return {
        "role": role_id,
        "label": label,
        "min_count": min_count,
        "max_count": max_count,
        "allowed_types": list(allowed_types or ANY_ANALYTIC_TYPES),
        "description": description,
    }


DEFAULT_OPTIONS: Dict[str, Any] = {
    "test_size": 0.25,
    "cv_folds": 5,
    "random_state": 42,
    "polynomial_degree": 1,
    "add_log_terms": False,
    "add_exp_terms": False,
    "n_estimators": 300,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "min_samples_leaf": 1,
    "kernel": "rbf",
    "alpha": 0.0001,
    "penalty": "elasticnet",
    "max_iter": 2000,
}


def method(
    method_id: str,
    name: str,
    category: str,
    family: str,
    scene: str,
    requirement_summary: str,
    outputs: str,
    required_roles: Optional[List[Dict[str, Any]]] = None,
    optional_roles: Optional[List[Dict[str, Any]]] = None,
    tags: Optional[List[str]] = None,
    implemented: bool = True,
    min_rows: int = 2,
) -> Dict[str, Any]:
    return {
        "method_id": method_id,
        "id": method_id,
        "name": name,
        "category": category,
        "family": family,
        "scene": scene,
        "assumptions": [requirement_summary],
        "outputs": [outputs],
        "tags": list(tags or []),
        "implemented": implemented,
        "requirement": {
            "summary": requirement_summary,
            "required_roles": list(required_roles or []),
            "optional_roles": list(optional_roles or []),
            "min_rows": min_rows,
        },
        "default_options": dict(DEFAULT_OPTIONS),
    }


METHOD_DEFINITIONS: List[Dict[str, Any]] = [
    method(
        "descriptive_statistics", "描述统计", "基础分析", "basic",
        "先了解变量分布、缺失、均值和频数。未指定变量时默认分析全部非 ID 字段。",
        "无需指定因变量；可选一个或多个分析变量", "均值、标准差、频数、缺失率",
        optional_roles=[role("independent_variables", "分析变量", ANY_ANALYTIC_TYPES, 0, None, "留空时分析全部可用字段")],
    ),
    method(
        "correlation_analysis", "Pearson 相关分析", "基础分析", "basic",
        "探索数值变量之间的线性相关。", "至少 2 个数值分析变量", "相关矩阵、p 值",
        required_roles=[role("independent_variables", "分析变量", ORDERED_NUMERIC_TYPES, 2, None)],
    ),
    method(
        "chi_square_test", "交叉表 / 卡方检验", "基础分析", "basic",
        "检验两个分类变量是否有关联。", "两个分类变量", "列联表、χ²、p 值",
        required_roles=[role("dependent_variable", "变量 A", CLASS_TYPES, 1, 1), role("group_variable", "变量 B", CLASS_TYPES, 1, 1)],
    ),
    method(
        "t_test", "独立样本 t 检验", "差异检验", "difference",
        "比较两个组在连续结果上的均值差异。", "连续因变量 + 二分类分组变量", "组均值、t、p 值",
        required_roles=[role("dependent_variable", "结果变量", ORDERED_NUMERIC_TYPES, 1, 1), role("group_variable", "分组变量", BINARY_TYPES, 1, 1)],
    ),
    method(
        "one_way_anova", "单因素方差分析", "差异检验", "difference",
        "比较多个组在连续结果上的均值差异。", "连续因变量 + 分类分组变量", "组均值、F、p 值",
        required_roles=[role("dependent_variable", "结果变量", ORDERED_NUMERIC_TYPES, 1, 1), role("group_variable", "分组变量", CLASS_TYPES, 1, 1)],
    ),
    method(
        "linear_regression", "线性/非线性项 OLS 回归", "回归模型", "regression",
        "解释连续结局的影响因素，可选二次项、三次项、log 和 exp 特征。", "连续因变量 + 至少 1 个自变量", "R²、系数、p 值、VIF、诊断",
        required_roles=[role("dependent_variable", "因变量", ORDERED_NUMERIC_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)],
        optional_roles=[role("control_variables", "控制变量", ANY_ANALYTIC_TYPES, 0, None)], tags=["解释", "可加非线性项"],
    ),
    method(
        "logistic_regression", "二元 Logistic 回归", "分类模型", "classification",
        "解释二分类结局，输出 OR、方向和显著性。", "二分类因变量 + 至少 1 个自变量", "OR、系数、p 值、AUC",
        required_roles=[role("dependent_variable", "因变量", BINARY_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)],
        optional_roles=[role("control_variables", "控制变量", ANY_ANALYTIC_TYPES, 0, None)], tags=["解释", "二分类"],
    ),
    method(
        "ordinal_logistic", "有序 Logistic 回归", "分类模型", "classification",
        "因变量是有序等级，例如低/中/高。", "有序型因变量 + 至少 1 个自变量", "阈值参数、系数、p 值",
        required_roles=[role("dependent_variable", "因变量", ["有序型"], 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)],
        optional_roles=[role("control_variables", "控制变量", ANY_ANALYTIC_TYPES, 0, None)], tags=["有序"],
    ),
    method(
        "poisson_regression", "Poisson 回归", "回归模型", "regression",
        "解释非负计数型结局，例如次数、数量和事件数。", "计数型因变量 + 至少 1 个自变量", "IRR、系数、偏差",
        required_roles=[role("dependent_variable", "因变量", COUNT_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)],
        optional_roles=[role("control_variables", "控制变量", ANY_ANALYTIC_TYPES, 0, None)], tags=["计数"],
    ),
    method(
        "lasso_regression", "LASSO 回归", "回归模型", "regression",
        "变量较多时做稀疏筛选并缓解共线性。", "连续因变量 + 至少 1 个自变量", "稀疏系数、预测评估",
        required_roles=[role("dependent_variable", "因变量", ORDERED_NUMERIC_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["筛变量"],
    ),
    method(
        "ridge_regression", "Ridge 回归", "回归模型", "regression",
        "共线性较强但不希望变量被直接压成 0。", "连续因变量 + 至少 1 个自变量", "正则化系数、预测评估",
        required_roles=[role("dependent_variable", "因变量", ORDERED_NUMERIC_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["共线性"],
    ),
    method(
        "elastic_net_regression", "Elastic Net 回归", "回归模型", "regression",
        "结合 LASSO 和 Ridge，适合高维且变量相关的场景。", "连续因变量 + 至少 1 个自变量", "混合正则化系数、预测评估",
        required_roles=[role("dependent_variable", "因变量", ORDERED_NUMERIC_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["筛变量", "共线性"],
    ),
    method(
        "sgd_regression", "SGD 回归", "回归模型", "regression",
        "较大样本或高维特征的快速线性预测。", "连续因变量 + 至少 1 个自变量", "预测指标、系数重要性",
        required_roles=[role("dependent_variable", "因变量", ORDERED_NUMERIC_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["大样本", "正则化"],
    ),
    method(
        "random_forest_regression", "随机森林回归", "回归模型", "regression",
        "适合非线性关系、复杂交互和预测探索。", "连续因变量 + 至少 1 个自变量", "R²/RMSE/MAE、变量重要性",
        required_roles=[role("dependent_variable", "因变量", ORDERED_NUMERIC_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["非线性", "树模型"],
    ),
    method(
        "xgboost_regression", "XGBoost 回归", "回归模型", "regression",
        "结构化表格数据的强预测基线。", "连续因变量 + 至少 1 个自变量", "预测指标、变量重要性",
        required_roles=[role("dependent_variable", "因变量", ORDERED_NUMERIC_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["非线性", "Boosting"],
    ),
    method(
        "catboost_regression", "CatBoost 回归", "回归模型", "regression",
        "适合类别字段较多的表格数据。", "连续因变量 + 至少 1 个自变量", "预测指标、变量重要性",
        required_roles=[role("dependent_variable", "因变量", ORDERED_NUMERIC_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["非线性", "Boosting"],
    ),
    method(
        "svm_regression", "SVM 回归", "回归模型", "regression",
        "核函数非线性回归，可作为预测模型补充。", "连续因变量 + 至少 1 个自变量", "R²/RMSE/MAE、Permutation importance",
        required_roles=[role("dependent_variable", "因变量", ORDERED_NUMERIC_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["核方法"],
    ),
    method(
        "random_forest_classification", "随机森林分类", "分类模型", "classification",
        "二分类/多分类预测的鲁棒非线性基线。", "分类因变量 + 至少 1 个自变量", "Accuracy、F1、AUC/混淆矩阵、变量重要性",
        required_roles=[role("dependent_variable", "因变量", CLASS_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["二分类", "多分类", "树模型"],
    ),
    method(
        "xgboost_classification", "XGBoost 分类", "分类模型", "classification",
        "结构化表格分类的强预测模型。", "分类因变量 + 至少 1 个自变量", "Accuracy、F1、AUC/混淆矩阵、变量重要性",
        required_roles=[role("dependent_variable", "因变量", CLASS_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["二分类", "多分类", "Boosting"],
    ),
    method(
        "catboost_classification", "CatBoost 分类", "分类模型", "classification",
        "类别字段较多时的表格分类模型。", "分类因变量 + 至少 1 个自变量", "Accuracy、F1、AUC/混淆矩阵、变量重要性",
        required_roles=[role("dependent_variable", "因变量", CLASS_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["二分类", "多分类", "Boosting"],
    ),
    method(
        "sgd_classification", "SGD 分类", "分类模型", "classification",
        "大样本或高维特征的快速线性分类。", "分类因变量 + 至少 1 个自变量", "Accuracy、F1、系数重要性",
        required_roles=[role("dependent_variable", "因变量", CLASS_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["二分类", "多分类", "大样本"],
    ),
    method(
        "svm_classification", "SVM 分类", "分类模型", "classification",
        "核函数分类模型，适合作为非线性分类补充。", "分类因变量 + 至少 1 个自变量", "Accuracy、F1、混淆矩阵",
        required_roles=[role("dependent_variable", "因变量", CLASS_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["二分类", "多分类", "核方法"],
    ),
    method(
        "model_comparison", "模型对比", "模型对比", "comparison",
        "比较多个回归/分类预测模型的测试集和交叉验证表现。", "连续或分类因变量 + 至少 1 个自变量", "测试集指标、交叉验证、模型排名",
        required_roles=[role("dependent_variable", "因变量", ANY_ANALYTIC_TYPES, 1, 1), role("independent_variables", "自变量", ANY_ANALYTIC_TYPES, 1, None)], tags=["回归", "分类", "自动比较"],
    ),
    method(
        "mediation_analysis", "中介效应分析", "机制模型", "mechanism",
        "检验 X 是否通过 M 影响 Y。", "连续 Y + X + 中介变量", "a/b/c/c′ 路径、间接效应",
        required_roles=[role("dependent_variable", "结果变量 Y", ORDERED_NUMERIC_TYPES, 1, 1), role("independent_variables", "自变量 X", ANY_ANALYTIC_TYPES, 1, None), role("mediator_variables", "中介变量 M", ANY_ANALYTIC_TYPES, 1, None)],
        optional_roles=[role("control_variables", "控制变量", ANY_ANALYTIC_TYPES, 0, None)],
    ),
    method(
        "moderation_analysis", "调节效应分析", "机制模型", "mechanism",
        "检验 W 是否改变 X 对 Y 的影响。", "连续 Y + X + 调节变量", "交互项系数、p 值",
        required_roles=[role("dependent_variable", "结果变量 Y", ORDERED_NUMERIC_TYPES, 1, 1), role("independent_variables", "自变量 X", ANY_ANALYTIC_TYPES, 1, None), role("moderator_variables", "调节变量 W", ANY_ANALYTIC_TYPES, 1, None)],
        optional_roles=[role("control_variables", "控制变量", ANY_ANALYTIC_TYPES, 0, None)],
    ),
    method(
        "sem", "结构方程模型 SEM", "机制模型", "mechanism",
        "潜变量、多路径和理论模型拟合。", "潜变量和路径设定", "拟合指标、路径系数",
        implemented=False,
    ),
]


METHOD_BY_ID: Dict[str, Dict[str, Any]] = {item["method_id"]: item for item in METHOD_DEFINITIONS}


def get_method_definition(method_id: str) -> Optional[Dict[str, Any]]:
    return METHOD_BY_ID.get(str(method_id or ""))
