"""File operation tools."""

import logging
import os
import shutil
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional

from localclaw.core.models import ErrorType, ExecutionResult, RiskLevel
from localclaw.tools.base import Tool, ToolError, register_tool


logger = logging.getLogger(__name__)


def _humanize_bytes(size_bytes: int) -> str:
    """Convert byte counts into a readable storage string."""

    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024.0 or unit == "PB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{size_bytes} B"


def _extract_office_text(file_path: Path) -> Optional[str]:
    """Extract plain text from .doc or .docx files.

    Tries python-docx for .docx, then Word COM automation for .doc (Windows only).
    Returns None if extraction fails.
    """
    suffix = file_path.suffix.lower()

    # .docx: use python-docx (ZIP-based Open XML)
    if suffix == ".docx":
        try:
            import docx  # python-docx
            doc = docx.Document(str(file_path))
            lines = [para.text for para in doc.paragraphs if para.text.strip()]
            return "\n".join(lines) or ""
        except Exception as exc:
            logger.debug("python-docx failed for %s: %s", file_path, exc)

    # .doc (OLE binary) or .docx fallback: try Word COM on Windows
    try:
        import win32com.client  # pywin32
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        extracted: Optional[str] = None
        try:
            doc = word.Documents.Open(str(file_path.resolve()))
            extracted = doc.Content.Text
            doc.Close(False)
        finally:
            try:
                word.Quit()
            except Exception:
                pass
        if extracted is not None:
            return extracted
    except Exception as exc:
        logger.debug("Word COM failed for %s: %s", file_path, exc)

    return None


def _extract_pdf_text(file_path: Path) -> str:
    """Extract text from a PDF file using pypdf."""

    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is required to read PDF files. Install it with `pip install pypdf`.") from exc

    reader = PdfReader(str(file_path))
    parts: List[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text.strip())
    return "\n".join(parts).strip()




def _resolve_local_path(base_dir: Path, raw_path: str) -> Path:
    """Resolve user paths with Desktop shorthand compatibility on Windows."""

    text = str(raw_path or "").strip()
    if not text:
        return base_dir

    desktop_dir = Path.home() / "Desktop"
    normalized = text.replace("\\", "/")
    normalized_lower = normalized.lower()

    desktop_aliases = {
        "desktop",
        "/desktop",
        "~/desktop",
        "妗岄潰",
        "/妗岄潰",
        "~/妗岄潰",
    }
    if normalized_lower in desktop_aliases:
        return desktop_dir

    if normalized_lower.startswith("/desktop/"):
        suffix = normalized[len("/Desktop/") :]
        return desktop_dir / Path(suffix)
    if normalized_lower.startswith("~/desktop/"):
        suffix = normalized[len("~/Desktop/") :]
        return desktop_dir / Path(suffix)
    if normalized.startswith("/妗岄潰/"):
        suffix = normalized[len("/妗岄潰/") :]
        return desktop_dir / Path(suffix)

    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return base_dir / path


class FileReadTool(Tool):
    """Tool for reading file contents."""
    
    name = "file_read"
    description = "Read contents from a file"
    risk_level = RiskLevel.LOW
    inputs = {"path": "string"}
    outputs = {"content": "string"}
    
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        super().__init__()
        self._base_dir = base_dir or Path.cwd()
    
    async def execute(self, path: str, **kwargs) -> ExecutionResult:
        """Execute file read operation."""
        try:
            file_path = self._resolve_path(path)

            if not file_path.exists():
                return ExecutionResult.from_error(
                    f"File not found: {path}",
                    ErrorType.VALIDATION_ERROR,
                )

            if not file_path.is_file():
                return ExecutionResult.from_error(
                    f"Not a file: {path}",
                    ErrorType.VALIDATION_ERROR,
                )

            suffix = file_path.suffix.lower()

            # Office document extraction (.doc via Word COM, .docx via python-docx)
            if suffix in (".doc", ".docx"):
                content = _extract_office_text(file_path)
                if content is None:
                    return ExecutionResult.from_error(
                        f"Unable to extract text from Office document: {path}",
                        ErrorType.SYSTEM_ERROR,
                    )
            else:
                # Try UTF-8 first, fallback to GBK for Windows Chinese files
                content = None
                for encoding in ["utf-8", "gbk", "gb2312", "latin-1"]:
                    try:
                        with open(file_path, "r", encoding=encoding) as f:
                            content = f.read()
                        break
                    except (UnicodeDecodeError, LookupError):
                        continue

                if content is None:
                    return ExecutionResult.from_error(
                        f"Unable to decode file with supported encodings: {path}",
                        ErrorType.SYSTEM_ERROR,
                    )

            return ExecutionResult.success(
                message=f"Read file: {path}",
                data={"content": content, "path": str(file_path)},
            )

        except PermissionError:
            return ExecutionResult.from_error(
                f"Permission denied: {path}",
                ErrorType.PERMISSION_ERROR,
            )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)
    
    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to base directory."""
        return _resolve_local_path(self._base_dir, path)


class FileWriteTool(Tool):
    """Tool for writing file contents."""
    
    name = "file_write"
    description = "Write contents to a file"
    risk_level = RiskLevel.LOW
    inputs = {"path": "string", "content": "string", "mode": "string"}
    outputs = {"path": "string", "bytes_written": "integer"}
    
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        super().__init__()
        self._base_dir = base_dir or Path.cwd()
    
    async def execute(self, path: str, content: str, mode: str = "write", **kwargs) -> ExecutionResult:
        """Execute file write operation."""
        try:
            file_path = self._resolve_path(path)
            
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            open_mode = "w" if mode == "write" else "a"
            
            with open(file_path, open_mode, encoding="utf-8") as f:
                bytes_written = f.write(content)
            
            return ExecutionResult.success(
                message=f"Wrote file: {path}",
                data={"path": str(file_path), "bytes_written": bytes_written},
            )
            
        except PermissionError:
            return ExecutionResult.from_error(
                f"Permission denied: {path}",
                ErrorType.PERMISSION_ERROR,
            )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)
    
    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to base directory."""
        return _resolve_local_path(self._base_dir, path)


class FileListTool(Tool):
    """Tool for listing directory contents."""
    
    name = "file_list"
    description = "List contents of a directory"
    risk_level = RiskLevel.LOW
    inputs = {"path": "string"}
    outputs = {"files": "list", "directories": "list", "details": "object"}
    
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        super().__init__()
        self._base_dir = base_dir or Path.cwd()
    
    async def execute(self, path: str = ".", all: bool = False, long: bool = False, **kwargs) -> ExecutionResult:
        """Execute directory listing operation."""
        try:
            dir_path = self._resolve_path(path)
            
            if not dir_path.exists():
                return ExecutionResult.from_error(
                    f"Directory not found: {path}",
                    ErrorType.VALIDATION_ERROR,
                )
            
            if not dir_path.is_dir():
                return ExecutionResult.from_error(
                    f"Not a directory: {path}",
                    ErrorType.VALIDATION_ERROR,
                )
            
            files = []
            directories = []
            details = {}
            
            for item in dir_path.iterdir():
                # Skip hidden files unless all is True
                if not all and item.name.startswith("."):
                    continue
                
                if item.is_file():
                    files.append(item.name)
                    if long:
                        details[item.name] = {
                            "type": "file",
                            "size": item.stat().st_size,
                            "mtime": item.stat().st_mtime,
                            "mode": item.stat().st_mode
                        }
                elif item.is_dir():
                    directories.append(item.name)
                    if long:
                        details[item.name] = {
                            "type": "directory",
                            "mtime": item.stat().st_mtime,
                            "mode": item.stat().st_mode
                        }
            
            data = {
                "path": str(dir_path),
                "files": sorted(files),
                "directories": sorted(directories),
            }
            
            if long:
                data["details"] = details
            
            return ExecutionResult.success(
                message=f"Listed directory: {path}",
                data=data,
            )
            
        except PermissionError:
            return ExecutionResult.from_error(
                f"Permission denied: {path}",
                ErrorType.PERMISSION_ERROR,
            )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)
    
    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to base directory."""
        return _resolve_local_path(self._base_dir, path)


class FileDeleteTool(Tool):
    """Tool for deleting files."""
    
    name = "file_delete"
    description = "Delete a file or directory"
    risk_level = RiskLevel.HIGH
    inputs = {"path": "string", "recursive": "boolean"}
    outputs = {"deleted": "boolean"}
    
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        super().__init__()
        self._base_dir = base_dir or Path.cwd()
    
    async def execute(self, path: str, recursive: bool = True, **kwargs) -> ExecutionResult:
        """Execute file delete operation."""
        try:
            file_path = self._resolve_path(path)
            
            if not file_path.exists():
                return ExecutionResult.from_error(
                    f"File not found: {path}",
                    ErrorType.VALIDATION_ERROR,
                )
            
            if file_path.is_file():
                file_path.unlink()
            elif file_path.is_dir():
                if recursive:
                    shutil.rmtree(file_path)
                else:
                    # Try to remove empty directory
                    try:
                        file_path.rmdir()
                    except OSError:
                        return ExecutionResult.from_error(
                            f"Directory not empty: {path}",
                            ErrorType.VALIDATION_ERROR,
                        )
            
            return ExecutionResult.success(
                message=f"Deleted: {path}",
                data={"deleted": True, "path": str(file_path)},
            )
            
        except PermissionError:
            return ExecutionResult.from_error(
                f"Permission denied: {path}",
                ErrorType.PERMISSION_ERROR,
            )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)
    
    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to base directory."""
        return _resolve_local_path(self._base_dir, path)


class PdfExtractTool(Tool):
    """Tool for extracting text from PDF files or directories of PDFs."""

    name = "pdf_extract"
    description = "Extract text from a PDF file or all PDF files in a directory"
    risk_level = RiskLevel.LOW
    inputs = {"path": "string"}
    outputs = {
        "content": "string",
        "output": "string",
        "count": "integer",
        "files": "list",
    }

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        super().__init__()
        self._base_dir = base_dir or Path.cwd()

    async def execute(
        self,
        path: str,
        recursive: bool = False,
        max_chars_per_file: int = 2000,
        **kwargs,
    ) -> ExecutionResult:
        """Extract text from one PDF or all PDFs inside a directory."""

        del kwargs

        try:
            target_path = self._resolve_path(path)

            if not target_path.exists():
                return ExecutionResult.from_error(
                    f"Path not found: {path}",
                    ErrorType.VALIDATION_ERROR,
                )

            if target_path.is_dir():
                globber = target_path.rglob("*.pdf") if recursive else target_path.glob("*.pdf")
                pdf_paths = sorted(globber, key=lambda item: item.name.lower())
                label_prefix = target_path.name or str(target_path)
            else:
                if target_path.suffix.lower() != ".pdf":
                    return ExecutionResult.from_error(
                        f"Not a PDF file: {path}",
                        ErrorType.VALIDATION_ERROR,
                    )
                pdf_paths = [target_path]
                label_prefix = target_path.parent.name or target_path.parent.as_posix()

            files: List[Dict[str, Any]] = []
            sections: List[str] = []
            errors: List[Dict[str, str]] = []

            for pdf_path in pdf_paths:
                label = pdf_path.name if target_path.is_file() else f"{label_prefix}/{pdf_path.name}"
                try:
                    text = _extract_pdf_text(pdf_path)
                    if max_chars_per_file > 0:
                        text = text[:max_chars_per_file]
                    if not text.strip():
                        text = "PDF contains no extractable text."

                    sections.append(f"### {label}\n{text}")
                    files.append(
                        {
                            "path": str(pdf_path),
                            "name": pdf_path.name,
                            "label": label,
                            "directory": pdf_path.parent.name,
                            "characters": len(text),
                        }
                    )
                except Exception as exc:
                    error_text = f"Extraction error: {exc}"
                    sections.append(f"### {label}\n{error_text}")
                    errors.append({"path": str(pdf_path), "error": str(exc)})

            if not pdf_paths:
                message = f"No PDF files found in {target_path}"
                sections.append(f"### {label_prefix}\n[No PDF files found]")
            else:
                message = f"Extracted text from {len(files)} PDF file(s)"

            content = "\n\n".join(sections).strip()
            return ExecutionResult.success(
                message=message,
                data={
                    "path": str(target_path),
                    "content": content,
                    "output": content,
                    "count": len(files),
                    "files": files,
                    "errors": errors,
                },
            )

        except PermissionError:
            return ExecutionResult.from_error(
                f"Permission denied: {path}",
                ErrorType.PERMISSION_ERROR,
            )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)

    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to base directory."""
        return _resolve_local_path(self._base_dir, path)



class FileMkdirTool(Tool):
    """Tool for creating directories."""
    
    name = "file_mkdir"
    description = "Create a directory"
    risk_level = RiskLevel.LOW
    inputs = {"path": "string", "parents": "boolean"}
    outputs = {"path": "string"}
    
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        super().__init__()
        self._base_dir = base_dir or Path.cwd()
    
    async def execute(self, path: str, parents: bool = True, **kwargs) -> ExecutionResult:
        """Execute directory creation operation."""
        try:
            dir_path = self._resolve_path(path)
            
            dir_path.mkdir(parents=parents, exist_ok=True)
            
            return ExecutionResult.success(
                message=f"Created directory: {path}",
                data={"path": str(dir_path)},
            )
            
        except PermissionError:
            return ExecutionResult.from_error(
                f"Permission denied: {path}",
                ErrorType.PERMISSION_ERROR,
            )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)
    
    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to base directory."""
        return _resolve_local_path(self._base_dir, path)


class FileAppendTool(Tool):
    """Tool for appending content to a file."""
    
    name = "file_append"
    description = "Append content to a file"
    risk_level = RiskLevel.LOW
    inputs = {"path": "string", "content": "string"}
    outputs = {"path": "string", "bytes_written": "integer"}
    
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        super().__init__()
        self._base_dir = base_dir or Path.cwd()
    
    async def execute(self, path: str, content: str, **kwargs) -> ExecutionResult:
        """Execute file append operation."""
        try:
            file_path = self._resolve_path(path)
            
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(file_path, "a", encoding="utf-8") as f:
                bytes_written = f.write(content)
            
            return ExecutionResult.success(
                message=f"Appended to file: {path}",
                data={"path": str(file_path), "bytes_written": bytes_written},
            )
            
        except PermissionError:
            return ExecutionResult.from_error(
                f"Permission denied: {path}",
                ErrorType.PERMISSION_ERROR,
            )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)
    
    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to base directory."""
        return _resolve_local_path(self._base_dir, path)


class DiskUsageTool(Tool):
    """Tool for checking disk usage on a drive or filesystem path."""

    name = "disk_usage"
    description = "Check total, used, and free disk space for a path"
    risk_level = RiskLevel.LOW
    inputs = {"path": "string"}
    outputs = {
        "path": "string",
        "total_bytes": "integer",
        "used_bytes": "integer",
        "free_bytes": "integer",
        "free_percent": "float",
    }

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        super().__init__()
        self._base_dir = base_dir or Path.cwd()

    async def execute(self, path: str = ".", **kwargs) -> ExecutionResult:
        """Execute disk usage lookup."""

        del kwargs

        try:
            target_path = self._resolve_path(path)

            if not target_path.exists():
                return ExecutionResult.from_error(
                    f"Path not found: {path}",
                    ErrorType.VALIDATION_ERROR,
                )

            usage = shutil.disk_usage(str(target_path))
            total_bytes = int(usage.total)
            used_bytes = int(usage.used)
            free_bytes = int(usage.free)
            free_percent = round((free_bytes / total_bytes) * 100, 1) if total_bytes else 0.0
            used_percent = round((used_bytes / total_bytes) * 100, 1) if total_bytes else 0.0

            return ExecutionResult.success(
                message=f"Checked disk usage: {path}",
                data={
                    "path": str(target_path),
                    "total_bytes": total_bytes,
                    "used_bytes": used_bytes,
                    "free_bytes": free_bytes,
                    "total": _humanize_bytes(total_bytes),
                    "used": _humanize_bytes(used_bytes),
                    "free": _humanize_bytes(free_bytes),
                    "free_percent": free_percent,
                    "used_percent": used_percent,
                },
            )
        except PermissionError:
            return ExecutionResult.from_error(
                f"Permission denied: {path}",
                ErrorType.PERMISSION_ERROR,
            )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)

    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to base directory."""
        return _resolve_local_path(self._base_dir, path)


def register_file_tools(base_dir: Optional[Path] = None) -> None:
    """Register all file tools."""
    register_tool(FileReadTool(base_dir))
    register_tool(FileWriteTool(base_dir))
    register_tool(FileListTool(base_dir))
    register_tool(PdfExtractTool(base_dir))
    register_tool(FileDeleteTool(base_dir))
    register_tool(FileMkdirTool(base_dir))
    register_tool(FileAppendTool(base_dir))
    register_tool(DiskUsageTool(base_dir))

