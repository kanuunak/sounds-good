"""S8: 오케스트레이션. python main.py <녹음.m4a> [--config config.yaml] [--force <stage>].

멱등성의 두 축 덕분에 어느 지점에서 죽어도 단순 재실행으로 이어서 완료된다:
  - db.ingest_analysis는 수업 단위 교체(그 audio_hash의 lesson_sentences를 DELETE 후 재INSERT).
  - 카드 carded 마킹은 apkg 생성 성공을 확인한 뒤에만 수행(export 실패 시 카드 유실 방지, D8).
각 stage는 자기 출력 파일이 있으면 스킵(캐싱)하므로 재실행 비용이 작다.

외부 의존: 없음 (argparse 표준 라이브러리).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import obsidian_sync
import research_log
from db import DB
from stages import analyze, convert, export_anki, export_obsidian, speakers, transcribe
from util import file_hash, load_config

# --force 대상 → 삭제할 캐시 파일 (아키텍처 연쇄 무효화 표와 동일)
_FORCE_CHAIN = {
    "convert": ["01_audio.wav", "02_transcript.json", "03_speakers.json", "04_analysis.json"],
    "transcribe": ["02_transcript.json", "03_speakers.json", "04_analysis.json"],
    "speakers": ["03_speakers.json", "04_analysis.json"],
    "analyze": ["04_analysis.json"],
}


def _timed(name: str, fn, *args, **kwargs):
    print(f"[{name}] 시작")
    t = time.perf_counter()
    result = fn(*args, **kwargs)
    print(f"[{name}] 완료 ({time.perf_counter() - t:.1f}s)")
    return result


def _invalidate(h: str, force: str | None, cfg: dict) -> None:
    if force is None:
        return
    if force not in _FORCE_CHAIN:
        print(f"[중단] 알 수 없는 --force 대상: '{force}'", file=sys.stderr)
        print(f"       유효한 값: {', '.join(_FORCE_CHAIN)}", file=sys.stderr)
        sys.exit(1)
    work = Path(cfg["work_dir"]) / h
    for fname in _FORCE_CHAIN[force]:
        p = work / fname
        if p.exists():
            p.unlink()
            print(f"[force:{force}] 삭제: {p}")


def run(audio_path: Path, cfg: dict, force: str | None, language: str = "en") -> None:
    h = file_hash(audio_path)
    _invalidate(h, force, cfg)
    db = DB(Path(cfg["db_path"]))

    wav = _timed("convert", convert.run, audio_path, cfg)
    transcript_p = _timed("transcribe", transcribe.run, wav, h, cfg, audio_path.name, language)
    transcript = json.loads(Path(transcript_p).read_text(encoding="utf-8"))

    if transcript["diarization_status"] == "failed":
        # D9: 화자분리 전체 실패 시 LLM에게 화자를 추측시키지 않고 중단한다(오염 > 누락).
        sys.exit(f"[중단] 화자분리 전체 실패. work/{h}/02_transcript.json 확인 후 수동 조치 필요")

    speakers_p = _timed("speakers", speakers.run, transcript_p, h, cfg)
    # S12: 약점 패턴 피드백은 언어별 격리(D-zh3) — 이 회차와 같은 언어의 통계만 넣는다.
    analysis_p = _timed("analyze", analyze.run, transcript_p, speakers_p, h, cfg,
                        db.top_weak_patterns(language=transcript.get("language", "en")))
    analysis = json.loads(Path(analysis_p).read_text(encoding="utf-8"))

    stats = _timed("db", db.ingest_analysis, h, analysis, audio_path.name)
    cards = db.select_unexported_cards(h, cfg["max_cards_per_lesson"], cfg["max_cards_per_issue_type"])
    apkg = _timed("export_anki", export_anki.run, cards, h, analysis["lesson_date"], cfg,
                  analysis.get("language", "en"))
    if apkg is not None:
        # ★ 반드시 apkg 생성 성공을 확인한 뒤에만 마킹한다(D8). 선마킹 시 export 실패하면 카드 영구 유실.
        db.mark_cards_exported([c["dup_key"] for c in cards])
    # Obsidian 은 선택 사항(2026-11-17): 해당 언어의 vault 경로가 config 에 없으면 건너뛴다.
    vault_used = cfg.get("vault_path_zh" if analysis.get("language") == "zh" else "vault_path")
    if vault_used:
        _timed("export_obsidian", export_obsidian.run, db, analysis, h, cfg)
        # vault에 쓴 뒤 Remotely Save 동기화를 트리거해 폰에서도 받게 한다(실패해도 무시).
        obsidian_sync.trigger(vault_used)
    # 연구용 원시 데이터 적재(config research_log: true 일 때만. 실패해도 무시).
    research_log.record_run(cfg, h, event="cli_run",
                            extra={"cards_exported": len(cards) if apkg is not None else 0,
                                   "ingest_stats": stats})

    stt = [it for it in analysis.get("items", []) if it.get("stt_suspect")]
    print("\n===== 요약 =====")
    print(f"새 문장 {stats['new']} / 재등장 {stats['repeated']}")
    if apkg is not None:
        print(f"카드 {len(cards)}장 → {apkg}")
    else:
        print("새 카드 없음")
    if vault_used:
        print(f"Obsidian 갱신 완료 → {vault_used}")
        print(f"STT 오인식 의심 {len(stt)}개 | archive: _archive/{analysis['lesson_date']}-{h}-detail.md")
    else:
        print(f"STT 오인식 의심 {len(stt)}개 (Obsidian 미설정 — 건너뜀)")


def main() -> None:
    parser = argparse.ArgumentParser(description="전화영어 복습 파이프라인 (m4a → Anki + Obsidian)")
    parser.add_argument("audio", help="녹음 파일 경로 (.m4a)")
    parser.add_argument("--config", default="config.yaml", help="config.yaml 경로")
    parser.add_argument("--force", default=None,
                        help=f"재실행할 stage와 그 이후 캐시 무효화: {', '.join(_FORCE_CHAIN)}")
    parser.add_argument("--lang", default="en", choices=["en", "zh"],
                        help="수업 언어 (기본 en)")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    audio_path = Path(args.audio)
    if not audio_path.exists():
        sys.exit(f"[중단] 입력 파일이 없습니다: {audio_path}")

    h = file_hash(audio_path)
    try:
        run(audio_path, cfg, args.force, args.lang)
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[실패] {e}", file=sys.stderr)
        print(f"       중간 결과 확인: {Path(cfg['work_dir']) / h}/", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
