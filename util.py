"""공통 유틸리티 — 해시, config 로딩(lazy env 해석), 정규화, 캐시 경로.

00_ARCHITECTURE.md v1.2 계약을 따른다. 외부 의존: PyYAML.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import yaml

PIPELINE_VERSION = "1.7"  # S10 Stage 4: 카드 배치 검증/수리 추가


def file_hash(path: Path) -> str:
    """파일 내용의 SHA256 앞 12자리(hex)."""
    path = Path(path)
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def load_config(path: Path = Path("config.yaml")) -> dict:
    """config.yaml을 파싱해 dict로 반환.

    env:XXX 값은 여기서 해석하지 않는다(lazy). 실제 사용 시점에 resolve()가 해석한다.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config 파일을 찾을 수 없습니다: {path}")
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"config 파싱 결과가 dict가 아닙니다: {path}")
    return cfg


def resolve(cfg: dict, key: str) -> str:
    """cfg[key]를 사용 시점에 해석한다.

    값이 'env:XXX' 형식이면 환경변수 XXX를 읽고, 미설정이면 어떤 변수가 필요한지
    명확한 에러로 실패한다(lazy 검증 — 미사용 provider 키 때문에 실패하지 않도록).
    """
    if key not in cfg:
        raise KeyError(f"config에 '{key}' 항목이 없습니다.")
    value = cfg[key]
    if isinstance(value, str) and value.startswith("env:"):
        env_name = value[len("env:"):]
        env_value = os.environ.get(env_name)
        if not env_value:
            raise RuntimeError(
                f"환경변수 '{env_name}'가 설정되지 않았습니다 "
                f"(config의 '{key}: {value}'가 이 변수를 요구함). "
                f"'export {env_name}=...' 후 다시 실행하세요."
            )
        return env_value
    return value


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+", re.UNICODE)


def normalize(s: str) -> str:
    """소문자화 → 구두점 제거 → 연속 공백을 단일 공백으로 → strip."""
    s = s.lower()
    s = _PUNCT_RE.sub("", s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


def dup_key(natural_sentence: str) -> str:
    """normalize(natural_sentence)의 sha256 앞 16자리."""
    normalized = normalize(natural_sentence)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def stage_output(work_dir: Path, audio_hash: str, filename: str) -> Path:
    """중간 결과 파일 경로: {work_dir}/{audio_hash}/{filename}."""
    return Path(work_dir) / audio_hash / filename
