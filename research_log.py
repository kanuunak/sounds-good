"""연구용 원시 데이터 적재 — project one accept 대비 (혹시나를 위한 보험).

왜 필요한가: work/{hash}/ 는 캐시라 재실행·--force 로 덮어써진다. 논문 회의록이 요구했던
실험(널 테스트, recast 빈도, 오염률 재측정)은 "그때 그 실행"의 전사·프롬프트·LLM 원응답이
남아있어야 가능한데, 지금 구조로는 최신 1부만 남는다. 여기는 실행 1회 = 폴더 1개로 쌓는다
(append-only, 덮어쓰기 없음).

윤리 규칙:
- 기존 녹음은 연구 동의가 확인되지 않았다 → 모든 기록은 consent="development_only" 태깅.
  동의받은 새 녹음 상대가 생기면 그때 태그를 확장한다.
- 비밀번호·API 키·토큰은 기록하지 않는다 — config 파일을 통째로 복사하지 않고
  비밀이 아닌 값만 골라 적는다.
- 오디오 원본은 복사하지 않는다(용량 + 개인 음성). audio_hash로 sounds/ 원본과 연결된다.
- data/ 는 .gitignore 대상 — 이 데이터는 이 PC 밖으로 나가지 않는다.

끄기: SG_RESEARCH_LOG=0. 실패는 항상 무시된다(카드 생성을 절대 막지 않는다).
외부 의존: 없음 (stdlib only).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
from pathlib import Path

from util import PIPELINE_VERSION, stage_output

# work/{hash}/ 에서 스냅샷할 파일들 (있는 것만 복사).
_ARTIFACTS = (
    "02_transcript.json",
    "03_speakers.json",
    "04_prompt.txt",
    "04_raw_response.txt",
    "04_validation_raw.txt",  # S10 Stage 4: 검증/수리 원응답
    "04_analysis.json",
)

# config에서 기록해도 안전한 키만 명시적으로 고른다 (allowlist — 비밀 유출 원천 차단).
_SAFE_CFG_KEYS = ("llm_provider", "llm_model", "whisper_model", "whisper_batch_size",
                  "max_cards_per_lesson", "max_cards_per_issue_type", "llm_fallbacks")


def _enabled(cfg: dict) -> bool:
    """기본은 꺼짐(2026-11-17, 공개 공유 대비) — config 에 `research_log: true` 가 있을 때만 쌓는다.
    환경변수 SG_RESEARCH_LOG 가 있으면 그것이 우선한다(0/false/no = 끔, 그 외 = 켬)."""
    env = os.getenv("SG_RESEARCH_LOG")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no")
    return bool(cfg.get("research_log", False))


def record_run(cfg: dict, audio_hash: str, event: str = "run",
               extra: dict | None = None) -> Path | None:
    """실행 산출물을 data/research/{UTC시각}-{hash}/ 로 스냅샷. 반환: 폴더 경로(꺼짐/실패=None)."""
    if not _enabled(cfg):
        return None
    try:
        work = Path(cfg["work_dir"])
        root = Path(cfg.get("research_dir", "data/research"))
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = root / f"{stamp}-{audio_hash}"
        n = 2
        while run_dir.exists():  # 같은 초에 두 번 실행돼도 덮어쓰지 않는다
            run_dir = root / f"{stamp}-{audio_hash}-{n}"
            n += 1
        run_dir.mkdir(parents=True)

        copied = []
        for name in _ARTIFACTS:
            src = stage_output(work, audio_hash, name)
            if src.exists():
                shutil.copy2(src, run_dir / name)
                copied.append(name)

        # 자주 쓰는 판별값은 메타로 승격해 폴더를 열지 않고도 필터링할 수 있게 한다.
        language = ""
        diarization_status = ""
        tp = run_dir / "02_transcript.json"
        if tp.exists():
            try:
                tr = json.loads(tp.read_text(encoding="utf-8"))
                language = tr.get("language", "")
                diarization_status = tr.get("diarization_status", "")
            except (json.JSONDecodeError, OSError):
                pass

        meta = {
            "recorded_at_utc": stamp,
            "event": event,
            "audio_hash": audio_hash,
            "pipeline_version": PIPELINE_VERSION,
            "language": language,
            "diarization_status": diarization_status,
            "consent": "development_only",  # 연구 동의 미확보 — _HANDOFF.md 규칙
            "artifacts": copied,
            "config": {k: cfg[k] for k in _SAFE_CFG_KEYS if k in cfg},
        }
        if extra:
            meta["extra"] = extra
        (run_dir / "run_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[research_log] 원시 데이터 적재: {run_dir} ({len(copied)}개 파일)")
        return run_dir
    except Exception as e:  # 연구 적재 실패가 파이프라인을 망치면 안 된다
        print(f"[research_log] 적재 실패(무시하고 계속): {e}")
        return None
