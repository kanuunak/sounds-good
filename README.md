# 🎧 sounds good — 전화영어 수업을 Anki 복습 카드로

전화영어(전화중국어) 수업 녹음을 넣으면, **내가 틀리게 말한 문장**을 찾아 **Anki 카드**로 만들어 주는 개인용 도구입니다. 전부 내 PC에서 돕니다.

```
녹음(.m4a) → 음성 인식 + 화자 구분(WhisperX) → 내 화자 선택 → LLM 분석 → 카드 검증 → Anki .apkg (+ Obsidian 노트)
```

- **카드 앞면에는 한국어 뜻만** 나옵니다. 입으로 영어로 말해 본 뒤 뒤집으면 정답, 내가 실제로 한 말, 왜 그렇게 말하는지가 나옵니다. 틀린 문장을 앞면에 두지 않는 건 오답을 반복해 읽으면 그 오답이 굳기 때문입니다.
- **튜터가 수업 중에 고쳐 준 표현**을 가장 좋은 정답으로 삼습니다.
- 수업이 쌓이면 **약점 유형**(전치사, 시제, 관사 …)을 집계해 다음 수업 분석에 반영합니다.
- 웹 화면(이력 검색, 약점 현황, 새 수업 등록)과 명령줄 둘 다 됩니다.

> 개인 프로젝트를 그대로 공개한 것입니다. 작성자 환경(Linux, RTX 3060 Ti 8GB)에서만 검증됐고, 다른 환경은 아래 안내대로 해도 막힐 수 있습니다.

## 필요한 것

| | |
|---|---|
| OS | Linux. Windows 는 **WSL2(Ubuntu)** 안에서 설치하세요(작성자가 직접 검증하지는 못했습니다). **macOS 는 아직 지원하지 않습니다** — 설치 목록이 Linux + NVIDIA 전용이라 패키지 설치 단계에서 실패합니다. |
| GPU | NVIDIA, VRAM 8GB 이상 권장 (음성 인식에 필요. CPU 로도 돌지만 매우 느립니다) |
| Python | 3.11 |
| 그 외 | `ffmpeg`, [Anki](https://apps.ankiweb.net/), HuggingFace 계정 |
| 분석 LLM | 아래 둘 중 하나 — [llama.cpp](https://github.com/ggml-org/llama.cpp)(로컬, 무료) 또는 Claude Code 구독 |

## 설치

> 처음이라면 **[SETUP.md](SETUP.md)** 를 보세요. WSL2 준비, HuggingFace 가입·모델 동의·토큰 등록, Anki 로 가져오기까지 한 단계씩 적어 두었고, 막혔을 때 보는 증상별 표가 있습니다. 아래는 요약입니다.

```bash
git clone https://github.com/kanuunak/sounds-good.git
cd sounds-good
python3.11 -m venv .venv
source .venv/bin/activate
pip install --no-deps -r requirements.txt     # --no-deps 를 꼭 붙이세요(아래 설명)
cp config.example.yaml config.yaml
```

`requirements.txt` 는 작성자 PC에서 실제로 돌아가는 패키지 156개의 정확한 버전 목록입니다(Linux x86_64, CUDA 12). `whisperx` 와 `gradio` 가 서로 다른 `huggingface-hub` 버전을 요구해서 `--no-deps` 없이 설치하면 pip 이 충돌로 거부합니다. 이 조합 그대로는 문제없이 동작합니다.

### 화자 구분 모델 이용 동의 (각자 1회)

1. HuggingFace 에 로그인해서 [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) 과 [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0) 페이지의 이용 조건에 동의합니다.
2. [토큰](https://huggingface.co/settings/tokens)을 만들어 환경변수로 넣습니다: `export HF_TOKEN=hf_...` (`~/.bashrc` 에 추가)

## 분석 LLM 고르기

`config.yaml` 에서 고릅니다. 같은 수업 2개로 비교한 결과입니다.

| | 로컬 (llama.cpp + Qwen3.5-9B) | Claude 구독 (`claude -p`) |
|---|---|---|
| 비용 | 무료 | Claude 구독 필요 |
| 전사 내용 | PC 밖으로 안 나감 | Anthropic 으로 전송됨 |
| 카드 품질 | 쓸 만함. 카드 수가 적고 설명이 거칠며 가끔 어색한 카드가 섞임 | 가장 좋음. 놓치는 교정이 적고 설명이 자연스러움 |
| 수업 1개 분석 | 약 3분 (RTX 3060 Ti) | 약 3~4분 |
| 준비 | `llama-server` 설치. 첫 실행 때 모델 5.7GB 다운로드 | Claude Code 설치·로그인 |

- **로컬**: 예시 설정 그대로 두면 됩니다. 분석할 때만 모델 서버를 띄웠다가 끝나면 내립니다(음성 인식과 GPU 메모리를 나눠 쓰기 위해서입니다). `--reasoning-budget` 을 0 으로 끄면 지시문의 예시를 내 발화로 착각한 가짜 카드가 생기니 끄지 마세요.
- **Claude**: `config.example.yaml` 의 (B) 안내대로 두 줄을 바꿉니다. API 키는 필요 없고 Claude Code 로그인을 그대로 씁니다.
- Gemini / Anthropic API / OpenAI API 도 설정으로 고를 수 있습니다(`stages/analyze.py` 참고).

어느 쪽을 쓰든, 카드의 "내가 한 말"이 실제 전사에 없으면 그 카드는 코드가 버립니다.

## 사용

```bash
python app.py            # 웹 화면: http://127.0.0.1:7860
python main.py 녹음.m4a   # 명령줄 (중국어 수업은 --lang zh)
```

1. **＋ 새 수업** → 녹음 파일(.m4a)을 올리고 언어를 고른 뒤 **분석 시작** (음성 인식에 몇 분 걸립니다)
2. 두 화자의 발화 샘플을 보고 **내 목소리**를 고릅니다
3. **카드 만들기** → 결과 확인 → `.apkg` 를 내려받아 Anki 에서 가져오기
4. **이력** 탭에서 지난 수업의 카드·요약·전사본을 다시 볼 수 있고, **현황** 탭에 약점 유형이 쌓입니다

안드로이드 폰이 USB 로 연결돼 있으면 `./run.sh` 가 가장 최근 통화 녹음을 가져와 바로 처리합니다.

### 다른 기기(폰)에서 접속하기

기본은 이 PC에서만 열립니다. 집 안 다른 기기에서 쓰려면 환경변수를 주고 실행하세요. **로그인 없이 열어 두지 마세요** — 녹음 업로드와 수업 이력 열람이 모두 가능한 화면입니다.

```bash
SG_HOST=0.0.0.0 SG_PORT=7860 SG_AUTH_USER=아이디 SG_AUTH_PASS=비밀번호 python app.py
```

### Obsidian (선택)

`config.yaml` 의 `vault_path` 를 채우면 연습 문장, 문장 모음, 약점 표, 수업별 상세 노트가 그 폴더에 자동으로 만들어집니다. 비워 두면 건너뜁니다.

## 알아둘 것

- **녹음과 개인정보**: 수업 녹음에는 상대방(튜터)의 목소리가 들어 있습니다. 본인 학습용으로만 쓰고, 녹음·전사본·`data/` 폴더를 공유하거나 공개 저장소에 올리지 마세요. 통화 녹음에 관한 규정은 지역과 서비스 약관마다 다릅니다.
- **`data/history.db` 가 원본**입니다. 카드를 이미 만든 문장, 약점 통계가 여기 있습니다. 백업하세요. `work/` 는 지워도 되는 캐시입니다.
- 같은 녹음을 다시 올리면 "이미 처리한 녹음"으로 막습니다.
- 화자 구분이 완전히 실패한 녹음은 분석하지 않고 멈춥니다(튜터의 말을 내 실수로 카드에 넣지 않기 위해서입니다).
- 지원 언어는 영어와 중국어(카드에 병음 포함)이고, 설명은 한국어로 나옵니다.

## 어떻게 만들었나

이 프로젝트는 설계 문서를 먼저 쓰고, 단계별 지시문을 AI 코딩 도구에 하나씩 주는 방식으로 만들었습니다. 그 문서를 그대로 넣어 두었습니다. 작성 당시의 기록이라 지금 코드와 다른 부분이 있습니다(예: 로컬 LLM 은 ollama 가 아니라 llama.cpp 로 바뀌었습니다).

- [`00_ARCHITECTURE.md`](00_ARCHITECTURE.md) — 전체 설계, 단계 사이의 파일 계약, DB 구조
- [`prompts/`](prompts/) — 단계별 구현 지시문(S1 변환 → S8 통합, S10 카드 일관성, S12 중국어)

## 라이선스

[MIT](LICENSE). 음성 인식·화자 구분 모델과 LLM 은 각자의 라이선스와 이용 조건을 따릅니다.
