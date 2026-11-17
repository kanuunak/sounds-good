"""S7: Obsidian export → vault에 md 4종. 00_ARCHITECTURE.md 계약.

절대 규칙:
- 뷰 3개(Speaking Practice / Sentence Bank / Weak Patterns)는 DB에서 매번 통째로 재생성(덮어쓰기).
  append 금지, 기존 파일 파싱 금지 (D1 — md는 뷰지 원본이 아니다).
- 모든 파일 쓰기는 임시파일 → os.replace() 원자적 교체 (L7 — NAS 동기화 중 반쪽 파일 방지).
- 뷰 파일 상단에 자동생성 경고 주석. Dataview 미사용, frontmatter는 archive에만 최소한(D12).

외부 의존: 없음 (표준 라이브러리만).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from util import stage_output

_AUTOGEN = "<!-- 자동 생성 파일. 직접 수정 금지 — 다음 실행 시 덮어써짐 -->"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _cell(value: object) -> str:
    """표 셀용: None 안전 + 파이프/개행이 표를 깨지 않도록 정리."""
    s = "" if value is None else str(value)
    return s.replace("|", "\\|").replace("\n", " ").strip()


def _write_speaking_practice(vault: Path, db, language: str = "en") -> None:
    practice = db.recent_practice(lessons=2, language=language)[:25]
    lang_label = "중국어" if language == "zh" else "영어"
    lines = [_AUTOGEN, "", f"## 오늘의 연습 — 한국어를 보고 {lang_label}로 말하기", ""]
    if not practice:
        lines.append("_아직 연습할 문장이 없습니다._")
    else:
        for i, p in enumerate(practice, 1):
            lines.append(f"{i}. {p.get('korean_meaning') or ''}")
            lines.append("   > [!note]- 정답")
            lines.append(f"   > {p.get('natural_sentence') or ''}")
            lines.append("")
    _atomic_write(vault / "Speaking Practice.md", "\n".join(lines) + "\n")


def _write_sentence_bank(vault: Path, db, language: str = "en") -> None:
    rows = db.all_sentences(language=language)
    zh = language == "zh"
    # 최근 수업일을 같이 찍는다. 날짜가 없으면 어느 회차 문장인지 분별할 수 없어서
    # "업데이트가 된 건가?" 를 파일만 보고 판단할 수 없었다(실사용 피드백).
    pin_h = " 병음 |" if zh else ""
    pin_d = " --- |" if zh else ""
    lines = [_AUTOGEN, "", "# Sentence Bank", "",
             "> 최근 = 이 문장이 마지막으로 등장한 수업 날짜 (정렬 기준도 이 날짜입니다).", "",
             f"| 최근 | 자연스러운 문장 |{pin_h} 한국어 | 유형 | 등장 |",
             f"| --- | --- |{pin_d} --- | --- | --- |"]
    for r in rows[:100]:
        pin_c = f" {_cell(r.get('pinyin'))} |" if zh else ""
        lines.append(
            f"| {_cell(r.get('last_date'))} | {_cell(r.get('natural_sentence'))} "
            f"|{pin_c} {_cell(r.get('korean_meaning'))} "
            f"| {_cell(r.get('issue_type'))} | {r.get('seen_count', 0)} |"
        )
    if len(rows) > 100:
        lines += ["", f"…외 {len(rows) - 100}개 (검색은 이 파일에서 Ctrl+F)"]
    _atomic_write(vault / "Sentence Bank.md", "\n".join(lines) + "\n")


def _write_weak_patterns(vault: Path, db, language: str = "en") -> None:
    stats = db.pattern_stats(language=language)
    lines = [_AUTOGEN, "", "# Weak Patterns", "",
             "> ※ correction 카드만 집계합니다 (upgrade·이전 카드 제외 → S10 이후 데이터부터).", "",
             "| 패턴 | 누적 | 최근 | 대표 예문 |",
             "| --- | --- | --- | --- |"]
    for s in stats:
        lines.append(
            f"| {_cell(s.get('issue_type'))} | {s.get('total_count', 0)} "
            f"| {s.get('recent_count', 0)} | {_cell(s.get('example'))} |"
        )
    _atomic_write(vault / "Weak Patterns.md", "\n".join(lines) + "\n")


def _write_archive(vault: Path, analysis: dict, audio_hash: str) -> None:
    lesson_date = analysis.get("lesson_date", "") or "unknown-date"
    summary = analysis.get("lesson_summary_ko", "") or ""
    topic = (summary.split(".")[0].strip() if summary else "")[:80]
    items = analysis.get("items", []) or []
    card_items = [it for it in items if not it.get("stt_suspect")]
    stt_items = [it for it in items if it.get("stt_suspect")]

    # frontmatter (값에 콜론 가능 → 따옴표로 감싸고 내부 " 는 ' 로 치환)
    topic_q = topic.replace('"', "'")
    lines = [
        "---",
        f"date: {lesson_date}",
        f'topic: "{topic_q}"',
        f"card_count: {len(card_items)}",
        "---",
        _AUTOGEN,
        "",
        f"# {lesson_date} 수업 상세",
        "",
        "## 요약",
        "",
        summary if summary else "_요약 없음_",
        "",
        "## 분석 항목",
        "",
        "| 시각 | 우선순위 | 유형 | 당시 발화 | 자연스러운 표현 | 설명 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for it in card_items:
        lines.append(
            f"| {_cell(it.get('timestamp'))} | {_cell(it.get('priority'))} "
            f"| {_cell(it.get('issue_type'))} | {_cell(it.get('original_sentence'))} "
            f"| {_cell(it.get('natural_sentence'))} | {_cell(it.get('explanation_ko'))} |"
        )

    if stt_items:
        lines += ["", "## STT 오인식 의심 — 원본 확인 필요", ""]
        for it in stt_items:
            ts = it.get("timestamp") or "??:??"
            lines.append(f"- **[{ts}]** {it.get('original_sentence') or ''}")

    filename = f"{lesson_date}-{audio_hash}-detail.md"
    _atomic_write(vault / "_archive" / filename, "\n".join(lines) + "\n")


def _mmss(sec: float) -> str:
    m, s = divmod(int(sec or 0), 60)
    return f"{m:02d}:{s:02d}"


def build_transcript_md(cfg: dict, audio_hash: str, lesson_date: str) -> str | None:
    """전사·화자분리 결과를 사람이 읽을 수 있는 마크다운으로. 없으면 None.

    볼트 저장(_write_transcript)과 웹 UI 다운로드가 **이 함수를 공유한다** — 복제하면
    두 산출물이 어긋난다. 다른 LLM(웹 GPT/Claude)에 통째로 붙여넣어 분석받는 용도도 겸한다.
    """
    work = Path(cfg["work_dir"])
    tp = stage_output(work, audio_hash, "02_transcript.json")
    if not tp.exists():
        return None
    tr = json.loads(tp.read_text(encoding="utf-8"))

    my = ""
    sp = stage_output(work, audio_hash, "03_speakers.json")
    if sp.exists():
        try:
            my = json.loads(sp.read_text(encoding="utf-8")).get("my_speaker", "")
        except (json.JSONDecodeError, OSError):
            my = ""

    turns = tr.get("turns", [])
    n_an = sum(1 for t in turns if t.get("analyzable"))
    lines = [
        "---",
        f"date: {lesson_date}",
        f"audio_hash: {audio_hash}",
        f"diarization: {tr.get('diarization_status', '')}",
        f"turns: {len(turns)}",
        f"analyzable: {n_an}",
        "---",
        _AUTOGEN,
        "",
        f"# {lesson_date} 전사 원본 (검수용)",
        "",
        f"> 원본 파일: `{tr.get('source_filename', '')}` · 길이 {tr.get('duration_sec', 0):.0f}초",
        f"> **ME** = 내 발화({my or '?'}). ✅ = LLM 분석 대상(analyzable) 턴.",
        "",
    ]
    for t in turns:
        who = "**ME**" if my and t.get("speaker") == my else (t.get("speaker") or "?")
        mark = " ✅" if t.get("analyzable") else ""
        lines.append(f"- `[{_mmss(t.get('start'))}]` {who}{mark} — {(t.get('text') or '').strip()}")

    return "\n".join(lines) + "\n"


def _write_transcript(vault: Path, cfg: dict, audio_hash: str, lesson_date: str) -> bool:
    """전사 원본을 vault archive에 남긴다(품질 검수용). work/ 는 비동기화 대상이라 따로 쓴다."""
    md = build_transcript_md(cfg, audio_hash, lesson_date)
    if md is None:
        return False
    # 카드 분석(detail)과 섞이지 않게 전사 원본은 하위 폴더에 둔다.
    _atomic_write(vault / "_archive" / "transcript" / f"{lesson_date}-{audio_hash}.md", md)
    return True


def run(db, analysis: dict, audio_hash: str, cfg: dict) -> None:
    language = analysis.get("language", "en")
    if language == "zh":
        # S12: zh 뷰는 별도 폴더. 같은 vault 안의 새 하위 폴더면 만들어 준다(부모=vault 존재 필수).
        vault = Path(cfg.get("vault_path_zh", "")).expanduser()
        if not str(cfg.get("vault_path_zh", "")):
            raise RuntimeError("[export_obsidian] config에 vault_path_zh가 없습니다 (S12)")
        if not vault.exists():
            if not vault.parent.exists():
                raise RuntimeError(f"[export_obsidian] vault_path_zh의 부모가 없습니다: {vault.parent}")
            vault.mkdir()
    else:
        vault = Path(cfg["vault_path"]).expanduser()
    if not vault.exists():
        raise FileNotFoundError(
            f"[export_obsidian] vault_path가 존재하지 않습니다: {vault} — "
            f"config.yaml의 vault_path를 실제 Obsidian vault 경로로 설정하세요."
        )
    _write_speaking_practice(vault, db, language)
    _write_sentence_bank(vault, db, language)
    _write_weak_patterns(vault, db, language)
    _write_archive(vault, analysis, audio_hash)
    got_tr = _write_transcript(vault, cfg, audio_hash, analysis.get("lesson_date", "") or "unknown-date")
    print(f"[export_obsidian] 뷰 3종 + archive{' + 전사원본' if got_tr else ''} 갱신 완료 → {vault}")
