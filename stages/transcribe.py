"""S2: WhisperX STT + 화자분리 → work/{hash}/02_transcript.json. v1.2 계약.

★ 핵심: segment-level speaker는 쓰지 않는다(30초 다수결, 순도 51~100%로 널뜀 — 실측 L13).
   word_segments의 단어 단위 speaker로 turn을 재구성한다(D17). turn 재구성/상태 판정은
   whisperx 없이 검증 가능하도록 순수 함수(build_turns / diarization_status)로 분리했다.

외부 의존: whisperx만 (torch 등을 끌고 옴). whisperx는 run() 내부에서 지연 임포트.
"""
from __future__ import annotations

import gc
import json
import os
from pathlib import Path

from util import PIPELINE_VERSION, resolve, stage_output

# whisperx.load_audio가 내부에서 쓰는 샘플레이트(16kHz mono) — duration 계산용.
_SAMPLE_RATE = 16000
# analyzable 임계값 (D18, v1.3에서 상향: 영어 회차 실측 반영). 조정 가능하나 필터 제거는 금지.
_MIN_WORDS = 10
_MIN_DURATION = 3.0
# S12: 중국어는 align이 글자 단위 word를 내놓아 '단어 수' 기준이 왜곡된다 → 글자 수 기준.
# 15자는 가설값 — 실측(첫 zh 회차 게이트)에서 조정 가능. 지원 언어는 아래 두 개뿐.
_MIN_CHARS_ZH = 15
_SUPPORTED_LANGUAGES = ("en", "zh")
_DIARIZE_MODEL = "pyannote/speaker-diarization-3.1"  # community-1은 gated 403 (L16/D19)


def diarization_status(word_segments: list[dict]) -> str:
    """단어 단위 speaker 라벨 유무로 판정: 전부 있음=ok, 일부 없음=partial, 전부 없음=failed."""
    if not word_segments:
        return "failed"
    unknown = sum(1 for w in word_segments if not w.get("speaker"))
    if unknown == 0:
        return "ok"
    if unknown == len(word_segments):
        return "failed"
    return "partial"


def build_turns(word_segments: list[dict], language: str = "en") -> list[dict]:
    """word_segments(단어 단위 speaker)를 인접 동일화자 병합으로 turn 재구성.

    각 turn: start(첫 유효 단어 start), end(마지막 유효 단어 end), speaker,
    text(단어 공백 연결), word_count, analyzable(v1.3: 단어수>=10 AND 길이>=3.0초;
    S12: zh는 word=글자라 글자수>=_MIN_CHARS_ZH AND 길이>=3.0초).
    speaker 없는 단어는 "UNKNOWN". text가 빈 turn은 버린다.
    """
    turns: list[dict] = []
    cur: dict | None = None
    prev_end: float | None = None

    def finalize(c: dict) -> dict:
        tokens = [t for t in c["words"] if t]
        starts = [s for s in c["starts"] if s is not None]
        ends = [e for e in c["ends"] if e is not None]
        start = starts[0] if starts else (c["fallback_start"] if c["fallback_start"] is not None else 0.0)
        end = ends[-1] if ends else start
        wc = len(tokens)
        duration = end - start
        # zh는 토큰이 글자라 '단어 수'가 정보량을 과소평가한다 → 글자 수(공백 제외)로 판정.
        if language == "zh":
            long_enough = sum(len(t) for t in tokens) >= _MIN_CHARS_ZH
        else:
            long_enough = wc >= _MIN_WORDS
        # whisperx는 pandas 경유라 start/end가 numpy.float64 → 비교 결과가 numpy.bool_이 되고
        # json이 직렬화하지 못한다(numpy 2.x는 에러 메시지가 헷갈리게 'bool'로 나옴). 네이티브 캐스팅 필수.
        return {
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "speaker": str(c["speaker"]),
            "text": " ".join(tokens),
            "word_count": int(wc),
            "analyzable": bool(long_enough and duration >= _MIN_DURATION),
        }

    for w in word_segments:
        spk = w.get("speaker") or "UNKNOWN"
        if cur is None or spk != cur["speaker"]:
            if cur is not None:
                t = finalize(cur)
                if t["text"]:
                    turns.append(t)
                    prev_end = t["end"]
            cur = {"speaker": spk, "words": [], "starts": [], "ends": [], "fallback_start": prev_end}
        cur["words"].append((w.get("word") or "").strip())
        cur["starts"].append(w.get("start"))
        cur["ends"].append(w.get("end"))

    if cur is not None:
        t = finalize(cur)
        if t["text"]:
            turns.append(t)
    return turns


def run(wav_path: Path, audio_hash: str, cfg: dict, source_filename: str,
        language: str = "en") -> Path:
    """WhisperX 실행 → work/{hash}/02_transcript.json 생성. 존재하면 스킵."""
    if language not in _SUPPORTED_LANGUAGES:
        raise ValueError(f"[transcribe] 지원하지 않는 언어: {language!r} (지원: {_SUPPORTED_LANGUAGES})")
    wav_path = Path(wav_path)
    out_path = stage_output(Path(cfg["work_dir"]), audio_hash, "02_transcript.json")

    if out_path.exists():
        try:
            cached = json.loads(out_path.read_text(encoding="utf-8"))
            # 언어도 캐시 키다 — 같은 녹음을 다른 언어로 다시 돌리면 재전사해야 한다
            # (analyze의 my_speaker 캐시 버그와 같은 부류, S12에서 처음부터 차단).
            if (cached.get("pipeline_version") == PIPELINE_VERSION
                    and cached.get("language", "en") == language):
                print(f"[transcribe] 캐시 사용, 스킵: {out_path}")
                return out_path
            print("[transcribe] pipeline_version 또는 language 불일치 → 캐시 무시하고 재생성")
        except (json.JSONDecodeError, OSError):
            print("[transcribe] 캐시 손상 → 재생성")

    # HF 토큰을 먼저 확인(전사 5분 돌린 뒤 토큰 때문에 죽는 낭비 방지).
    try:
        hf_token = resolve(cfg, "hf_token")
    except Exception as e:
        raise RuntimeError(
            "[transcribe] HF_TOKEN 환경변수 설정 + "
            "https://huggingface.co/pyannote/speaker-diarization-3.1 라이선스 동의 필요"
        ) from e

    import torch
    import whisperx
    from whisperx.diarize import DiarizationPipeline

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        print("[transcribe] 경고: CUDA를 찾지 못해 cpu로 폴백합니다 (매우 느립니다).")

    # 8GB VRAM(RTX 3060 Ti, L4) 기본값. 데스크톱이 GPU를 공유하므로 작게 잡는다.
    # 부족하면 config에 whisper_batch_size를 더 낮춰(예: 2, 1) 조절.
    batch_size = int(cfg.get("whisper_batch_size", 4))

    def _empty_cache() -> None:
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()

    print(f"[transcribe] 모델 로드 (whisper={cfg['whisper_model']}, device={device}, "
          f"compute_type=int8, batch_size={batch_size}, language={language})")
    model = whisperx.load_model(cfg["whisper_model"], device, compute_type="int8", language=language)
    audio = whisperx.load_audio(str(wav_path))
    duration_sec = round(len(audio) / _SAMPLE_RATE, 2)

    print("[transcribe] 전사 중...")
    result = model.transcribe(audio, batch_size=batch_size, language=language)
    # 8GB에선 다음 단계 모델을 올리기 전에 whisper 모델을 내려 VRAM을 비워야 OOM을 피한다 (L20).
    del model
    _empty_cache()

    print("[transcribe] 정렬(align) 중...")
    # zh는 whisperx 기본 zh wav2vec2 모델(글자 단위 정렬, 최초 1회 다운로드 큼).
    model_a, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(result["segments"], model_a, metadata, audio, device, return_char_alignments=False)
    del model_a
    _empty_cache()

    print(f"[transcribe] 화자분리 중 (model={_DIARIZE_MODEL}, min=max=2)...")
    try:
        diarize_model = DiarizationPipeline(model_name=_DIARIZE_MODEL, token=hf_token, device=device)
        diarize_df = diarize_model(audio, min_speakers=2, max_speakers=2)
        result = whisperx.assign_word_speakers(diarize_df, result)
    except Exception as e:
        # 모델 이용 동의·토큰 문제는 '화자분리 실패'로 저장하면 안 된다(2026-11-17): 실패한 전사가 캐시에 남아
        # 토큰을 고친 뒤에도 계속 '화자분리 전체 실패'로 나온다. 원인을 알려 주고 여기서 멈춘다.
        msg = f"{type(e).__name__}: {e}".lower()
        if any(k in msg for k in ("401", "403", "gated", "unauthorized", "access to model",
                                  "token", "could not download")):
            raise RuntimeError(
                "[transcribe] 화자 구분 모델을 받지 못했습니다. HuggingFace 에서 "
                "pyannote/speaker-diarization-3.1 과 pyannote/segmentation-3.0 두 모델의 이용 조건에 모두 "
                "동의했는지, HF_TOKEN 이 올바른지 확인하세요 (SETUP.md 3단계)."
            ) from e
        # 그 밖의 화자분리 실패: 전체 UNKNOWN으로 저장하고 진행. 중단 판단은 main(S8)이 한다.
        print(f"[transcribe] 경고: 화자분리 실패 → 전체 UNKNOWN 처리합니다: {e}")

    word_segments = result.get("word_segments", []) or []
    turns = build_turns(word_segments, language)
    status = diarization_status(word_segments)

    transcript = {
        "pipeline_version": PIPELINE_VERSION,
        "audio_hash": audio_hash,
        "source_filename": source_filename,
        "duration_sec": duration_sec,
        "language": language,
        "diarization_status": status,
        "turns": turns,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    tmp_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, out_path)

    n_analyzable = sum(1 for t in turns if t["analyzable"])
    print(f"[transcribe] 완료: turns={len(turns)} (analyzable={n_analyzable}), "
          f"diarization_status={status} → {out_path}")
    return out_path
