"""S4: LLM 분석 → work/{hash}/04_analysis.json. v1.3 계약.

STUDENT(=my_speaker) 발화만 분석해 복습 가치 있는 문장을 뽑는다.
- analyzable==false / speaker==UNKNOWN turn은 프롬프트에서 제외(경계 파편 오염 방지).
- provider 분기: claude_cli | gemini | anthropic | openai | llama_cpp (각 SDK는 lazy import).
- 런타임 분석 프롬프트(_PROMPT)는 설계 완료본이라 한 글자도 바꾸지 않는다.

외부 의존: 선택한 provider의 SDK만 (google-genai / anthropic / openai). llama_cpp·claude_cli 는 SDK 없음.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from util import PIPELINE_VERSION, resolve, stage_output

_ISSUE_TYPES = ["tense", "article", "preposition", "word_order", "vocabulary", "konglish", "fluency"]
# S12: 중국어 전용 enum. word_order/vocabulary/fluency는 en과 공유(통계 조회 호환).
_ISSUE_TYPES_ZH = ["word_order", "measure_word", "vocabulary", "grammar", "expression", "fluency"]
_PRIORITIES = ["high", "medium", "low"]
_CARD_TYPES = ["correction", "upgrade"]  # S10: correction=최소수정 / upgrade=더 자연스러운 대안
_MAX_TRANSCRIPT_CHARS = 30000

# LLM이 채워야 할 출력 스키마 (S10 카드 contract). 코드가 04 필드명으로 매핑한다.
# 관계: original → (card_type) → target → korean(=target 번역) / explanation(=original→target 변화).
_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "original": {"type": "string"},
        "card_type": {"type": "string", "enum": _CARD_TYPES},
        "target": {"type": "string"},
        "korean": {"type": "string"},
        "explanation": {"type": "string"},
        "issue_type": {"type": "string", "enum": _ISSUE_TYPES},
        "priority": {"type": "string", "enum": _PRIORITIES},
        "practice_pattern": {"type": "string"},
        "pattern_examples": {"type": "array", "items": {"type": "string"}},
        "stt_suspect": {"type": "boolean"},
        "timestamp": {"type": "string"},
    },
    "required": [
        "original", "card_type", "target", "korean", "explanation",
        "issue_type", "priority", "practice_pattern", "pattern_examples",
        "stt_suspect", "timestamp",
    ],
}
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "lesson_summary_ko": {"type": "string"},
        "items": {"type": "array", "items": _ITEM_SCHEMA},
    },
    "required": ["lesson_summary_ko", "items"],
}


def _response_schema_for(language: str) -> dict:
    """en은 동결 스키마 그대로, zh는 pinyin 필수 + zh enum으로 파생(원본 불변)."""
    if language != "zh":
        return _RESPONSE_SCHEMA
    item = json.loads(json.dumps(_ITEM_SCHEMA))  # deep copy
    item["properties"]["pinyin"] = {"type": "string"}
    item["properties"]["issue_type"] = {"type": "string", "enum": _ISSUE_TYPES_ZH}
    item["required"] = item["required"] + ["pinyin"]
    return {
        "type": "object",
        "properties": {
            "lesson_summary_ko": {"type": "string"},
            "items": {"type": "array", "items": item},
        },
        "required": ["lesson_summary_ko", "items"],
    }

# ── 런타임 분석 프롬프트 (설계 완료본 — 수정 금지, 그대로 복사) ───────────────────
_PROMPT = """당신은 한국인 영어 학습자를 위한 회화 코치다. 아래는 전화영어 수업의 전사본이다.
STUDENT는 한국인 학습자, TUTOR는 원어민 튜터다.

<transcript>
{transcript_text}
</transcript>

이 학습자의 기존 약점 패턴 (누적 횟수순): {weak_patterns_text}

STUDENT의 발화만 분석하여, 복습 가치가 있는 문장을 선별하고 각각에 대해 지정된 스키마의 JSON을 생성하라.

## 분석 범위

- 전체 대화가 주어진다. 맥락 판단에 전부 사용하라.
- 단, **카드로 뽑을 수 있는 것은 [분석대상] 표시가 있는 STUDENT 발화뿐이다.**
  표시 없는 STUDENT 발화는 화자분리 파편일 수 있으므로 대상으로 삼지 마라.
- **TUTOR 발화는 길이와 무관하게 전부 읽어라.** 튜터의 교정은 대개 짧다
  (예: "You end up watching YouTube clips"). 그 짧은 한 줄이 이 수업에서 가장
  값진 정답이므로 놓치지 마라.

## 카드 생성 규칙 (반드시 이 순서와 관계를 지켜라)

카드 하나는 아래 다섯 필드가 서로 정확히 대응해야 한다. **관계가 깨지는 카드는 아예 만들지 마라.**

1. **original** — STUDENT가 [분석대상] 발화에서 **실제로 말한 문장을 그대로** 쓴다.
   새로 지어내거나 문법을 고쳐 적지 마라. STT 기호/공백 정리 정도만 허용한다.

2. **card_type** — 다음 둘 중 하나로 정한다.
   - `correction`: original에 명확한 문법 오류나 부자연스러운 부분이 있어 고쳐야 할 때.
   - `upgrade`: original이 틀린 것은 아니지만, 원어민이 더 자연스럽게 말하는 대안을 배울 가치가 있을 때.

3. **target** — 학습자가 다음에 말해야 할 영어 문장. **card_type에 따라 다르게 만든다.**
   - `correction`이면 **최소 수정(minimal edit)**: original의 의미와 문장 구조를 최대한 그대로 두고,
     문제가 된 부분(전치사/시제/관사/어순/표현)만 고쳐라. 통째로 다른 멋진 문장으로 rewrite하지 마라.
     예) original "I look back myself sometimes" → target "I look back on myself sometimes" (O)
        / "Sometimes I reflect on myself" (X — 이건 correction이 아니라 upgrade다)
   - `upgrade`이면: 같은 의미를 원어민이 말할 법한 표현으로 바꿔라. 단 의미는 실질적으로 같아야 한다.
   - **튜터가 이미 고쳐준 표현이 있으면 그것을 target에 그대로 쓴다**(네가 새로 짓지 마라). 대개 최고의 정답이다.

4. **korean** — **target 전체 문장의 의미를 한국어로 번역**한다.
   korean과 target은 1:1로 대응해야 한다. target에 없는 내용을 넣지 말고, target보다 많은 의미를 담지 마라.
   (사용자가 korean만 보고 target을 그대로 말할 수 있어야 한다.)

5. **explanation** — **original에서 target으로 실제로 바뀐 부분만** 한국어로 설명한다(최대 2문장).
   - **target에 반영되지 않은 교정은 절대 언급하지 마라.** (예: target이 reflect on인데 look back on을 설명하면 안 됨)
   - `correction`이면 무엇이 왜 틀렸고 어떻게 고쳤는지. `upgrade`이면 "틀린 게 아니라 더 자연스러운 대안"임을 밝혀라.
   - 규칙 나열이 아니라 "왜 그렇게 말하는지 + 원어민의 사고방식"으로 써라.

## issue_type

target에서 실제로 다뤄진 학습 포인트로 분류하라: tense|article|preposition|word_order|vocabulary|konglish|fluency.
특히 한국어 직역(예: "I have a promise", "play with friends", "my condition is not good")은 konglish다.

## priority

- high: 같은 유형 오류가 이 수업에서 2회 이상 반복 / 의미 전달을 방해 / 튜터가 직접 고쳐준 표현.
- medium: 문법은 맞지만 원어민이 잘 쓰지 않는 표현(주로 upgrade).
- low: 사소한 관사·전치사 등 일회성 실수.
- 기존 약점 패턴 목록에 있는 유형이 재등장하면 priority를 한 단계 올려라.

## practice_pattern / pattern_examples

target의 핵심 패턴을 짧게(practice_pattern) 적고, 그 패턴을 쓰는 다른 예문 2개(pattern_examples)를 든다.

## 제외/판정 원칙

- 평가 기준은 "전화 회화의 구어체"다. 문어체로 고치지 마라. 축약형·캐주얼·단순한 구조는 문제가 아니다.
- 원어민이 전화로 듣고 눈치채지 못할 문장은 제외하라. "더 세련되게" 만들 수 있다는 이유만으로 뽑지 마라.
- STUDENT가 스스로 고쳐 말한(self-correction) 발화는 제외하라.
- STT 오인식으로 보이면(문맥상 말이 안 되거나 고유명사가 깨짐) 교정하지 말고 stt_suspect=true로만 표시하라.
- 문장 맨 앞/끝에 상대 화자 단어 조각이 섞인 경우, 그 조각을 오류로 판정하지 마라. 문장 전체가 학습자 발화인 것만 대상으로 하라.
- 확신이 서지 않으면 포함하지 말고 버려라. 적게 뽑는 것이 낫다.

## lesson_summary_ko

수업에서 다룬 주제와 학습자의 전반적 경향을 3문장 이내로 요약하라."""
# ── 런타임 분석 프롬프트 끝 ───────────────────────────────────────────────────

# ── S12: 중국어 수업용 프롬프트 (영어 프롬프트의 구조를 유지, zh 특성 반영) ──────
_PROMPT_ZH = """당신은 한국인 중국어 학습자를 위한 회화 코치다. 아래는 전화중국어 수업의 전사본이다.
STUDENT는 한국인 학습자, TUTOR는 중국어 튜터다.

<transcript>
{transcript_text}
</transcript>

이 학습자의 기존 약점 패턴 (누적 횟수순): {weak_patterns_text}

STUDENT의 발화만 분석하여, 복습 가치가 있는 문장을 선별하고 각각에 대해 지정된 스키마의 JSON을 생성하라.

## 전사 특성 (판정 전에 반드시 숙지)

- 이 전사는 **글자 단위 정렬**이라, 인접 턴 경계에서 앞 화자의 마지막 1~3글자가 다음 화자 턴
  앞에 붙거나 그 반대일 수 있다 (예: "在公" / "司工作有点忙"으로 쪼개짐).
  **경계의 글자 조각을 문법 오류로 판정하지 마라.** 문장 전체가 학습자 발화인 것만 대상으로 하라.
- 중국어 수업은 튜터가 학습자의 말을 **살짝 고쳐서 되풀이하는(echo) 교정**이 많다.
  STUDENT 발화를 TUTOR가 몇 글자만 바꿔 반복했다면 그것이 실시간 교정이다. 놓치지 마라.

## 분석 범위

- 전체 대화가 주어진다. 맥락 판단에 전부 사용하라.
- 단, **카드로 뽑을 수 있는 것은 [분석대상] 표시가 있는 STUDENT 발화뿐이다.**
  표시 없는 STUDENT 발화는 화자분리 파편일 수 있으므로 대상으로 삼지 마라.
- **TUTOR 발화는 길이와 무관하게 전부 읽어라.** 튜터의 교정은 대개 짧다.
  그 짧은 한 줄이 이 수업에서 가장 값진 정답이므로 놓치지 마라.

## 카드 생성 규칙 (반드시 이 순서와 관계를 지켜라)

카드 하나는 아래 여섯 필드가 서로 정확히 대응해야 한다. **관계가 깨지는 카드는 아예 만들지 마라.**

1. **original** — STUDENT가 [분석대상] 발화에서 **실제로 말한 문장을 그대로** 쓴다(간체).
   새로 지어내거나 문법을 고쳐 적지 마라. STT 기호/공백 정리 정도만 허용한다.

2. **card_type** — 다음 둘 중 하나로 정한다.
   - `correction`: original에 명확한 문법 오류나 부자연스러운 부분이 있어 고쳐야 할 때.
   - `upgrade`: original이 틀린 것은 아니지만, 원어민이 더 자연스럽게 말하는 대안을 배울 가치가 있을 때.

3. **target** — 학습자가 다음에 말해야 할 중국어 문장(간체). **card_type에 따라 다르게 만든다.**
   - `correction`이면 **최소 수정(minimal edit)**: original의 의미와 문장 구조를 최대한 그대로 두고,
     문제가 된 부분(어순/양사/了·过·着/보어/표현)만 고쳐라. 통째로 다른 멋진 문장으로 rewrite하지 마라.
   - `upgrade`이면: 같은 의미를 원어민이 말할 법한 표현으로 바꿔라. 단 의미는 실질적으로 같아야 한다.
   - **튜터가 이미 고쳐준 표현이 있으면 그것을 target에 그대로 쓴다**(네가 새로 짓지 마라). 대개 최고의 정답이다.

4. **pinyin** — **target 전체의 한어병음**을 성조 부호 포함(nǐ hǎo 방식), 단어 단위 띄어쓰기로 쓴다.
   pinyin과 target은 1:1로 대응해야 한다. target에 없는 글자의 병음을 넣지 마라.

5. **korean** — **target 전체 문장의 의미를 한국어로 번역**한다.
   korean과 target은 1:1로 대응해야 한다. target에 없는 내용을 넣지 말고, target보다 많은 의미를 담지 마라.
   (사용자가 korean만 보고 target을 그대로 말할 수 있어야 한다.)

6. **explanation** — **original에서 target으로 실제로 바뀐 부분만** 한국어로 설명한다(최대 2문장).
   - **target에 반영되지 않은 교정은 절대 언급하지 마라.**
   - `correction`이면 무엇이 왜 틀렸고 어떻게 고쳤는지. `upgrade`이면 "틀린 게 아니라 더 자연스러운 대안"임을 밝혀라.
   - 규칙 나열이 아니라 "왜 그렇게 말하는지 + 원어민의 사고방식"으로 써라.

## issue_type

target에서 실제로 다뤄진 학습 포인트로 분류하라: word_order|measure_word|vocabulary|grammar|expression|fluency.
- word_order: 어순 (시간사/장소어 위치, 부사 위치 등)
- measure_word: 양사 (个/件/条 등의 누락·오용)
- grammar: 了/过/着, 보어, 把/被, 비교문 등 문법 구조
- expression: 한국어식 직역 등 문법은 맞지만 원어민이 쓰지 않는 표현
- vocabulary: 단어 선택 오류 / fluency: 더듬음·불필요한 반복이 의미 전달을 방해

## priority

- high: 같은 유형 오류가 이 수업에서 2회 이상 반복 / 의미 전달을 방해 / 튜터가 직접 고쳐준 표현.
- medium: 문법은 맞지만 원어민이 잘 쓰지 않는 표현(주로 upgrade).
- low: 사소한 양사·조사 등 일회성 실수.
- 기존 약점 패턴 목록에 있는 유형이 재등장하면 priority를 한 단계 올려라.

## practice_pattern / pattern_examples

target의 핵심 패턴을 짧게(practice_pattern) 적고, 그 패턴을 쓰는 다른 예문 2개(pattern_examples)를 간체로 든다.

## 제외/판정 원칙

- 평가 기준은 "전화 회화의 구어체"다. 문어체로 고치지 마라. 축약·캐주얼·단순한 구조는 문제가 아니다.
- 원어민이 전화로 듣고 눈치채지 못할 문장은 제외하라. "더 세련되게" 만들 수 있다는 이유만으로 뽑지 마라.
- STUDENT가 스스로 고쳐 말한(self-correction) 발화는 제외하라.
- STT 오인식으로 보이면(문맥상 말이 안 되거나 동음자 혼동으로 보임) 교정하지 말고 stt_suspect=true로만 표시하라.
- 문장 맨 앞/끝에 상대 화자 글자 조각이 섞인 경우, 그 조각을 오류로 판정하지 마라.
- 확신이 서지 않으면 포함하지 말고 버려라. 적게 뽑는 것이 낫다.

## lesson_summary_ko

수업에서 다룬 주제와 학습자의 전반적 경향을 한국어 3문장 이내로 요약하라."""
# ── 중국어 프롬프트 끝 ────────────────────────────────────────────────────────


def _mmss(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def _extract_lesson_date(source_filename: str) -> str:
    """source_filename에서 YYMMDD(삼성 통화녹음 패턴) 추출 → YYYY-MM-DD. 실패 시 오늘."""
    for yy, mm, dd in re.findall(r"(\d{2})(\d{2})(\d{2})", source_filename or ""):
        if 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31:
            return f"20{yy}-{mm}-{dd}"
    return _dt.date.today().isoformat()


def _weak_patterns_text(weak_patterns: list[dict]) -> str:
    if not weak_patterns:
        return "(없음)"
    return ", ".join(f"{p['issue_type']}({p['total_count']}회)" for p in weak_patterns)


def _build_transcript_text(turns: list[dict], my_speaker: str,
                           language: str = "en") -> tuple[str, int, int]:
    """전체 대화(UNKNOWN 제외)를 [MM:SS] STUDENT/TUTOR 라인으로 넣는다.

    필터는 '무엇을 보여줄지'가 아니라 '무엇을 오류로 판정할지'에만 적용한다(v1.4, D14 준수).
    analyzable STUDENT 발화에만 [분석대상] 표시를 달고, TUTOR 발화는 길이와 무관하게 전부 넣는다
    (튜터의 짧은 실시간 교정이 가장 값진 정답이므로 — D21). UNKNOWN turn만 제외.
    반환: (텍스트, 제외한 UNKNOWN 수, 분석대상 수).
    """
    lines: list[str] = []
    n_unknown = 0
    n_target = 0
    for t in turns:
        if t.get("speaker") == "UNKNOWN":
            n_unknown += 1
            continue
        role = "STUDENT" if t["speaker"] == my_speaker else "TUTOR"
        is_target = role == "STUDENT" and bool(t.get("analyzable"))
        if is_target:
            n_target += 1
        mark = " [분석대상]" if is_target else ""
        text = t["text"].strip()
        if language == "zh":
            # zh 전사는 글자가 공백으로 연결돼 있다(글자 단위 정렬) → 붙여써야 자연스러운 중국어.
            text = text.replace(" ", "")
        lines.append(f"[{_mmss(t['start'])}] {role}{mark}: {text}")
    return "\n".join(lines), n_unknown, n_target


# ── provider 분기: 각각 (parsed_dict | None, raw_text) 반환 ────────────────────

def _call_gemini(prompt: str, cfg: dict, schema: dict) -> tuple[dict | None, str]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=resolve(cfg, "gemini_api_key"))
    resp = client.models.generate_content(
        model=cfg["llm_model"],
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    raw = resp.text or ""
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError:
        return None, raw


def _call_anthropic(prompt: str, cfg: dict, schema: dict) -> tuple[dict | None, str]:
    import anthropic

    client = anthropic.Anthropic(api_key=resolve(cfg, "anthropic_api_key"))
    tool = {
        "name": "emit_analysis",
        "description": "분석 결과를 지정된 스키마로 반환한다.",
        "input_schema": schema,
    }
    msg = client.messages.create(
        model=cfg["llm_model"],
        max_tokens=8192,
        temperature=0.2,
        tools=[tool],
        tool_choice={"type": "tool", "name": "emit_analysis"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input), json.dumps(block.input, ensure_ascii=False)
    return None, str(msg.content)


def _call_openai(prompt: str, cfg: dict, schema: dict) -> tuple[dict | None, str]:
    import openai

    client = openai.OpenAI(api_key=resolve(cfg, "openai_api_key"))
    resp = client.chat.completions.create(
        model=cfg["llm_model"],
        temperature=0.2,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "analysis", "schema": schema, "strict": True},
        },
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.choices[0].message.content or ""
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError:
        return None, raw


# ── llama_cpp: 로컬 LLM (llama-server, 2026-11-17) ──────────────────────────────
# 집 PC에서 API 키·구독 없이 돌리는 경로. `llama-server -m 모델.gguf` 를 먼저 띄워 두면 된다.
# - llama-server 의 OpenAI 호환 주소(/v1/chat/completions)를 표준 라이브러리로 호출한다(SDK 의존 없음).
# - response_format=json_schema 를 주면 llama.cpp 가 스키마를 문법(grammar)으로 강제한다 →
#   작은 모델도 스키마를 벗어난 출력을 낼 수 없다. 예전 ollama 분기(format="json", 스키마 미강제)는
#   한 번도 실전 검증된 적이 없어 이 분기로 교체했다.
_LLAMA_CPP_DEFAULT_URL = "http://127.0.0.1:8080"

# 자동 기동(llama_server_cmd): 전사(WhisperX)와 로컬 LLM 은 8GB VRAM 을 같이 못 쓴다(모델만 6.6GB 실측).
# 그래서 서버를 상시로 띄워 두지 않고, 분석이 필요한 순간에 띄웠다가 run() 이 끝나면 내린다.
# 이미 떠 있는 서버(사용자가 직접 띄운 것)는 건드리지 않는다.
_LLAMA_STARTED: list[subprocess.Popen] = []


def _llama_healthy(base: str) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(base + "/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _ensure_llama_server(cfg: dict, base: str) -> None:
    cmd = cfg.get("llama_server_cmd")
    if not cmd or _llama_healthy(base):
        return
    import shlex
    log_path = Path(tempfile.gettempdir()) / "sounds-good-llama-server.log"
    print(f"[analyze] 로컬 모델 서버를 띄웁니다(첫 실행은 모델 다운로드로 오래 걸립니다). 로그: {log_path}")
    proc = subprocess.Popen(shlex.split(cmd), stdout=log_path.open("ab"), stderr=subprocess.STDOUT)
    _LLAMA_STARTED.append(proc)
    deadline = time.time() + int(cfg.get("llama_server_wait", 1800))
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"[analyze] llama-server 가 바로 종료됐습니다(exit={proc.returncode}). 로그: {log_path}")
        if _llama_healthy(base):
            return
        time.sleep(2)
    raise RuntimeError(f"[analyze] llama-server 가 제한 시간 안에 준비되지 않았습니다. 로그: {log_path}")


def _stop_llama_servers() -> None:
    while _LLAMA_STARTED:
        proc = _LLAMA_STARTED.pop()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
            print("[analyze] 로컬 모델 서버를 내렸습니다(GPU 메모리 반환).")


def _call_llama_cpp(prompt: str, cfg: dict, schema: dict) -> tuple[dict | None, str]:
    import urllib.error
    import urllib.request

    base = str(cfg.get("llm_base_url") or _LLAMA_CPP_DEFAULT_URL).rstrip("/")
    _ensure_llama_server(cfg, base)
    body = {
        "model": cfg.get("llm_model") or "local",   # llama-server 는 띄운 모델 하나만 쓰므로 표시용
        "temperature": 0.2,
        "max_tokens": int(cfg.get("llm_max_tokens", 4096)),
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "analysis", "schema": schema, "strict": True}},
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=int(cfg.get("llm_timeout", 900))) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # "HTTP 503" 꼴로 남겨 _is_transient 가 모델 로딩 중(503)을 재시도하게 한다.
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"[analyze] llama.cpp 서버 오류 HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"[analyze] llama.cpp 서버에 연결하지 못했습니다({base}): {e.reason} — "
            f"llama-server 를 먼저 띄우세요 (README의 '로컬 LLM' 참고)") from e
    choice = (d.get("choices") or [{}])[0]
    raw = (choice.get("message") or {}).get("content") or ""
    if choice.get("finish_reason") == "length":
        print("[analyze] 경고: 로컬 모델 출력이 max_tokens 에서 잘렸습니다 (llm_max_tokens 또는 서버 -c 확인)")
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError:
        return None, raw


# ── claude_cli: Claude 구독으로 분석 (claude -p, 2026-09-29) ─────────────────────
# API 키·별도 결제 없이 Claude Code 로그인(구독)을 쓴다. 사용량은 Claude Code와 같은 한도에서 차감.
# 실측으로 정한 격리 옵션 — 빼면 문제가 생긴다:
#   --strict-mcp-config + 빈 --mcp-config : 사용자 MCP 서버 정의가 매 호출 ~33K 토큰을 먹었다(34,062→1,023)
#   --setting-sources ""                  : 사용자 hooks/플러그인이 분석 호출에 끼지 않게
#   --tools "" / --disable-slash-commands : 분석만 하면 된다. 도구·스킬 불필요
#   --no-session-persistence              : 호출마다 세션 기록이 쌓이지 않게
#   프로젝트 밖 고정 cwd                   : CLAUDE.md/AGENTS.md 자동 탐색이 끼지 않게
#   --bare 는 쓰면 안 된다                 : bare 모드는 OAuth(구독 로그인)를 아예 읽지 않는다
_CLAUDE_CLI_SYSTEM = ("You are an analysis engine. Return the result strictly in the requested JSON schema. "
                      "Do not use tools. Write all Korean fields in natural Korean.")


def _claude_cli_path(cfg: dict) -> str:
    """claude 실행 파일 위치. systemd 서비스는 셸의 nvm PATH를 물려받지 못해 직접 찾는다."""
    if cfg.get("claude_cli_path"):
        return os.path.expanduser(cfg["claude_cli_path"])
    found = shutil.which("claude")
    if found:
        return found
    cands = sorted(Path.home().glob(".nvm/versions/node/*/bin/claude"))
    if cands:
        return str(cands[-1])
    raise RuntimeError("[analyze] claude CLI를 찾지 못했습니다 — config.yaml에 claude_cli_path를 지정하세요")


def _call_claude_cli(prompt: str, cfg: dict, schema: dict) -> tuple[dict | None, str]:
    # API 키가 환경에 있으면 CLI가 구독 대신 API 과금으로 간다 → 자식 프로세스에서 제거한다.
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")}
    cmd = [_claude_cli_path(cfg), "-p",
           "--model", cfg["llm_model"],
           "--output-format", "json",
           "--no-session-persistence",
           "--tools", "", "--disable-slash-commands",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--setting-sources", "",
           "--system-prompt", _CLAUDE_CLI_SYSTEM,
           "--json-schema", json.dumps(schema, ensure_ascii=False)]
    cwd = Path(tempfile.gettempdir()) / "sounds-good-claude-cli"
    cwd.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=str(cwd), env=env,
                       timeout=int(cfg.get("claude_cli_timeout", 600)))
    raw = r.stdout or ""
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"[analyze] claude CLI 비정상 종료(exit={r.returncode}): "
                           f"{(r.stderr or raw).strip()[:300]}")
    if d.get("is_error"):
        # api_error_status(429/529 등)를 메시지에 넣어 _is_transient/_error_message가 판별하게 한다.
        raise RuntimeError(f"[analyze] claude CLI 오류 status={d.get('api_error_status')} "
                           f"{d.get('subtype')}: {str(d.get('result'))[:300]}")
    so = d.get("structured_output")
    if isinstance(so, dict):
        return so, json.dumps(so, ensure_ascii=False)
    try:
        return json.loads(d.get("result") or ""), raw
    except json.JSONDecodeError:
        return None, raw


_PROVIDERS = {
    "gemini": _call_gemini,
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "llama_cpp": _call_llama_cpp,
    "claude_cli": _call_claude_cli,
}


# 서버 쪽 일시적 장애로 보이는 신호들. 이게 뜨면 잠시 뒤 재시도하면 대개 된다.
# (실측 2026-09-11: Gemini가 503 UNAVAILABLE "high demand"를 반복해 실행이 통째로 실패했다.)
_TRANSIENT_MARKS = ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL", "DEADLINE_EXCEEDED",
                    "high demand", "overloaded", "try again", "temporarily",
                    "timed out", "timeout", "connection reset", "connection aborted",
                    " 429", " 500", " 502", " 503", " 504", " 529",
                    # claude_cli는 "status=NNN" 꼴로 던진다(앞 공백 마크와 안 맞아 529 과부하가
                    # 재시도 없이 곧장 폴백으로 갔다, 2026-11-07). 429는 일부러 뺀다 — 구독 한도라 즉시 폴백.
                    "status=500", "status=502", "status=503", "status=504", "status=529")
_BACKOFF_SEC = (4, 10, 25)  # 재시도 간격. 총 4회 시도까지.


def _is_transient(e: Exception) -> bool:
    """재시도할 가치가 있는 오류인가. 인증/키 오류처럼 재시도해도 소용없는 건 제외한다."""
    # claude -p 타임아웃(기본 600초)을 재시도하면 최대 4×600초 = 40분 동안 UI가 묶인다.
    # 긴 전사 때문에 걸린 타임아웃은 다시 돌려도 또 걸리므로 곧장 다음 모델로 넘긴다.
    if isinstance(e, subprocess.TimeoutExpired):
        return False
    msg = f"{type(e).__name__}: {e}"
    if any(k in msg.upper() for k in ("API_KEY", "UNAUTHENTICATED", "PERMISSION_DENIED", "INVALID_ARGUMENT")):
        return False
    # Claude 구독 한도·Gemini 일일 할당량: 초 단위 재시도로는 안 풀린다 → 곧장 폴백으로.
    if any(k in msg.lower() for k in ("usage limit", "hit your limit", "limit reached", "resource_exhausted")):
        return False
    return any(m.lower() in msg.lower() for m in _TRANSIENT_MARKS)


def _call_one(prompt: str, cfg: dict, work_dir: Path, audio_hash: str,
              schema: dict) -> tuple[dict, str]:
    """provider 하나로 호출. 반환: (파싱된 dict, 원응답 문자열).

    일시적 서버 오류(503 등)는 백오프 재시도한다 — 예전에는 예외가 그대로 올라가서
    수업 하나가 통째로 날아갔다. 인증 오류처럼 재시도가 무의미한 건 즉시 올린다.
    """
    provider = cfg["llm_provider"]
    if provider not in _PROVIDERS:
        raise ValueError(f"[analyze] 알 수 없는 llm_provider: {provider} (가능: {list(_PROVIDERS)})")
    caller = _PROVIDERS[provider]

    raw = ""
    parse_fails = 0
    max_attempts = len(_BACKOFF_SEC) + 1
    for attempt in range(1, max_attempts + 1):
        try:
            data, raw = caller(prompt, cfg, schema)
        except Exception as e:
            if attempt < max_attempts and _is_transient(e):
                wait = _BACKOFF_SEC[attempt - 1]
                print(f"[analyze] 일시적 오류 — {wait}초 뒤 재시도 "
                      f"({attempt}/{max_attempts}): {str(e)[:120]}")
                time.sleep(wait)
                continue
            raise
        if data is not None:
            return data, raw
        parse_fails += 1
        print(f"[analyze] 경고: 응답 JSON 파싱 실패 (시도 {attempt}/{max_attempts})")
        if parse_fails >= 2:   # 파싱 실패는 2회까지만 (같은 요청을 계속 보내도 소용없다)
            break

    raw_path = stage_output(work_dir, audio_hash, "04_raw_response.txt")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw, encoding="utf-8")
    raise RuntimeError(f"[analyze] LLM 응답을 JSON으로 파싱하지 못했습니다. 원본: {raw_path}")


# 이번 분석에서 각 호출을 실제로 처리한 provider/model (04_analysis.json의 served_by로 남는다).
_SERVED: list[str] = []


def _call_llm(prompt: str, cfg: dict, work_dir: Path, audio_hash: str,
              schema: dict) -> tuple[dict, str]:
    """기본 provider → 실패하면 llm_fallbacks를 순서대로 시도한다(2026-09-29).

    기본은 Claude 구독(claude -p), 한도·장애 시 Gemini로 넘어간다. Gemini 무료 등급은
    모델별로 하루 20회가 따로라 모델을 여러 개 걸어두면 사실상 막히지 않는다.
    어떤 실패든 넘어간다 — 목표는 '카드가 만들어지는 것'이고, 원인은 로그와 served_by에 남는다.
    """
    chain = [(cfg["llm_provider"], cfg["llm_model"])]
    chain += [(f["provider"], f["model"]) for f in (cfg.get("llm_fallbacks") or [])]
    last_err: Exception | None = None
    for i, (provider, model) in enumerate(chain):
        c = {**cfg, "llm_provider": provider, "llm_model": model}
        try:
            data, raw = _call_one(prompt, c, work_dir, audio_hash, schema)
            _SERVED.append(f"{provider}/{model}")
            if i > 0:
                print(f"[analyze] ⚠️ 대체 모델로 처리됨: {provider}/{model}")
            return data, raw
        except Exception as e:
            last_err = e
            if i + 1 < len(chain):
                nxt = chain[i + 1]
                print(f"[analyze] ⚠️ {provider}/{model} 실패 → {nxt[0]}/{nxt[1]}로 대체: {str(e)[:140]}")
    if len(chain) > 1:
        tried = " → ".join(f"{pv}/{m}" for pv, m in chain)
        raise RuntimeError(f"[analyze] 모든 모델 실패 ({tried}). 마지막 오류: {last_err}") from last_err
    raise last_err


# ── S10 Stage 4: 카드 배치 검증/수리 ─────────────────────────────────────────
# 호출 예산: 검증 batch 1회 + (실패분 있을 때만) 수리 batch 1회. 무한 retry 금지.
# 어떤 오류도 카드 생성을 막지 않는다(검증 실패 시 생성 카드 그대로 진행).
# 외부 리뷰(2026-08-21)로 "필수"로 격상: 여러 군데 고친 카드의 설명이 일부 변경만 다루는
# 문제는 결정적 검사로 잡을 수 없어 LLM 검증(특히 B)이 필요하다.

_VALIDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "index": {"type": "integer"},
                "verdict": {"type": "string", "enum": ["pass", "fail", "stt_risky"]},
                "failed_checks": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            },
            "required": ["index", "verdict", "failed_checks", "reason"],
        }},
    },
    "required": ["results"],
}

_VALIDATION_PROMPT = """당신은 외국어 학습 카드의 품질 검수자다. 아래는 {lang_label} 회화 수업에서 뽑힌 복습 카드들이다.

필드 계약: original(학습자가 실제로 말한 문장) → card_type(correction=오류 최소수정 | upgrade=자연스러운 대안)
→ target(다음에 말해야 할 문장) → korean(target의 1:1 한국어 번역) → explanation(original→target 변화 설명).

<cards>
{cards_text}
</cards>

각 카드를 다음 항목으로 검사하라. 하나라도 위반이면 verdict=fail이다.
- A. korean이 target 전체 의미와 1:1로 일치하는가.
- B. explanation이 original→target의 **모든 변경점**을 다루는가. 변경이 여러 개인데 일부만
  설명하면 위반이다. 반대로 target에 반영되지 않은 변경을 설명해도 위반이다.
- C. correction인데 target이 최소수정을 넘어 문장을 통째로 rewrite하지 않았는가.
- D. upgrade인데 문법 오류를 고친 것처럼 설명하지 않는가.
- E. original에 실제로 없는 오류를 지어내(hallucinate) 고치지 않았는가.
- F. 사용자가 korean만 보고 target을 말하는 연습이 성립하는가.
- G. original이 STT(음성인식) 오류로 깨져 보여 카드로 쓰기 위험한가.
  ("오류 확정"이 아니라 "전사 신뢰도가 낮아 위험"이면 충분) → 이 경우 verdict=stt_risky.{check_h}

pass가 기본이다. 사소한 표현 취향 차이로 fail을 주지 마라 — 계약 위반만 잡아라.
fail이면 failed_checks에 위반 항목 문자(A~{last_check})를, reason에 근거를 한 문장으로 써라."""

_REPAIR_PROMPT = """당신은 {lang_label} 회화 학습 카드를 고치는 코치다. 아래 카드들이 품질 검수에서 실패했다.
각 카드에 대해 **지적된 문제만** 고쳐 카드를 다시 써라.

필드 계약: original(수정 절대 금지) → card_type(correction=최소수정 | upgrade=자연스러운 대안)
→ target → korean(target의 1:1 번역) → explanation(original→target의 모든 변경점만 설명, 최대 2문장).

- B 위반이면: explanation이 target의 모든 변경점을 다루게 고쳐라. 변경점이 너무 많아 두 문장으로
  설명이 안 되면 target을 더 보수적인 최소수정으로 줄여서 설명 가능하게 만들어라.
- C 위반이면: target을 original의 구조를 유지하는 최소수정으로 다시 써라.
- 지적되지 않은 부분은 그대로 두어라.{pinyin_rule}

<cards>
{cards_text}
</cards>"""


def _repair_schema_for(language: str) -> dict:
    item = {
        "type": "object",
        "properties": {
            "index": {"type": "integer"},
            "card_type": {"type": "string", "enum": _CARD_TYPES},
            "target": {"type": "string"},
            "korean": {"type": "string"},
            "explanation": {"type": "string"},
        },
        "required": ["index", "card_type", "target", "korean", "explanation"],
    }
    if language == "zh":
        item["properties"]["pinyin"] = {"type": "string"}
        item["required"] = item["required"] + ["pinyin"]
    return {"type": "object", "properties": {"items": {"type": "array", "items": item}},
            "required": ["items"]}


def _card_block(i: int, it: dict, language: str, note: str = "") -> str:
    lines = [f"[카드 {i}]{note}",
             f"original: {it.get('original_sentence') or ''}",
             f"card_type: {it.get('card_type') or ''}",
             f"target: {it.get('natural_sentence') or ''}"]
    if language == "zh":
        lines.append(f"pinyin: {it.get('pinyin') or ''}")
    lines += [f"korean: {it.get('korean_meaning') or ''}",
              f"explanation: {it.get('explanation_ko') or ''}"]
    return "\n".join(lines)


def _deterministic_fails(it: dict) -> list[str]:
    """LLM 없이 잡히는 명백한 위반만 (보조 검사)."""
    fails = []
    if not (it.get("original_sentence") or "").strip():
        fails.append("original 비어 있음")
    for f in ("natural_sentence", "korean_meaning", "explanation_ko"):
        if not (it.get(f) or "").strip():
            fails.append(f"{f} 비어 있음")
    if it.get("card_type") not in _CARD_TYPES:
        fails.append(f"card_type 값 이상: {it.get('card_type')!r}")
    if (it.get("card_type") == "correction"
            and (it.get("original_sentence") or "").strip() == (it.get("natural_sentence") or "").strip()):
        fails.append("correction인데 original==target")
    return fails


def _validate_and_repair(items: list[dict], cfg: dict, work_dir: Path,
                         audio_hash: str, language: str) -> tuple[list[dict], dict]:
    """카드 배치 검증(1회) → 실패분 배치 수리(1회). 반환: (items, 검증 리포트).

    실패는 절대 전파하지 않는다 — 검증 자체가 죽으면 카드를 그대로 쓴다(스펙: 안전 폴백).
    """
    report = {"enabled": True, "checked": 0, "llm_fail": [], "det_fail": [],
              "stt_risky": [], "repaired": [], "dropped": [], "error": None}
    todo = [(i, it) for i, it in enumerate(items) if not it.get("stt_suspect")]
    if not todo:
        return items, report
    report["checked"] = len(todo)
    lang_label = "중국어" if language == "zh" else "영어"

    # 1) 결정적 보조 검사 (LLM 없이). original이 비면 수리 불가 → 카드 제외.
    det_reasons: dict[int, list[str]] = {}
    for i, it in todo:
        fails = _deterministic_fails(it)
        if fails:
            det_reasons[i] = fails
            report["det_fail"].append({"index": i, "reasons": fails})
            if "original 비어 있음" in fails:
                it["stt_suspect"] = True  # 카드화 제외 (원발화 없는 카드는 성립 불가)
                report["dropped"].append(i)

    raws = []
    try:
        # 2) LLM 배치 검증 1회
        cards_text = "\n\n".join(_card_block(i, it, language) for i, it in todo
                                 if i not in report["dropped"])
        check_h = ("\n- H. pinyin이 target과 1:1로 일치하는가(성조 부호 포함)."
                   if language == "zh" else "")
        vprompt = _VALIDATION_PROMPT.format(lang_label=lang_label, cards_text=cards_text,
                                            check_h=check_h,
                                            last_check="H" if language == "zh" else "G")
        vresult, vraw = _call_llm(vprompt, cfg, work_dir, audio_hash, _VALIDATION_SCHEMA)
        raws.append("=== validation ===\n" + vraw)

        fail_reasons: dict[int, str] = {}
        valid_idx = {i for i, _ in todo}
        for r in vresult.get("results", []) or []:
            i = r.get("index")
            if i not in valid_idx:
                continue
            if r.get("verdict") == "stt_risky":
                items[i]["stt_suspect"] = True
                report["stt_risky"].append({"index": i, "reason": r.get("reason", "")})
            elif r.get("verdict") == "fail":
                fail_reasons[i] = f"{','.join(r.get('failed_checks', []))}: {r.get('reason', '')}"
                report["llm_fail"].append({"index": i, "checks": r.get("failed_checks", []),
                                           "reason": r.get("reason", "")})
        # 결정적 실패도 수리 대상에 합류 (제외된 카드 빼고)
        for i, reasons in det_reasons.items():
            if i not in report["dropped"] and not items[i].get("stt_suspect"):
                fail_reasons.setdefault(i, "; ".join(reasons))

        # 3) 실패분만 배치 수리 1회
        if fail_reasons:
            blocks = "\n\n".join(
                _card_block(i, items[i], language, note=f" — 지적사항: {fail_reasons[i]}")
                for i in sorted(fail_reasons))
            pinyin_rule = ("\n- pinyin은 고친 target 전체와 1:1로 다시 써라(성조 부호 포함)."
                           if language == "zh" else "")
            rprompt = _REPAIR_PROMPT.format(lang_label=lang_label, cards_text=blocks,
                                            pinyin_rule=pinyin_rule)
            rresult, rraw = _call_llm(rprompt, cfg, work_dir, audio_hash,
                                      _repair_schema_for(language))
            raws.append("=== repair ===\n" + rraw)
            for r in rresult.get("items", []) or []:
                i = r.get("index")
                if i not in fail_reasons:
                    continue
                it = items[i]
                fixed = {
                    "card_type": r.get("card_type"),
                    "natural_sentence": r.get("target"),
                    "korean_meaning": r.get("korean"),
                    "explanation_ko": r.get("explanation"),
                }
                if language == "zh":
                    fixed["pinyin"] = r.get("pinyin")
                it.update(fixed)
                # 수리 결과 자체가 계약 위반이면 보수적으로 카드 제외 ("확신 없으면 버려라").
                if _deterministic_fails(it):
                    it["stt_suspect"] = True
                    report["dropped"].append(i)
                else:
                    report["repaired"].append(i)
            n_rep, n_drop = len(report["repaired"]), len(report["dropped"])
            print(f"[analyze] 검증: {len(fail_reasons)}건 실패 → 수리 {n_rep}건, 제외 {n_drop}건")
        else:
            print(f"[analyze] 검증: {report['checked']}장 전부 통과"
                  + (f" (STT 위험 {len(report['stt_risky'])}건 제외)" if report["stt_risky"] else ""))
    except Exception as e:
        # 검증/수리 실패가 카드 생성을 막으면 안 된다 — 생성 카드 그대로 진행.
        print(f"[analyze] 경고: 카드 검증 실패(생성 카드 그대로 사용): {e}")
        report["error"] = str(e)
    finally:
        if raws:
            vpath = stage_output(work_dir, audio_hash, "04_validation_raw.txt")
            vpath.parent.mkdir(parents=True, exist_ok=True)
            vpath.write_text("\n\n".join(raws), encoding="utf-8")
    return items, report


# ── 근거 검사(grounding): LLM 없이 하는 결정적 안전장치 (2026-11-17) ──────────────
# 로컬 9B 모델 실측: 프롬프트 지시문의 예시("I look back myself sometimes", "I have a promise")를
# 학습자 발화로 착각해 카드로 만들었고, 같은 카드를 여러 번 반복 출력했다. 오염 카드는 복습 루프에
# 영구히 남으므로(D9와 같은 이유) 모델을 믿지 않고 코드로 거른다. 모든 provider에 적용한다.
#   ① original 이 STUDENT 발화에 실제로 있는가 — 한 턴 안에서 '2토큰 이상 연속으로 일치하는 구간'이
#      original 의 80% 이상을 덮어야 통과. (말 더듬 정리는 통과: 전사 "kimjang is held on usually held
#      on november" → original "Kimjang is usually held on November." = 100%. 흔한 앞부분만 우연히 겹치는
#      가짜는 탈락: "I have a promise" = "i have a" 3/4 = 75%.)
#   ② 같은 target 이 두 번 나오면 첫 번째만 남긴다
# 회귀 확인: 기존 실제 카드 71장은 전부 통과해야 한다(test: 기존 work/*/04_analysis.json 대조).
_GROUND_MIN_COVERAGE = 0.8


def _ground_tokens(text: str, language: str) -> list[str]:
    s = re.sub(r"[^\w\s]", " ", (text or "").lower(), flags=re.UNICODE)
    if language == "zh":
        return [ch for ch in s if not ch.isspace()]   # zh 는 글자 단위(전사가 글자 정렬이라 공백이 들쭉날쭉)
    return s.split()


def _coverage(toks: list[str], turn: list[str]) -> float:
    """turn 안에서 2토큰 이상 연속 일치하는 구간이 toks 를 덮는 비율."""
    import difflib
    blocks = difflib.SequenceMatcher(None, toks, turn, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks if b.size >= 2) / len(toks)


def _is_grounded(original: str, student_turns: list[list[str]], language: str) -> bool:
    toks = _ground_tokens(original, language)
    if not toks:
        return False
    if len(toks) == 1:
        return any(toks[0] in turn for turn in student_turns)
    return any(_coverage(toks, turn) >= _GROUND_MIN_COVERAGE for turn in student_turns)


def _ground_and_dedupe(items: list[dict], turns: list[dict], my_speaker: str,
                       language: str) -> tuple[list[dict], dict]:
    """전사에 근거 없는 카드와 중복 카드를 제거한다. 반환: (남은 items, 리포트)."""
    student = [_ground_tokens(t.get("text", ""), language)
               for t in turns if t.get("speaker") == my_speaker]
    kept, ungrounded, duplicates, seen = [], [], [], set()
    for it in items:
        orig = it.get("original_sentence") or ""
        if not _is_grounded(orig, student, language):
            ungrounded.append(orig)
            continue
        key = " ".join(_ground_tokens(it.get("natural_sentence") or "", language))
        if key in seen:
            duplicates.append(it.get("natural_sentence"))
            continue
        seen.add(key)
        kept.append(it)
    if ungrounded or duplicates:
        print(f"[analyze] 근거 검사: 전사에 없는 발화 {len(ungrounded)}건, 중복 {len(duplicates)}건 제외")
        for o in ungrounded:
            print(f"[analyze]   전사에 없음 → 제외: {o[:80]!r}")
    return kept, {"ungrounded": ungrounded, "duplicates": duplicates}


def run(transcript_path: Path, speakers_path: Path, audio_hash: str,
        cfg: dict, weak_patterns: list[dict]) -> Path:
    """LLM 분석 → work/{hash}/04_analysis.json 생성. 존재하면 스킵.

    분석 중 자동으로 띄운 로컬 모델 서버(llama_server_cmd)는 성공·실패와 무관하게 여기서 내린다.
    """
    try:
        return _run(transcript_path, speakers_path, audio_hash, cfg, weak_patterns)
    finally:
        _stop_llama_servers()


def _run(transcript_path: Path, speakers_path: Path, audio_hash: str,
         cfg: dict, weak_patterns: list[dict]) -> Path:
    work_dir = Path(cfg["work_dir"])
    out_path = stage_output(work_dir, audio_hash, "04_analysis.json")

    speakers = json.loads(Path(speakers_path).read_text(encoding="utf-8"))
    my_speaker = speakers["my_speaker"]

    if out_path.exists():
        try:
            cached = json.loads(out_path.read_text(encoding="utf-8"))
            if (cached.get("pipeline_version") == PIPELINE_VERSION
                    and cached.get("my_speaker") == my_speaker):
                print(f"[analyze] 캐시 사용, 스킵: {out_path}")
                return out_path
            print("[analyze] pipeline_version 또는 my_speaker 불일치 → 캐시 무시하고 재분석")
        except (json.JSONDecodeError, OSError):
            print("[analyze] 캐시 손상 → 재분석")

    transcript = json.loads(Path(transcript_path).read_text(encoding="utf-8"))

    language = transcript.get("language", "en")
    if language not in ("en", "zh"):
        raise RuntimeError(f"[analyze] 지원하지 않는 언어: {language!r}")

    transcript_text, n_unknown, n_target = _build_transcript_text(
        transcript["turns"], my_speaker, language)
    print(f"[analyze] 전사 투입: 전체 맥락(TUTOR 포함), UNKNOWN {n_unknown}개 제외, "
          f"분석대상(STUDENT analyzable) {n_target}개")

    if len(transcript_text) > _MAX_TRANSCRIPT_CHARS:
        raise RuntimeError(
            f"[analyze] 전사본이 {len(transcript_text)}자로 {_MAX_TRANSCRIPT_CHARS}자를 초과 "
            f"— 비용 방어를 위해 중단합니다."
        )
    if n_target == 0:
        raise RuntimeError("[analyze] 분석대상(analyzable STUDENT) turn이 0개입니다.")

    template = _PROMPT_ZH if language == "zh" else _PROMPT
    prompt = template.replace("{transcript_text}", transcript_text).replace(
        "{weak_patterns_text}", _weak_patterns_text(weak_patterns)
    )

    print(f"[analyze] LLM 분석 중 (provider={cfg['llm_provider']}, model={cfg['llm_model']}, "
          f"language={language})...")
    _SERVED.clear()
    result, raw_response = _call_llm(prompt, cfg, work_dir, audio_hash,
                                     _response_schema_for(language))

    # 연구용 원시 데이터(one accept): 성공한 실행의 프롬프트/원응답도 남긴다.
    # 기존엔 파싱 실패 시에만 raw를 저장해 성공 사례의 근거가 유실됐다.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stage_output(work_dir, audio_hash, "04_prompt.txt").write_text(prompt, encoding="utf-8")
    stage_output(work_dir, audio_hash, "04_raw_response.txt").write_text(raw_response, encoding="utf-8")

    # S10 카드 필드(original/target/korean/explanation/card_type)를 기존 04 필드명으로 매핑한다.
    # (db.py/export가 쓰는 이름을 유지 → 그 단계들은 무변경. corrected_sentence는 S10에서 폐기 → None.)
    items = []
    valid_issue_types = _ISSUE_TYPES_ZH if language == "zh" else _ISSUE_TYPES
    for it in result.get("items", []) or []:
        if it.get("issue_type") not in valid_issue_types:
            print(f"[analyze] 경고: 알 수 없는 issue_type={it.get('issue_type')!r}")
        if it.get("priority") not in _PRIORITIES:
            print(f"[analyze] 경고: 알 수 없는 priority={it.get('priority')!r}")
        if it.get("card_type") not in _CARD_TYPES:
            print(f"[analyze] 경고: 알 수 없는 card_type={it.get('card_type')!r}")
        items.append({
            "card_type": it.get("card_type"),
            "original_sentence": it.get("original"),
            "natural_sentence": it.get("target"),
            "korean_meaning": it.get("korean"),
            "pinyin": it.get("pinyin"),  # S12: zh만 채워짐 (en은 None)
            "explanation_ko": it.get("explanation"),
            "corrected_sentence": None,  # S10: 단일 target으로 통합, 미사용
            "issue_type": it.get("issue_type"),
            "priority": it.get("priority"),
            "practice_pattern": it.get("practice_pattern"),
            "pattern_examples": it.get("pattern_examples", []),
            "stt_suspect": bool(it.get("stt_suspect", False)),
            "timestamp": it.get("timestamp"),
        })

    # 근거 검사: 전사에 없는 발화로 만든 카드·중복 카드 제거 (LLM 검증보다 먼저 — 가짜 카드에 호출을 쓰지 않는다).
    items, grounding_report = _ground_and_dedupe(items, transcript["turns"], my_speaker, language)

    # S10 Stage 4: 배치 검증 + 수리 (config card_validation, 기본 켜짐. 실패해도 진행).
    validation_report = {"enabled": False}
    if cfg.get("card_validation", True) and items:
        items, validation_report = _validate_and_repair(items, cfg, work_dir, audio_hash, language)

    analysis = {
        "pipeline_version": PIPELINE_VERSION,
        "my_speaker": my_speaker,
        "language": language,  # S12: 전사에서 그대로 이월(코드 기록, LLM 산출 아님)
        "lesson_date": _extract_lesson_date(transcript.get("source_filename", "")),
        "lesson_summary_ko": result.get("lesson_summary_ko", ""),
        "items": items,
        "grounding": grounding_report,    # 근거 검사로 제외된 것(전사에 없는 발화/중복)
        "validation": validation_report,  # 연구용 기록(one accept) + 디버깅
        "served_by": list(_SERVED),       # 호출별 실제 처리 모델(분석→검증→수리 순). 폴백 발생 추적용
    }

    import os
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    tmp_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, out_path)

    print(f"[analyze] 완료: items={len(items)}개 → {out_path}")
    return out_path
