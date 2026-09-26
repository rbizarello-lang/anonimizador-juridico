"""Versao do que esta rodando, para conferir na janela e em cada lote qual implementacao gerou o texto.

- ``APP_VERSION``: versao do app (a mesma do ``pyproject.toml``).
- extrator: ``FAST_EXTRACTOR_VERSION`` (muda quando a leitura do PDF muda; invalida o cache).
- ``DETECTION_VERSION``: cadeia de deteccao (IDs, lista de alvos, capa, GLiNER, fidelidade; 2.1 = + propagacao do lote).
- codigo: impressao digital dos ``.py`` carregados (muda com qualquer edicao, mesmo sem commit).
- commit: o commit do git em que a pasta esta, lido direto de ``.git`` (sem chamar programa externo).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .fast_extract import FAST_EXTRACTOR_VERSION

APP_VERSION = "0.4.0"
DETECTION_VERSION = "2.1"

_SRC = Path(__file__).resolve().parents[1]
_ROOT = _SRC.parent


@dataclass(frozen=True, slots=True)
class VersionInfo:
    app: str
    extractor: str
    detection: str
    code: str
    commit: str = ""
    branch: str = ""

    def short(self) -> str:
        return f"v{self.app} · código {self.code}"

    def line(self) -> str:
        parts = [
            f"v{self.app}",
            f"extração {self.extractor}",
            f"detecção {self.detection}",
            f"código {self.code}",
        ]
        if self.commit:
            parts.append(f"commit {self.commit}" + (f" ({self.branch})" if self.branch else ""))
        return " · ".join(parts)


@lru_cache(maxsize=1)
def version_info() -> VersionInfo:
    commit, branch = git_head()
    return VersionInfo(
        app=APP_VERSION,
        extractor=FAST_EXTRACTOR_VERSION,
        detection=DETECTION_VERSION,
        code=code_fingerprint(),
        commit=commit,
        branch=branch,
    )


def code_fingerprint(root: Path = _SRC) -> str:
    """SHA-256 dos .py do app (quebra de linha normalizada: CRLF e LF dao o mesmo valor)."""

    hasher = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        hasher.update(path.relative_to(root).as_posix().encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes().replace(b"\r\n", b"\n"))
        hasher.update(b"\0")
    return hasher.hexdigest()[:7]


def git_head(root: Path = _ROOT) -> tuple[str, str]:
    """(commit curto, branch) da pasta; vazio se nao for um checkout git."""

    try:
        git_dir = root / ".git"
        if git_dir.is_file():
            pointer = git_dir.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir:"):
                return "", ""
            git_dir = (root / pointer[len("gitdir:"):].strip()).resolve()
        common = git_dir
        commondir = git_dir / "commondir"
        if commondir.is_file():
            common = (git_dir / commondir.read_text(encoding="utf-8").strip()).resolve()
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:7], ""
        ref = head[len("ref:"):].strip()
        branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        for base in (git_dir, common):
            ref_file = base / ref
            if ref_file.is_file():
                return ref_file.read_text(encoding="utf-8").strip()[:7], branch
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0][:7], branch
        return "", branch
    except OSError:
        return "", ""
