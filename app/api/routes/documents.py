from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from app.api.dependencies import get_current_user
from app.core.document_catalog import DocumentEntry, document_catalog
from app.schemas.documents import DocumentListResponse, DocumentMeta

router = APIRouter()


def _build_meta(entry: DocumentEntry, request: Request) -> DocumentMeta:
    # Trả relative URL - frontend sẽ tự ghép với API base + Authorization header.
    download = (
        f"{request.scope.get('root_path', '')}/api/documents/file"
        f"?citation_id={quote(entry.citation_id, safe='')}"
    )
    return DocumentMeta(
        citation_id=entry.citation_id,
        title=entry.title,
        category=entry.category,
        filename=entry.filename,
        media_type=entry.media_type,
        available=entry.available,
        download_url=download,
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    items = [_build_meta(entry, request) for entry in document_catalog.list_entries()]
    return {"items": items}


@router.get("/resolve", response_model=DocumentMeta)
async def resolve_document(
    request: Request,
    citation_id: str = Query(..., min_length=1),
    _user: dict = Depends(get_current_user),
):
    entry = document_catalog.resolve(citation_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Citation not found",
        )
    return _build_meta(entry, request)


@router.get("/file")
async def get_document_file(
    citation_id: str = Query(..., min_length=1),
    inline: bool = Query(True, description="Hiển thị inline (PDF viewer) hoặc tải về"),
    _user: dict = Depends(get_current_user),
):
    entry = document_catalog.resolve(citation_id)
    path = document_catalog.absolute_path(citation_id)
    print(path)
    if not entry or not path or not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    disposition = "inline" if inline else "attachment"
    safe_name = quote(entry.filename)

    return FileResponse(
        path=str(path),
        media_type=entry.media_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{safe_name}",
            "X-Citation-Id": entry.citation_id,
            "X-Document-Category": entry.category,
        },
    )


@router.post("/reload", status_code=status.HTTP_200_OK)
async def reload_catalog(_user: dict = Depends(get_current_user)):
    """Quét lại docs/ khi có mapping.json mới mà không cần restart server."""
    document_catalog.reload()
    return {"items_count": len(document_catalog.list_entries())}
