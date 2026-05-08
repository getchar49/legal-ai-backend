import os
import re
from typing import AsyncIterator, Optional
from urllib.parse import quote

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_current_user
from app.core.document_catalog import DocumentEntry, document_catalog
from app.schemas.documents import DocumentListResponse, DocumentMeta

router = APIRouter()

# 256 KiB chunks: large enough to be efficient, small enough to keep
# Time-To-First-Byte for the first viewer-rendered page in single-digit
# seconds even on slow links.
_CHUNK_SIZE = 256 * 1024

# `bytes=START-END` where either side may be empty (suffix ranges and
# open-ended ranges are both legal per RFC 9110 §14).
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$", re.IGNORECASE)


def _build_meta(entry: DocumentEntry, request: Request) -> DocumentMeta:
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Citation not found")
    return _build_meta(entry, request)


async def _iter_file(path: str, start: int, end: int) -> AsyncIterator[bytes]:
    """Yield ``[start, end]`` (inclusive) of ``path`` in 256 KiB slices."""
    remaining = end - start + 1
    async with await anyio.open_file(path, mode="rb") as fp:
        if start:
            await fp.seek(start)
        while remaining > 0:
            chunk = await fp.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _parse_range(header_value: str, file_size: int) -> Optional[tuple[int, int]]:
    """Return an inclusive ``(start, end)`` for a `bytes=` Range header,
    or ``None`` if the header is malformed/unsatisfiable."""
    match = _RANGE_RE.match(header_value.strip())
    if not match:
        return None
    raw_start, raw_end = match.group(1), match.group(2)

    if raw_start == "" and raw_end == "":
        return None  # `bytes=-` is invalid

    if raw_start == "":
        # Suffix range: last N bytes.
        suffix = int(raw_end)
        if suffix == 0:
            return None
        start = max(file_size - suffix, 0)
        end = file_size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else file_size - 1

    if start < 0 or start >= file_size or end < start:
        return None
    return start, min(end, file_size - 1)


@router.get("/file")
async def get_document_file(
    citation_id: str = Query(..., min_length=1),
    inline: bool = Query(True, description="Hiển thị inline (PDF viewer) hoặc tải về"),
    range_header: Optional[str] = Header(default=None, alias="range"),
    _user: dict = Depends(get_current_user),
):
    entry = document_catalog.resolve(citation_id)
    path = document_catalog.absolute_path(citation_id)
    if not entry or not path or not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    file_size = os.path.getsize(path)
    safe_name = quote(entry.filename)
    disposition = "inline" if inline else "attachment"

    common_headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{safe_name}",
        "Accept-Ranges": "bytes",
        "X-Citation-Id": entry.citation_id,
        "X-Document-Category": entry.category,
        # Documents are user-scoped (auth via Bearer token), so the
        # browser cache is fine but no shared cache.
        "Cache-Control": "private, max-age=300",
    }

    if range_header:
        parsed = _parse_range(range_header, file_size)
        if parsed is None:
            return Response(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                headers={"Content-Range": f"bytes */{file_size}", **common_headers},
            )
        start, end = parsed
        length = end - start + 1
        return StreamingResponse(
            _iter_file(str(path), start, end),
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type=entry.media_type,
            headers={
                **common_headers,
                "Content-Length": str(length),
                "Content-Range": f"bytes {start}-{end}/{file_size}",
            },
        )

    return StreamingResponse(
        _iter_file(str(path), 0, file_size - 1),
        status_code=status.HTTP_200_OK,
        media_type=entry.media_type,
        headers={
            **common_headers,
            "Content-Length": str(file_size),
        },
    )


@router.post("/reload", status_code=status.HTTP_200_OK)
async def reload_catalog(_user: dict = Depends(get_current_user)):
    document_catalog.reload()
    return {"items_count": len(document_catalog.list_entries())}