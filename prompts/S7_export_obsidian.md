# S7: Obsidian export 구현 (v1.1)

첨부된 00_ARCHITECTURE.md의 계약을 따르는 `stages/export_obsidian.py`를 작성하라.

## 절대 규칙

- 3개 뷰 파일은 **DB에서 매번 통째로 재생성(덮어쓰기)**한다. append 금지, 기존 내용 파싱 금지.
- 모든 파일 쓰기는 임시파일에 쓴 뒤 `os.replace()`로 원자적 교체 (NAS 동기화 중 파일 깨짐 방지).
- 뷰 파일 상단에 반드시: `<!-- 자동 생성 파일. 직접 수정 금지 — 다음 실행 시 덮어써짐 -->`

## 요구사항

```python
def run(db, analysis: dict, audio_hash: str, cfg: dict) -> None:
```

vault_path 아래에 4개 파일 생성:

### 1. Speaking Practice.md (매번 재생성)

- db.recent_practice(lessons=2)의 high/medium 문장만, 최대 25줄
- 형식 (설명 없음, 입으로 연습하는 밀도):
```
## 오늘의 연습 — 한국어를 보고 영어로 말하기

1. 요즘 야근이 많아서 피곤해요
   > [!note]- 정답
   > I've been working late a lot, so I'm exhausted.
```
- Obsidian 접이식 callout(`> [!note]-`)으로 정답 숨김

### 2. Sentence Bank.md (매번 재생성)

- db.all_sentences()를 최근순으로: `| 자연스러운 문장 | 한국어 | 유형 | 등장 |` 테이블
- 100행 초과 시 이후는 "…외 N개 (검색은 이 파일에서 Ctrl+F)"로 자름

### 3. Weak Patterns.md (매번 재생성)

- db.pattern_stats() 누적 횟수순: `| 패턴 | 누적 | 최근 | 대표 예문 |` 테이블
- 대표 예문은 해당 유형에서 등장횟수 최고 문장 1개

### 4. _archive/{lesson_date}-{audio_hash}-detail.md (매번 원자적 덮어쓰기)

- 파일명에 audio_hash를 포함하는 이유: 같은 날짜 수업 2회 충돌 방지 + --force analyze
  재분석 시 이 수업 것만 갱신됨. 스킵하지 않고 항상 현재 분석 결과로 덮어쓴다.
- frontmatter: date, topic(=summary 첫 문장), card_count
- 내용: lesson_summary_ko, 전체 분석 항목 표(타임스탬프 포함),
  **stt_suspect 항목 별도 섹션** ("STT 오인식 의심 — 원본 확인 필요", 타임스탬프 필수)

## 완료 기준

- [ ] 실행 후 4개 파일이 vault에 생성되고 Obsidian에서 정상 렌더링 (접이식 callout 동작 확인)
- [ ] 2회 실행해도 뷰 3개 내용 일관, archive는 같은 파일에 덮어쓰기 (파일 수 불변)
- [ ] vault_path가 없는 경로면 명확한 에러
- [ ] 외부 의존 없음 (frontmatter는 f-string으로 충분 — 단, 값에 콜론 포함 시 따옴표 감싸기 처리할 것)
