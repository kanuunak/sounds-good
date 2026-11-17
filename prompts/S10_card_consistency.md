# S10 — Anki 카드 일관성 개선 (진행 중)

> 목적: **모델(gemini-3.6-flash)은 그대로 두고, 카드의 내부 일관성 contract를 정의하고
> 그 계약을 출력이 지키는지 검증한다.** 합격 기준은 코드가 아니라 lesson A/B 카드 출력 품질.
> STT/WhisperX/배포/DEMO_MODE/모델/temperature는 건드리지 않는다.

## 배경 문제 (실측 확인됨)

기존 스키마에 `corrected_sentence`(문법만 고침)와 `natural_sentence`(원어민 표현)가 둘 다 있어,
문법오류인데 target이 화려하게 rewrite되고 explanation은 최소수정을 설명 → **설명↔정답 불일치**.
예) target "reflect on ourselves"인데 explanation은 "look back on"을 설명.

## 카드 contract (핵심)

```
original  = 학습자가 실제로 말한 문장 (transcript 보존, 지어내기 금지)
card_type = correction | upgrade
target    = 다음에 말할 영어
             correction → 최소 수정(minimal edit), 구조 유지
             upgrade    → 더 자연스러운 대안(틀린 것 아님)
             튜터가 고쳐준 표현이 있으면 그대로 사용
korean    = target 전체의 1:1 번역 (더/덜 담지 않음)
explanation = original → target 에서 실제로 바뀐 부분만 (반영 안 된 교정 언급 금지)
```

## 단계 분해 (하나씩 승인 진행)

| 단계 | 내용 | 파일 | LLM(사용량) | 상태 |
|---|---|---|---|---|
| 0 | Before 베이스라인 캡처 | — | — | ✅ 완료 |
| **1** | 스키마(card_type 추가, corrected 제거) + 프롬프트 재작성 + run() 매핑 + PIPELINE_VERSION 1.5 | `stages/analyze.py`, `util.py` | ✅ A/B 검증함 | ✅ **완료 (2026-08-08)** |
| 2 | DB: `card_type`·`issue_type`을 **발생 단위(`lesson_sentences`)** 에 저장·집계(과거 소급 방지) + 마이그레이션(기존 안 깸) + **Weak Patterns는 correction 발생만**(upgrade·legacy/null 제외) + 화면 안내 | `db.py`, `app.py`, `export_obsidian.py` | 없음 | ✅ **완료 (2026-08-09, card_type·issue_type 소급 버그 둘 다 수정, Codex 리뷰 반영)** |
| 3 | **Anki Model/필드/ID·덱은 무변경**(기존 카드·학습이력 보존, L5/L6) — **뒷면 afmt HTML만** Original/Target/Why 중심으로. card_type은 **태그로만**(필드 아님) | `stages/export_anki.py` | 없음 | ✅ **완료 (2026-08-25)** |
| 4 | ~~(필요 시에만)~~ **필수로 격상**(외부 리뷰 2026-08-21) Validation — 카드 **전체 batch 1회 검증** → 실패분만 **batch 1회 repair**(개별 호출 아님, 사용량 절약). 검사 A~G. API오류 안전폴백 + deterministic sanity check | `stages/analyze.py` | +1~2회/수업 | ✅ **완료 (2026-08-25, fb9a5d2)** |
| 5 | ~~Stage 4 채택 결정~~(채택 완료) + Before/After 표 + 양모드 테스트 + ~~hf-clean 동기화·재배포~~ | (테스트) | A/B | ⬜ **데모 종료로 재배포분은 무효(2026-08-25)** |

의존: 1→2→3 순, 4는 1 이후 독립(생략 가능), 5는 마지막.
**정제(2026-08-08 웹 리뷰):** ① Stage2는 correction만 집계(legacy 제외→"S10 이후부터" 표시). ② Stage3는
Model 불변·뒷면 HTML만. ③ Stage4는 batch 검증/수리 각 1회 + 조건부. ④ Stage5에서 A/B 보고 Stage4 채택 결정.

## Stage 1 결과 (완료분)

- `stages/analyze.py`: `_ITEM_SCHEMA`가 S10 필드(original/card_type/target/korean/explanation/…),
  `_PROMPT`를 "카드 생성 규칙" contract로 재작성, `run()`에서 LLM 필드 → 기존 04 필드명으로 매핑
  (natural_sentence=target, korean_meaning=korean, explanation_ko=explanation, corrected_sentence=None, +card_type).
  → **db.py/export_anki는 무변경으로도 안 깨짐** (card_type만 아직 저장/표시 안 됨 = Stage 2·3).
- `util.py`: `PIPELINE_VERSION = "1.5"` (프롬프트/스키마 변경 → 캐시 무효화, L11).
- 검증: lesson B "look back" 케이스가 target=`look back on ...`(최소수정), explanation이 target과 일치 →
  설명↔정답 불일치 해소 확인. 두 샘플 모두 이번엔 전부 `correction`(오류 많은 회차라 upgrade 0, 정상).
- 미해결(→ Stage 4): STT 깨진 문장("attend the rotary")을 correction으로 "고쳐"버리는 케이스는
  `stt_suspect`가 맞음. validation/deterministic check에서 잡아야 함.

## Stage 4 Validation (미리 정의 — 필요 시에만, batch)

**호출 방식:** 카드 전체를 한 번에 검증(batch 1회) → PASS/FAIL 목록 → 실패분만 한 번에 수정(batch repair 1회).
개별 카드마다 호출하지 않는다(사용량 절약). 무한 retry 금지. validation/ API 오류 시 기존 오류 처리 정책(생성 카드
그대로 진행, traceback 미노출). Deterministic sanity check(보조, 명백한 것만): 빈 필수 필드 / original==target인데
correction / 허용 안 된 card_type.

검사 항목:
- A. korean ↔ target 전체 의미 일치
- B. explanation이 실제 original→target 변화만 설명
- C. correction인데 target을 불필요하게 통째 rewrite하지 않았는가
- D. upgrade인데 grammar error처럼 설명하지 않는가
- E. original에 없는 오류를 hallucinate하지 않는가
- F. korean을 보고 target을 발화하는 것이 합리적인가
- **G. 전사 신뢰도** — original이 STT로 깨져 카드로 쓰기 **위험**한가. ("STT 오류 확정"이 아니라
  "전사 신뢰도가 낮아 카드화가 위험함"으로 판단.) 위험하면 stt_suspect=true로만 표시하고 카드화하지 않는다.
  (Stage 1에서 관측된 "attend the rotary → win the lottery" 억지 교정이 이 케이스.)

## 절대 규칙 (재확인)

- STT·배포·모델·temperature 무변경. deploy/DEMO_MODE 구조 무변경.
- HANDOFF "뒤집지 말 것" 결정 무변경. 기존 CLI/로컬 Gradio/HF DEMO_MODE 동작 유지.
- DB 마이그레이션은 기존 데이터 안 깨는 방향(nullable/default). 기존 카드는 card_type 없으면 legacy로 안전 처리.
- 한 번에 여러 변수 바꾸지 않기. 무관한 리팩터링 금지.

## 재개 방법 (다음 세션)

1. 이 문서 + `HANDOFF.md` + `00_ARCHITECTURE.md` 읽기.
2. Stage 2(db)부터. LLM 없이 되는 2·3 먼저 → 4(validation) → 5(배포 동기화).
3. Before/After는 `samples/lesson_a.json`·`lesson_b.json` 사용.
4. 배포 반영은 master 수정 → hf-clean로 포팅 → `git push space hf-clean:main` (orphan 브랜치, history 없음).

## Stage 3 결과 (완료 2026-08-25)

`stages/export_anki.py` — **뒷면 템플릿과 CSS만** 변경. 모델 ID/이름/필드 목록/템플릿 이름/덱 ID/
앞면(qfmt)은 전부 그대로다(L5/L6 — 기존 카드의 학습 이력 보존).

- **뒷면 순서를 S10 계약과 일치시켰다**: 질문 → **정답(target)** → **내가 말한 것(original)** → **왜(explanation)**
  → 패턴/예문. 예전에는 원발화가 패턴·예문보다 **아래** 맨 끝에 있어서 "내가 말한 것 ↔ 말해야 할 것"의
  대비가 보이지 않았다. 단 D2의 근거(오답 재읽기 = 각인)는 유효하므로 원발화는 작은 회색 이탤릭 유지.
- `{{FrontSide}}` → `{{Korean}}`: 답을 본 뒤에도 "(입으로 …말한 뒤 뒤집기)" 안내가 남던 것 제거.
- **빈 필드 조건부 처리** `{{#F}}…{{/F}}`: 예전엔 패턴이 비어도 "패턴: "만 덩그러니 남았다.
- **야간 모드 대응**: `.card`의 `background:#fff` 하드코딩 제거(폰 야간 복습 시 흰 배경이 남던 원인)
  + `.nightMode` 색 보정. 데스크톱/AnkiDroid 선택자 둘 다 기재.
- **card_type은 태그로만**: `card_type::correction|upgrade|legacy` (필드 추가 금지 제약 준수).
- 영어/중국어 모델이 어긋나지 않도록 템플릿·CSS를 공용 빌더(`_back()`, `_css()`)로 뽑았다.

검증(생성된 .apkg를 실제로 열어 sqlite 대조): 기존 8/14 영어 apkg 대비 **모델ID·모델명·필드목록·
템플릿명·덱ID·앞면 동일**, 뒷면만 상이. 태그에 card_type 추가(기존 apkg엔 없음), card_type 없으면
legacy 폴백. 중국어 모델도 ID/필드/덱 불변이며 병음이 정답 바로 아래. 빈 필드 카드는 라벨째 생략됨.

**주의:** `output/`에 남아 있는 **예전 .apkg 파일들은 옛 템플릿을 담고 있다.** 나중에 그 파일을
다시 임포트하면 노트타입이 옛 뒷면으로 되돌아갈 수 있다. 새 템플릿은 다음에 내보내는 회차부터 실린다.

### 미적용(의도적으로 범위 밖)

- original↔target **차이 하이라이트**(바뀐 부분만 색칠)는 필드 내용에 HTML을 심어야 해서
  "뒷면 HTML만" 범위를 넘는다. 필요하면 별도 단계로.
- correction/upgrade를 뒷면에서 **시각적으로 구분**하는 것은 필드가 없어 템플릿 분기가 불가능하다
  (태그로만 구분). 구분이 필요해지면 모델 변경을 감수해야 하므로 별도 판단 필요.
