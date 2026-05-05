"""Document catalog: chỉ mục các tài liệu pháp luật phục vụ trích dẫn.

Mỗi thư mục con của ``docs/`` chứa một file ``mapping.json`` dạng::

    {
        "<citation_id>": "<filename>",
        ...
    }

``citation_id`` là id mà external chat API trả về trong ``annotations``.
Catalog chuẩn hóa id (lowercase + strip), dò ngược ra đường dẫn file thực
để route ``/api/documents`` có thể stream tài liệu cho frontend.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

# docs/ nằm cùng cấp với app/, project root = app/../
DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"

# Map đuôi file -> media type. Mở rộng được khi sau này có thêm định dạng.
ALLOWED_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
}

DEFAULT_MIME = "application/octet-stream"


@dataclass(frozen=True)
class DocumentEntry:
    citation_id: str
    category: str
    filename: str
    relative_path: str
    media_type: str
    title: str | None = None
    available: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


class DocumentCatalog:
    """Index citation_id -> DocumentEntry, build từ các file mapping.json."""

    def __init__(self, docs_root: Path = DOCS_ROOT) -> None:
        self.docs_root = docs_root
        #print(self.docs_root)
        self._entries: dict[str, DocumentEntry] = {}
        self._lock = threading.RLock()
        self._load()

    @staticmethod
    def normalize_id(citation_id: str | None) -> str:
        return (citation_id or "").strip().lower()

    def _iter_mapping_files(self) -> Iterable[Path]:
        if not self.docs_root.exists():
            logger.warning("Docs root does not exist: %s", self.docs_root)
            return []
        #print(self.docs_root.glob("**/mapping.json"))
        return sorted(self.docs_root.glob("**/mapping.json"))

    def _load(self) -> None:
        with self._lock:
            self._entries = {}
            for mapping_path in self._iter_mapping_files():
                #print(mapping_path)
                category = mapping_path.parent.name
                try:
                    raw = json.loads(mapping_path.read_text(encoding="utf-8"))
                    #print(raw)
                except Exception:
                    logger.exception("Failed to load mapping at %s", mapping_path)
                    continue
                if not isinstance(raw, dict):
                    logger.warning("mapping.json must be a dict: %s", mapping_path)
                    continue

                for raw_id, filename in raw.items():
                    if not isinstance(raw_id, str) or not isinstance(filename, str):
                        continue
                    norm = self.normalize_id(raw_id)
                    if not norm or not filename.strip():
                        continue

                    file_path = mapping_path.parent / filename
                    try:
                        relative = file_path.relative_to(self.docs_root).as_posix()
                    except ValueError:
                        logger.warning(
                            "File %s is outside docs root, skipped", file_path
                        )
                        continue

                    media_type = ALLOWED_MIME.get(file_path.suffix.lower(), DEFAULT_MIME)
                    exists = file_path.exists()
                    if not exists:
                        logger.warning(
                            "Mapping references missing file: %s (citation_id=%s)",
                            file_path,
                            norm,
                        )

                    if norm in self._entries:
                        # Cảnh báo trùng lặp giữa các category, giữ entry mới nhất.
                        logger.warning(
                            "Duplicate citation_id %s, overriding %s with %s",
                            norm,
                            self._entries[norm].relative_path,
                            relative,
                        )

                    self._entries[norm] = DocumentEntry(
                        citation_id=norm,
                        category=category,
                        filename=filename,
                        relative_path=relative,
                        media_type=media_type,
                        title=Path(filename).stem,
                        available=exists,
                    )

            logger.info(
                "DocumentCatalog loaded %d entries from %s",
                len(self._entries),
                self.docs_root,
            )

    def reload(self) -> None:
        """Quét lại catalog. Hữu ích khi thêm mapping.json mới mà không restart."""
        self._load()

    def resolve(self, citation_id: str | None) -> DocumentEntry | None:
        return self._entries.get(self.normalize_id(citation_id))

    def absolute_path(self, citation_id: str | None) -> Path | None:
        entry = self.resolve(citation_id)
        if not entry:
            return None
        path = (self.docs_root / entry.relative_path).resolve()
        # Hardening: bảo đảm path nằm trong docs_root.
        try:
            path.relative_to(self.docs_root.resolve())
        except ValueError:
            logger.error("Resolved path escapes docs_root: %s", path)
            return None
        return path

    def list_entries(self) -> list[DocumentEntry]:
        return list(self._entries.values())


document_catalog = DocumentCatalog()
