from fastapi import APIRouter, Depends, File, UploadFile

from backend.auth import require_admin
from backend.dependencies import document_service
from backend.models import User
from backend.schemas import DocumentDeleteResponse, DocumentListResponse, DocumentUploadResponse


router = APIRouter()


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(_: User = Depends(require_admin)):
    """获取已上传的文档列表（管理员）"""
    return document_service.list_documents()


@router.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...), _: User = Depends(require_admin)):
    """上传文档并进行 embedding（管理员）"""
    return await document_service.upload_document(file)


@router.delete("/documents/{filename}", response_model=DocumentDeleteResponse)
async def delete_document(filename: str, _: User = Depends(require_admin)):
    """删除文档在 Milvus 中的向量（保留本地文件，管理员）"""
    return document_service.delete_document(filename)
