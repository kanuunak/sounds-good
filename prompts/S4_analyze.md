# S4: LLM 분석 구현 (v1.3)

첨부된 00_ARCHITECTURE.md의 계약을 따르는 `stages/analyze.py`를 작성하라.
**아래 "런타임 분석 프롬프트"는 이미 설계 완료된 것이다. 문구를 수정하지 말고 그대로 사용하라.**

## 요구사항

```python
def run(transcript_path: Path, speakers_path: Path, audio_hash: str,
        cfg: dict, weak_patterns: list[dict]) -> Path:
    """LLM 분석 → work/{hash}/04_analysis.json 생성. 존재하면 스킵."""
```

- `weak_patterns`: db.py가 주는 `[{"issue_type": "tense", "total_count": 12}, ...]` (상위 5개, 없으면 빈 리스트)
- transcript의 turns를 `[MM:SS] STUDENT: ...` / `[MM:SS] TUTOR: ...` 형식 텍스트로 변환해
  프롬프트에 삽입.
- **`analyzable == false`인 turn과 speaker가 "UNKNOWN"인 turn은 프롬프트에서 제외한다.**
  턴 경계에서 단어가 상대 화자로 새는 현상이 실측으로 확인되었고, 짧은 파편을 넣으면
  튜터 발화 조각이 학습자 문장으로 오염된다. 제외한 turn 수를 경고로 출력.
- provider 분기:
  - `anthropic`: anthropic SDK, **tool use로 JSON 스키마 강제** (아래 스키마). temperature=0.2
  - `openai`: openai SDK, response_format json_schema 사용
  - `ollama`: ollama 라이브러리, format="json" + 프롬프트 말미에 "JSON 외 아무것도 출력하지 마라" 추가
- API 키는 util.resolve()로 사용 시점에만 해석 (lazy)
- 응답 파싱 실패 시 1회 재시도(같은 요청), 그래도 실패면 원본 응답을
  work/{hash}/04_raw_response.txt로 저장하고 예외 발생
- transcript가 30,000자 초과면 실행 전 중단하고 경고 (비용 방어)
- 성공 시 04_analysis.json 저장 (pipeline_version 포함). lesson_date는 transcript JSON의
  source_filename에서 YYMMDD 패턴 추출 시도, 실패 시 오늘 날짜.
- 캐시 파일의 pipeline_version이 다르면 무시하고 재분석

## JSON 스키마 (tool use input_schema로 사용)

00_ARCHITECTURE.md의 04_analysis.json 스키마와 동일. issue_type과 priority는 enum으로 강제할 것.

## 런타임 분석 프롬프트 (그대로 사용)

```
당신은 한국인 영어 학습자를 위한 회화 코치다. 아래는 전화영어 수업의 전사본이다.
STUDENT는 한국인 학습자, TUTOR는 원어민 튜터다.

<transcript>
{transcript_text}
</transcript>

이 학습자의 기존 약점 패턴 (누적 횟수순): {weak_patterns_text}

STUDENT의 발화만 분석하여, 복습 가치가 있는 문장을 선별하고 각각에 대해 지정된 스키마의 JSON을 생성하라.

## 판정 기준

- 평가 기준은 "전화 회화의 구어체"다. 문어체 기준으로 고치지 마라.
  축약형, 캐주얼한 표현, 단순한 문장 구조는 문제가 아니다.
- 원어민이 전화로 듣고 눈치채지 못할 문장은 제외하라.
  "더 세련되게" 만들 수 있다는 이유로 포함하지 마라.
- 화자가 스스로 고쳐 말한(self-correction) 문장은 제외하라.
- **★ 튜터의 실시간 교정을 최우선으로 채굴하라.** 튜터가 학습자의 문장을 다시 말해주거나
  바꿔 말한 구간이 있으면(예: 학습자 "it goes to watch YouTube clips" 직후 튜터
  "You end up watching YouTube clips"), 그것은 원어민이 직접 준 정답이다.
  이런 항목은 priority를 high로 하고, natural_sentence에 **튜터가 말한 표현을 그대로 쓴다.**
  네가 새로 지어내지 마라. explanation_ko에 "튜터가 바로 고쳐준 표현"임을 밝혀라.
- 문법은 맞지만 어색한 문장을 반드시 검토하라: 원어민이 같은 상황에서
  실제로 어떻게 말할지 생각하고, 다르면 issue_type을 적절히 분류하라.
  특히 한국어 직역 표현(예: "I have a promise", "play with friends",
  "my condition is not good")을 주의 깊게 찾아라. 이 경우 issue_type은 konglish.
- corrected_sentence는 문법만 고친 것, natural_sentence는 원어민이 실제로
  말할 법한 것이다. 문법이 문제면 둘이 다를 수 있고, 어색함이 문제면
  corrected_sentence와 original_sentence가 같을 수 있다.
- 문장이 STT 오인식으로 보이면(문맥상 말이 안 되거나 고유명사가 깨진 경우)
  교정하지 말고 stt_suspect를 true로 표시하라.
- 기존 약점 패턴 목록에 있는 유형이 재등장하면 priority를 한 단계 올려라.
- 전사본은 자동 화자분리 결과라 문장 앞뒤에 상대 화자의 단어가 한두 개 섞여 있을 수 있다.
  문장 맨 앞이나 맨 끝의 조각(예: 문두의 "?"나 어미만 남은 파편)은 학습자의 오류로
  판정하지 마라. 문장 전체가 학습자 발화로 보이는 것만 분석하라.
- 확신이 서지 않으면 포함하지 말고 버려라. 적게 뽑는 것이 낫다.
- 발견한 모든 가치 있는 항목을 출력하라. 개수 제한은 시스템이 처리한다.

## priority 기준

- high: 같은 유형 오류가 이 수업에서 2회 이상 반복됨 / 의미 전달을 방해함
- medium: 문법은 맞지만 원어민이 쓰지 않는 표현
- low: 사소한 관사·전치사 등 일회성 실수

## 제외 대상

- 과도하지 않은 filler (um, uh 등)
- self-correction한 발화
- 고유명사 STT 오류로 보이는 것 (stt_suspect로만 표시)

## explanation_ko 작성 규칙

- 규칙 나열이 아니라 "왜 어색하게 들리는지 + 원어민의 사고방식"을 설명하라.
- 최대 2문장.
- 예: "약속에 have를 쓰면 물건을 소유한 것처럼 들려요. 원어민은 일정 개념으로 생각해서 I have plans라고 말해요."

## lesson_summary_ko

수업에서 다룬 주제와 학습자의 전반적 경향을 3문장 이내로 요약하라.
```

## 완료 기준

- [ ] 실제 02_transcript.json + 03_speakers.json으로 실행 시 04_analysis.json 생성, 스키마 검증 통과
- [ ] items의 모든 issue_type/priority가 enum 값 안에 있음
- [ ] analyzable=false / UNKNOWN turn이 프롬프트 텍스트에서 제외됨 (로그 확인)
- [ ] 튜터가 교정해준 표현이 transcript에 있을 때 high priority 항목으로 잡히는지 확인
- [ ] ollama provider로도 (품질은 낮아도) 파싱 가능한 JSON이 나옴
- [ ] 재실행 시 API 호출 없이 스킵 (비용 확인!)
- [ ] 외부 의존: anthropic, openai, ollama (provider별 lazy import — 안 쓰는 SDK 미설치여도 동작)
