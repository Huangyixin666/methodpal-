from __future__ import annotations

from app.schemas.variable import VariableCandidate, VariableInferResponse
from app.services.store import DATASETS, PROJECTS, VARIABLE_CONFIGS


def infer_variables(dataset_id: str | None, columns: list[str] | None, research_goal: str | None = None) -> VariableInferResponse:
    dataset = DATASETS.get(dataset_id or "")
    if dataset:
        profile_cols = dataset["profile"]["columns"]
        col_names = [c["name"] for c in profile_cols]
        type_map = {c["name"]: c.get("type", "unknown") for c in profile_cols}
        role_map = {c["name"]: c.get("role_suggestion") for c in profile_cols}
    else:
        col_names = columns or ["编号", "年龄", "性别", "家庭支持", "学业压力", "睡眠时间", "心理健康得分"]
        type_map = {c: "unknown" for c in col_names}
        role_map = {}

    dependent = [c for c in col_names if role_map.get(c) == "dependent"] or col_names[-1:]
    controls = [c for c in col_names if role_map.get(c) == "control"] or [c for c in col_names if c in {"年龄", "性别", "年级"}]
    exclude = set(dependent) | set(controls) | {"编号", "id", "ID"}
    independents = [c for c in col_names if c not in exclude]

    return VariableInferResponse(
        dependent_candidates=[VariableCandidate(name=c, type=type_map.get(c, "unknown"), reason="名称或变量角色显示其适合作为结果变量。") for c in dependent],
        independent_candidates=[VariableCandidate(name=c, type=type_map.get(c, "unknown"), reason="可作为解释变量进入影响因素模型。") for c in independents],
        control_candidates=[VariableCandidate(name=c, type=type_map.get(c, "unknown"), reason="常见人口学或背景控制变量。") for c in controls],
        message="变量角色推荐已完成。当前为规则占位版，后续可接入 LLM 做研究语义理解。",
    )


def save_variable_config(config: dict) -> dict:
    """Persist variable roles through the same project workspace used by React.

    ``VARIABLE_CONFIGS`` remains as a compatibility/audit store, while the
    project record is the source of truth for the current workspace.
    """
    key = config.get("project_id") or config.get("dataset_id") or "default"
    selection = {
        "dependent_variable": config.get("dependent_variable") or "",
        "independent_variables": list(config.get("independent_variables") or []),
        "control_variables": list(config.get("control_variables") or []),
        "mediator_variables": list(config.get("mediator_variables") or []),
        "moderator_variables": list(config.get("moderator_variables") or []),
        "group_variable": config.get("group_variable"),
        "variable_types": dict(config.get("variable_types") or {}),
        "analysis_goal": config.get("analysis_goal") or "",
    }
    stored = {**config, "selected_variables": selection}
    VARIABLE_CONFIGS[key] = stored

    project = None
    project_id = str(config.get("project_id") or "")
    if project_id:
        project = PROJECTS.get(project_id)
        if project:
            project["selected_variables"] = selection
            if config.get("selected_model") is not None:
                project["selected_model"] = config.get("selected_model")
            project["analysis_id"] = None
            project["ai_explanation"] = None
            if project.get("selected_model"):
                try:
                    from app.services.method_recommender import recommend_methods

                    payload = dict(selection)
                    payload.update({
                        "dataset_id": project.get("dataset_id") or config.get("dataset_id"),
                        "selected_model": project.get("selected_model"),
                        "research_goal": project.get("research_goal") or "",
                    })
                    response = recommend_methods(payload)
                    item = next((m for m in response.recommended if m.method_id == project.get("selected_model")), None)
                    project["progress"] = 70 if item and item.enabled else 55
                except Exception:
                    project["progress"] = 55
            elif project.get("dataset_id"):
                project["progress"] = 35
            PROJECTS[project_id] = project

    return {"ok": True, "config_id": key, "config": stored, "project": project}
