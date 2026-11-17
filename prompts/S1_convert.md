# S1: 오디오 변환 + 공통 유틸 구현 (v1.1)

첨부된 00_ARCHITECTURE.md의 계약을 따르는 `util.py`와 `stages/convert.py`를 작성하라.

## util.py 요구사항

```python
PIPELINE_VERSION = "1.3"

def file_hash(path: Path) -> str: ...      # SHA256 앞 12자리
def load_config(path: Path = Path("config.yaml")) -> dict: ...
def resolve(cfg: dict, key: str) -> str: ...   # env:XXX lazy 해석
def normalize(s: str) -> str: ...          # 소문자화, 구두점 제거, 공백 정리
def dup_key(natural_sentence: str) -> str: ...  # normalize 후 sha256 앞 16자리
def stage_output(work_dir: Path, audio_hash: str, filename: str) -> Path: ...
```

- `load_config`는 `"env:XXX"` 값을 즉시 해석하지 않는다. `resolve(cfg, key)`가 사용 시점에
  환경변수를 읽고, 없으면 어떤 변수가 필요한지 명확한 에러로 실패한다
  (lazy 검증 — ollama 사용자가 미사용 API 키 때문에 실패하면 안 됨).
- yaml 파싱은 PyYAML 사용.

## stages/convert.py 요구사항

```python
def run(audio_path: Path, cfg: dict) -> Path:
    """m4a → work/{hash}/01_audio.wav. 이미 존재하면 변환 없이 경로 반환."""
```

- ffmpeg subprocess 호출: mono, 16kHz. `cfg["bandpass_filter"]`가 true면
  `-af "highpass=f=100,lowpass=f=3600"` 추가 (협대역 전화 녹음용 — 광대역 녹음 유입 가능성 대비 옵션)
- ffmpeg가 PATH에 없으면 설치 안내 메시지와 함께 실패
- ffmpeg 실패 시 stderr를 포함한 예외 발생
- work/{hash}/ 디렉터리는 없으면 생성

## 완료 기준

- [ ] `python -c "from stages.convert import run"` 임포트 성공
- [ ] m4a 하나로 실행 시 work/{hash}/01_audio.wav 생성, 16kHz mono 확인(ffprobe)
- [ ] 같은 파일로 재실행 시 변환을 건너뛰고 즉시 반환 (로그로 확인)
- [ ] bandpass_filter: false 설정 시 필터 없이 변환됨
- [ ] normalize("Hello,  World!") == "hello world"
- [ ] resolve()가 미설정 환경변수에 대해 변수명이 포함된 에러를 냄
- [ ] 외부 의존: PyYAML만. 그 외 추가 금지.
