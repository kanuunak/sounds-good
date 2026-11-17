"""S3: 내 화자 선택 CLI.

02_transcript.json의 turns를 화자별로 보여주고, 사용자가 자기 화자를 고른다.
전역 기본 화자 옵션은 두지 않는다 — SPEAKER_00/01 라벨은 녹음마다 같은 사람에게
고정되지 않기 때문에 매 수업 확인이 안전하다 (결정 D10).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from util import stage_output


def _mmss(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def _collect(turns: list[dict]) -> dict[str, dict]:
    """화자별 총 발화 시간과 analyzable 턴 목록을 모은다."""
    by_speaker: dict[str, dict] = {}
    for t in turns:
        spk = t["speaker"]
        info = by_speaker.setdefault(spk, {"total_sec": 0.0, "samples": []})
        info["total_sec"] += t["end"] - t["start"]
        if t.get("analyzable"):
            info["samples"].append(t)
    return by_speaker


def _pick_samples(turns: list[dict], n: int = 3) -> list[dict]:
    """길이가 긴 순 상위 n개를 고른 뒤 시간순으로 되돌린다.

    짧은 맞장구나 턴 경계 파편은 화자 판별에 도움이 안 되고 오히려 헷갈리게 한다.
    """
    longest = sorted(turns, key=lambda t: t["end"] - t["start"], reverse=True)[:n]
    return sorted(longest, key=lambda t: t["start"])


def _prompt(speakers: list[str]) -> str:
    valid = {str(i) for i in range(len(speakers))}
    hint = "/".join(sorted(valid))
    while True:
        answer = input(f"\n내 화자 번호는? [{hint}]: ").strip()
        if answer in valid:
            return speakers[int(answer)]
        print(f"  {hint} 중 하나를 입력하세요.")


def run(transcript_path: Path, audio_hash: str, cfg: dict) -> Path:
    """내 화자를 판정 → work/{hash}/03_speakers.json 생성. 존재하면 스킵."""
    out_path = stage_output(Path(cfg["work_dir"]), audio_hash, "03_speakers.json")
    if out_path.exists():
        print(f"[speakers] 캐시 사용, 화자 선택 건너뜀: {out_path}")
        return out_path

    try:
        transcript = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
        turns: list[dict] = transcript["turns"]
        by_speaker = _collect(turns)

        known = sorted(s for s in by_speaker if s != "UNKNOWN")
        if not known:
            raise RuntimeError(
                "화자 라벨이 UNKNOWN뿐입니다. diarization_status='failed'인 수업은 "
                "main에서 중단되어야 하며 이 단계에 도달해서는 안 됩니다."
            )

        print("\n=== 화자를 선택하세요 ===")
        for idx, spk in enumerate(known):
            info = by_speaker[spk]
            samples = _pick_samples(info["samples"])
            print(f"\n[{idx}] {spk} — 총 발화 {info['total_sec']:.0f}초")
            if not samples:
                print("  (분석 가능한 긴 발화 없음)")
            for t in samples:
                text = t["text"].strip()
                if len(text) > 90:
                    text = text[:90] + "..."
                print(f"  [{_mmss(t['start'])}] {text}")

        my_speaker = _prompt(known)

        payload = {"my_speaker": my_speaker, "confirmed_by": "manual"}
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp_path, out_path)

        print(f"[speakers] 완료: my_speaker={my_speaker} → {out_path}")
        return out_path

    except Exception as exc:
        raise RuntimeError(f"[speakers] 화자 선택 실패: {exc}") from exc
