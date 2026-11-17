# S5: SQLite 저장 계층 구현 (v1.1)

첨부된 00_ARCHITECTURE.md의 계약(스키마 포함)을 따르는 `db.py`를 작성하라.
**핵심: 모든 통계는 lesson_sentences에서 집계한다. 누적 카운터 컬럼을 만들지 마라.**

## 요구사항

```python
class DB:
    def __init__(self, db_path: Path): ...   # 없으면 스키마 생성
    def ingest_analysis(self, audio_hash: str, analysis: dict, source_filename: str) -> dict:
        """04_analysis.json을 DB에 반영(멱등). 반환: {"new": n, "repeated": m}"""
    def select_unexported_cards(self, audio_hash: str, max_cards: int, max_per_type: int) -> list[dict]:
        """카드 후보 조회. DB를 변경하지 않는다."""
    def mark_cards_exported(self, dup_keys: list[str]) -> None:
        """APKG 생성 성공 후에만 호출. carded=1 마킹."""
    def top_weak_patterns(self, n: int = 5) -> list[dict]:
        """[{"issue_type": "tense", "total_count": 12}, ...] — lesson_sentences 집계"""
    def all_sentences(self) -> list[dict]: ...          # Sentence Bank 뷰용 (등장횟수 포함)
    def pattern_stats(self) -> list[dict]: ...          # Weak Patterns 뷰용 (집계)
    def recent_practice(self, lessons: int = 2) -> list[dict]: ...  # 최근 N개 수업의 high/medium
```

## ingest_analysis 로직 (전체 하나의 트랜잭션)

1. `stt_suspect == true` 항목은 무시 (archive 표시는 S7 담당)
2. lessons에 UPSERT (audio_hash, lesson_date, source_filename, summary_ko, processed_at)
3. **해당 audio_hash의 기존 lesson_sentences를 DELETE** ← 재분석 시 수업 단위 교체, 멱등성의 핵심
4. 각 item에 대해:
   - dup_key = util.dup_key(natural_sentence)
   - sentences에 없으면 INSERT (carded=0). 있으면 priority만 더 높은 쪽으로 갱신 (high>medium>low)
   - lesson_sentences에 INSERT (audio_hash, dup_key, timestamp, priority, original_sentence)
5. 반환값의 new = 이번에 sentences에 새로 INSERT된 수, repeated = 이미 존재하던 dup_key 수

## select_unexported_cards 로직

1. 이 audio_hash의 lesson_sentences에 등장하고 sentences.carded = 0인 문장 대상
2. 정렬: priority(high→medium→low) → 전체 등장횟수(lesson_sentences COUNT) 내림차순
3. 같은 issue_type은 max_per_type개까지
4. 상위 max_cards개 반환. **DB 변경 없음.**

## 완료 기준

- [ ] 같은 analysis로 ingest 3회 → lessons 1행, lesson_sentences 행 수 불변, pattern_stats 수치 불변
- [ ] 두 개의 다른 audio_hash에 같은 문장 ingest → sentences 1행, lesson_sentences 2행, 등장횟수 2
- [ ] select를 2번 호출해도 같은 결과 (변경 없음 확인). mark 후에는 빈 리스트
- [ ] max_per_type 캡 동작 확인
- [ ] 외부 의존 없음 (sqlite3 표준 라이브러리)
