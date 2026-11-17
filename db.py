"""S5: SQLite 저장 계층. 00_ARCHITECTURE.md 스키마 계약.

핵심 원칙:
- SQLite가 원본(source of truth). 모든 통계는 lesson_sentences에서 집계(COUNT/GROUP BY)한다.
  누적 카운터 컬럼을 만들지 않는다 → 재실행해도 수치가 부풀지 않는 멱등성의 근거.
- 같은 수업 재반영(ingest) 시 그 audio_hash의 lesson_sentences를 DELETE 후 재INSERT(수업 단위 교체).
- carded=1(이미 Anki로 나간 카드)은 재분석 때도 되돌리지 않는다.

외부 의존: 없음 (sqlite3 표준 라이브러리). util만 임포트.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path

from util import dup_key

_PRIO_RANK = {"high": 3, "medium": 2, "low": 1}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
  audio_hash TEXT PRIMARY KEY,
  lesson_date TEXT NOT NULL,
  source_filename TEXT,
  summary_ko TEXT,
  language TEXT NOT NULL DEFAULT 'en',  -- S12: 회차 언어 (en | zh)
  processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sentences (
  dup_key TEXT PRIMARY KEY,
  natural_sentence TEXT NOT NULL,
  korean_meaning TEXT,
  pinyin TEXT,             -- S12: zh 회차만 채움 (natural_sentence의 병음 — 문장 단위 대표값)
  corrected_sentence TEXT,
  issue_type TEXT,
  explanation_ko TEXT,
  practice_pattern TEXT,
  pattern_examples TEXT,
  priority TEXT,
  card_type TEXT,          -- S10: correction | upgrade (기존 카드는 NULL=legacy)
  carded INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lesson_sentences (
  audio_hash TEXT NOT NULL REFERENCES lessons(audio_hash),
  dup_key TEXT NOT NULL REFERENCES sentences(dup_key),
  timestamp TEXT,
  priority TEXT,
  original_sentence TEXT,
  card_type TEXT,          -- S10: 발생 단위 card_type (집계 기준). 과거 소급 방지.
  issue_type TEXT,         -- S10: 발생 단위 issue_type (집계 기준). sentences.issue_type은 최초값 고정이라 사용 안 함.
  PRIMARY KEY (audio_hash, dup_key)
);
"""


def _higher_priority(a: str | None, b: str | None) -> str:
    return a if _PRIO_RANK.get(a or "", 0) >= _PRIO_RANK.get(b or "", 0) else b


class DB:
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """기존 DB 호환 마이그레이션. 기존 데이터/스키마를 깨지 않는 최소 변경만."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(sentences)").fetchall()}
        if "card_type" not in cols:  # S10: sentences.card_type (vestigial, 집계엔 미사용)
            self.conn.execute("ALTER TABLE sentences ADD COLUMN card_type TEXT")
        if "pinyin" not in cols:  # S12: zh 문장의 병음 (en 문장은 NULL)
            self.conn.execute("ALTER TABLE sentences ADD COLUMN pinyin TEXT")
        ls_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(lesson_sentences)").fetchall()}
        if "card_type" not in ls_cols:  # S10: 발생 단위 card_type (집계·선택 기준). 기존 발생은 NULL=legacy.
            self.conn.execute("ALTER TABLE lesson_sentences ADD COLUMN card_type TEXT")
        if "issue_type" not in ls_cols:  # S10: 발생 단위 issue_type. 기존 발생은 NULL=legacy.
            self.conn.execute("ALTER TABLE lesson_sentences ADD COLUMN issue_type TEXT")
        l_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(lessons)").fetchall()}
        if "language" not in l_cols:  # S12: 기존 회차는 전부 영어였다 → 'en'으로 채운다.
            self.conn.execute("ALTER TABLE lessons ADD COLUMN language TEXT NOT NULL DEFAULT 'en'")

    def close(self) -> None:
        self.conn.close()

    # ── 쓰기 ──────────────────────────────────────────────────────────────
    def ingest_analysis(self, audio_hash: str, analysis: dict, source_filename: str) -> dict:
        """04_analysis.json을 DB에 멱등 반영. 반환: {"new": n, "repeated": m}."""
        items = [it for it in analysis.get("items", []) if not it.get("stt_suspect")]
        now = _dt.datetime.now().isoformat(timespec="seconds")
        new = 0
        repeated = 0
        seen: set[str] = set()

        with self.conn:  # 하나의 트랜잭션 (예외 시 롤백)
            self.conn.execute(
                """INSERT INTO lessons (audio_hash, lesson_date, source_filename, summary_ko,
                                        language, processed_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(audio_hash) DO UPDATE SET
                     lesson_date=excluded.lesson_date,
                     source_filename=excluded.source_filename,
                     summary_ko=excluded.summary_ko,
                     language=excluded.language,
                     processed_at=excluded.processed_at""",
                (audio_hash, analysis.get("lesson_date", ""), source_filename,
                 analysis.get("lesson_summary_ko", ""), analysis.get("language", "en"), now),
            )
            # 수업 단위 교체: 이 수업의 등장 이력을 통째로 지우고 다시 넣는다.
            self.conn.execute("DELETE FROM lesson_sentences WHERE audio_hash=?", (audio_hash,))

            for it in items:
                dk = dup_key(it["natural_sentence"])
                row = self.conn.execute(
                    "SELECT priority FROM sentences WHERE dup_key=?", (dk,)
                ).fetchone()
                if row is None and dk not in seen:
                    # 주의: card_type은 sentences(문장 단위)에 저장하지 않는다. 문장 단위로 두면
                    # 같은 target의 과거 lesson 기록까지 하나의 현재 값을 공유해 소급 재분류된다.
                    # → card_type은 아래 lesson_sentences(발생 단위)에만 저장한다.
                    self.conn.execute(
                        """INSERT INTO sentences
                           (dup_key, natural_sentence, korean_meaning, pinyin, corrected_sentence,
                            issue_type, explanation_ko, practice_pattern, pattern_examples,
                            priority, carded)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                        (dk, it["natural_sentence"], it.get("korean_meaning"), it.get("pinyin"),
                         it.get("corrected_sentence"), it.get("issue_type"),
                         it.get("explanation_ko"), it.get("practice_pattern"),
                         json.dumps(it.get("pattern_examples", []), ensure_ascii=False),
                         it.get("priority")),
                    )
                    new += 1
                elif dk not in seen:
                    # 기존 문장: priority만 더 높은 쪽으로 갱신 (carded 등은 보존).
                    self.conn.execute(
                        "UPDATE sentences SET priority=? WHERE dup_key=?",
                        (_higher_priority(row["priority"], it.get("priority")), dk),
                    )
                    repeated += 1
                seen.add(dk)

                # 발생 단위 기록 — card_type·issue_type을 여기(lesson_sentences)에 저장한다(집계·선택 기준).
                # sentences.issue_type은 최초 삽입 시 값이 고정되므로 재분류 집계에 쓰지 않는다(card_type과 동일 이유).
                # 같은 수업에 같은 dk가 두 번 나오면 마지막 것으로 대체.
                self.conn.execute(
                    """INSERT OR REPLACE INTO lesson_sentences
                       (audio_hash, dup_key, timestamp, priority, original_sentence, card_type, issue_type)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (audio_hash, dk, it.get("timestamp"), it.get("priority"),
                     it.get("original_sentence"), it.get("card_type"), it.get("issue_type")),
                )

        return {"new": new, "repeated": repeated}

    def mark_cards_exported(self, dup_keys: list[str]) -> None:
        """APKG 생성 성공 후에만 호출. carded=1 마킹."""
        if not dup_keys:
            return
        with self.conn:
            self.conn.executemany(
                "UPDATE sentences SET carded=1 WHERE dup_key=?", [(k,) for k in dup_keys]
            )

    # ── 조회 (DB 변경 없음) ───────────────────────────────────────────────
    def select_unexported_cards(self, audio_hash: str, max_cards: int, max_per_type: int) -> list[dict]:
        """이 수업에 등장 + carded=0인 문장을 우선순위·등장횟수로 정렬해 상한만큼 반환. DB 미변경."""
        rows = self.conn.execute(
            """SELECT s.dup_key, s.natural_sentence, s.korean_meaning, s.pinyin, s.corrected_sentence,
                      ls.issue_type, s.explanation_ko, s.practice_pattern, s.pattern_examples,
                      s.priority, ls.card_type,
                      ls.original_sentence, ls.timestamp,
                      (SELECT COUNT(*) FROM lesson_sentences x WHERE x.dup_key=s.dup_key) AS seen_count
               FROM lesson_sentences ls
               JOIN sentences s ON s.dup_key = ls.dup_key
               WHERE ls.audio_hash=? AND s.carded=0""",
            (audio_hash,),
        ).fetchall()

        ordered = sorted(
            rows,
            key=lambda r: (-_PRIO_RANK.get(r["priority"] or "", 0), -r["seen_count"]),
        )

        out: list[dict] = []
        per_type: dict[str, int] = {}
        for r in ordered:
            if len(out) >= max_cards:
                break
            itype = r["issue_type"] or ""
            if per_type.get(itype, 0) >= max_per_type:
                continue
            per_type[itype] = per_type.get(itype, 0) + 1
            out.append({
                "dup_key": r["dup_key"],
                "natural_sentence": r["natural_sentence"],
                "korean_meaning": r["korean_meaning"],
                "pinyin": r["pinyin"],
                "corrected_sentence": r["corrected_sentence"],
                "issue_type": r["issue_type"],
                "explanation_ko": r["explanation_ko"],
                "practice_pattern": r["practice_pattern"],
                "pattern_examples": json.loads(r["pattern_examples"] or "[]"),
                "priority": r["priority"],
                "card_type": r["card_type"],
                "original_sentence": r["original_sentence"],
                "timestamp": r["timestamp"],
                "seen_count": r["seen_count"],
            })
        return out

    def top_weak_patterns(self, n: int = 5, language: str = "en") -> list[dict]:
        """[{"issue_type": ..., "total_count": ...}] — lesson_sentences 집계 상위 n개.

        S12: 언어별 격리 — en 약점 통계가 zh 분석 프롬프트에 섞이면 안 된다(D-zh3).
        """
        rows = self.conn.execute(
            """SELECT ls.issue_type AS issue_type, COUNT(*) AS total_count
               FROM lesson_sentences ls JOIN sentences s ON s.dup_key=ls.dup_key
               JOIN lessons l ON l.audio_hash=ls.audio_hash
               WHERE ls.issue_type IS NOT NULL AND ls.card_type = 'correction' AND l.language=?
               GROUP BY ls.issue_type
               ORDER BY total_count DESC, ls.issue_type ASC
               LIMIT ?""",
            (language, n),
        ).fetchall()
        return [{"issue_type": r["issue_type"], "total_count": r["total_count"]} for r in rows]

    def all_sentences(self, language: str = "en") -> list[dict]:
        """Sentence Bank 뷰용. 최근 등장순, 등장횟수 포함.

        s.issue_type(최초 삽입값)을 그대로 쓴다 — natural_sentence/korean_meaning처럼
        문장의 대표값을 보여주는 뷰이지 집계(COUNT/GROUP BY)가 아니므로 발생 단위로
        바꿀 필요가 없다(Weak Patterns 집계만 ls.issue_type을 쓴다).
        """
        rows = self.conn.execute(
            """SELECT s.natural_sentence, s.korean_meaning, s.pinyin, s.issue_type,
                      COUNT(ls.audio_hash) AS seen_count,
                      MAX(l.lesson_date) AS last_date
               FROM sentences s
               JOIN lesson_sentences ls ON ls.dup_key=s.dup_key
               JOIN lessons l ON l.audio_hash=ls.audio_hash
               WHERE l.language=?
               GROUP BY s.dup_key
               ORDER BY last_date DESC, seen_count DESC""",
            (language,),
        ).fetchall()
        return [dict(r) for r in rows]

    def pattern_stats(self, language: str = "en") -> list[dict]:
        """Weak Patterns 뷰용. issue_type별 누적/최근 횟수 + 대표 예문. 언어별 격리(S12)."""
        recent_hashes = {
            r["audio_hash"]
            for r in self.conn.execute(
                "SELECT audio_hash FROM lessons WHERE language=? "
                "ORDER BY lesson_date DESC, processed_at DESC LIMIT 2",
                (language,),
            ).fetchall()
        }
        stats: list[dict] = []
        types = self.conn.execute(
            """SELECT ls.issue_type AS issue_type, COUNT(*) AS total_count
               FROM lesson_sentences ls JOIN sentences s ON s.dup_key=ls.dup_key
               JOIN lessons l ON l.audio_hash=ls.audio_hash
               WHERE ls.issue_type IS NOT NULL AND ls.card_type = 'correction' AND l.language=?
               GROUP BY ls.issue_type ORDER BY total_count DESC""",
            (language,),
        ).fetchall()
        for t in types:
            itype = t["issue_type"]
            recent_count = sum(
                1
                for r in self.conn.execute(
                    """SELECT ls.audio_hash
                       FROM lesson_sentences ls JOIN sentences s ON s.dup_key=ls.dup_key
                       WHERE ls.issue_type=? AND ls.card_type = 'correction'""",
                    (itype,),
                ).fetchall()
                if r["audio_hash"] in recent_hashes
            )
            # 대표 예문: 이 유형에서 correction 발생이 가장 많은 문장 1개.
            example = self.conn.execute(
                """SELECT s.natural_sentence, COUNT(*) AS c
                   FROM lesson_sentences ls JOIN sentences s ON s.dup_key=ls.dup_key
                   JOIN lessons l ON l.audio_hash=ls.audio_hash
                   WHERE ls.issue_type=? AND ls.card_type = 'correction' AND l.language=?
                   GROUP BY s.dup_key ORDER BY c DESC LIMIT 1""",
                (itype, language),
            ).fetchone()
            stats.append({
                "issue_type": itype,
                "total_count": t["total_count"],
                "recent_count": recent_count,
                "example": example["natural_sentence"] if example else "",
            })
        return stats

    def recent_practice(self, lessons: int = 2, language: str = "en") -> list[dict]:
        """최근 N개 수업의 high/medium 문장(중복 제거). Speaking Practice 뷰용. 언어별 격리(S12)."""
        recent = [
            r["audio_hash"]
            for r in self.conn.execute(
                "SELECT audio_hash FROM lessons WHERE language=? "
                "ORDER BY lesson_date DESC, processed_at DESC LIMIT ?",
                (language, lessons),
            ).fetchall()
        ]
        if not recent:
            return []
        placeholders = ",".join("?" * len(recent))
        rows = self.conn.execute(
            f"""SELECT s.dup_key, s.korean_meaning, s.natural_sentence, ls.issue_type, ls.priority
                FROM lesson_sentences ls JOIN sentences s ON s.dup_key=ls.dup_key
                WHERE ls.audio_hash IN ({placeholders}) AND ls.priority IN ('high','medium')""",
            recent,
        ).fetchall()
        # dup_key 중복 제거, 더 높은 priority 유지
        best: dict[str, dict] = {}
        for r in rows:
            cur = best.get(r["dup_key"])
            if cur is None or _PRIO_RANK.get(r["priority"] or "", 0) > _PRIO_RANK.get(cur["priority"], 0):
                best[r["dup_key"]] = {
                    "korean_meaning": r["korean_meaning"],
                    "natural_sentence": r["natural_sentence"],
                    "issue_type": r["issue_type"],
                    "priority": r["priority"],
                }
        return sorted(best.values(), key=lambda d: -_PRIO_RANK.get(d["priority"] or "", 0))
