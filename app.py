"""Gradio 웹 UI — 단일 코드베이스, DEMO_MODE 환경변수로 실행 모드를 나눈다.

DEMO_MODE=false (기본, 로컬): 업로드 → convert → transcribe(WhisperX) → 화자 선택
  → analyze → db → anki → export_obsidian(vault 파일 저장). UI는 한국어(사용자 본인).
DEMO_MODE=true (HF Public 데모): 업로드 비활성. 미리 전사해둔 samples/*.json 선택
  → 화자 선택 → analyze → db → anki → Obsidian은 화면 미리보기(파일 미기록).
  **UI는 영어**(공개 쇼케이스 관객 대상). whisperx/torch/pyannote를 import하지 않고 부팅된다.

stages/·db.py·util.py 의 비즈니스 로직은 변경하지 않는다 — 호출만 한다.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

import gradio as gr

# Gradio 6부터 theme/css는 Blocks()가 아니라 launch()로 전달한다(5는 그 반대).
# public Space=gradio 6, private Space=gradio 5.50 이므로 양쪽 다 동작해야 한다.
_GR_MAJOR = int(str(getattr(gr, "__version__", "5")).split(".")[0])

# 아래 import는 GPU/STT 라이브러리를 끌어오지 않는다(analyze의 google-genai, export_anki의
# genanki도 각자 lazy/경량). convert·transcribe(=whisperx)만 DEMO_MODE=false 경로에서 lazy import.
from db import DB
from stages import analyze, export_anki, export_obsidian
from stages.speakers import _collect, _mmss, _pick_samples
from util import PIPELINE_VERSION, file_hash, load_config, stage_output

DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"
CFG = load_config()

HERE = Path(__file__).resolve().parent
SAMPLES_DIR = HERE / "samples"
GITHUB_URL = "https://github.com/"  # TODO: 실제 저장소 URL로 교체


# UI 언어: 기본은 모드에 따름(공개 데모=영어, 로컬=한국어).
# UI_LANG=en / ko 로 강제할 수 있다 — 로컬 전체 파이프라인을 영어권 관객에게 시연할 때 쓴다.
_UI_LANG = os.getenv("UI_LANG", "").strip().lower()


def T(ko: str, en: str) -> str:
    """UI 문구 선택. UI_LANG이 지정되면 그것이 우선한다."""
    if _UI_LANG == "en":
        return en
    if _UI_LANG == "ko":
        return ko
    return en if DEMO_MODE else ko


# 데모 드롭다운 라벨 → 샘플 파일. 첫 번째가 기본값(튜터 교정 채굴이 잘 드러나는 회차를 앞에).
SAMPLES = {
    "Alone time & social media (2026-07-22)": "lesson_b.json",
    "Working from home (2026-07-10)": "lesson_a.json",
}

UPLOAD_DIR = Path(CFG["work_dir"]) / "_uploads"

CSS = """
/* 폭: Gradio 6에서 .gradio-container 는 세로 flex 아이템이라 `margin: 0 auto`를 주면 폭이 '내용물 크기'로
   줄어든다 → 탭마다(새 수업 410px / 이력 900px) 페이지 폭이 달라 보였다(2026-11-07). width 100% + 상한 + 가운데. */
.gradio-container { width: 100% !important; max-width: 1120px !important; margin: 0 !important;
  align-self: center !important; }
footer { display: none !important; }
.sg-cap { font-size: 12.5px; color: var(--body-text-color-subdued); margin: 10px 2px 2px; }

#sg-hero { padding: 14px 0 4px; border-bottom: 1px solid var(--border-color-primary); margin-bottom: 6px; }
#sg-hero h1 { font-size: 28px; line-height: 1.2; margin: 0 0 4px; letter-spacing: -0.5px; font-weight: 700; }
#sg-hero .sg-sub { color: var(--body-text-color-subdued); font-size: 14px; margin: 0 0 10px; }

/* 홈 대시보드(로컬): 누적 수치 + 약점 패턴. 2026-11-07 — 18회차·약점이 아코디언 뒤에 숨어 있었다. */
.sg-dash-grid { display: flex; flex-wrap: wrap; gap: 10px; margin: 0 0 6px; }
.sg-panel { flex: 1 1 250px; border: 1px solid var(--border-color-primary); border-radius: 10px;
  padding: 11px 13px; background: var(--block-background-fill); }
.sg-panel .k { font-size: 10.5px; letter-spacing: .08em; text-transform: uppercase;
  color: var(--body-text-color-subdued); margin-bottom: 8px; }
.sg-bar { display: flex; align-items: center; gap: 8px; font-size: 13px; margin: 5px 0; }
.sg-bar .l { flex: 0 0 62px; word-break: keep-all; }
.sg-bar .t { flex: 1; height: 8px; background: var(--border-color-primary); border-radius: 4px; overflow: hidden; }
.sg-bar .t i { display: block; height: 100%; background: var(--color-accent); border-radius: 4px; }
.sg-bar .c { flex: 0 0 26px; text-align: right; font-variant-numeric: tabular-nums;
  color: var(--body-text-color-subdued); }
.sg-recent { font-size: 13px; line-height: 1.75; }
.sg-recent .d { font-family: ui-monospace, monospace; font-size: 12px; color: var(--color-accent); margin-right: 7px; }
.sg-recent .c { color: var(--body-text-color-subdued); margin-left: 6px; white-space: nowrap; }
.sg-dash .sg-step { margin-top: 10px; }
.sg-dash .sg-sum { margin: 6px 0 4px; }
.sg-dash .sg-sum .box { padding: 9px 12px; }
.sg-dash .sg-sum .v { font-size: 20px; }
#sg-hero .sg-flow { display: flex; flex-wrap: wrap; gap: 6px; }
#sg-hero .sg-chip { font-size: 11.5px; letter-spacing: .04em; text-transform: uppercase;
  padding: 4px 9px; border-radius: 999px; border: 1px solid var(--border-color-primary);
  color: var(--body-text-color-subdued); }
#sg-hero .sg-chip.on { border-color: var(--color-accent); color: var(--color-accent); font-weight: 600; }

.sg-step { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 9px; margin: 22px 0 8px; }
/* 폰 폭에서 "STEP 1"이 STEP / 1 로 꺾이던 것 방지 */
.sg-step .n { font-size: 11px; font-weight: 700; letter-spacing: .1em; color: var(--color-accent);
  white-space: nowrap; }
.sg-step .t { font-size: 16px; font-weight: 650; }
.sg-step .d { font-size: 13px; color: var(--body-text-color-subdued); }

.sg-note { border-left: 3px solid var(--color-accent); padding: 10px 14px; border-radius: 0 8px 8px 0;
  background: var(--block-background-fill); font-size: 13.5px; color: var(--body-text-color-subdued);
  line-height: 1.6; margin: 8px 0; }

.sg-status { display: flex; align-items: center; gap: 10px; padding: 13px 15px; border-radius: 10px;
  border: 1px solid var(--border-color-primary); background: var(--block-background-fill);
  font-size: 14px; margin: 10px 0; }
.sg-status--err { border-color: #d9534f; color: #d9534f; }
.sg-spin { width: 15px; height: 15px; border-radius: 50%; flex: none;
  border: 2px solid var(--border-color-primary); border-top-color: var(--color-accent);
  animation: sg-rot .8s linear infinite; }
@keyframes sg-rot { to { transform: rotate(360deg); } }

.sg-spk { border: 1px solid var(--border-color-primary); border-radius: 10px; padding: 13px 15px;
  margin: 8px 0; background: var(--block-background-fill); }
.sg-spk h4 { margin: 0 0 8px; font-size: 14px; display: flex; gap: 8px; align-items: baseline; }
.sg-spk .sec { font-weight: 400; font-size: 12px; color: var(--body-text-color-subdued); }
.sg-spk ul { margin: 0; padding-left: 2px; list-style: none; }
.sg-spk li { font-size: 13px; line-height: 1.65; color: var(--body-text-color-subdued); }
.sg-spk .ts { font-family: ui-monospace, monospace; font-size: 11.5px; color: var(--color-accent);
  margin-right: 7px; }

.sg-sum { display: flex; flex-wrap: wrap; gap: 10px; margin: 12px 0; }
.sg-sum .box { flex: 1 1 130px; border: 1px solid var(--border-color-primary); border-radius: 10px;
  padding: 11px 13px; background: var(--block-background-fill); }
.sg-sum .k { font-size: 10.5px; letter-spacing: .08em; text-transform: uppercase;
  color: var(--body-text-color-subdued); }
.sg-sum .v { font-size: 21px; font-weight: 700; line-height: 1.3; }

.sg-card { border: 1px solid var(--border-color-primary); border-radius: 12px; padding: 15px 17px;
  margin: 11px 0; background: var(--block-background-fill); }
.sg-card .head { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 11px; align-items: center; }
.sg-badge { font-size: 10.5px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
  padding: 3px 8px; border-radius: 5px; background: var(--color-accent); color: #fff; }
.sg-badge.upg { background: #7a8a99; }
.sg-tag { font-size: 11px; padding: 3px 8px; border-radius: 5px;
  border: 1px solid var(--border-color-primary); color: var(--body-text-color-subdued); }
.sg-card .lab { font-size: 10px; letter-spacing: .09em; text-transform: uppercase;
  color: var(--body-text-color-subdued); display: block; margin-bottom: 3px; }
.sg-card .front { font-size: 15px; line-height: 1.5; }
.sg-card .back { font-size: 17px; font-weight: 600; line-height: 1.45; margin-top: 11px; }
.sg-card .why { font-size: 13.5px; line-height: 1.65; color: var(--body-text-color-subdued);
  margin-top: 11px; padding-top: 11px; border-top: 1px dashed var(--border-color-primary); }
.sg-card .hint { font-size: 12px; color: var(--body-text-color-subdued); opacity: .8; margin-top: 7px; }
.sg-flip { margin-top: 12px; padding-top: 11px; border-top: 1px dashed var(--border-color-primary); }
.sg-flip > summary { cursor: pointer; font-size: 12.5px; font-weight: 700; letter-spacing: .03em;
  color: var(--color-accent); list-style: none; display: inline-flex; align-items: center; gap: 6px; }
.sg-flip > summary::-webkit-details-marker { display: none; }
.sg-flip > summary::before { content: "▸"; }
.sg-flip[open] > summary::before { content: "▾"; }
.sg-flip[open] > summary { margin-bottom: 4px; }
.gradio-container table td, .gradio-container table th { word-break: keep-all !important; }
/* 화자 선택지: 발화 샘플을 줄바꿈 그대로 보여준다(라디오 라벨은 기본이 한 줄) */
#sg-speaker span { white-space: pre-line; line-height: 1.65; }
#sg-speaker label { align-items: flex-start !important; padding: 10px 12px !important; }
.sg-card .said { font-size: 12px; color: var(--body-text-color-subdued); opacity: .75; margin-top: 8px;
  font-style: italic; }
/* 업로드 상자: 기본 드롭존이 폰 한 화면을 다 먹어서 줄인다(2026-11-07). */
#sg-upload .wrap { font-size: 13px; padding: 4px 0; min-height: 0 !important; }
#sg-upload .wrap .icon-wrap, #sg-upload .wrap > span.icon { transform: scale(.8); margin: 0; }
#sg-lang { padding-top: 2px; }
#sg-lang .wrap { gap: 8px; }
/* 이력 목록(라디오를 목록처럼): 두 줄 항목, 폭에 맞춰 흐름, 선택 항목은 강조. 동그라미는 숨김. */
/* nowrap 필수: Gradio 기본 flex-wrap:wrap 과 column + max-height 가 겹치면 옆 칸으로 넘어가 가로 스크롤이 생긴다 */
#sg-hist .wrap { flex-direction: column; flex-wrap: nowrap; gap: 6px; max-height: 480px; overflow-y: auto;
  padding: 2px; }
#sg-hist label { display: flex; align-items: flex-start; width: 100%; margin: 0; padding: 9px 12px !important;
  border: 1px solid var(--border-color-primary); border-radius: 10px; background: var(--block-background-fill);
  cursor: pointer; box-shadow: none; }
/* 선택 항목 강조. 접힌 상태(항목 1개)는 선택값이 비어 있으므로 :only-child 로 똑같이 강조한다 */
#sg-hist label.selected, #sg-hist label:only-child { border-color: var(--color-accent);
  box-shadow: inset 3px 0 0 var(--color-accent); }
#sg-hist input { display: none; }
#sg-hist span { white-space: pre-line; line-height: 1.5; font-size: 13.5px; word-break: keep-all; }
#sg-hist span::first-line { font-size: 12px; color: var(--color-accent); font-weight: 600; letter-spacing: .02em; }
/* 머리글 + [＋ 새 수업] 버튼: 구분선은 Row 전체에, 버튼은 내용 폭만 */
#sg-head { align-items: center; gap: 12px; border-bottom: 1px solid var(--border-color-primary);
  margin-bottom: 6px; }
#sg-head #sg-hero { border-bottom: 0; margin-bottom: 0; }
#sg-add { align-self: center; white-space: nowrap; flex-grow: 0 !important; }
@media (max-width: 600px) {   /* 폰(폴드 접은 화면 ~350px 포함): 제목 한 줄, 부제 숨김, 버튼 작게 */
  #sg-hero h1 { font-size: 19px; white-space: nowrap; }
  #sg-hero .sg-sub { display: none; }
  #sg-head { gap: 8px; }
  /* min-width 0 으로 두면 버튼이 20px 로 찌그러져 글자가 튀어나온다(실측) → 글자 폭만큼은 확보 */
  #sg-add { min-width: 92px !important; padding-left: 10px !important; padding-right: 10px !important;
    font-size: 13px; }
  #sg-hist .wrap { max-height: none; }   /* 폰에선 목록 내부 스크롤 대신 페이지 스크롤 */
}
/* [＋ 새 수업] 팝업 — Gradio 6엔 모달 컴포넌트가 없어 Column 을 고정 오버레이로 띄운다(2026-11-07). */
#sg-modal { position: fixed !important; inset: 0; z-index: 1000; background: rgba(10, 13, 20, .62);
  padding: 18px 10px 40px; overflow-y: auto; flex-direction: column; align-items: center; gap: 0; }
#sg-modal-panel { width: 100%; max-width: 760px; background: var(--background-fill-primary);
  border: 1px solid var(--border-color-primary); border-radius: 14px; padding: 6px 16px 18px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, .45);
  flex: 0 0 auto !important; }  /* Gradio Column 의 flex-grow 가 패널을 화면 끝까지 늘리는 것 방지 */
#sg-modal-head { align-items: center; }
#sg-modal-head .sg-step { margin: 10px 0 6px; }
#sg-close { align-self: center; }
"""


# ── 표시 헬퍼 ────────────────────────────────────────────────────────────────
def _e(v: object) -> str:
    """HTML 이스케이프(None 안전)."""
    return _html.escape("" if v is None else str(v), quote=False)


def _cell(v: object) -> str:
    """마크다운 표 셀(파이프·개행 정리)."""
    return ("" if v is None else str(v)).replace("|", "\\|").replace("\n", " ").strip()


def _clip(text: str, n: int = 95) -> str:
    """길면 자르되 잘렸다는 표시를 남긴다(화자 판별 화면이라 잘림이 티나야 한다)."""
    t = (text or "").strip()
    return t if len(t) <= n else t[:n].rstrip() + "…"


# issue_type / priority 는 DB엔 영문 enum으로 저장된다(집계 호환). 화면에서만 한국어로 바꾼다.
_ISSUE_KO = {"tense": "시제", "article": "관사", "preposition": "전치사", "word_order": "어순",
             "vocabulary": "어휘", "konglish": "콩글리시", "fluency": "유창성",
             "measure_word": "양사", "grammar": "문법", "expression": "표현"}
_PRIO_KO = {"high": "높음", "medium": "보통", "low": "낮음"}


def _issue_label(v: str | None) -> str:
    v = v or ""
    return T(_ISSUE_KO.get(v, v), v)


def _prio_label(v: str | None) -> str:
    v = v or ""
    return T(_PRIO_KO.get(v, v), v)


def _topic(summary: str | None, filename: str | None, n: int = 46) -> str:
    """이력 표의 '주제' 칸: 요약 첫 문장. 요약이 없으면 확장자 뗀 파일명.

    예전 표는 `통화_튜터이름_260911_072110.m4a` 같은 파일명이 두 줄씩 차지해 어느 수업인지 알 수 없었다.
    """
    s = (summary or "").strip()
    if s:
        first = re.split(r"(?<=[.!?。])\s", s, 1)[0]
        # LLM 요약은 "오늘 수업에서는 …", "학습자는 …"로 시작하는 게 대부분 — 주제 칸에선 군더더기.
        first = re.sub(r"^(오늘|이번) 수업에서는\s*|^학습자(는|가)\s*", "", first)
        return _clip(first, n)
    return _clip(Path(filename or "").stem, n)


def _status(text: str, kind: str = "loading") -> str:
    """즉시 보이는 상태 표시. kind: loading | err | info"""
    if kind == "loading":
        return f'<div class="sg-status"><span class="sg-spin"></span><span>{_e(text)}</span></div>'
    cls = "sg-status sg-status--err" if kind == "err" else "sg-status"
    icon = "⚠️" if kind == "err" else "ℹ️"
    return f'<div class="{cls}"><span>{icon}</span><span>{_e(text)}</span></div>'


def _cards_html(cards: list[dict], note: bool = True, empty: str | None = None) -> str:
    """카드 목록 HTML. note=False 면 맨 위 안내문을 빼고(카드 검색 탭), lesson_date 가 있으면 날짜 태그를 단다."""
    if not cards:
        msg = empty or T("새 카드가 없습니다.", "No new cards.")
        return f'<div class="sg-status"><span>{_e(msg)}</span></div>'
    front_lab = T("앞면 — Anki가 먼저 보여주는 면", "FRONT — what Anki shows first")
    flip_lab = T("뒤집기 (정답 보기)", "Flip the card")
    back_lab = T("뒷면 — 이렇게 말하면 됩니다", "BACK — say it like this")
    hint_lab = T("입으로 영어로 말한 뒤 뒤집으세요", "say it out loud in English, then flip")
    said_lab = T("당시 내 발화", "what I actually said")
    # D2: 앞면에는 학습자의 틀린 문장을 절대 노출하지 않는다 — 데모에서도 그 점이 보이게 한다.
    d2 = T("앞면에는 <b>한국어 뜻만</b> 나옵니다. 내가 틀리게 말한 문장은 뒤집은 뒤에만 보여줍니다 — "
           "오답을 반복해서 다시 읽으면 그 오답이 각인되기 때문입니다.",
           "The front shows <b>only the Korean meaning</b>. Your original mistake appears only after you "
           "flip — re-reading your own wrong sentence would just burn it in.")
    out = [f'<div class="sg-note">🃏 {d2}</div>'] if note else []
    for c in cards:
        ctype = (c.get("card_type") or "").lower()
        badge = ""
        if ctype in ("correction", "upgrade"):
            cls = "sg-badge upg" if ctype == "upgrade" else "sg-badge"
            badge = f'<span class="{cls}">{_e(ctype)}</span>'
        tags = "".join(
            f'<span class="sg-tag">{_e(v)}</span>'
            for v in (c.get("lesson_date"), _issue_label(c.get("issue_type")),
                      _prio_label(c.get("priority"))) if v)
        said = ""
        if c.get("original_sentence"):
            said = f'<div class="said">🗣 {_e(said_lab)} — {_e(c["original_sentence"])}</div>'
        why = ""
        if c.get("explanation_ko"):
            why = f'<div class="why">💡 {_e(c["explanation_ko"])}</div>'
        out.append(
            '<article class="sg-card">'
            f'<div class="head">{badge}{tags}</div>'
            f'<div class="front"><span class="lab">{_e(front_lab)}</span>'
            f'{_e(c.get("korean_meaning"))}'
            f'<div class="hint">{_e(hint_lab)}</div></div>'
            '<details class="sg-flip">'
            f'<summary>{_e(flip_lab)}</summary>'
            f'<div class="back"><span class="lab">{_e(back_lab)}</span>'
            f'{_e(c.get("natural_sentence"))}</div>{why}{said}</details>'
            '</article>')
    return "".join(out)


def _summary_html(stats: dict, n_cards: int, n_stt: int, lesson_date: str,
                  summary_ko: str = "", weak_before: list[dict] | None = None,
                  n_items: int = 0) -> str:
    boxes = [
        (T("생성 카드", "Cards created"), n_cards),
        (T("새 문장", "New sentences"), stats.get("new", 0)),
        (T("재등장", "Repeated"), stats.get("repeated", 0)),
        (T("STT 의심", "STT-suspect"), n_stt),
    ]
    cells = "".join(f'<div class="box"><div class="k">{_e(k)}</div><div class="v">{_e(v)}</div></div>'
                    for k, v in boxes)
    head = f'<div class="sg-step"><span class="n">{_e(T("완료", "DONE"))}</span>' \
           f'<span class="t">{_e(lesson_date)}</span></div>'
    note = ""
    if summary_ko:
        lab = T("수업 요약", "Lesson summary (Korean)")
        note = f'<div class="sg-note"><b>{_e(lab)}</b><br>{_e(summary_ko)}</div>'
    # 누적 루프의 증거: 이전 수업들에서 모인 약점 패턴이 이번 분석에 실제로 투입됐다.
    picked = ""
    if n_items:
        lab = T(f"후보 {n_items}개 중 {n_cards}장을 카드로 만들었습니다 "
                f"(유형별 최대 {CFG.get('max_cards_per_issue_type', 5)}장, 이미 카드인 문장은 제외).",
                f"{n_items} candidates from this lesson → the code picked {n_cards} "
                f"(per-type cap + sentences already carded are skipped)")
        picked = f'<div class="sg-note">🎛 {_e(lab)}</div>'
    fed = ""
    if weak_before:
        pat = ", ".join(f'{_e(_issue_label(w["issue_type"]))} ×{_e(w["total_count"])}' for w in weak_before)
        lab = T("이번 분석에 투입된 누적 약점 패턴", "Weak patterns from earlier lessons fed into this analysis")
        fed = f'<div class="sg-note">🔁 <b>{_e(lab)}</b><br>{pat}</div>'
    return f'{head}<div class="sg-sum">{cells}</div>{picked}{fed}{note}'


def _pipeline_rest_html() -> str:
    """카드 이후 단계(누적 DB + Obsidian 뷰)를 데모에서 설명한다.

    로컬 파이프라인은 카드 생성으로 끝나지 않는다 — SQLite가 원본이고(D1),
    통계는 등장 이력에서 집계하며(D7), Obsidian 뷰 3종 + archive가 매번 재생성된다.
    """
    title = T("카드가 끝이 아닙니다", "Cards are not the end of the pipeline")
    body = T(
        "모든 수업은 <b>SQLite 이력</b>에도 쌓입니다 — 이미 카드로 만든 문장은 다시 만들지 않고, "
        "실수 유형별 횟수가 누적됩니다. 그 누적 약점 패턴은 <b>다음 수업 분석에 그대로 투입</b>되어 "
        "반복되는 약점의 우선순위를 올립니다. 그리고 <b>Obsidian 노트 3종 + 회차별 상세</b>가 매번 재생성됩니다.",
        "Every lesson also lands in a <b>SQLite history</b>: sentences already turned into cards are never "
        "duplicated, and each mistake type is counted. Those accumulated weak patterns are <b>fed back into "
        "the next lesson's analysis</b> to raise the priority of recurring weaknesses. On top of that, "
        "<b>three Obsidian notes plus a per-lesson detail file</b> are regenerated every run.")
    nudge = T(
        "▶ 위 STEP 1에서 <b>다른 수업</b>을 골라 한 번 더 만들어 보세요. 같은 세션에서는 이력이 누적되어 "
        "중복이 걸러지고 약점 패턴 수치가 올라갑니다.",
        "▶ Go back to STEP 1 and run the <b>other lesson</b> too. Within a session the history accumulates: "
        "duplicates get filtered out and the weak-pattern counts climb.")
    local = T(
        "로컬 버전에서는 아래 노트가 미리보기가 아니라 <b>Obsidian vault에 실제 파일로</b> 저장됩니다.",
        "In the local version these notes are not a preview — they are written as <b>real files into an "
        "Obsidian vault</b>.")
    return (_step(T("이후", "STEP 3"), title)
            + f'<div class="sg-note">{body}</div>'
            + f'<div class="sg-note">{nudge}</div>'
            + f'<div class="sg-note">{local}</div>')


# ── 로컬(DEMO_MODE=false) 전용 ────────────────────────────────────────────────
def _db() -> DB:
    return DB(Path(CFG["db_path"]))


def _lesson_exists(audio_hash: str) -> dict | None:
    db = _db()
    try:
        row = db.conn.execute(
            "SELECT audio_hash, lesson_date, source_filename FROM lessons WHERE audio_hash=?",
            (audio_hash,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


_ALL = "all"  # 이력 필터의 '전체' 값


def history_rows(q: str = "", lang: str = _ALL, month: str = _ALL) -> list[dict]:
    """처리 이력(표시용 + audio_hash). 행 클릭 조회에 hash가 필요해 분리했다.

    필터(2026-11-07): 언어(en/zh), 월(YYYY-MM), 검색어 — 검색은 요약·파일명뿐 아니라
    그 회차에서 나온 문장(정답·한국어 뜻·내 발화)까지 본다. "그 표현 언제 했더라"가 실제 용도라서.
    """
    where, args = [], []
    if lang and lang != _ALL:
        where.append("l.language=?"); args.append(lang)
    if month and month != _ALL:
        where.append("substr(l.lesson_date,1,7)=?"); args.append(month)
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        where.append("""(l.summary_ko LIKE ? OR l.source_filename LIKE ? OR EXISTS (
                           SELECT 1 FROM lesson_sentences ls JOIN sentences s ON s.dup_key=ls.dup_key
                           WHERE ls.audio_hash=l.audio_hash
                             AND (s.natural_sentence LIKE ? OR s.korean_meaning LIKE ?
                                  OR ls.original_sentence LIKE ?)))""")
        args += [like] * 5
    sql = f"""SELECT l.audio_hash, l.lesson_date, l.source_filename, l.language, l.summary_ko,
                     (SELECT COUNT(*) FROM lesson_sentences ls JOIN sentences s ON s.dup_key=ls.dup_key
                      WHERE ls.audio_hash=l.audio_hash AND s.carded=1) AS card_count
              FROM lessons l {"WHERE " + " AND ".join(where) if where else ""}
              ORDER BY l.lesson_date DESC, l.processed_at DESC"""
    db = _db()
    try:
        return [dict(r) for r in db.conn.execute(sql, args).fetchall()]
    finally:
        db.close()


def month_choices() -> list[tuple[str, str]]:
    """이력 월 필터 선택지: 전체 + DB에 있는 YYYY-MM(최근순)."""
    db = _db()
    try:
        months = [r[0] for r in db.conn.execute(
            "SELECT DISTINCT substr(lesson_date,1,7) FROM lessons ORDER BY 1 DESC").fetchall()]
    finally:
        db.close()
    return [(T("전체", "All"), _ALL)] + [(m, m) for m in months if m]


def dashboard_html() -> tuple[str, str]:
    """누적 현황(로컬 전용, '현황' 탭). 반환: (요약, 상세) — 둘 다 현황 탭에 위아래로 놓인다.

    요약 = 마지막 수업 + 숫자 타일 4개. 상세 = 약점 패턴 TOP 5 막대 + 최근 3회차.
    처음엔 요약을 '새 수업' 탭 위에 얹었는데 첫 화면이 답답하다는 피드백으로 현황 탭으로 옮겼다.
    2026-11-07: 첫 화면이 빈 업로드 폼뿐이라 18회차·약점 패턴이라는 이 앱의 가치가 아코디언
    뒤에 숨어 있었다. 통계는 전부 DB 집계(D1/D7)이고 여기서는 그리기만 한다.
    """
    db = _db()
    try:
        per_lang = {r["language"]: dict(r) for r in db.conn.execute(
            """SELECT l.language, COUNT(*) AS lessons, MAX(l.lesson_date) AS last_date,
                      (SELECT COUNT(DISTINCT ls.dup_key) FROM lesson_sentences ls
                        JOIN lessons x ON x.audio_hash=ls.audio_hash WHERE x.language=l.language) AS sentences,
                      (SELECT COUNT(DISTINCT ls.dup_key) FROM lesson_sentences ls
                        JOIN sentences s ON s.dup_key=ls.dup_key
                        JOIN lessons x ON x.audio_hash=ls.audio_hash
                       WHERE x.language=l.language AND s.carded=1) AS carded
               FROM lessons l GROUP BY l.language""").fetchall()}
        weak = db.top_weak_patterns(n=5, language="en")
    finally:
        db.close()

    en = per_lang.get("en")
    if not en:
        empty = ('<div class="sg-note">'
                 + _e(T("아직 처리한 수업이 없습니다. 첫 녹음을 올려보세요.", "No lessons yet — upload your first recording."))
                 + "</div>")
        return empty, empty

    ago = ""
    try:
        days = (_dt.date.today() - _dt.date.fromisoformat(en["last_date"])).days
        ago = T(f"마지막 수업 {en['last_date']} · {days}일 전", f"last lesson {en['last_date']} · {days}d ago")
    except (TypeError, ValueError):
        ago = T(f"마지막 수업 {en['last_date']}", f"last lesson {en['last_date']}")
    head = _step(T("현황", "SO FAR"), T("영어 수업 누적", "English lessons so far"), ago)

    top = _issue_label(weak[0]["issue_type"]) if weak else "—"
    tiles = [(T("수업", "Lessons"), en["lessons"]), (T("복습 문장", "Sentences"), en["sentences"]),
             (T("Anki 카드", "Anki cards"), en["carded"]), (T("약점 1위", "Top weakness"), top)]
    cells = "".join(f'<div class="box"><div class="k">{_e(k)}</div><div class="v">{_e(v)}</div></div>'
                    for k, v in tiles)

    bars = ""
    if weak:
        mx = max(w["total_count"] for w in weak) or 1
        bars = "".join(
            f'<div class="sg-bar"><span class="l">{_e(_issue_label(w["issue_type"]))}</span>'
            f'<span class="t"><i style="width:{int(100 * w["total_count"] / mx)}%"></i></span>'
            f'<span class="c">{_e(w["total_count"])}</span></div>' for w in weak)
    else:
        bars = f'<div class="sg-recent">{_e(T("아직 집계할 교정 카드가 없습니다.", "No corrections yet."))}</div>'
    weak_panel = (f'<div class="sg-panel"><div class="k">'
                  f'{_e(T("약점 패턴 TOP 5 — 교정(correction) 카드 기준", "Weak patterns — corrections only"))}'
                  f'</div>{bars}</div>')

    recent = [r for r in history_rows() if r["language"] == "en"][:3]
    unit = T("장", "")
    rows = "".join(
        f'<div><span class="d">{_e(r["lesson_date"][5:])}</span>{_e(_topic(r["summary_ko"], r["source_filename"], 34))}'
        f'<span class="c">{_e(r["card_count"])}{_e(unit)}</span></div>' for r in recent)
    recent_panel = (f'<div class="sg-panel"><div class="k">{_e(T("최근 수업", "Recent lessons"))}</div>'
                    f'<div class="sg-recent">{rows}</div></div>')

    zh = per_lang.get("zh")
    zh_line = ""
    if zh:
        zh_line = ('<div class="sg-recent" style="margin:2px 0 4px">'
                   + _e(T(f"중국어: {zh['lessons']}회차 · 문장 {zh['sentences']} · Anki 카드 {zh['carded']} · 마지막 {zh['last_date']}",
                          f"Chinese: {zh['lessons']} lessons · {zh['sentences']} sentences · last {zh['last_date']}"))
                   + "</div>")
    top = f'<div class="sg-dash">{head}<div class="sg-sum">{cells}</div></div>'
    detail = f'<div class="sg-dash-grid">{weak_panel}{recent_panel}</div>{zh_line}'
    return top, detail


def _lang_label(language: str | None) -> str:
    """이력 표의 언어 칸. 파일명은 '전화영어'인데 내용은 중국어인 회차가 있어
    (업체 이름만으로는 수업 언어를 알 수 없다) 제목만 보고는 구분되지 않는다."""
    return T("중국어", "Chinese") if language == "zh" else T("영어", "English")


def _history_label(r: dict) -> str:
    """목록 한 항목(두 줄): `날짜 · 언어 · 카드 N` / 주제(요약 첫 문장, 자르지 않고 자연 줄바꿈).

    Gradio 표(Dataframe)는 칸 너비가 고정이라 폴드 접은 화면에서 눌리고, 잘라낸 '…' 뒤에 또
    줄바꿈이 생겨 이상했다(사용자 피드백) → 라디오 목록으로 바꿈. 라디오 라벨은 HTML이 안 돼
    줄바꿈 문자 + CSS pre-line 으로 두 줄을 만든다(화자 선택 라디오와 같은 수법).
    """
    unit = T("카드 ", "cards ")
    return (f'{r["lesson_date"]} · {_lang_label(r["language"])} · {unit}{r["card_count"]}\n'
            f'{_topic(r["summary_ko"], r["source_filename"], 120)}')


def _history_choices(rows: list[dict]) -> list[tuple[str, str]]:
    return [(_history_label(r), r["audio_hash"]) for r in rows]


def history_view(q: str = "", lang: str = _ALL, month: str = _ALL) -> tuple[list[tuple[str, str]], list[str]]:
    """이력 목록 선택지(라벨, hash) + hash 목록."""
    rows = history_rows(q, lang, month)
    return _history_choices(rows), [r["audio_hash"] for r in rows]


def _toggle_label(collapsed: bool) -> str:
    return T("목록 펼치기 ▾", "Expand list ▾") if collapsed else T("목록 접기 ▴", "Collapse list ▴")


def _expanded(q, lang, month, active_h=None):
    """목록 펼침 상태의 (목록 update, collapsed=False, 버튼 라벨). 활성 회차는 선택 표시를 유지한다."""
    rows = history_rows(q, lang, month)
    hs = [r["audio_hash"] for r in rows]
    return (gr.update(choices=_history_choices(rows), value=active_h if active_h in hs else None),
            False, _toggle_label(False))


def _collapsed(active_h, q, lang, month):
    """목록 접힘 상태: 활성 회차 한 항목(없으면 현재 필터의 최신 회차)만 남긴다."""
    rows = history_rows(q, lang, month)
    if not rows:
        return gr.update(choices=[], value=None), True, _toggle_label(True)
    row = next((r for r in rows if r["audio_hash"] == active_h), rows[0])
    # value=None 이 핵심: 이미 선택된 라디오는 다시 눌러도 이벤트가 안 난다. 접힌 항목의 선택을
    # 비워 두면 그 항목을 누를 때 select 가 발생해 펼칠 수 있다(강조는 CSS :only-child 로).
    return (gr.update(choices=_history_choices([row]), value=None),
            True, _toggle_label(True))


def filter_history(q, lang, month, active_h):
    """검색어·언어·월이 바뀌면 목록을 다시 그린다(접힘 해제). 상세 영역은 그대로 둔다."""
    return _expanded(q, lang, month, active_h)


def toggle_history(collapsed, active_h, q, lang, month):
    """접기/펼치기 버튼. 접을 때는 활성 회차(없으면 최신) 한 항목만 남긴다."""
    return _expanded(q, lang, month, active_h) if collapsed else _collapsed(active_h, q, lang, month)


_CARD_LIMIT = 60   # 카드 검색 탭이 한 번에 그리는 최대 장수


def card_rows(q: str = "", lang: str = _ALL, issue: str = _ALL) -> tuple[list[dict], int]:
    """카드 검색(2026-11-17): 회차가 아니라 카드 자체를 찾는다. 반환: (최근순 최대 _CARD_LIMIT 장, 전체 수).

    검색어는 정답 문장·한국어 뜻·내 발화·설명·연습 패턴을 본다. "그 표현 뭐였더라"가 용도.
    """
    where, args = [], []
    if lang and lang != _ALL:
        where.append("l.language=?"); args.append(lang)
    if issue and issue != _ALL:
        where.append("ls.issue_type=?"); args.append(issue)
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        where.append("""(s.natural_sentence LIKE ? OR s.korean_meaning LIKE ? OR ls.original_sentence LIKE ?
                         OR s.explanation_ko LIKE ? OR s.practice_pattern LIKE ?)""")
        args += [like] * 5
    sql = f"""SELECT s.natural_sentence, s.korean_meaning, s.explanation_ko,
                     ls.issue_type, ls.priority, ls.card_type, ls.original_sentence, l.lesson_date
              FROM lesson_sentences ls
              JOIN sentences s ON s.dup_key = ls.dup_key
              JOIN lessons l ON l.audio_hash = ls.audio_hash
              {"WHERE " + " AND ".join(where) if where else ""}
              ORDER BY l.lesson_date DESC, l.processed_at DESC, ls.rowid"""
    db = _db()
    try:
        rows = [dict(r) for r in db.conn.execute(sql, args).fetchall()]
    finally:
        db.close()
    return rows[:_CARD_LIMIT], len(rows)


def issue_choices() -> list[tuple[str, str]]:
    """카드 검색의 유형 필터: 전체 + DB에 실제로 있는 유형(많은 순)."""
    db = _db()
    try:
        types = [r[0] for r in db.conn.execute(
            """SELECT issue_type FROM lesson_sentences WHERE issue_type IS NOT NULL
               GROUP BY issue_type ORDER BY COUNT(*) DESC""").fetchall()]
    finally:
        db.close()
    return [(T("전체 유형", "All types"), _ALL)] + [(_issue_label(t), t) for t in types]


def cards_view(q: str = "", lang: str = _ALL, issue: str = _ALL) -> str:
    """카드 검색 탭 본문: 건수 한 줄 + 카드 목록."""
    rows, total = card_rows(q, lang, issue)
    if total > len(rows):
        head = T(f"{total}장 중 최근 {len(rows)}장 — 검색어나 필터로 좁혀 보세요.",
                 f"Showing the latest {len(rows)} of {total} cards — narrow it down.")
    else:
        head = T(f"{total}장", f"{total} cards")
    return (f'<div class="sg-cap">{_e(head)}</div>'
            + _cards_html(rows, note=False,
                          empty=T("조건에 맞는 카드가 없습니다.", "No cards match.")))


def home_view():
    """접속 시·카드 생성 뒤에 같이 갱신되는 것: 현황 탭 + 이력 목록(필터 초기화, 펼침) + 월 선택지
    + 카드 검색 탭(필터 초기화)."""
    top, detail = dashboard_html()
    list_u, collapsed, label = _expanded("", _ALL, _ALL)
    return (top, detail, list_u, collapsed, label,
            gr.update(choices=month_choices(), value=_ALL), "", _ALL,
            cards_view(), gr.update(choices=issue_choices(), value=_ALL), "", _ALL)


def _transcript_file(lesson_date: str, audio_hash: str) -> Path | None:
    """전사본을 내려받을 수 있는 .md 파일로 만든다(다른 LLM에 붙여넣어 분석받는 용도).

    볼트 저장본과 같은 생성 함수를 쓴다. work/{hash}/ 가 아니라 _downloads/ 에 두는 이유는
    --force 로 단계 캐시를 지워도 이 파일이 같이 날아가지 않게 하기 위해서다.
    """
    md = export_obsidian.build_transcript_md(CFG, audio_hash, lesson_date)
    if md is None:
        return None
    out = Path(CFG["work_dir"]) / "_downloads" / f"{lesson_date}-전사본.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    return out


def on_history_select(evt: gr.SelectData, collapsed, q, lang, month):
    """이력 항목 클릭. 펼쳐진 목록 → 그 회차로 **한 항목으로 접고** 바로 아래에 상세.
    접힌 항목을 다시 누르면 → 목록을 (현재 필터로) 펼친다. 상세는 그대로.

    (2026-11-07 사용자 피드백: 긴 표 아래에 상세가 붙으니 눌러도 반응이 안 보여 "조회된 게 맞나?" 싶었다.)
    """
    h = evt.value
    if isinstance(h, (list, tuple)):  # (label, value) 꼴로 올 때 대비
        h = h[-1]
    known = {r["audio_hash"] for r in history_rows()}
    if h not in known:  # 라벨이 넘어온 경우: 현재 필터 목록의 index 로 찾는다
        rows = history_rows(q, lang, month)
        idx = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        h = rows[idx]["audio_hash"] if isinstance(idx, int) and 0 <= idx < len(rows) else None
    if not h:
        return (gr.update(), False, _toggle_label(False), "", gr.update(value=None, visible=False),
                gr.update(value=None, visible=False), gr.update(visible=False), "", None, False)
    if collapsed:
        keep = gr.update()
        return _expanded(q, lang, month, h) + (keep,) * 7
    return _collapsed(h, q, lang, month) + _lesson_detail(h)


def _lesson_detail(h: str) -> tuple:
    """회차 상세 7종: (카드 html, apkg, 전사본 md, 원본 버튼, 원본 html, hash state, 원본 펼침 state)."""
    db = _db()
    try:
        lesson = db.conn.execute(
            "SELECT lesson_date, source_filename, summary_ko, language FROM lessons WHERE audio_hash=?",
            (h,)).fetchone()
        cards = db.conn.execute(
            """SELECT s.natural_sentence, s.korean_meaning, s.explanation_ko, s.carded,
                      ls.issue_type, ls.priority, ls.card_type, ls.original_sentence
               FROM lesson_sentences ls JOIN sentences s ON s.dup_key=ls.dup_key
               WHERE ls.audio_hash=?""", (h,)).fetchall()
    finally:
        db.close()
    head = (_step(T("이력", "PAST"), f"{lesson['lesson_date']}",
                  _clip(lesson["source_filename"] or "", 50))
            + (f'<div class="sg-note">{_e(lesson["summary_ko"])}</div>'
               if lesson["summary_ko"] else ""))

    # 초기 CLI 시절 회차는 날짜 없는 파일명으로 나가서 회차별 apkg가 없을 수 있다.
    apkg = export_anki.apkg_path(CFG["output_dir"], lesson["lesson_date"], h,
                                 lesson["language"] or "en")
    if apkg.exists():
        apkg_update = gr.update(value=str(apkg), visible=True)
    else:
        apkg_update = gr.update(value=None, visible=False)
        head += ('<div class="sg-note">'
                 + _e(T("이 회차는 저장된 .apkg 파일이 없습니다(카드는 이미 Anki로 나갔습니다).",
                        "No stored .apkg for this lesson (its cards were already exported)."))
                 + "</div>")
    txt = _transcript_file(lesson["lesson_date"], h)
    txt_update = (gr.update(value=str(txt), visible=True) if txt
                  else gr.update(value=None, visible=False))

    # 전사 원본(hist_raw)은 빈 값으로 — 이전에 보던 회차의 전사가 새 회차 카드 밑에 남지 않게.
    # 버튼도 '보기' 상태로 되돌린다(다른 회차를 고르면 접힌 상태에서 시작).
    return (head + _cards_html([dict(c) for c in cards]), apkg_update, txt_update,
            gr.update(visible=True, value=_raw_btn_label(False)), "", h, False)


def _raw_btn_label(is_open: bool) -> str:
    return (T("전사 원본 닫기", "Hide raw transcript") if is_open
            else T("전사 원본 보기", "Show raw transcript"))


def toggle_raw_transcript(h, is_open):
    """같은 버튼으로 전사 원본을 열고 닫는다(전사가 길어서 접을 수단이 필요했다)."""
    if is_open:
        return "", False, gr.update(value=_raw_btn_label(False))
    return show_raw_transcript(h), True, gr.update(value=_raw_btn_label(True))


def show_raw_transcript(h):
    """선택한 회차의 전사·화자분리 원본을 읽기 좋게 보여준다."""
    if not h:
        return ""
    try:
        tp = stage_output(Path(CFG["work_dir"]), h, "02_transcript.json")
        if not tp.exists():
            return _status(T("이 회차의 전사 원본이 work/ 에 없습니다(정리되었을 수 있음).",
                             "No raw transcript on disk for this lesson."), "info")
        tr = json.loads(tp.read_text(encoding="utf-8"))
        sp = stage_output(Path(CFG["work_dir"]), h, "03_speakers.json")
        me = json.loads(sp.read_text(encoding="utf-8")).get("my_speaker", "") if sp.exists() else ""
        out = [_step(T("전사 원본", "RAW"),
                     T("전사 · 화자분리 결과", "transcript & diarization"),
                     f'diarization={tr.get("diarization_status","")} · turns={len(tr.get("turns",[]))}'),
               '<div class="sg-spk"><ul>']
        for t in tr.get("turns", []):
            mine = me and t.get("speaker") == me
            who = T("나", "ME") if mine else (t.get("speaker") or "?")
            mark = " ✅" if t.get("analyzable") else ""
            style = ' style="color:var(--color-accent);font-weight:600"' if mine else ""
            out.append(f'<li><span class="ts">{_e(_mmss(t.get("start", 0)))}</span>'
                       f'<b{style}>{_e(who)}{mark}</b> — {_e((t.get("text") or "").strip())}</li>')
        out.append("</ul></div>")
        return "".join(out)
    except Exception as e:
        print(f"[web:raw] 오류: {e}")
        return _status(T("전사 원본을 읽지 못했습니다.", "Could not read the raw transcript."), "err")


def step1_transcribe(file_obj, lang):
    """로컬 1단계: 업로드 → convert → transcribe(WhisperX) → 화자 후보. (제너레이터: 진행 표시)"""
    hidden = gr.update(visible=False)
    lang = lang or "en"
    if file_obj is None:
        yield (_status(T("먼저 .m4a 파일을 업로드하세요.", "Upload an .m4a file first."), "info"),
               gr.update(choices=[], visible=False), None, hidden)
        return
    yield (_status(T("오디오 변환 + WhisperX 전사·화자분리 중… 수 분 걸릴 수 있습니다.",
                     "Converting audio, then WhisperX transcription + speaker diarization… "
                     "this can take a few minutes.")),
           gr.update(choices=[], visible=False), None, hidden)
    try:
        # whisperx를 끌어오는 stage는 이 경로(비데모)에서만 lazy import한다.
        from stages import convert, transcribe

        src = Path(file_obj)
        original_name = Path(getattr(file_obj, "orig_name", src.name)).name
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        kept = UPLOAD_DIR / original_name
        if Path(file_obj).resolve() != kept.resolve():
            shutil.copy(file_obj, kept)

        h = file_hash(kept)
        existing = _lesson_exists(h)
        if existing and os.getenv("SG_ALLOW_DUP", "0").strip().lower() not in ("1", "true", "yes"):
            # 같은 내용(해시)의 녹음은 이미 DB에 있다 → 중복 처리를 막는다.
            yield (_status(T(f"이미 처리한 녹음입니다 — {existing['source_filename']} "
                             f"({existing['lesson_date']}). 처리 이력에서 확인하세요. "
                             f"그래도 다시 돌리려면 SG_ALLOW_DUP=1 로 실행하세요.",
                             f"This recording was already processed — {existing['source_filename']} "
                             f"({existing['lesson_date']}). See the history above. "
                             f"To redo it anyway, run with SG_ALLOW_DUP=1."), "err"),
                   gr.update(choices=[], visible=False), None, hidden)
            return
        prefix = T("새 수업입니다.", "New lesson.")

        wav = convert.run(kept, CFG)
        transcript_p = _transcribe_call(wav, h, CFG, original_name, lang)
        transcript = json.loads(Path(transcript_p).read_text(encoding="utf-8"))

        if transcript["diarization_status"] == "failed":
            # D9: 화자분리 전체 실패 → 중단(화자 추측 금지).
            yield (_status(T("화자분리 전체 실패 — 오염 카드 방지를 위해 중단합니다. 다른 회차를 시도하세요.",
                             "Speaker diarization failed completely — stopping so no contaminated "
                             "cards are made. Try another recording."), "err"),
                   gr.update(choices=[], visible=False), None, hidden)
            return

        html, radio, state = _speaker_section(transcript, h, str(transcript_p), original_name,
                                              demo=False, prefix=prefix)
        yield html, radio, state, gr.update(visible=True)
    except Exception as e:
        print(f"[web:step1] 오류: {e}")
        # 단계 코드가 직접 쓴 안내("[convert] ffmpeg를 …", "[transcribe] HF_TOKEN …")는 다음에 할 일이
        # 적혀 있으므로 그대로 보여 준다. 처음 설치한 사람이 '처리 중 오류'만 보고 막히던 자리(2026-11-17).
        own = str(e).strip()
        if own.startswith("[convert]") or own.startswith("[transcribe]"):
            text = own.split("\n")[0][:300]
        else:
            text = T("처리 중 오류가 발생했습니다. 실행한 터미널(또는 로그)의 마지막 줄을 확인하세요.",
                     "Something went wrong. Check the last lines of the terminal or log.")
        yield (_status(text, "err"),
               gr.update(choices=[], visible=False), None, hidden)


# ── ZeroGPU (HF Private Space 전용) ──────────────────────────────────────────
# Space에서만 참이고 로컬에서는 항상 거짓. stages/ 는 건드리지 않고 전사 호출만 감싼다.
IS_ZEROGPU = bool(os.getenv("SPACES_ZERO_GPU"))
_ZEROGPU_DURATION = int(os.getenv("ZEROGPU_DURATION", "120"))


def _transcribe_impl(wav, h, cfg, original_name, language="en"):
    """실제 전사 호출. whisperx는 여기서 lazy import한다(데모 모드는 이 경로를 안 탄다)."""
    from stages import transcribe

    return transcribe.run(wav, h, cfg, original_name, language)


if IS_ZEROGPU:
    # ★ ZeroGPU는 앱 시작 시점에 모듈 최상위를 스캔해 @spaces.GPU 함수를 찾는다.
    #   함수 내부에서 동적으로 데코레이트하면 "No @spaces.GPU function detected"로 기동 실패한다.
    import spaces

    _transcribe_call = spaces.GPU(duration=_ZEROGPU_DURATION)(_transcribe_impl)
else:
    _transcribe_call = _transcribe_impl


# ── 데모(DEMO_MODE=true) 전용 ─────────────────────────────────────────────────
def _demo_config() -> dict:
    """방문자마다 격리된 임시 DB/경로. Spaces는 재시작 시 파일시스템이 초기화된다."""
    cfg = load_config()
    tmp = Path(tempfile.gettempdir()) / "sounds-good" / uuid.uuid4().hex[:12]
    cfg["work_dir"] = str(tmp / "work")
    cfg["db_path"] = str(tmp / "history.db")
    cfg["output_dir"] = str(tmp / "output")
    return cfg


def demo_load_sample(sample_label, state):
    """데모 1단계: 선택한 샘플 02_transcript.json을 읽어 화자 후보를 보여준다. (제너레이터)

    ★ 같은 세션에서 두 번째 수업을 열면 **기존 세션 DB를 재사용**한다. 그래야 이 파이프라인의
      핵심인 누적(중복 제거 → 약점 패턴 집계 → 다음 분석에 피드백)이 실제로 보인다.
    """
    hidden = gr.update(visible=False)
    yield (_status("Loading the pre-transcribed sample…"),
           gr.update(choices=[], visible=False), None, hidden)
    try:
        fname = SAMPLES.get(sample_label)
        if not fname:
            yield _status("Pick a sample first.", "info"), gr.update(choices=[], visible=False), None, hidden
            return

        # 세션 유지: 이전에 만든 임시 DB가 있으면 그대로 쓴다(없으면 새로 만든다).
        cfg = (state or {}).get("cfg") or _demo_config()
        transcript = json.loads((SAMPLES_DIR / fname).read_text(encoding="utf-8"))
        # 샘플 원본은 수정하지 않고, 세션 작업 디렉토리로 복사해 사용한다.
        h = transcript.get("audio_hash", "demo0001")
        tp = stage_output(Path(cfg["work_dir"]), h, "02_transcript.json")
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")

        html, radio, state = _speaker_section(
            transcript, h, str(tp), transcript.get("source_filename", "sample.m4a"),
            demo=True, cfg=cfg, prefix=sample_label)
        yield html, radio, state, gr.update(visible=True)
    except Exception as e:
        print(f"[web:demo_load] 오류: {e}")
        yield (_status("Could not load the sample. Please try again.", "err"),
               gr.update(choices=[], visible=False), None, hidden)


# ── 공통: 화자 후보 / Obsidian 미리보기 / 카드 생성 ──────────────────────────
def _speaker_section(transcript, h, transcript_path, source_filename, demo, cfg=None, prefix=""):
    turns = transcript["turns"]
    by_speaker = _collect(turns)
    known = sorted(s for s in by_speaker if s != "UNKNOWN")

    head = (_step(T("2단계", "STEP 2"),
                  T("내(학습자) 목소리 고르기", "Pick the learner's voice"),
                  T("발화 내용을 보고 고르세요", "pick by what they actually said"))
            + f'<div class="sg-note"><b>{_e(prefix)}</b><br>'
            f'{_e(T("전사 완료", "Transcript ready"))} · '
            f'diarization={_e(transcript["diarization_status"])} · turns={len(turns)}</div>')

    # 라디오 선택지 자체에 발화 샘플을 넣는다 — 문장 칸을 그대로 클릭해서 고를 수 있게.
    sec_lab = T("총 발화", "speech")
    choices = []
    for spk in known:
        info = by_speaker[spk]
        samples = _pick_samples(info["samples"], 3)
        quoted = "\n".join(f'[{_mmss(t["start"])}] {_clip(t["text"], 80)}' for t in samples)
        choices.append((f'{spk} · {sec_lab} {info["total_sec"]:.0f}s\n{quoted}', spk))

    state = {"hash": h, "transcript_path": transcript_path, "source_filename": source_filename,
             "demo": demo, "cfg": cfg, "language": transcript.get("language", "en")}
    return head, gr.update(choices=choices, value=(known[0] if known else None),
                           visible=True), state


def _obsidian_preview(db) -> str:
    """export_obsidian.run()을 호출하지 않고(파일 미기록), db 집계로 뷰 미리보기 문자열만 만든다."""
    parts = [f"### {T('🗒️ Obsidian 뷰 미리보기', '🗒️ Obsidian notes (preview)')}",
             f"> {T('Demo에서는 화면 미리보기로만 보여줍니다. 로컬 버전에서는 Obsidian vault에 자동 저장됩니다.', 'In this demo the notes are only previewed here; the local version writes them straight into an Obsidian vault.')}",
             "",
             f"**{T('🗣️ Speaking Practice — 한국어 보고 영어로 말하기', '🗣️ Speaking Practice — read the Korean, say it in English')}**"]
    practice = db.recent_practice(lessons=2)[:25]
    if practice:
        for i, p in enumerate(practice, 1):
            parts.append(f"{i}. {p.get('korean_meaning') or ''}  \n   ↳ _{p.get('natural_sentence') or ''}_")
    else:
        parts.append(f"_{T('연습 문장이 아직 없습니다.', 'No practice sentences yet.')}_")

    parts += ["", f"**{T('📚 Sentence Bank', '📚 Sentence Bank')}**",
              f"| {T('자연스러운 문장', 'Natural sentence')} | {T('한국어', 'Korean')} | {T('유형', 'Type')} | {T('등장', 'Seen')} |",
              "| --- | --- | --- | --- |"]
    for r in db.all_sentences()[:20]:
        parts.append(f"| {_cell(r.get('natural_sentence'))} | {_cell(r.get('korean_meaning'))} "
                     f"| {_cell(r.get('issue_type'))} | {r.get('seen_count', 0)} |")

    parts += ["", f"**{T('📊 Weak Patterns', '📊 Weak Patterns')}**",
              f"_{T('※ correction 카드만 집계합니다 (upgrade·이전 카드 제외).', 'Counts corrections only (upgrades and legacy cards excluded).')}_",
              f"| {T('패턴', 'Pattern')} | {T('누적', 'Total')} | {T('최근', 'Recent')} | {T('대표 예문', 'Example')} |",
              "| --- | --- | --- | --- |"]
    for s in db.pattern_stats():
        parts.append(f"| {_cell(s.get('issue_type'))} | {s.get('total_count', 0)} "
                     f"| {s.get('recent_count', 0)} | {_cell(s.get('example'))} |")
    return "\n".join(parts)


def _error_message(e: Exception) -> str:
    """사용자에게 보일 오류 안내. 원인별로 '다음에 뭘 해야 하는지'가 달라서 나눈다.

    예전에는 전부 "처리 중 오류가 발생했습니다"라 사용자가 로그를 직접 뒤져야 했다(실측 2026-09-11:
    무료 할당량 소진이었는데 화면만 보고는 알 수 없었다). 상세 내용은 로그에만 남긴다.
    """
    s = str(e)
    up = s.upper()
    low = s.lower()
    if "모든 모델 실패" in s:
        # Claude(구독) → Gemini 대체 모델까지 전부 막혔다.
        return T("Claude 구독 한도와 Gemini 대체 모델 할당량이 모두 막혔습니다. "
                 "Claude 한도가 풀린 뒤(보통 몇 시간) 또는 내일(Gemini) 다시 눌러주세요. "
                 "전사는 저장돼 있어서 다시 누르면 분석부터 이어집니다.",
                 "Claude and all fallback models are rate-limited. Try again later; "
                 "the transcript is saved, so it resumes from analysis.")
    if "claude cli" in low:
        # Claude 구독(claude -p) 경로. 한도는 Claude Code와 공유한다.
        if any(k in low for k in ("usage limit", "hit your limit", "limit reached", "status=429", "rate limit")):
            return T("Claude 구독 사용 한도에 도달했습니다. 한도가 풀린 뒤 다시 누르거나, "
                     "config.yaml의 llm_provider를 gemini로 되돌리면 바로 됩니다.",
                     "Claude subscription usage limit reached. Retry after it resets, "
                     "or switch llm_provider back to gemini in config.yaml.")
        if "status=529" in low or "overloaded" in low:
            return T("Claude 서버가 지금 과부하입니다. 몇 분 뒤 다시 눌러주세요.",
                     "Claude is overloaded right now. Try again in a few minutes.")
    if "RESOURCE_EXHAUSTED" in up or "429" in s:
        # 무료 등급은 모델별·하루 단위 한도가 있다. 재시도 대기시간이 오면 같이 알려준다.
        import re as _re
        m = _re.search(r"retryDelay['\"]?[:\s]+['\"]?(\d+)", s)
        hint = f" (약 {m.group(1)}초 뒤 재시도 가능)" if m else ""
        return T(f"오늘 분석 API 무료 할당량을 다 썼습니다{hint}. "
                 f"내일 초기화되거나, config.yaml의 llm_model을 다른 모델로 바꾸면 바로 됩니다 "
                 f"(모델별로 할당량이 따로입니다).",
                 f"The daily free quota for the analysis API is used up{hint}. "
                 f"It resets tomorrow, or switch llm_model in config.yaml.")
    if "UNAVAILABLE" in up or "503" in s:
        return T("분석 서버가 지금 과부하입니다. 몇 분 뒤 다시 눌러주세요. "
                 "(자동 재시도 3회를 했는데도 실패했습니다)",
                 "The analysis server is overloaded. Try again in a few minutes.")
    if "API_KEY" in up or "UNAUTHENTICATED" in up or "PERMISSION_DENIED" in up:
        return T("분석 서비스 인증에 실패했습니다. API 키를 확인하세요 "
                 "(~/.config/sounds-good.env 의 GEMINI_API_KEY).",
                 "Authentication failed. Check the API key.")
    return T("처리 중 오류가 발생했습니다. 자세한 내용은 로그를 확인하세요: "
             "~/.local/state/sounds-good/app.log",
             "Something went wrong. See the log for details.")


def _close_db(db) -> None:
    """제너레이터는 yield 이후 **다른 스레드에서 재개**될 수 있고, sqlite 연결은 만든 스레드에서만
    닫을 수 있다(실측: `sqlite3.ProgrammingError ... created in thread ... this is thread ...`).
    카드 생성 결과에는 영향이 없으므로 닫기 실패는 삼킨다. 정상 경로에서는 yield 전에 미리 닫는다.
    """
    try:
        db.close()
    except Exception:
        pass


def make_cards(my_speaker, state):
    """공통 2단계: analyze → db → anki. Obsidian은 로컬=파일저장, 데모=화면 미리보기. (제너레이터)

    출력: (결과 html, apkg update, obsidian md). 이력 표 갱신은 build_ui의 .then 체인이 한다.
    """
    no_file = gr.update(value=None, visible=False)
    if not state or not my_speaker:
        yield (_status(T("먼저 분석하고 화자를 선택하세요.", "Load a lesson and pick your speaker first."), "info"),
               no_file, "")
        return

    yield (_status(T("LLM이 대화를 분석하고 카드를 만드는 중… 보통 20~40초 걸립니다.",
                     "The LLM is analyzing the conversation and building your cards… usually 20–40s.")),
           no_file, "")

    demo = state.get("demo", False)
    cfg = state["cfg"] if demo else CFG
    db = DB(Path(cfg["db_path"]))
    try:
        h = state["hash"]
        transcript_p = Path(state["transcript_path"])

        # 라디오 선택값으로 03_speakers.json 계약 파일을 기록(UI에선 CLI 입력을 우회).
        speakers_p = stage_output(Path(cfg["work_dir"]), h, "03_speakers.json")
        speakers_p.parent.mkdir(parents=True, exist_ok=True)
        speakers_p.write_text(
            json.dumps({"my_speaker": my_speaker, "confirmed_by": "manual"}, ensure_ascii=False, indent=2),
            encoding="utf-8")

        language = state.get("language", "en")
        weak_before = db.top_weak_patterns(language=language)  # S12: 언어별 격리(D-zh3)
        analysis_p = analyze.run(transcript_p, speakers_p, h, cfg, weak_before)
        analysis = json.loads(Path(analysis_p).read_text(encoding="utf-8"))

        stats = db.ingest_analysis(h, analysis, state["source_filename"])
        cards = db.select_unexported_cards(h, cfg["max_cards_per_lesson"], cfg["max_cards_per_issue_type"])
        apkg = export_anki.run(cards, h, analysis["lesson_date"], cfg, language)
        if apkg is not None:
            db.mark_cards_exported([c["dup_key"] for c in cards])  # D8: apkg 성공 후에만

        obsidian_md = ""
        warn = ""
        if demo:
            obsidian_md = _obsidian_preview(db)  # 파일 미기록
        else:
            try:
                # 로컬 전용 통합이라 여기서만 lazy import한다(공개 데모엔 이 모듈들이 없다).
                import obsidian_sync
                import research_log
                # Obsidian 은 선택 사항(2026-11-17): 해당 언어의 vault 경로가 없으면 건너뛴다.
                vault_used = cfg.get("vault_path_zh" if language == "zh" else "vault_path")
                if vault_used:
                    export_obsidian.run(db, analysis, h, cfg)  # vault에 파일 저장
                    obsidian_sync.trigger(vault_used)  # 폰 동기화 트리거(실패 무시)
                research_log.record_run(cfg, h, event="web_run",
                                        extra={"cards_exported": len(cards) if apkg else 0,
                                               "ingest_stats": stats})
            except Exception as e:
                warn = ('<div class="sg-note">⚠️ '
                        + _e(T("Obsidian 뷰 갱신 실패(카드는 정상). 다음 실행에서 재갱신됩니다.",
                               "Could not update the Obsidian notes (cards are fine). "
                               "They will be regenerated on the next run.")) + '</div>')
                print(f"[web:obsidian] 경고: {e}")

        # ★ 마지막 DB 사용이 끝났다 → yield 전에 닫는다. yield 뒤에는 다른 스레드일 수 있어
        #   그때 닫으면 sqlite가 거부한다(실측된 버그).
        _close_db(db)

        stt = [it for it in analysis.get("items", []) if it.get("stt_suspect")]
        html = (_summary_html(stats, len(cards), len(stt), analysis["lesson_date"],
                              analysis.get("lesson_summary_ko", "") if demo else "",
                              weak_before, len(analysis.get("items", []) or []))
                + warn + _cards_html(cards)
                + (_pipeline_rest_html() if demo else ""))

        apkg_update = gr.update(value=str(apkg), visible=True) if apkg is not None else no_file
        yield html, apkg_update, obsidian_md
    except Exception as e:
        print(f"[web:make_cards] 오류: {e}")
        yield _status(_error_message(e), "err"), no_file, ""
    finally:
        _close_db(db)   # 정상 경로에선 이미 닫혔다(이중 호출 안전)


# ── UI ───────────────────────────────────────────────────────────────────────
def _hero() -> str:
    sub = T("전화영어 수업 녹음을 실제로 복습하게 되는 Anki 카드로 바꿉니다.",
            "Turn your phone-English lessons into Anki cards you will actually review.")
    steps = [(T("녹음", "Recording"), False),
             (T("STT · 화자분리", "STT · diarization"), not DEMO_MODE),
             (T("LLM 분석", "LLM analysis"), True),
             (T("Anki 카드", "Anki cards"), True),
             (T("Obsidian 뷰", "Obsidian notes"), True)]
    # 파이프라인 칩은 공개 데모(관객)용 설명이다. 혼자 쓰는 로컬 화면에선 폰에서 세 줄만 먹어서 뺀다.
    chips = ""
    if DEMO_MODE:
        chips = ('<div class="sg-flow">'
                 + "".join(f'<span class="sg-chip{" on" if on else ""}">{_e(t)}</span>' for t, on in steps)
                 + "</div>")
    return f'<div id="sg-hero"><h1>🎧 sounds good</h1><p class="sg-sub">{_e(sub)}</p>{chips}</div>'


def _step(n: str, title: str, desc: str = "") -> str:
    d = f'<span class="d">{_e(desc)}</span>' if desc else ""
    return f'<div class="sg-step"><span class="n">{_e(n)}</span><span class="t">{_e(title)}</span>{d}</div>'


def _theme():
    return gr.themes.Soft(primary_hue="orange", neutral_hue="slate",
                          font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"])


def _shared_components():
    """화자 선택 → 카드 만들기 → 결과·다운로드. 데모 화면과 로컬 '새 수업' 탭이 같은 순서로 만든다."""
    status_html = gr.HTML()
    speaker_radio = gr.Radio(
        elem_id="sg-speaker",
        label=T("내(학습자) 화자 선택", "Which speaker is the learner?"),
        info=T("긴 발화 샘플을 보고 고르세요.", "Use the sample lines above to tell them apart."),
        choices=[], visible=False)
    cards_btn = gr.Button(T("카드 만들기", "Build my cards"), variant="primary", visible=False)
    result_html = gr.HTML()
    apkg_out = gr.File(label=T("Anki 카드 다운로드 (.apkg)", "Download Anki deck (.apkg)"),
                       visible=False)
    obsidian_md = gr.Markdown()
    return status_html, speaker_radio, cards_btn, result_html, apkg_out, obsidian_md


def build_ui() -> gr.Blocks:
    kw = {} if _GR_MAJOR >= 6 else {"theme": _theme(), "css": CSS}
    # fill_width=True: Gradio 6는 기본값이면 페이지 폭을 '활성 탭 내용의 고유 폭'에 맞춰 줄인다 →
    # 새 수업 탭은 410px, 이력 탭은 900px처럼 탭마다 좌우 폭이 달라 보였다(2026-11-07 사용자 피드백).
    # 꽉 채우게 하고 CSS의 .gradio-container max-width 로만 상한을 둔다.
    with gr.Blocks(title="sounds good — phone-English review cards", fill_width=True, **kw) as demo:
        # 컴포넌트는 표시 순서대로 생성하고, 이벤트 핸들러는 맨 끝에 등록한다.
        if DEMO_MODE:
            gr.HTML(_hero())
        else:
            # 머리글 오른쪽에 [＋ 새 수업] — 등록은 탭이 아니라 팝업(오버레이)에서 한다(2026-11-07 사용자 요청).
            with gr.Row(elem_id="sg-head"):
                gr.HTML(_hero(), scale=6)
                add_btn = gr.Button(T("＋ 새 수업", "＋ New lesson"), variant="primary",
                                    scale=0, min_width=132, elem_id="sg-add")
        state = gr.State()

        # ── 1단계 입력 (모드별) ──
        if DEMO_MODE:
            gr.HTML(
                '<div class="sg-note">This public demo runs on <b>pre-transcribed sample lessons</b>. '
                'Real transcription (WhisperX + pyannote speaker diarization) needs a GPU and runs in '
                'the local / private version — everything after it (LLM analysis, card building, '
                'Anki export) is <b>really executed here</b>.<br>'
                'The lessons are real English classes taken by a Korean learner, so the cards explain '
                'things <b>in the learner\'s native language</b>.</div>')
            gr.HTML(_step("STEP 1", "Pick a lesson", "a real 10-minute class, already transcribed"))
            sample_dd = gr.Dropdown(label="Sample lesson", choices=list(SAMPLES.keys()),
                                    value=list(SAMPLES.keys())[0])
            start_btn = gr.Button("Load lesson", variant="primary")
            (status_html, speaker_radio, cards_btn,
             result_html, apkg_out, obsidian_md) = _shared_components()
        else:
            # 홈 = 이력(기본) + 현황 탭. 등록은 [＋ 새 수업] 팝업(2026-11-07 사용자 요청 3차).
            # 이전 형태들: 한 페이지+아코디언(이력 펼치면 업로드 밀림) → 새 수업/현황/이력 3탭 → 지금.
            with gr.Tabs():
                with gr.Tab(T("이력", "History")):
                    # 검색 · 언어 · 월 필터 (2026-11-07 사용자 요청). min_width 로 폴드 접은 화면(~350px)에선 세로로 쌓이게.
                    hist_q = gr.Textbox(show_label=False, max_lines=1,
                                        placeholder=T("검색 — 주제, 정답 문장, 한국어 뜻, 내 발화",
                                                      "Search topic, sentences, Korean, what I said"))
                    with gr.Row():
                        hist_lang = gr.Dropdown(show_label=False, scale=1, min_width=120, value=_ALL,
                                                choices=[(T("전체 언어", "All languages"), _ALL),
                                                         (T("영어", "English"), "en"),
                                                         (T("중국어", "Chinese"), "zh")])
                        hist_month = gr.Dropdown(show_label=False, scale=1, min_width=120, value=_ALL,
                                                 choices=month_choices())
                        hist_toggle = gr.Button(_toggle_label(False), size="sm", scale=0, min_width=120)
                    # 목록 = 라디오(항목을 누르면 그 회차로 접히고 아래 상세). 표가 아니라 폭에 맞춰 흐른다.
                    _init_choices, _ = history_view()
                    # interactive=True 필수: 이 라디오는 이벤트 '출력'으로만 쓰여(.select 는 입력이 아님) Gradio 가
                    # 자동으로 disabled 로 렌더링했다 → 클릭 무반응 + 회색(2026-11-07 실측).
                    history = gr.Radio(choices=_init_choices, value=None, show_label=False,
                                       elem_id="sg-hist", interactive=True)
                    hist_collapsed = gr.State(False)  # 목록이 선택 회차 한 항목으로 접혀 있는가
                    hist_detail = gr.HTML()
                    hist_apkg = gr.File(label=T("이 회차 Anki 카드 (.apkg)",
                                                "Anki deck for this lesson (.apkg)"),
                                        visible=False, interactive=False)
                    hist_txt = gr.File(label=T("전사본 내려받기 (.md) — 다른 AI에 붙여넣어 분석받기",
                                               "Download transcript (.md)"),
                                       visible=False, interactive=False)
                    hist_raw_btn = gr.Button(T("전사 원본 보기", "Show raw transcript"),
                                             size="sm", visible=False)
                    hist_raw = gr.HTML()
                    hist_state = gr.State()
                    hist_raw_open = gr.State(False)  # 전사 원본 펼침 여부(같은 버튼으로 토글)

                with gr.Tab(T("카드", "Cards")):
                    # 카드 검색(2026-11-17): 회차를 거치지 않고 카드를 바로 찾는다.
                    card_q = gr.Textbox(show_label=False, max_lines=1,
                                        placeholder=T("카드 검색 — 정답 문장, 한국어 뜻, 내 발화, 설명",
                                                      "Search cards — sentence, Korean, what I said, explanation"))
                    with gr.Row():
                        card_lang = gr.Dropdown(show_label=False, scale=1, min_width=120, value=_ALL,
                                                choices=[(T("전체 언어", "All languages"), _ALL),
                                                         (T("영어", "English"), "en"),
                                                         (T("중국어", "Chinese"), "zh")])
                        card_issue = gr.Dropdown(show_label=False, scale=1, min_width=120, value=_ALL,
                                                 choices=issue_choices())
                    card_html = gr.HTML()

                with gr.Tab(T("현황", "Stats")):
                    # 접속 때마다 DB에서 다시 그린다(demo.load / 카드 생성 뒤 .then).
                    dash_top = gr.HTML()
                    dash_detail = gr.HTML()

            # ── [＋ 새 수업] 팝업: Gradio 6엔 모달이 없어 position:fixed 오버레이 Column 으로 만든다 ──
            # SG_MODAL_OPEN=1 은 미리보기/캡처용(처음부터 열림). 평소엔 닫힌 채 시작.
            with gr.Column(visible=os.getenv("SG_MODAL_OPEN") == "1", elem_id="sg-modal") as modal:
                with gr.Column(elem_id="sg-modal-panel"):
                    with gr.Row(elem_id="sg-modal-head"):
                        gr.HTML(_step(T("새 수업", "NEW"),
                                      T("수업 녹음 올리기", "Upload a lesson recording"),
                                      T("전사·화자분리까지 수 분 걸립니다",
                                        "transcription + speaker diarization takes a few minutes")),
                                scale=8)
                        close_btn = gr.Button("✕", size="sm", scale=0, min_width=44, elem_id="sg-close")
                    # 라벨 칩 없이 드롭존만, 그 아래 언어 선택(가로), 버튼 — 세로 한 줄기.
                    audio_in = gr.File(show_label=False, file_types=[".m4a"], type="filepath",
                                       height=110, elem_id="sg-upload")
                    gr.HTML(f'<div class="sg-cap">{_e(T("수업 언어 — 자동 감지하지 않으니 매번 고르세요", "Lesson language — no auto-detection"))}</div>')
                    lang_radio = gr.Radio(
                        show_label=False, elem_id="sg-lang",
                        choices=[(T("영어", "English"), "en"), (T("중국어", "Chinese"), "zh")],
                        value="en")
                    start_btn = gr.Button(T("분석 시작", "Start analysis"), variant="primary")
                    (status_html, speaker_radio, cards_btn,
                     result_html, apkg_out, obsidian_md) = _shared_components()

        if DEMO_MODE:
            gr.HTML(
                '<div class="sg-note"><b>How it works locally</b><br>'
                'The full pipeline — WhisperX transcription with speaker diarization, LLM analysis, '
                'SQLite history, Anki <code>.apkg</code> export and Obsidian notes — runs from one command '
                f'(<code>python main.py lesson.m4a</code>). Pipeline v{_e(PIPELINE_VERSION)}.</div>')

        # ── 이벤트 (pipeline은 concurrency_limit=1로 중복 실행 방지) ──
        if DEMO_MODE:
            start_btn.click(demo_load_sample, inputs=[sample_dd, state],
                            outputs=[status_html, speaker_radio, state, cards_btn], concurrency_limit=1)
        else:
            start_btn.click(step1_transcribe, inputs=[audio_in, lang_radio],
                            outputs=[status_html, speaker_radio, state, cards_btn], concurrency_limit=1)
        cards_evt = cards_btn.click(make_cards, inputs=[speaker_radio, state],
                                    outputs=[result_html, apkg_out, obsidian_md], concurrency_limit=1)
        if not DEMO_MODE:
            list_outputs = [history, hist_collapsed, hist_toggle]
            home_outputs = [dash_top, dash_detail, *list_outputs, hist_month, hist_q, hist_lang,
                            card_html, card_issue, card_q, card_lang]
            cards_evt.then(home_view, outputs=home_outputs)  # 새 회차 즉시 반영
            for comp in (card_q, card_lang, card_issue):
                comp.change(cards_view, inputs=[card_q, card_lang, card_issue], outputs=card_html)
            history.select(on_history_select, inputs=[hist_collapsed, hist_q, hist_lang, hist_month],
                           outputs=[*list_outputs,
                                    hist_detail, hist_apkg, hist_txt, hist_raw_btn, hist_raw,
                                    hist_state, hist_raw_open])
            hist_toggle.click(toggle_history,
                              inputs=[hist_collapsed, hist_state, hist_q, hist_lang, hist_month],
                              outputs=list_outputs)
            for comp in (hist_q, hist_lang, hist_month):
                comp.change(filter_history, inputs=[hist_q, hist_lang, hist_month, hist_state],
                            outputs=list_outputs)
            hist_raw_btn.click(toggle_raw_transcript, inputs=[hist_state, hist_raw_open],
                               outputs=[hist_raw, hist_raw_open, hist_raw_btn])
            # 팝업 열기/닫기. 닫아도 안의 결과(카드·apkg)는 남는다 — 다시 열면 그대로 보인다.
            add_btn.click(lambda: gr.update(visible=True), outputs=modal)
            close_btn.click(lambda: gr.update(visible=False), outputs=modal)
            # value= 는 앱 시작 시 한 번만 평가된다 → 서비스를 재시작하기 전까지 새로 처리한
            # 회차가 새 접속에서도 안 보였다(실측: 8/14). 접속할 때마다 DB에서 다시 읽는다.
            demo.load(home_view, outputs=home_outputs)
    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.queue()  # concurrency_limit / 제너레이터 스트리밍을 위해 큐 활성화
    kw = {"theme": _theme(), "css": CSS} if _GR_MAJOR >= 6 else {}

    # ── 외부 노출(NAS 역방향 프록시) 옵션 — 전부 미설정이면 기존 동작(로컬 전용·인증 없음) 그대로 ──
    # SG_HOST=0.0.0.0  : 랜에서 접근 가능하게 바인딩(프록시가 붙으려면 필요)
    # SG_PORT=7860     : 포트 고정
    # SG_AUTH_USER/PASS: 로그인 요구. ★ 외부에 열 거면 반드시 설정할 것 —
    #   이 앱은 업로드·LLM 호출·개인 수업 이력 열람이 전부 가능하므로 무인증 공개는 위험하다.
    host = os.getenv("SG_HOST")
    if host:
        kw["server_name"] = host
    port = os.getenv("SG_PORT")
    if port:
        kw["server_port"] = int(port)
    user, pw = os.getenv("SG_AUTH_USER"), os.getenv("SG_AUTH_PASS")
    if user and pw:
        kw["auth"] = (user, pw)
    elif host:
        print("[app] 경고: SG_HOST로 외부 바인딩했지만 SG_AUTH_USER/SG_AUTH_PASS가 없습니다 "
              "— 인증 없이 열립니다. 외부 공개라면 반드시 설정하세요.")
    ui.launch(max_file_size="30mb", **kw)
