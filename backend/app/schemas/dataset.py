from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field

IssueType = Literal["missing", "outlier_high", "outlier_low"]


class CellIssue(BaseModel):
    type: IssueType
    suggestion: str
    label: str


class Cell(BaseModel):
    value: str
    issue: Optional[CellIssue] = None
    fixed: Optional[bool] = None


class DatasetProfileColumn(BaseModel):
    name: str
    type: str
    missing_count: int = 0
    missing_rate: float = 0.0
    unique_count: int = 0
    role_suggestion: Optional[str] = None

    # Optional richer profiling fields used by newer frontend/agent code.
    non_missing_count: Optional[int] = None
    numeric_count: Optional[int] = None
    numeric_rate: Optional[float] = None
    mean: Optional[float] = None
    std: Optional[float] = None
    min: Optional[float] = None
    q1: Optional[float] = None
    median: Optional[float] = None
    q3: Optional[float] = None
    max: Optional[float] = None
    top_values: Optional[List[dict]] = None


class DatasetProfile(BaseModel):
    row_count: int
    column_count: int
    issue_count: int
    missing_count: int
    outlier_count: int
    columns: List[DatasetProfileColumn]
    summary: Optional[str] = None


class DatasetResponse(BaseModel):
    dataset_revision: int = 0
    data_fingerprint: Optional[str] = None
    dataset_id: str
    name: str
    headers: List[str]

    # rows/cells are the frontend-visible rows. For normal datasets this is all rows;
    # for very large datasets this can be a preview, while backend analysis still uses _full_rows in storage.
    rows: List[List[str]]
    cells: List[List[Cell]]
    profile: DatasetProfile
    message: str

    # v0.6.8 preview metadata. These fields make it explicit whether the editor is showing all rows.
    preview_row_count: int = 0
    full_row_count: int = 0
    is_preview: bool = False

    # Uploaded files keep an immutable source copy so users can reinterpret
    # the header after import without uploading the file again.
    source_kind: Optional[str] = None
    header_mode: Optional[Literal["row", "none"]] = None
    header_row: Optional[int] = None
    can_reparse_header: bool = False


class DatasetRowsResponse(BaseModel):
    dataset_id: str
    offset: int
    limit: int
    total_rows: int
    rows: List[List[str]]
    cells: List[List[Cell]]
    has_more: bool = False


class DatasetCellChange(BaseModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    value: str = ""


class DatasetCellPatchRequest(BaseModel):
    changes: List[DatasetCellChange] = Field(default_factory=list)


class DatasetHeaderReparseRequest(BaseModel):
    header_mode: Literal["row", "none"] = "row"
    header_row: int = Field(default=1, ge=1)


class DatasetRowInsertRequest(BaseModel):
    index: Optional[int] = Field(default=None, ge=0)
    count: int = Field(default=1, ge=1, le=100)


class DatasetRowDeleteRequest(BaseModel):
    indices: List[int] = Field(default_factory=list)


class DatasetColumnInsertRequest(BaseModel):
    name: str = "新变量"
    index: Optional[int] = Field(default=None, ge=0)
    default_value: str = ""


class DatasetColumnDeleteRequest(BaseModel):
    indices: List[int] = Field(default_factory=list)


class PasteDatasetRequest(BaseModel):
    headers: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)
    name: Optional[str] = None


class CleanSuggestion(BaseModel):
    row: int
    col: int
    column: str
    issue_type: str
    current_value: str
    suggestion: str
    message: str


class CleanApplyRequest(BaseModel):
    strategy: str = "accept_suggestions"
    fixes: Optional[List[CleanSuggestion]] = None
