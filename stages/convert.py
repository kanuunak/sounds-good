"""S1: m4a → 16kHz mono wav 변환 (ffmpeg). 00_ARCHITECTURE.md v1.2 계약.

외부 의존: 없음 (표준 라이브러리 + 시스템 ffmpeg). util만 임포트한다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from util import file_hash, stage_output


def run(audio_path: Path, cfg: dict) -> Path:
    """m4a → work/{hash}/01_audio.wav. 이미 존재하면 변환 없이 경로 반환."""
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"[convert] 입력 오디오 파일이 없습니다: {audio_path}")

    h = file_hash(audio_path)
    out_path = stage_output(Path(cfg["work_dir"]), h, "01_audio.wav")

    if out_path.exists():
        print(f"[convert] 캐시 사용, 변환 건너뜀: {out_path}")
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "[convert] ffmpeg를 PATH에서 찾을 수 없습니다. "
            "설치 후 다시 실행하세요 (예: 'sudo pacman -S ffmpeg' 또는 'apt install ffmpeg')."
        )

    # 임시파일도 .wav로 끝나야 ffmpeg가 wav muxer를 고른다 (.tmp면 포맷 추론 실패).
    tmp_path = out_path.with_name(out_path.name + ".tmp.wav")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(audio_path),
        "-ac", "1",
        "-ar", "16000",
    ]
    if cfg.get("bandpass_filter"):
        # 협대역 전화 녹음용 (광대역 녹음 유입 대비 옵션)
        cmd += ["-af", "highpass=f=100,lowpass=f=3600"]
    cmd += [str(tmp_path)]

    print(f"[convert] 변환 시작: {audio_path.name} → {out_path}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"[convert] ffmpeg 변환 실패 (returncode={result.returncode}):\n{result.stderr}"
        )

    # 원자적 교체: 중간에 죽어 부분 파일이 캐시(01_audio.wav)로 오인되는 사고 방지.
    os.replace(tmp_path, out_path)
    print(f"[convert] 변환 완료: {out_path}")
    return out_path
