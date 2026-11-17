# S8: main.py 오케스트레이션 구현 (v1.1)

첨부된 00_ARCHITECTURE.md의 계약을 따르는 `main.py`를 작성하라.
S1~S7 모듈은 이미 구현되어 있다고 가정한다.

## 요구사항

```
사용법: python main.py <녹음파일.m4a> [--config config.yaml] [--force <stage명>]
```

흐름:

```python
def run(audio_path: Path, cfg: dict, force: str | None) -> None:
    h = file_hash(audio_path)
    invalidate(h, force, cfg)          # 아래 연쇄 무효화 표 참조
    db = DB(cfg["db_path"])
    wav        = convert.run(audio_path, cfg)
    transcript_p = transcribe.run(wav, h, cfg, source_filename=audio_path.name)
    transcript = json.loads(transcript_p.read_text())
    if transcript["diarization_status"] == "failed":
        # 아키텍처 문서의 화자분리 실패 정책: 중단
        sys.exit(f"[중단] 화자분리 전체 실패. work/{h}/02_transcript.json 확인 후 수동 조치 필요")
    speakers_p = speakers_stage.run(transcript_p, h, cfg)
    analysis_p = analyze.run(transcript_p, speakers_p, h, cfg, db.top_weak_patterns())
    analysis   = json.loads(analysis_p.read_text())
    stats      = db.ingest_analysis(h, analysis, audio_path.name)
    cards      = db.select_unexported_cards(h, cfg["max_cards_per_lesson"], cfg["max_cards_per_issue_type"])
    apkg       = export_anki.run(cards, h, analysis["lesson_date"], cfg)   # Path | None
    if apkg is not None:
        db.mark_cards_exported([c["dup_key"] for c in cards])   # ★ 반드시 생성 성공 후
    export_obsidian.run(db, analysis, h, cfg)
```

## --force 연쇄 무효화 (아키텍처 문서 표와 동일)

- convert → work/{h}/01,02,03,04 삭제
- transcribe → 02,03,04 삭제
- speakers → 03,04 삭제
- analyze → 04 삭제

잘못된 stage명이면 유효한 이름 목록을 보여주고 종료.

## 출력

- 각 stage 전후로 `[stage명] 시작/완료 (N.Ns)` 로그
- 마지막 요약: 새 문장/재등장 수, 카드 수와 apkg 경로(없으면 "새 카드 없음"),
  Obsidian 갱신 완료, stt_suspect 항목 수와 archive 경로
- 예외 시: 죽은 stage명 + work/{h}/ 경로 안내, 종료코드 1

## 주의

- ingest는 수업 단위 교체(멱등), 카드 마킹은 export 성공 후 — 이 두 가지 덕에
  어느 지점에서 죽어도 단순 재실행으로 이어서 완료된다. 이 가정을 주석으로 명시할 것.
- argparse 사용, 외부 의존 없음

## 완료 기준

- [ ] m4a 하나로 end-to-end 성공 (S3 화자 질문 1회만 개입)
- [ ] 즉시 재실행: API 호출 없이 수 초 내 완료, 카드 0장, DB 수치 불변
- [ ] --force transcribe 후: 02~04 삭제되고 S2부터 재실행, 화자 질문 다시 나옴
- [ ] export_anki에서 강제 예외 발생시킨 뒤 재실행 → 같은 카드가 다시 선택되어 apkg 생성됨 (유실 없음)
