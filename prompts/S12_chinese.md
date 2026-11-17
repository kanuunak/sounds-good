# S12 — 중국어(zh) 수업 지원 (로컬 전용)

> 초안 v0.1 (2026-08-25, Claude Code 작성 — 사용자 검토 전까지 동결 아님)
> 결정 완료: 언어는 **업로드 시 UI에서 선택**, 중국어 카드 뒷면은 **한자 + 병음**, 범위는 **로컬 파이프라인만**(공개 데모 Space 불변).
> **S12 전체 완료 (2026-08-25, 사용자 오마카세 위임, 한 세션에 Stage 1→4):**
> Stage 1 (f26619d) 언어 배관 + zh STT 게이트 통과 → §7.
> Stage 2 (3c25410) zh 프롬프트+pinyin 스키마, 실 Gemini 검증 3카드 (튜터 교정 "不安排我去出差" 마이닝 성공 = D21 zh에서도 작동, STT 오인식 "上口" stt_suspect 정확).
> Stage 3+4 (e0fc9bb) Anki "Chinese Speaking" 덱(새 고정 ID 1699417523/2117051937 — L5 불변), vault_path_zh 뷰, DB 언어 격리. 260722 회차 실 e2e로 카드 2장 실제 적재 완료.
> **남은 것**: zh 카드도 영어와 같은 '설명 완전성' 한계 확인됨(카드2: 去过 추가가 설명에 없음) → S10 Stage 4b 배치 검증이 언어 공통 과제.

## 0. 배경 / 목표

사용자는 전화영어 외에 전화중국어 수업도 듣는다(`sounds/`의 일부 파일 = 실제 중국어 수업).
현재 파이프라인은 S2가 `language="en"`을 강제(스펙)하고 있어 중국어 회차를 처리할 수 없다.
목표: **같은 파이프라인·같은 DB·같은 습관(Anki 복습 루프)** 안에서 중국어 회차를 처리한다.
새 파이프라인을 복제하지 않는다 — 언어는 회차(lesson)의 속성이다.

## 1. 설계 결정

- **D-zh1. 언어는 회차 속성**: `lessons.language` 컬럼(`en`/`zh`), 마이그레이션으로 추가, 기존 행은 `en`.
- **D-zh2. 명시 선택, 자동 감지 금지**: UI 라디오(기본 en) + `main.py --lang` + `run.sh` 전달.
  화자 라벨이 회차마다 뒤집히던 전례(D10/L9)처럼, 휴리스틱보다 매번 명시가 안전하다.
- **D-zh3. 통계는 언어별로 격리**: weak patterns / Sentence Bank / Speaking Practice / S4로 피드백되는
  `top_weak_patterns`는 전부 언어 필터. 영어 preposition 통계가 중국어 분석 프롬프트에 흘러들면 안 된다.
- **D-zh4. D2 유지**: 앞면은 한국어 뜻만. 뒷면 = 한자 문장 + 병음(보조 줄, 회색) + 내 원발화(ref).
- **D-zh5. Anki 덱 분리**: 새 `_MODEL_ID_ZH`/`_DECK_ID_ZH` 상수(기존 영어 상수는 L5 — 절대 불변),
  덱명 `chinese_speaking`, 파일명 `{date}-{hash}-chinese_speaking.apkg`.
- **D-zh6. Obsidian 뷰 분리**: config에 `vault_path_zh`(예: `…/08. 어학 역량/전화중국어 복습`).
  zh 회차는 zh 뷰 3종 + `_archive`에만 쓴다. 폴더명은 사용자가 확정.
- **D-zh7. 데모 Space 불변**: `DEMO_MODE=true` 경로는 코드·동작 모두 그대로(영어 쇼케이스).

## 2. 단계별 언어 의존 지점 (현행 코드 기준)

| 단계 | 현재 | 변경 |
| --- | --- | --- |
| S1 convert | 언어 무관 | 없음 |
| S2 transcribe | `language="en"` 하드코드 ×3 (`load_model`/`transcribe`/`load_align_model`), `_MIN_WORDS=10` | `run(wav, h, cfg, name, language)` 파라미터화. zh align은 whisperx 기본 zh 모델(글자 단위, 최초 1회 다운로드 큼). `02_transcript.json`의 `"language"`에 실제 값 기록 |
| S2 analyzable | 10단어/3.0초 | **zh는 word=글자**이므로 단어 기준이 왜곡됨. zh 전용 `_MIN_CHARS_ZH = 15`(가설) / 3.0초 — 실측 후 확정 (§5 게이트) |
| S3 speakers | 언어 무관 | 없음 |
| S4 analyze | 영어 학습 전제 프롬프트 | zh 프롬프트 변형(한국어 설명·튜터 교정 마이닝·[분석대상] 마커·적게 뽑기 등 구조는 동일). 스키마에 `pinyin` 필드(zh만). `issue_type` enum zh판 필요 → §6 |
| S5 db | `lessons`에 language 없음 | 컬럼 추가 + 전 조회 언어 필터 (D-zh3) |
| S6 export_anki | 영어 덱 하나 | zh 덱/모델 추가 (D-zh5), 뒷면 템플릿에 병음 줄 |
| S7 export_obsidian | vault 1개 | `vault_path_zh` 분기 (D-zh6) |
| S8 main / app.py | 언어 개념 없음 | `--lang` / UI 라디오, 이력에 언어 표시. DEMO_MODE 불변 |

`PIPELINE_VERSION` → **"1.6"** (캐시 키에 포함 — 기존 영어 회차를 `--force`로 재실행하면 재전사 발생.
이미 카드화된 회차는 재실행할 일이 없으므로 실질 영향 없음).

## 3. 구현 순서 (한 단계씩, 각 단계 사용자 승인 후 진행)

- **Stage 1 — 배관 (LLM 비용 0)**: 언어 파라미터 end-to-end(UI/CLI → S2 → `lessons.language`),
  en 경로 동작 불변(회귀 확인), zh 전사가 실제로 돎. **여기서 §5 STT 게이트 수행.**
- **Stage 2 — S4 zh 분석**: zh 프롬프트 + `pinyin` 스키마 + enum 확정. 실 Gemini 1회 검증.
- **Stage 3 — 출력**: Anki zh 덱(새 ID, 병음 템플릿) + Obsidian zh 뷰. **첫 .apkg 임포트 전 눈검수(D8 — 카드는 되돌리지 않는다).**
- **Stage 4 — 통계 격리 + UI**: 언어 필터 전면 적용(D-zh3), app 이력에 언어 컬럼, weak-pattern 피드백 언어별 확인.

## 4. 검증 기준

- Stage 1: 기존 영어 회차 재처리 결과가 v1.5와 turns/analyzable 동일(임계값 변화 없음 확인),
  zh 실파일 전사 diarization_status=ok/partial, 턴 재구성 정상.
- Stage 2: zh 카드 후보가 계약(S10: original→card_type→target→korean→explanation) 준수 + pinyin이 target과 1:1.
- Stage 3: 영어 덱 GUID/ID 불변(기존 카드 학습기록 보존), zh apkg 임포트 시 새 덱으로만 들어감.
- Stage 4: en 회차만 있는 DB에서 zh 뷰가 비어 있고 그 역도 성립; `top_weak_patterns('en')`에 zh 항목 0.

## 5. 중국어 STT 게이트 (영어 게이트의 전례)

영어 때처럼 **첫 카드화 전에 사람이 전사를 눈으로 검수**한다 (`_archive/transcript/` md가 이미 있어 수월).
확인 항목: ① 한자 전사 정확도(수업 도입부 한국어 섞임이 ko 오판 없이 zh로 나오는지) ② 학습자
오류가 "교정되지 않고" 보존되는지(카드 재료) ③ 글자 수 기준 analyzable이 실제 분석 가치와 맞는지
(여기서 `_MIN_CHARS_ZH` 확정) ④ 간체/번체 일관성(필요시 initial_prompt로 간체 유도 검토).

## 6. 미결(사용자 결정 필요)

1. zh `issue_type` enum: 제안 = `word_order(어순) / measure_word(양사) / vocabulary / grammar / expression / fluency`.
   en enum과 겹치는 값은 그대로 두어 DB 조회 호환 유지.
2. `vault_path_zh` 실제 폴더명.
3. ~~`_MIN_CHARS_ZH` 초기값 15자 가설~~ → **15 확정 (2026-08-25, 2회차 실측)**. 근거:
   analyzable은 STUDENT 턴에만 카드 자격을 주므로 학습자 턴만 보면 되는데, 15→20 상향 시
   260722는 반복 맞장구 1턴(저가치)만 잃지만 **260715는 실내용 턴("我到今天没有时间整理…")을 잃는다**.
   또 13자 턴에도 실내용("在车里吐了")이 있어 15가 이미 공격적인 쪽 — 상향 기각.
   저가치 턴은 프롬프트의 "적게 뽑아라"가 이미 거른다(260722 실측: 반복 턴에서 카드 0). 하향은 미검토.
4. 화자 선택 힌트: zh 수업도 자유회화형이면 "튜터가 더 말한다" 휴리스틱 역전 가능(영어 실측 전례) —
   샘플 발화 클릭 방식이 이미 있으니 추가 조치 불필요로 제안.

## 7. Stage 1 실측 기록 (2026-08-25)

- **테스트 파일**: `sounds/(중국어 수업 녹음).m4a` (346초, hash 440b856d137e).
- **결과**: turns=45, analyzable=11, **diarization_status=ok** (0 UNKNOWN — 영어 실측 partial보다 깨끗).
  zh align은 whisperx 기본 zh wav2vec2, 글자 단위 정렬 확인(word=글자, text는 글자 공백 연결).
- **게이트 판정 (§5 항목별)**:
  ① 한자 전사 정확도 좋음 — 간체 일관, 도입부 ko 오판 없음, 대화 내용(출장·인도 환경·물/음식) 일관되게 판독 가능.
  ② 학습자 발화의 어색함 보존됨 (예: "有是亲有是", "可以上口" — 실발화/STT 혼재 의심 구간도 교정 없이 그대로) ✅
  ③ 15자/3.0초 기준: 긴 서사 턴은 전부 포착, 짧은 맞장구/에코(7~11자)는 제외 — 대체로 적절.
    경계 사례: 17자 턴이 "후천 귀국" 반복+맞장구 위주로 정보량 낮음 → **Stage 2 실측 후 15→20 상향 검토**.
  ④ 간체 일관 → initial_prompt 불필요.
- **새 발견 (Stage 2에서 고려)**:
  (a) **경계 번짐**: 튜터·학습자가 서로 문장을 이어 말하는 구간에서 마지막 1~3글자가 다음 화자 턴에 붙는다
      ("在公/司工作", "回/来"). 영어보다 에코·겹말이 잦아 1~3자 미니 턴이 다수(45턴 중 ~20개).
      → S4 프롬프트에 "인접 턴 경계의 글자 번짐은 화자 오류로 간주하지 말 것" 지시 필요.
  (b) zh text는 글자가 공백으로 연결돼 있다 → S4 투입 시 공백 제거(붙여쓰기)해서 넣는 게 자연스러움.
  (c) 화자 역할: 이 회차도 학습자(S00)가 튜터보다 길게 말함 — "튜터가 더 말한다" 휴리스틱 역전 재확인(§6-4 유지).
- **연구 원시 데이터**: `data/research/20260825T043606Z-440b856d137e/` (event=zh_gate, consent=development_only).
