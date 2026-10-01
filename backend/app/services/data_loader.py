from __future__ import annotations

import io
import os
import zipfile
import uuid
from pathlib import Path

import pandas as pd
from fastapi import HTTPException, UploadFile

from app.schemas.dataset import DatasetResponse
from app.services.data_profiler import dataframe_to_dataset, dataset_dict_to_response, get_dataset
from app.services.store import DATASETS, user_storage_dir, owned_file_path


def _parse_uploaded_content(filename: str, content: bytes, header_mode: str = "row", header_row: int = 1) -> pd.DataFrame:
    if header_mode not in {"row", "none"}:
        raise HTTPException(status_code=400, detail="header_mode 仅支持 row 或 none")
    if header_row < 1:
        raise HTTPException(status_code=400, detail="表头行号必须从 1 开始")
    if filename.lower().endswith('.xlsx'):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024 or len(archive.infolist()) > 2000:
                    raise HTTPException(413, 'Excel 解压后过大')
        except zipfile.BadZipFile:
            raise HTTPException(400, '无效 Excel 文件')
    pandas_header = None if header_mode == "none" else header_row - 1
    try:
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, header=pandas_header)
        elif filename.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False, header=pandas_header)
        else:
            raise HTTPException(status_code=400, detail="仅支持 csv/xlsx/xls 文件")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="文件读取失败：{}".format(exc)) from exc
    if df.shape[0] * df.shape[1] > 1000000 or df.shape[1] > 2000:
        raise HTTPException(413, '数据最多支持 100 万单元格和 2000 列')
    if header_mode == "none":
        df.columns = ["变量{}".format(i + 1) for i in range(len(df.columns))]
    return df


def _source_dir() -> Path:
    path = Path(user_storage_dir()) / "raw_datasets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return suffix if suffix in {".csv", ".xlsx", ".xls"} else ".bin"


def _attach_source_metadata(dataset_id: str, *, filename: str, path: Path, header_mode: str, header_row: int) -> DatasetResponse:
    stored = get_dataset(dataset_id) or {}
    stored["_source"] = {"kind": "upload", "filename": filename, "path": str(path), "header_mode": header_mode, "header_row": int(header_row)}
    stored["source_kind"] = "upload"
    stored["header_mode"] = header_mode
    stored["header_row"] = int(header_row)
    stored["can_reparse_header"] = True
    DATASETS[dataset_id] = stored
    return dataset_dict_to_response(stored)


async def load_upload_file(file: UploadFile, header_mode: str = "row", header_row: int = 1) -> DatasetResponse:
    filename = (file.filename or "uploaded_file").replace("\\", "/").split("/")[-1][:200]
    limit = int(os.getenv('METHODPAL_MAX_BODY_BYTES', str(20 * 1024 * 1024)))
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, '上传文件过大')
    from app.services.storage_limits import ensure_upload_space
    ensure_upload_space(len(content))
    df = _parse_uploaded_content(filename, content, header_mode, header_row)
    dataset_id = "ds_{}".format(uuid.uuid4().hex[:8])
    response = dataframe_to_dataset(df, name=filename, dataset_id=dataset_id, source_kind="upload", header_mode=header_mode, header_row=header_row, can_reparse_header=True)
    source_path = _source_dir() / "{}{}".format(dataset_id, _safe_suffix(filename))
    try:
        ensure_upload_space(len(content))
    except HTTPException:
        DATASETS.pop(dataset_id, None)
        raise
    try:
        source_path.write_bytes(content)
    except Exception as exc:
        stored = get_dataset(dataset_id) or response.model_dump()
        stored["source_kind"] = "upload"
        stored["header_mode"] = header_mode
        stored["header_row"] = int(header_row)
        stored["can_reparse_header"] = False
        DATASETS[dataset_id] = stored
        response.can_reparse_header = False
        response.message += " 原始文件副本保存失败，上传后无法重新解释表头；请联系管理员。"
        return response
    return _attach_source_metadata(dataset_id, filename=filename, path=source_path, header_mode=header_mode, header_row=header_row)


def reparse_uploaded_dataset(dataset_id: str, header_mode: str = "row", header_row: int = 1) -> DatasetResponse:
    existing = get_dataset(dataset_id)
    if not existing:
        raise KeyError("dataset not found")
    source = existing.get("_source") or {}
    source_path = owned_file_path(str(source.get("path") or ""))
    if source.get("kind") != "upload" or not source.get("path") or not source_path.exists():
        raise ValueError("当前数据没有可重新解析的原始上传文件")
    filename = str(source.get("filename") or existing.get("name") or source_path.name)
    content = source_path.read_bytes()
    df = _parse_uploaded_content(filename, content, header_mode, header_row)
    response = dataframe_to_dataset(df, name=existing.get("name") or filename, dataset_id=dataset_id, source_kind="upload", header_mode=header_mode, header_row=header_row, can_reparse_header=True)
    stored = get_dataset(dataset_id) or response.model_dump()
    stored["_source"] = {**source, "kind": "upload", "filename": filename, "path": str(source_path), "header_mode": header_mode, "header_row": int(header_row)}
    stored["source_kind"] = "upload"
    stored["header_mode"] = header_mode
    stored["header_row"] = int(header_row)
    stored["can_reparse_header"] = True
    stored["message"] = "已按新的表头设置从原始文件重新解析：{} 行 × {} 列。".format(response.profile.row_count, response.profile.column_count)
    DATASETS[dataset_id] = stored
    return dataset_dict_to_response(stored)
