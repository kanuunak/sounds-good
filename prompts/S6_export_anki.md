# S6: Anki export 구현 (v1.1)

첨부된 00_ARCHITECTURE.md의 계약을 따르는 `stages/export_anki.py`를 작성하라.

## 절대 규칙

**카드 앞면에 학습자의 틀린 문장(original_sentence)을 노출하지 않는다.**
틀린 문장을 앞면에서 반복 노출하면 오답이 각인된다. 참고용 표기는 뒷면 하단에만 허용.

## 요구사항

```python
def run(cards: list[dict], audio_hash: str, lesson_date: str, cfg: dict) -> Path | None:
    """genanki로 output/english_speaking.apkg 생성"""
```

- genanki 라이브러리 사용
- Model ID / Deck ID는 코드에 하드코딩된 고정 상수 (매번 랜덤 생성 금지 — 바뀌면 Anki가 다른 덱으로 인식)
- 덱 이름: cfg["anki_deck_name"] 단일 덱. 서브덱 금지.
- 노트 GUID = dup_key (재실행/재등장 시 Anki가 중복 대신 갱신 처리)
- **이 함수는 DB를 건드리지 않는다.** carded 마킹은 main이 apkg 생성 성공을 확인한 뒤 수행한다.

## 카드 템플릿 (KO→EN 말하기)

앞면:
```
{korean_meaning}

(입으로 영어로 말한 뒤 뒤집기)
```

뒷면:
```
{natural_sentence}

— {explanation_ko}
패턴: {practice_pattern}
예문: {pattern_examples 줄바꿈 나열}

(참고) 당시 내 발화: {original_sentence}
```

- 뒷면의 "당시 내 발화"는 작은 회색 글씨 CSS로
- 태그: `lesson::{lesson_date}`, `type::{issue_type}`, `priority::{priority}`
- cards가 빈 리스트면 apkg를 만들지 않고 "새 카드 없음" 출력 후 None 반환

## 완료 기준

- [ ] 생성된 .apkg를 실제 Anki에 임포트 → 카드 렌더링 확인
- [ ] 같은 apkg 두 번 임포트해도 카드 수 불변 (GUID 동작 확인)
- [ ] 앞면 어디에도 original_sentence가 없음
- [ ] 외부 의존: genanki만
