"""과거 회차의 .apkg를 현재 템플릿으로 다시 만든다 (유지보수용).

왜 필요한가: `output/`의 .apkg는 만들어진 시점의 카드 템플릿을 그대로 담고 있다.
S10 Stage 3처럼 뒷면 템플릿을 고치면 예전 파일들은 옛 모습 그대로 남고, 나중에 그 파일을
다시 임포트하면 노트타입이 예전으로 되돌아간다. 이 스크립트로 전부 최신 템플릿으로 맞춘다.

  python reexport_apkg.py              # 모든 회차 (회차별 파일)
  python reexport_apkg.py <audio_hash> # 특정 회차만
  python reexport_apkg.py --combined   # 위에 더해 전 회차 통합 1개 파일도 생성
                                       #   → output/_all_lessons.apkg (임포트 한 번으로 끝)

내보낼 카드 = 그 회차에 등장하면서 이미 Anki로 나간(carded=1) 문장.
이력 표의 '카드 수'와 같은 기준이라 화면에 보이는 숫자와 파일 내용이 일치한다.
한 문장이 여러 회차에 등장하면 양쪽 파일에 모두 들어가지만, 노트 GUID가 dup_key라
Anki에서는 같은 노트 하나로 합쳐진다(L6) — 중복 카드가 생기지 않는다.

DB는 건드리지 않는다(D8). carded 상태도 그대로 둔다.
외부 의존: 없음 (기존 db/util/stages만 사용).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from db import DB
from stages import export_anki
from util import load_config


def _cards_of(db: DB, audio_hash: str) -> list[dict]:
    rows = db.conn.execute(
        """SELECT s.dup_key, s.natural_sentence, s.korean_meaning, s.pinyin,
                  s.explanation_ko, s.practice_pattern, s.pattern_examples, s.priority,
                  ls.issue_type, ls.card_type, ls.original_sentence, ls.timestamp
           FROM lesson_sentences ls JOIN sentences s ON s.dup_key = ls.dup_key
           WHERE ls.audio_hash = ? AND s.carded = 1
           ORDER BY ls.timestamp""",
        (audio_hash,),
    ).fetchall()
    return [
        dict(r) | {"pattern_examples": json.loads(r["pattern_examples"] or "[]")}
        for r in rows
    ]


def _write_combined(cfg: dict, per_lesson: list[tuple[str, str, list[dict]]]) -> Path:
    """전 회차를 덱별로 담은 통합 .apkg. 회차별 파일을 12번 임포트하지 않아도 되게.

    같은 문장이 여러 회차에 나오면 노트 GUID가 같아 한 장으로 합친다(L6).
    덱/노트타입은 회차별 파일과 동일한 고정 상수를 쓰므로 결과물도 같다.
    """
    import genanki  # 이 스크립트에서만 필요

    decks = {
        "en": genanki.Deck(export_anki._DECK_ID, cfg["anki_deck_name"]),
        "zh": genanki.Deck(export_anki._DECK_ID_ZH,
                           cfg.get("anki_deck_name_zh", "chinese_speaking")),
    }
    seen: set[str] = set()
    counts = {"en": 0, "zh": 0}
    for lesson_date, lang, cards in per_lesson:
        for c in cards:
            if c["dup_key"] in seen:
                continue
            seen.add(c["dup_key"])
            decks[lang].add_note(export_anki.build_note(c, lesson_date, lang))
            counts[lang] += 1

    out = Path(cfg["output_dir"]) / "_all_lessons.apkg"
    genanki.Package([d for d in decks.values() if d.notes]).write_to_file(str(out))
    print(f"\n통합 파일: {out}  (영어 {counts['en']}장 + 중국어 {counts['zh']}장)")
    return out


def main() -> None:
    cfg = load_config()
    args = [a for a in sys.argv[1:]]
    combined = "--combined" in args
    args = [a for a in args if not a.startswith("--")]
    only = args[0] if args else None

    db = DB(Path(cfg["db_path"]))
    try:
        q = "SELECT audio_hash, lesson_date, language, source_filename FROM lessons"
        params: tuple = ()
        if only:
            q += " WHERE audio_hash = ?"
            params = (only,)
        q += " ORDER BY lesson_date"
        lessons = db.conn.execute(q, params).fetchall()

        if not lessons:
            sys.exit(f"[중단] 해당 회차가 없습니다: {only}" if only else "[중단] 처리된 회차가 없습니다.")

        made, skipped, per_lesson = 0, [], []
        for le in lessons:
            h = le["audio_hash"]
            lang = le["language"] or "en"
            cards = _cards_of(db, h)
            if not cards:
                # 카드가 하나도 나가지 않은 회차 — 만들 .apkg가 없다.
                skipped.append((le["lesson_date"], h, le["source_filename"]))
                continue
            out = export_anki.run(cards, h, le["lesson_date"], cfg, lang)
            if out is not None:
                made += 1
                per_lesson.append((le["lesson_date"], lang, cards))
                print(f"  {le['lesson_date']} [{lang}] {len(cards)}장 → {out.name}")
    finally:
        db.close()

    if combined and per_lesson:
        _write_combined(cfg, per_lesson)

    print(f"\n{made}개 회차 재생성 완료.")
    if skipped:
        print(f"카드 없이 건너뛴 회차 {len(skipped)}개:")
        for date, h, name in skipped:
            print(f"  - {date} {h} ({name})")


if __name__ == "__main__":
    main()
