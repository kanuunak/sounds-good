# 전화영어 복습 파이프라인 — 마스터 아키텍처 (v1.3)

> 이 문서는 모든 단계 프롬프트(prompts/*.md)의 공통 기준이다.
> LLM에게 단계 프롬프트를 줄 때 **이 문서를 항상 함께 첨부**한다.
> v1.3 변경(영어 회차 실측 반영): analyzable 임계 상향(10단어/3.0초), 튜터 실시간 교정 채굴 추가.
> v1.2 변경(실측 검증 결과 반영): 02_transcript.json이 segments가 아니라 **turns**를 담는다.
> WhisperX의 segment-level speaker 라벨은 신뢰할 수 없음이 실측으로 확인됨(아래 주의 참조).
> v1.1 변경: lesson_sentences 테이블 도입(멱등성 확보), 카드 마킹을 export 성공 후로 이동,
> --force 연쇄 무효화, source_filename/pipeline_version 필드, default_my_speaker 삭제,
> 화자분리 실패 시 중단 정책, archive 파일명에 hash 포함.

## 목표

전화영어 수업 녹음(.m4a)을 입력하면:
1. 내 발화만 추출 → LLM 분석 → 복습 가치 높은 문장 10~20개 선별
2. Anki 카드(.apkg) 생성 (실제 복습은 여기서만 일어남)
3. Obsidian markdown 4종 생성 (뷰 3개 + archive 1개, 전부 DB/분석 결과에서 재생성)

## 3대 설계 원칙 (절대 위반 금지)

1. **SQLite가 원본(source of truth). Markdown은 전부 재생성되는 뷰. 복습 루프는 Anki 하나.**
2. **Anki 카드 앞면에 학습자의 틀린 문장을 절대 노출하지 않는다.** 카드는 한국어→영어 말하기 방식.
3. **모든 단계는 중간 결과를 파일로 남기고, 파일이 있으면 스킵한다(캐싱). 모든 DB 반영은 멱등이다.**
   같은 수업을 몇 번 재실행해도 DB 수치가 부풀지 않아야 한다.

## 파이프라인 및 파일 계약

```
m4a → [S1 convert] → work/{hash}/01_audio.wav
    → [S2 transcribe] → work/{hash}/02_transcript.json
    → [S3 speakers] → work/{hash}/03_speakers.json
    → [S4 analyze] → work/{hash}/04_analysis.json
    → [S5 db] → data/history.db
    → [S6 export_anki] → output/english_speaking.apkg
    → [S7 export_obsidian] → vault 내 md 4종
```

- `{hash}` = 원본 m4a 파일의 SHA256 앞 12자리
- 각 stage는 자기 출력 파일이 이미 존재하면 아무것도 하지 않고 반환한다
- `PIPELINE_VERSION = "1.3"` 상수를 util.py에 둔다. S2/S4 출력 JSON에 이 값을 기록하고,
  읽을 때 버전이 다르면 캐시를 무시하고 재생성한다 (프롬프트/스키마 변경 시 자동 무효화)

### 02_transcript.json (S2 출력)

```json
{
  "pipeline_version": "1.3",
  "audio_hash": "abc123def456",
  "source_filename": "통화_튜터이름_260703_072450.m4a",
  "duration_sec": 292.0,
  "language": "en",
  "diarization_status": "ok | partial | failed",
  "turns": [
    {"start": 1.25, "end": 8.40, "speaker": "SPEAKER_00",
     "text": "Hello, how was your week?", "word_count": 6, "analyzable": true}
  ]
}
```

**★ 주의 — turns는 word_segments에서 재구성한다 (실측으로 확인된 필수 사항).**
WhisperX 출력의 `segments[].speaker`는 약 30초짜리 덩어리에 대한 다수결 라벨이라
신뢰할 수 없다. 실측 결과 세그먼트 순도가 51~100%로 널뛰었고 절반이 동전던지기 수준이었다.
반드시 `word_segments[].speaker`(단어 단위 라벨)를 사용해 인접한 같은 화자 단어를 병합하여
turn을 만든다.

`analyzable` 필드: `word_count >= 10 AND (end - start) >= 3.0` 이면 true.

턴 경계에서 단어 1~3개가 상대 화자로 새는 현상이 두 회차 모두에서 확인됐다.
영어 회차 실측에서 필터 전 오염률 47%, 위 임계 적용 시 33%까지 내려가고
그 이상 올려도 더 내려가지 않는다(오염이 턴 *가장자리*에 있어서 길이로 못 거른다).
따라서 방어는 2단계다: ① 이 필터로 순수 파편 제거 ② S4 프롬프트가 문장 앞뒤 조각을
학습자 오류로 판정하지 않도록 지시. 필터를 더 조이면 멀쩡한 긴 발화까지 잃으므로 금지.

### 03_speakers.json (S3 출력)

```json
{"my_speaker": "SPEAKER_01", "confirmed_by": "manual"}
```

### 04_analysis.json (S4 출력)

```json
{
  "pipeline_version": "1.3",
  "lesson_date": "2026-07-03",
  "lesson_summary_ko": "string",
  "items": [
    {
      "original_sentence": "string",
      "stt_suspect": false,
      "corrected_sentence": "string",
      "natural_sentence": "string",
      "korean_meaning": "string",
      "issue_type": "tense|article|preposition|word_order|vocabulary|konglish|fluency",
      "explanation_ko": "string (2문장 이내)",
      "practice_pattern": "string",
      "pattern_examples": ["string", "string"],
      "priority": "high|medium|low",
      "timestamp": "01:23"
    }
  ]
}
```

## config.yaml

```yaml
vault_path: "/path/to/obsidian/vault/English"
work_dir: "./work"
db_path: "./data/history.db"
output_dir: "./output"
anki_deck_name: "English Speaking"
max_cards_per_lesson: 15
max_cards_per_issue_type: 5
bandpass_filter: true           # 협대역 전화 녹음이면 true, 광대역 녹음이면 false
llm_provider: "anthropic"       # anthropic | openai | ollama
llm_model: "claude-sonnet-4-6"
whisper_model: "large-v3"
whisper_batch_size: 4          # 8GB VRAM 기준. 브라우저 등 다른 GPU 소비자 있으면 2
hf_token: "env:HF_TOKEN"
anthropic_api_key: "env:ANTHROPIC_API_KEY"
openai_api_key: "env:OPENAI_API_KEY"
```

- `env:XXX` 치환은 **해당 값이 실제로 사용되는 시점에** 해석한다(lazy).
  ollama 사용자가 anthropic 키 미설정 때문에 실패해서는 안 된다.
- 화자 선택은 매 수업 CLI로 수행한다. 전역 기본 화자 옵션은 두지 않는다
  (SPEAKER_00/01 라벨은 녹음마다 같은 사람에게 고정되지 않기 때문).

## 디렉터리 구조

```
lesson_review/
├── config.yaml
├── main.py            # S8: 오케스트레이션
├── stages/
│   ├── __init__.py
│   ├── convert.py     # S1
│   ├── transcribe.py  # S2
│   ├── speakers.py    # S3
│   ├── analyze.py     # S4
│   ├── export_anki.py # S6
│   └── export_obsidian.py  # S7
├── db.py              # S5
├── util.py            # 해시, 캐싱, config, PIPELINE_VERSION (S1 프롬프트에 포함)
├── work/{hash}/       # 중간 결과 (gitignore, NAS 동기화 제외)
├── data/history.db
└── output/
```

## SQLite 스키마 (data/history.db)

```sql
CREATE TABLE IF NOT EXISTS lessons (
  audio_hash TEXT PRIMARY KEY,
  lesson_date TEXT NOT NULL,
  source_filename TEXT,
  summary_ko TEXT,
  processed_at TEXT NOT NULL
);

-- 문장의 누적·대표 정보 (수업 독립적)
CREATE TABLE IF NOT EXISTS sentences (
  dup_key TEXT PRIMARY KEY,          -- normalize(natural_sentence)의 sha256 앞 16자리
  natural_sentence TEXT NOT NULL,
  korean_meaning TEXT,
  corrected_sentence TEXT,
  issue_type TEXT,
  explanation_ko TEXT,
  practice_pattern TEXT,
  pattern_examples TEXT,             -- JSON 문자열
  priority TEXT,                     -- 지금까지 관측된 최고 우선순위
  carded INTEGER DEFAULT 0
);

-- 어떤 수업에 실제로 등장했는지 (등장 이력의 원본)
CREATE TABLE IF NOT EXISTS lesson_sentences (
  audio_hash TEXT NOT NULL REFERENCES lessons(audio_hash),
  dup_key TEXT NOT NULL REFERENCES sentences(dup_key),
  timestamp TEXT,
  priority TEXT,
  original_sentence TEXT,            -- 그 수업에서 실제로 말한 문장
  PRIMARY KEY (audio_hash, dup_key)
);
```

- **seen_count와 약점 패턴 통계는 별도 컬럼에 누적하지 않는다.
  전부 lesson_sentences에서 집계(COUNT/GROUP BY)로 계산한다.** 이것이 멱등성의 근거다.
- 같은 수업 재반영(ingest) 시: 그 audio_hash의 lesson_sentences를 DELETE 후 재INSERT
  (수업 단위 교체 = 재분석 결과가 깨끗하게 반영됨)
- `normalize(s)`: 소문자화 → 구두점 제거 → 연속 공백을 단일 공백으로 → strip
- 카드 GUID = dup_key

## 카드 생성 순서 (유실 방지)

```
후보 조회(select_unexported_cards, DB 변경 없음)
→ APKG 생성 성공
→ 성공한 dup_key만 mark_cards_exported로 carded=1
```

마킹이 생성보다 먼저 오면 안 된다.

## --force 연쇄 무효화

| --force 대상 | 삭제할 캐시 |
|---|---|
| convert | 01, 02, 03, 04 |
| transcribe | 02, 03, 04 |
| speakers | 03, 04 |
| analyze | 04 |

04를 지우고 재분석하면 S5의 수업 단위 교체(DELETE+INSERT)가 DB를 새 결과로 맞춘다.
단, 이미 carded=1로 export된 문장은 되돌리지 않는다 (Anki에 이미 나간 카드는 사실이므로).

## 화자분리 실패 정책

- diarization_status == "ok": 정상 진행
- "partial" (일부 턴이 UNKNOWN): UNKNOWN 턴은 분석에서 제외하고 진행, 경고 출력
- "failed" (전체 UNKNOWN): **분석을 진행하지 않고 중단한다.**
  LLM이 화자를 추측하게 하지 않는다 — 튜터의 예시 오류를 학습자 오류로 카드화하면
  오염된 카드가 복습 루프에 영구히 남기 때문. 수동 확인(다른 STT 시도 등)을 안내하고 종료.

## 공통 코딩 규칙

- Python 3.11+, 타입 힌트 필수, 외부 의존은 각 단계 프롬프트에 명시된 것만
- 모든 경로는 pathlib.Path
- markdown 파일 쓰기는 임시파일에 쓴 뒤 `os.replace()`로 원자적 교체
- 예외는 삼키지 말고 stage 이름을 붙여 재발생시킬 것
- 로그는 `print(f"[stage명] 메시지")` 수준. 프레임워크 금지.
- 오버엔지니어링 금지: 요구사항에 없는 클래스 계층·추상화·플러그인 구조 추가 금지

## 구현 순서

S1 → S2 → S3 → S4 → S5 → S6 → S7 → S8
각 단계 완료 시 해당 프롬프트 하단의 **완료 기준 체크리스트**를 전부 통과해야 다음으로.
