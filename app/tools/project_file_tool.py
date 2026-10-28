"""Safe project-scoped file writing for agent-produced artifacts."""
from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath


_SOURCE_EXTENSIONS = r"py|js|ts|tsx|jsx|html|css|json|yaml|yml|toml|sql|md|txt|sh|bat|ps1"
_DELIVERABLE_EXTENSIONS = r"pptx|docx|xlsx|csv|pdf"
_REQUESTED_PATH = re.compile(
    rf"(?i)(?:[a-z]:[\\/])?(?:[^\\/\s<>\"']+[\\/])*[^\\/\s<>\"']+\.(?:{_SOURCE_EXTENSIONS}|{_DELIVERABLE_EXTENSIONS})(?![A-Za-z0-9_.])"
)
_ARTIFACT_BLOCK = re.compile(
    r"<artifact\s+path=[\"']([^\"']+)[\"']\s*>\s*\r?\n?(.*?)</artifact\s*>", re.IGNORECASE | re.DOTALL
)
_CODE_BLOCK = re.compile(r"```[\w.+-]*\s*\r?\n(.*?)```", re.DOTALL)


def requested_filename(request: str) -> str | None:
    filenames = requested_filenames(request)
    return filenames[0] if filenames else None


def requested_filenames(request: str) -> list[str]:
    """Return distinct requested file names in their order of appearance."""
    filenames = []
    seen = set()
    request=re.sub(r'^\[검증 계약\]\s*\n.*?\n\[/검증 계약\]\s*$','',request or '',flags=re.S|re.M)
    for match in _REQUESTED_PATH.finditer(request):
        raw = match.group(0).rstrip(".,;:)")
        filename = PureWindowsPath(raw).name or PurePosixPath(raw).name
        key = filename.casefold()
        if key not in seen:
            seen.add(key)
            filenames.append(filename)
    return filenames


def requested_output_paths(request):
    request=re.sub(r'^\[검증 계약\]\s*\n.*?\n\[/검증 계약\]\s*$','',request or '',flags=re.S|re.M)
    paths=[]
    for match in _REQUESTED_PATH.finditer(request):
        raw=match[0].rstrip('.,;:)').replace('\\','/')
        path=PurePosixPath(raw)
        if PureWindowsPath(raw).drive or path.is_absolute() or raw.casefold().startswith('projects/'):
            raw=path.name
        if raw.casefold() not in {value.casefold() for value in paths}:paths.append(raw)
    return paths


def requested_source_path(request: str, workspace_root: str | Path) -> Path | None:
    """Find an explicitly named existing source file, constrained to the workspace."""
    filename = requested_filename(request or "")
    if not filename:
        return None
    root = Path(workspace_root).resolve()
    candidates = []
    token_match = re.search(r"(?i)([a-z]:[\\/][^\\/\s<>\"']+(?:[\\/][^\\/\s<>\"']+)*)", request or "")
    if token_match:
        token = token_match.group(1)
        # Korean particles often attach directly to a directory or filename.
        token = re.sub(r"(?:에서|으로|파일|에|을|를|이|가|은|는|로)$", "", token)
        token_path = Path(token)
        if token_path.suffix.casefold() in {"." + ext for ext in _SOURCE_EXTENSIONS.split("|")}:
            candidates.append(token_path)
        else:
            candidates.append(token_path / filename)
    relative_match = re.search(r"(?i)((?:projects|project)[\\/][^\\/\s<>\"']+(?:[\\/][^\\/\s<>\"']+)*)", request or "")
    if relative_match:
        token = re.sub(r"(?:에서|으로|파일|에|을|를|이|가|은|는|로)$", "", relative_match.group(1))
        token_path = Path(token)
        if token_path.suffix.casefold() in {"." + ext for ext in _SOURCE_EXTENSIONS.split("|")}:
            candidates.append(root / token_path)
        else:
            candidates.append(root / token_path / filename)
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.is_relative_to(root) and resolved.is_file() and resolved.suffix.casefold() in {"." + ext for ext in _SOURCE_EXTENSIONS.split("|")}:
                return resolved
        except (OSError, RuntimeError):
            continue
    return None


def requested_directory(request: str) -> bool:
    """Whether the representative explicitly included a folder with a filename."""
    match = _REQUESTED_PATH.search(request or "")
    if not match:
        return False
    raw = match.group(0).rstrip(".,;:)")
    if "\\" in raw or PureWindowsPath(raw).drive:
        return PureWindowsPath(raw).parent != PureWindowsPath(".")
    return PurePosixPath(raw).parent != PurePosixPath(".")


def is_file_creation_request(request: str) -> bool:
    text = (request or "").casefold()
    has_action = any(word in text for word in ("작성", "생성", "만들", "저장", "구현", "수정", "고쳐", "create", "write", "save", "build", "implement", "edit"))
    if not has_action:
        return False
    if requested_filename(request):
        return True
    return any(word in text for word in ("코드", "프로그램", "스크립트", "소스 파일", "파일", "code", "program", "script", "source file"))


def extract_agent_artifacts(output: str, fallback_filename: str | None = None) -> list[dict]:
    """Read explicit artifact blocks; for a single requested file, accept one fenced code block."""
    text = output or ""
    artifacts = []
    for match in _ARTIFACT_BLOCK.finditer(text):
        model_path, content = match.groups()
        path = model_path.strip()
        # Map model-provided absolute destinations to a filename inside the project sandbox.
        if PureWindowsPath(path).is_absolute() or PurePosixPath(path).is_absolute():
            path = PureWindowsPath(path).name or PurePosixPath(path).name
        content = content.strip("\r\n")
        fenced = re.fullmatch(r"\s*```[\w.+-]*\s*\r?\n(.*?)\r?\n```\s*", content, re.DOTALL)
        if fenced:
            content = fenced.group(1)
        artifacts.append({"path": path, "content": content})
    if artifacts or not fallback_filename:
        return artifacts
    blocks = _CODE_BLOCK.findall(text)
    if blocks:
        artifacts.append({"path": fallback_filename, "content": blocks[0].strip("\r\n")})
    return artifacts


class ProjectFileTool:
    """Writes generated files to the owning project's artifact folder."""

    MAX_FILE_BYTES = 2 * 1024 * 1024
    PROJECT_ID = re.compile(r"^PRJ-[A-Za-z0-9-]{4,64}$")

    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root).resolve()

    def read_existing_file(self, path: str | Path) -> dict:
        """Read a small source file only when it is inside this workspace."""
        target = Path(path).resolve(strict=True)
        if not target.is_relative_to(self.workspace_root) or not target.is_file():
            raise ValueError("작업공간 안의 파일만 읽을 수 있습니다.")
        if target.suffix.casefold() not in {"." + ext for ext in _SOURCE_EXTENSIONS.split("|")}:
            raise ValueError("지원하는 소스 파일 형식이 아닙니다.")
        if target.stat().st_size > self.MAX_FILE_BYTES:
            raise ValueError("파일은 2MB 이하만 읽을 수 있습니다.")
        return {"path": str(target), "content": target.read_text(encoding="utf-8-sig")}

    def replace_existing_file(self, path: str | Path, content: str, project_id: str) -> dict:
        """Atomically replace a requested existing workspace source file."""
        if not isinstance(project_id, str) or not self.PROJECT_ID.fullmatch(project_id):
            raise ValueError("유효하지 않은 project_id입니다.")
        if not isinstance(content, str) or "\x00" in content:
            raise ValueError("파일 내용은 유효한 문자열이어야 합니다.")
        target = Path(path).resolve(strict=True)
        if not target.is_relative_to(self.workspace_root) or not target.is_file():
            raise ValueError("작업공간 안의 기존 파일만 수정할 수 있습니다.")
        if target.suffix.casefold() not in {"." + ext for ext in _SOURCE_EXTENSIONS.split("|")}:
            raise ValueError("지원하는 소스 파일 형식이 아닙니다.")
        payload = content.encode("utf-8")
        if len(payload) > self.MAX_FILE_BYTES:
            raise ValueError("파일은 2MB 이하만 저장할 수 있습니다.")
        temp_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_bytes(payload)
            os.replace(temp_path, target)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        return {"status": "WRITTEN", "project_id": project_id, "relative_path": target.relative_to(self.workspace_root).as_posix(), "path": str(target), "bytes": len(payload)}

    def delete_project_directory(self, project_id: str) -> dict:
        """Remove one project's on-disk folder without following paths outside projects/."""
        if not isinstance(project_id, str) or not self.PROJECT_ID.fullmatch(project_id):
            raise ValueError("유효하지 않은 project_id입니다.")

        projects_root = (self.workspace_root / "projects").resolve()
        if projects_root.parent != self.workspace_root:
            raise ValueError("프로젝트 저장 폴더가 작업공간 내부에 있지 않습니다.")

        target = projects_root / project_id
        if target.is_symlink():
            raise ValueError("프로젝트 폴더가 심볼릭 링크이므로 삭제할 수 없습니다.")
        if not target.exists():
            return {"status": "NOT_FOUND", "project_id": project_id}

        resolved_target = target.resolve(strict=True)
        if resolved_target.parent != projects_root or not resolved_target.is_dir():
            raise ValueError("프로젝트 폴더가 허용된 저장 위치와 일치하지 않습니다.")

        shutil.rmtree(resolved_target)
        return {"status": "DELETED", "project_id": project_id, "path": str(resolved_target)}

    def write_file(self, project_id: str, path: str, content: str) -> dict:
        if not isinstance(project_id, str) or not self.PROJECT_ID.fullmatch(project_id):
            raise ValueError("유효하지 않은 project_id입니다.")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("저장할 상대 파일 경로가 비어 있습니다.")
        if not isinstance(content, str):
            raise ValueError("파일 내용은 문자열이어야 합니다.")
        if "\x00" in path or "\x00" in content:
            raise ValueError("파일 경로 또는 내용에 허용되지 않는 문자가 있습니다.")

        normalized = path.strip().replace("\\", "/")
        windows_path = PureWindowsPath(normalized)
        posix_path = PurePosixPath(normalized)
        if windows_path.is_absolute() or posix_path.is_absolute() or windows_path.drive:
            raise ValueError("절대 경로는 저장할 수 없습니다. 파일명 또는 프로젝트 내 상대 경로를 사용하세요.")
        if any(part in {"", ".", ".."} for part in posix_path.parts):
            raise ValueError("프로젝트 폴더 밖으로 나가는 상대 경로는 저장할 수 없습니다.")
        if posix_path.name.casefold() in {".env", "secrets.json", "credentials.json"}:
            raise ValueError("비밀정보 파일명은 생성할 수 없습니다.")

        payload = content.encode("utf-8")
        if len(payload) > self.MAX_FILE_BYTES:
            raise ValueError(f"파일은 {self.MAX_FILE_BYTES // (1024 * 1024)}MB 이하만 저장할 수 있습니다.")

        artifact_root = (self.workspace_root / "projects" / project_id / "artifacts").resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        target = artifact_root.joinpath(*posix_path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved_target = target.resolve()
        if not resolved_target.is_relative_to(artifact_root):
            raise ValueError("허용된 파일 저장 폴더 밖에는 저장할 수 없습니다.")

        temp_path = resolved_target.with_name(f".{resolved_target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_bytes(payload)
            os.replace(temp_path, resolved_target)
        finally:
            if temp_path.exists():
                temp_path.unlink()

        return {
            "status": "WRITTEN",
            "project_id": project_id,
            "relative_path": posix_path.as_posix(),
            "path": str(resolved_target),
            "bytes": len(payload),
        }
