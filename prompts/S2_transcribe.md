# S2: WhisperX STT + 화자분리 구현 (v1.3)

첨부된 00_ARCHITECTURE.md의 계약을 따르는 `stages/transcribe.py`를 작성하라.

## 배경 (구현 판단에 필요)

- 입력은 협대역(0~3.4kHz) 전화 녹음, mono 16kHz wav, 화자 2명(학습자 + 원어민 튜터)
- 실행 환경: RTX 3060 Ti 8GB VRAM → compute_type은 int8 사용
- pyannote 모델은 HuggingFace 토큰 필요 (util.resolve(cfg, "hf_token"))

## 요구사항

```python
def run(wav_path: Path, audio_hash: str, cfg: dict, source_filename: str) -> Path:
    """WhisperX 실행 → work/{hash}/02_transcript.json 생성. 존재하면 스킵."""
```

- whisperx 라이브러리 사용: `load_model(cfg["whisper_model"], device, compute_type="int8")`
  → transcribe(language="en") → align → DiarizationPipeline(min_speakers=2, max_speakers=2)
  → assign_word_speakers
- `batch_size`는 `cfg["whisper_batch_size"]`에서 읽는다(기본 4). 하드코딩 금지 —
  3060 Ti 8GB에서 large-v3 + batch_size 16은 CUDA OOM으로 죽는다(실측).
- **JSON 저장 전 모든 수치를 파이썬 네이티브 타입으로 캐스팅하라.**
  `float(start)`, `int(word_count)`, `bool(analyzable)`. whisperx 출력은 numpy 스칼라를
  포함하며 그대로 json.dumps 하면 TypeError로 죽는다(실측).
- **DiarizationPipeline의 모델은 반드시 `pyannote/speaker-diarization-3.1`을 명시한다.**
  whisperx 최신 버전의 기본값은 `speaker-diarization-community-1`인데 별도 승인이 필요한
  gated repo라 403으로 죽는다 (실측 확인).
- CUDA 없으면 device="cpu"로 폴백하고 경고 출력 (실패시키지 말 것)
- **★ turns 재구성 (이 단계의 핵심): `result["segments"][].speaker`를 절대 쓰지 마라.**
  약 30초 덩어리에 대한 다수결 라벨이라 실측 순도가 51~100%로 널뛴다(절반이 동전던지기).
  반드시 `result["word_segments"]`의 단어 단위 speaker를 사용해,
  인접한 같은 화자 단어를 이어 붙여 turn을 만든다.
  각 turn: start(첫 단어 start), end(마지막 단어 end), speaker, text(단어 연결),
  word_count, analyzable(= word_count >= 10 AND 길이 >= 3.0초).
  영어는 단어 사이에 공백을 넣어 text를 만든다.
- 출력 JSON은 아키텍처 문서의 02_transcript.json 스키마 그대로
  (pipeline_version, source_filename, diarization_status, turns 포함).
  speaker 필드가 없는 단어는 "UNKNOWN"으로 채운다.
- diarization_status 판정: 전 단어 라벨 있음="ok", 일부 UNKNOWN="partial", 전부 UNKNOWN="failed"
- text가 빈 문자열인 turn은 버린다.
- diarization 자체가 예외로 실패하면: 전사 결과를 speaker="UNKNOWN",
  diarization_status="failed"로 저장하고 경고 출력 후 반환한다. 중단 판단은 main(S8)이 한다.
- 캐시 파일을 읽을 때 pipeline_version이 util.PIPELINE_VERSION과 다르면 무시하고 재생성

## 완료 기준

- [ ] 실제 녹음 wav로 실행 시 02_transcript.json 생성, 스키마 일치
      (source_filename/pipeline_version/diarization_status/turns 포함)
- [ ] turns가 시간순 정렬되어 있고, 인접 turn의 speaker가 서로 다름 (병합 누락 없음)
- [ ] analyzable=true인 turn만 모아 읽었을 때 대화 흐름이 자연스럽게 복원됨 (육안 확인)
- [ ] diarize 모델이 3.1로 지정되어 403이 나지 않음
- [ ] 02_transcript.json이 정상 저장됨 (numpy 타입 캐스팅 확인)
- [ ] 재실행 시 스킵
- [ ] HF 토큰 미설정 시 "HF_TOKEN 환경변수 설정 +
      https://huggingface.co/pyannote/speaker-diarization-3.1 라이선스 동의 필요" 안내 에러
- [ ] 외부 의존: whisperx만 (whisperx가 torch 등을 끌고 옴)
