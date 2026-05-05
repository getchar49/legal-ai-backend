from pydantic import BaseModel


class DocumentMeta(BaseModel):
    citation_id: str
    title: str | None = None
    category: str
    filename: str
    media_type: str
    available: bool
    download_url: str


class DocumentListResponse(BaseModel):
    items: list[DocumentMeta]


class Citation(BaseModel):
    """Trích dẫn được resolve sẵn cho frontend.

    `available=True` nghĩa là backend có file local tương ứng (download_url
    có thể dùng được). Nếu False, FE chỉ hiển thị title, không cho click mở.
    """

    id: str
    title: str | None = None
    source_url: str | None = None
    available: bool
    category: str | None = None
    filename: str | None = None
    media_type: str | None = None
    download_url: str | None = None
