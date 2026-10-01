"""Contract tests for the resumable autonomous workflow."""

import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_ROOT = str(Path(__file__).resolve().parents[1])
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)


class FakeModel:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    def model_dump(self):
        def convert(value):
            if isinstance(value, FakeModel):
                return value.model_dump()
            if isinstance(value, list):
                return [convert(item) for item in value]
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            return value

        return {key: convert(value) for key, value in self.__dict__.items()}

    def dict(self):
        return self.model_dump()


class FakeResponse(FakeModel):
    pass


class FakeSuggestion(FakeModel):
    pass


def install_stubs():
    schemas = types.ModuleType("app.schemas.agent")
    for name in [
        "AgentAction",
        "AgentPlanStep",
        "AgentQuestion",
        "AgentQuestionOption",
        "AgentTraceEvent",
    ]:
        setattr(schemas, name, FakeModel)
    schemas.AgentResponse = FakeResponse
    sys.modules["app.schemas.agent"] = schemas

    dataset = {
        "found": True,
        "dataset_id": "dataset-1",
        "row_count": 120,
        "column_count": 3,
        "issue_count": 0,
        "missing_count": 0,
        "outlier_count": 0,
        "columns": [
            {"name": "outcome", "type": "continuous", "unique_count": 80},
            {"name": "age", "type": "integer", "unique_count": 50},
            {"name": "income", "type": "continuous", "unique_count": 100},
        ],
    }
    dataset_tools = types.ModuleType("app.services.agent_tools.dataset_tools")
    dataset_tools.get_dataset_summary = lambda dataset_id: dict(dataset) if dataset_id else {"found": False, "columns": []}
    dataset_tools.summarize_dataset_quality = lambda summary: []
    sys.modules["app.services.agent_tools.dataset_tools"] = dataset_tools

    variable_tools = types.ModuleType("app.services.agent_tools.variable_tools")

    def recommend_variables(summary, goal):
        return [
            FakeSuggestion(variable="outcome", suggested_role="dependent", suggested_type="continuous"),
            FakeSuggestion(variable="age", suggested_role="independent", suggested_type="integer"),
            FakeSuggestion(variable="income", suggested_role="independent", suggested_type="continuous"),
        ]

    default_selection = {
        "dependent_variable": "outcome",
        "independent_variables": ["age", "income"],
        "control_variables": [],
        "mediator_variables": [],
        "moderator_variables": [],
        "group_variable": None,
        "variable_types": {"outcome": "continuous", "age": "integer", "income": "continuous"},
    }
    variable_tools.recommend_variables = recommend_variables
    variable_tools.build_default_variable_selection = lambda suggestions: dict(default_selection)
    sys.modules["app.services.agent_tools.variable_tools"] = variable_tools

    method_tools = types.ModuleType("app.services.agent_tools.method_tools")
    method = FakeModel(
        method_id="xgboost_regression",
        name="XGBoost regression",
        enabled=True,
        selectable=True,
        availability="ready",
        missing_roles=[],
        reason="Suitable for prediction",
    )
    method_tools.recommend_methods_for_selection = lambda selection, dataset_id, goal: [method]
    method_tools.choose_primary_method = lambda methods, goal: "xgboost_regression"
    method_tools.get_method_configuration = lambda model, selection, dataset_id, goal: {
        "enabled": bool(selection.get("dependent_variable") and selection.get("independent_variables")),
        "availability": "ready",
        "missing_roles": [],
        "reason": "requirements satisfied",
    }
    method_tools.build_selection_for_method = lambda model, selection, dataset_id, goal: (selection, recommend_variables(dataset, goal))
    sys.modules["app.services.agent_tools.method_tools"] = method_tools

    analysis_tools = types.ModuleType("app.services.agent_tools.analysis_tools")
    analysis_tools.run_selected_analysis = lambda **kwargs: {
        "analysis_id": "analysis-1",
        "model_id": kwargs["model_id"],
        "model_name": "XGBoost regression",
        "metrics": {"r2": 0.42},
    }
    sys.modules["app.services.agent_tools.analysis_tools"] = analysis_tools

    store = types.ModuleType("app.services.store")
    store.AGENT_CHATS = {}
    store.AGENT_STATES = {}
    store.ANALYSES = {}
    store.PROJECTS = {}
    store.VARIABLE_CONFIGS = {}
    store.DATASETS = {'dataset-1': {'dataset_id': 'dataset-1', 'headers': [], 'rows': []}}
    store.JOBS = {}
    sys.modules["app.services.store"] = store
    return store


STORE = install_stubs()
workflow = importlib.import_module("app.services.langgraph_agent.autonomous_workflow")


def request(message="", thread_id=None, answers=None, research_goal=""):
    context = SimpleNamespace(
        project_id="project-1",
        dataset_id="dataset-1",
        dataset_version=1,
        research_goal=research_goal,
        analysis_goal=None,
        research_spec=None,
        selected_variables=None,
        selected_model=None,
        model_options={},
        data_review_status="reviewed",
    )
    return SimpleNamespace(
        message=message,
        action="autopilot",
        mode="autopilot",
        thread_id=thread_id,
        answers=answers or {},
        options={},
        context=context,
    )


class AutonomousWorkflowContractTests(unittest.TestCase):
    def setUp(self):
        STORE.AGENT_CHATS.clear()
        STORE.AGENT_STATES.clear()
        STORE.ANALYSES.clear()
        STORE.VARIABLE_CONFIGS.clear()
        STORE.PROJECTS.clear()
        STORE.PROJECTS["project-1"] = {
            "project_id": "project-1",
            "name": "test",
            "research_goal": "",
            "analysis_goal": None,
            "research_spec": None,
            "selected_variables": None,
            "selected_model": None,
            "model_options": {},
            "progress": 35,
        }

    def test_pauses_resumes_persists_and_reuses_goal(self):
        first = workflow.prepare_autonomous_intake(request(message="autopilot"))
        self.assertEqual(first["route"], "wait")
        self.assertEqual(first["response"].questions[0].id, "research_question")

        thread_id = first["session"]["thread_id"]
        resumed = workflow.prepare_autonomous_intake(
            request(
                message="predict outcome",
                thread_id=thread_id,
                answers={"research_question": "predict outcome"},
            )
        )
        self.assertEqual(resumed["route"], "plan")
        planned = workflow.prepare_autonomous_plan(
            request(thread_id=thread_id), resumed["session"], resumed["dataset"]
        )
        response = workflow.execute_autonomous_plan(
            request(thread_id=thread_id), planned["session"], planned["dataset"]
        )
        self.assertEqual(response.status, "completed")
        self.assertEqual(STORE.PROJECTS["project-1"]["analysis_goal"], "prediction")
        self.assertEqual(STORE.PROJECTS["project-1"]["analysis_id"], "analysis-1")
        self.assertEqual(
            STORE.PROJECTS["project-1"]["selected_variables"]["analysis_goal"],
            "prediction",
        )

        STORE.ANALYSES["analysis-1"] = {
            **response.execution_result,
            "dataset_id": "dataset-1",
        }
        from app.services.artifact_versions import source_snapshot
        STORE.ANALYSES['analysis-1']['model'] = 'xgboost_regression'
        STORE.ANALYSES['analysis-1']['source_snapshot'] = source_snapshot(STORE.DATASETS['dataset-1'], 'xgboost_regression', STORE.PROJECTS['project-1']['selected_variables'], {})
        repeated = workflow.prepare_autonomous_intake(request(message="autopilot"))
        self.assertEqual(repeated["route"], "wait")
        self.assertEqual(repeated["response"].status, "completed")
        self.assertEqual(repeated["session"]["research_spec"]["analysis_goal"], "prediction")

        new_request = workflow.prepare_autonomous_intake(request(message="describe data"))
        self.assertEqual(new_request["route"], "plan")
        self.assertEqual(new_request["session"]["research_spec"]["analysis_goal"], "description")

    def test_migrates_goal_from_last_completed_chat(self):
        STORE.AGENT_CHATS["project-1"] = {
            "messages": [{
                "role": "ai",
                "meta": {"workflow_state": {
                    "status": "completed",
                    "dataset_id": "dataset-1",
                    "dataset_version": 1,
                    "research_spec": {
                        "research_question": "predict outcome",
                        "analysis_goal": "prediction",
                        "target_variable": "outcome",
                        "target_type": "continuous",
                        "predictor_variables": ["age", "income"],
                        "control_variables": [],
                    },
                }},
            }],
        }
        repeated = workflow.prepare_autonomous_intake(request(message="autopilot"))
        self.assertEqual(repeated["route"], "plan")
        self.assertEqual(repeated["session"]["research_spec"]["analysis_goal"], "prediction")

    def test_execution_failure_has_no_descriptive_fallback(self):
        first = workflow.prepare_autonomous_intake(
            request(message="predict outcome", research_goal="predict outcome")
        )
        planned = workflow.prepare_autonomous_plan(request(), first["session"], first["dataset"])
        calls = []
        original = workflow.run_selected_analysis

        def fail(**kwargs):
            calls.append(kwargs["model_id"])
            raise RuntimeError("simulated model failure")

        workflow.run_selected_analysis = fail
        try:
            response = workflow.execute_autonomous_plan(request(), planned["session"], planned["dataset"])
        finally:
            workflow.run_selected_analysis = original

        self.assertEqual(response.status, "failed")
        self.assertEqual(calls, ["xgboost_regression"])
        self.assertNotIn("descriptive_statistics", calls)


if __name__ == "__main__":
    unittest.main()
