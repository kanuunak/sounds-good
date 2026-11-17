"""S6: Anki export → output/{date}-{hash}-english_speaking.apkg. 00_ARCHITECTURE.md 계약.

절대 규칙:
- 카드 앞면에 original_sentence(학습자의 틀린 문장)를 절대 넣지 않는다(D2). 앞면은 korean_meaning뿐.
  original은 뒷면 하단에 작은 회색 글씨로만 참고 표기.
- Model ID / Deck ID는 하드코딩된 고정 상수(L5). 노트 GUID = dup_key(L6, 재임포트 시 갱신).
- 단일 덱, 서브덱 금지. 구분은 태그로.
- 이 함수는 DB를 건드리지 않는다(D8). carded 마킹은 main이 apkg 생성 성공 후 수행.

외부 의존: genanki만.
"""
from __future__ import annotations

import html
from pathlib import Path

import genanki

# ★ 고정 상수 — 절대 바꾸지 말 것. 바뀌면 Anki가 기존 덱/노트를 다른 것으로 인식한다(L5).
_MODEL_ID = 1607392311
_DECK_ID = 2085531045
# S12: 중국어 전용 (영어 상수와 반드시 달라야 하고, 역시 절대 불변).
_MODEL_ID_ZH = 1699417523
_DECK_ID_ZH = 2117051937

def _back(zh: bool) -> str:
    """뒷면(afmt) — S10 계약 순서를 그대로 보여준다: target → original → why.

    설계 근거(S10 Stage 3):
    - 학습의 핵심은 "내가 말한 것"과 "말해야 할 것"의 대비다. 예전 뒷면은 원발화를 패턴·예문
      아래 맨 끝에 두어 그 대비가 보이지 않았다 → 정답 바로 밑으로 올린다.
    - 단, D2의 근거(틀린 문장을 다시 읽으면 각인된다)는 유효하므로 원발화는 작은 회색으로만.
    - 앞면은 손대지 않는다(한국어 뜻만, D2). {{FrontSide}} 대신 질문만 다시 렌더해
      "(입으로 …말한 뒤 뒤집기)" 안내가 답을 본 뒤에도 남지 않게 한다.
    - `{{#필드}}…{{/필드}}`로 빈 필드는 라벨째 생략한다(예전엔 "패턴: "만 덩그러니 남았다).
    """
    pinyin = '<div class="pinyin">{{Pinyin}}</div>' if zh else ""
    return (
        '<div class="ask">{{Korean}}</div>'
        '<hr id="answer">'
        '<div class="natural">{{Natural}}</div>'
        + pinyin
        + '{{#Original}}<div class="said">'
          '<span class="lbl">내가 말한 것</span>{{Original}}</div>{{/Original}}'
          '{{#Explanation}}<div class="expl">{{Explanation}}</div>{{/Explanation}}'
          '{{#Pattern}}<div class="pattern">'
          '<span class="lbl">패턴</span>{{Pattern}}</div>{{/Pattern}}'
          '{{#Examples}}<div class="examples">{{Examples}}</div>{{/Examples}}'
    )


def _css(natural_size: int) -> str:
    """카드 CSS. .card에 background를 박지 않는다 — 박으면 야간 모드에서 흰 배경이 남는다.

    폰(AnkiDroid)에서 밤에 복습하므로 nightMode 색 보정이 실제로 필요하다.
    Anki 데스크톱은 `.card.nightMode`, AnkiDroid는 `.nightMode .card` 형태라 둘 다 적는다.
    """
    return (
        ".card { font-family: sans-serif; font-size: 20px; text-align: center;"
        " color: #222; padding: 12px; }\n"
        # 앞면 (D2: 한국어 뜻만)
        ".korean { font-size: 22px; }\n"
        ".hint { color: #aaa; font-size: 14px; margin-top: 10px; }\n"
        # 뒷면
        ".ask { font-size: 15px; color: #999; }\n"
        f".natural {{ font-size: {natural_size}px; margin-top: 4px; font-weight: 600;"
        " line-height: 1.35; }\n"
        ".pinyin { font-size: 16px; color: #888; margin-top: 6px; }\n"
        ".said { font-size: 13px; color: #b3b3b3; font-style: italic; margin-top: 14px;"
        " line-height: 1.45; }\n"
        ".expl { margin-top: 14px; font-size: 16px; color: #444; line-height: 1.55; }\n"
        ".pattern { margin-top: 14px; font-size: 14px; color: #666; }\n"
        ".examples { margin-top: 4px; font-size: 14px; color: #666; line-height: 1.5; }\n"
        ".lbl { display: inline-block; font-size: 11px; font-style: normal; color: #aaa;"
        " border: 1px solid #ddd; border-radius: 3px; padding: 1px 5px; margin-right: 6px; }\n"
        "hr#answer { border: none; border-top: 1px solid #e6e6e6; margin: 14px 0; }\n"
        # 야간 모드
        ".nightMode .card, .card.nightMode { color: #e8e8e8; }\n"
        ".nightMode .ask, .card.nightMode .ask { color: #8e8e8e; }\n"
        ".nightMode .pinyin, .card.nightMode .pinyin { color: #9a9a9a; }\n"
        ".nightMode .said, .card.nightMode .said { color: #7d7d7d; }\n"
        ".nightMode .expl, .card.nightMode .expl { color: #cfcfcf; }\n"
        ".nightMode .pattern, .card.nightMode .pattern,"
        " .nightMode .examples, .card.nightMode .examples { color: #a8a8a8; }\n"
        ".nightMode .lbl, .card.nightMode .lbl { color: #888; border-color: #555; }\n"
        ".nightMode hr#answer, .card.nightMode hr#answer { border-top-color: #4a4a4a; }\n"
    )


_MODEL = genanki.Model(
    _MODEL_ID,
    "English Speaking (KO→EN)",
    # ★ 필드 목록은 절대 바꾸지 말 것 — 바꾸면 Anki가 기존 노트의 스키마 변경으로 처리한다.
    fields=[
        {"name": "Korean"},
        {"name": "Natural"},
        {"name": "Explanation"},
        {"name": "Pattern"},
        {"name": "Examples"},
        {"name": "Original"},
    ],
    templates=[
        {
            "name": "KO→EN 말하기",
            # 앞면: 한국어 뜻만. original_sentence는 절대 넣지 않는다(D2).
            "qfmt": (
                '<div class="korean">{{Korean}}</div>'
                '<div class="hint">(입으로 영어로 말한 뒤 뒤집기)</div>'
            ),
            "afmt": _back(zh=False),
        }
    ],
    css=_css(natural_size=26),
)


# S12: 중국어 모델 — 뒷면에 한자 + 병음. 앞면은 영어와 동일하게 한국어 뜻만(D2).
_MODEL_ZH = genanki.Model(
    _MODEL_ID_ZH,
    "Chinese Speaking (KO→ZH)",
    fields=[
        {"name": "Korean"},
        {"name": "Natural"},
        {"name": "Pinyin"},
        {"name": "Explanation"},
        {"name": "Pattern"},
        {"name": "Examples"},
        {"name": "Original"},
    ],
    templates=[
        {
            "name": "KO→ZH 말하기",
            "qfmt": (
                '<div class="korean">{{Korean}}</div>'
                '<div class="hint">(입으로 중국어로 말한 뒤 뒤집기)</div>'
            ),
            "afmt": _back(zh=True),
        }
    ],
    css=_css(natural_size=28),
)


def _esc(value: object) -> str:
    """None-안전 HTML 이스케이프(< > & 만). 카드 텍스트가 HTML을 깨지 않게 한다."""
    return html.escape("" if value is None else str(value), quote=False)


def apkg_path(output_dir: str | Path, lesson_date: str, audio_hash: str,
              language: str = "en") -> Path:
    """회차별 apkg 경로. app.py의 이력 다운로드도 이 규칙을 쓴다 — 여기 한 곳만 고칠 것."""
    name = "chinese_speaking" if language == "zh" else "english_speaking"
    return Path(output_dir) / f"{lesson_date}-{audio_hash}-{name}.apkg"


def build_note(c: dict, lesson_date: str, language: str = "en") -> genanki.Note:
    """카드 1장 → genanki 노트. 회차별 파일과 통합 파일(reexport_apkg.py)이 공유한다.

    여기 한 곳만 고치면 두 경로가 같이 바뀐다 — 필드 순서나 태그가 어긋나면 임포트 결과가
    갈라지므로 절대 복제하지 말 것.
    """
    zh = language == "zh"
    examples = c.get("pattern_examples") or []
    common_tail = [
        _esc(c.get("explanation_ko")),
        _esc(c.get("practice_pattern")),
        "<br>".join(_esc(e) for e in examples),
        _esc(c.get("original_sentence")),
    ]
    head = [_esc(c.get("korean_meaning")), _esc(c.get("natural_sentence"))]
    fields = head + [_esc(c.get("pinyin"))] + common_tail if zh else head + common_tail
    return genanki.Note(
        model=_MODEL_ZH if zh else _MODEL,
        fields=fields,
        guid=c["dup_key"],  # L6: dup_key를 GUID로 → 재임포트 시 중복 대신 갱신
        tags=[
            f"lesson::{lesson_date}",
            f"type::{c.get('issue_type') or 'unknown'}",
            f"priority::{c.get('priority') or 'unknown'}",
            # S10 Stage 3: card_type은 필드가 아니라 태그로만 넣는다(모델 불변 유지).
            # 없으면 legacy = S10 이전에 만들어진 카드.
            f"card_type::{c.get('card_type') or 'legacy'}",
        ],
    )


def run(cards: list[dict], audio_hash: str, lesson_date: str, cfg: dict,
        language: str = "en") -> Path | None:
    """genanki로 회차별 apkg 생성. 빈 리스트면 None 반환. DB 미변경."""
    if not cards:
        print("[export_anki] 새 카드 없음 — apkg를 만들지 않습니다.")
        return None

    if language == "zh":
        deck = genanki.Deck(_DECK_ID_ZH, cfg.get("anki_deck_name_zh", "chinese_speaking"))
    else:
        deck = genanki.Deck(_DECK_ID, cfg["anki_deck_name"])
    for c in cards:
        deck.add_note(build_note(c, lesson_date, language))

    out_dir = Path(cfg["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = apkg_path(out_dir, lesson_date, audio_hash, language)
    genanki.Package(deck).write_to_file(str(out_path))

    print(f"[export_anki] {len(cards)}장 → {out_path}")
    return out_path
