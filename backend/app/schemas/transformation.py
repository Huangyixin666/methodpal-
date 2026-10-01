from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DataTransformRequest(BaseModel):
    operation: Dict[str, Any] = Field(default_factory=dict)
    selected_variables: Optional[Dict[str, Any]] = None
    create_new_dataset: bool = True


class DataTransformPreview(BaseModel):
    ok: bool = True
    operation: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    summary: Dict[str, Any] = Field(default_factory=dict)
    preview_rows: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    variable_selection_patch: Optional[Dict[str, Any]] = None


class DataTransformApplyResponse(BaseModel):
    ok: bool = True
    operation: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    dataset: Dict[str, Any] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    variable_selection_patch: Optional[Dict[str, Any]] = None
