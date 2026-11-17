# 처음 설치하는 분을 위한 안내

README 의 "설치"를 한 단계씩 풀어 쓴 문서입니다. 위에서부터 순서대로 따라 하세요. 전부 하는 데 1~2시간쯤 걸립니다(대부분 내려받는 시간).

> 작성자는 Linux PC 한 대에서만 확인했습니다. **0단계(Windows)** 와 **4-A단계의 llama.cpp 설치**는 직접 검증하지 못한 부분이라, 막히면 표시된 공식 문서를 따라 주세요.

## 준비물 확인

- NVIDIA 그래픽카드가 달린 PC (VRAM 8GB 이상 권장)
- 디스크 여유 30GB 정도 (파이썬 패키지 약 8GB, 음성 인식 모델 약 5GB, 로컬 LLM 을 쓰면 추가로 약 6GB)
- 수업 녹음 파일(.m4a). 삼성 폰의 통화 녹음은 보통 `내장 저장공간/Recordings/Call` 에 있습니다.
- PC 에 [Anki](https://apps.ankiweb.net/) 설치 (폰에서 복습하려면 AnkiDroid / AnkiMobile 과 AnkiWeb 동기화)

## 0. (Windows 만) WSL2 준비

Windows 에서는 "WSL2" 라는 Windows 안의 Linux 에서 설치합니다.

1. Windows 에 최신 NVIDIA 그래픽 드라이버를 설치합니다.
2. **PowerShell 을 관리자 권한으로** 열고 `wsl --install` 을 실행한 뒤 재부팅합니다. Ubuntu 가 설치되고 사용자 이름·비밀번호를 정하게 됩니다.
3. 시작 메뉴에서 **Ubuntu** 를 엽니다. 이후의 모든 명령은 이 창에서 입력합니다.
4. `nvidia-smi` 를 입력해 그래픽카드 이름이 나오면 준비 완료입니다. 안 나오면 [Microsoft 의 WSL GPU 안내](https://learn.microsoft.com/windows/ai/directml/gpu-cuda-in-wsl)를 확인하세요.

녹음 파일은 Windows 탐색기에서 `\\wsl$\Ubuntu\home\<사용자이름>` 으로 들어가 복사해 넣거나, 웹 화면에서 바로 올리면 됩니다.

## 1. 기본 도구 설치

Ubuntu(WSL 포함) 기준입니다.

```bash
sudo apt update
sudo apt install -y git ffmpeg python3.11 python3.11-venv
```

`python3.11` 을 찾을 수 없다고 나오면(Ubuntu 24.04 등) 아래를 먼저 실행하고 다시 시도하세요.

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt update
```

확인: `python3.11 --version` 과 `ffmpeg -version` 이 버전을 출력하면 됩니다.

## 2. 프로그램 받기와 패키지 설치

```bash
git clone https://github.com/kanuunak/sounds-good.git
cd sounds-good
python3.11 -m venv .venv
source .venv/bin/activate
pip install --no-deps -r requirements.txt
cp config.example.yaml config.yaml
```

- `pip install` 은 수 GB 를 내려받습니다. **`--no-deps` 를 빼면 충돌 오류로 설치가 거부됩니다.**
- 터미널을 새로 열 때마다 `cd sounds-good && source .venv/bin/activate` 를 먼저 해야 합니다(프롬프트 앞에 `(.venv)` 가 보이면 된 것입니다).

## 3. HuggingFace 가입, 모델 이용 동의, 토큰

"누가 말했는지"를 구분하는 모델(pyannote)은 무료지만, 쓰는 사람마다 **가입 → 이용 조건 동의 → 토큰 발급**을 한 번 해야 합니다. 이걸 건너뛰면 음성 인식 단계에서 멈춥니다.

1. **가입**: <https://huggingface.co/join> 에서 이메일로 가입하고, 받은 메일의 인증 링크를 누릅니다.
2. **이용 조건 동의 (두 군데 모두)**: 로그인한 상태로 아래 두 페이지를 각각 엽니다. 페이지 위쪽에 이용 조건과 함께 소속·용도 등을 적는 칸이 나옵니다. 채우고 동의 버튼을 누르면 됩니다(개인 학습용이라고 적으면 충분합니다). 동의가 끝나면 그 자리에 모델 설명이 보입니다.
   - <https://huggingface.co/pyannote/speaker-diarization-3.1>
   - <https://huggingface.co/pyannote/segmentation-3.0>
3. **토큰 만들기**: <https://huggingface.co/settings/tokens> 에서 새 토큰을 만듭니다. 권한은 **Read** 면 됩니다. `hf_` 로 시작하는 긴 문자열이 나오는데 **이때만 볼 수 있으니 바로 복사**하세요. 이 토큰은 비밀번호와 같으니 남에게 보여 주거나 저장소에 올리지 마세요.
4. **토큰을 PC 에 등록**: 아래 명령의 `hf_여기에_붙여넣기` 를 복사한 토큰으로 바꿔서 실행합니다.

```bash
echo 'export HF_TOKEN=hf_여기에_붙여넣기' >> ~/.bashrc
source ~/.bashrc
```

확인: `echo $HF_TOKEN` 을 입력했을 때 토큰이 출력되면 됩니다.

## 4. 분석에 쓸 LLM 고르기

둘 중 하나만 하면 됩니다. 차이는 README 의 비교표를 보세요.

### 4-A. 로컬 모델 (무료, 전사 내용이 PC 밖으로 안 나감)

1. [llama.cpp](https://github.com/ggml-org/llama.cpp) 를 설치합니다. 운영체제별 방법은 공식 문서의 [install](https://github.com/ggml-org/llama.cpp/blob/master/docs/install.md) 과 [build](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md) 를 따르세요. NVIDIA GPU 를 쓰려면 CUDA 지원으로 설치·빌드해야 합니다.
2. 확인: `llama-server --version` 이 버전을 출력하면 됩니다.
3. `config.yaml` 은 고칠 것이 없습니다(예시 설정이 로컬 모델 기준입니다). 처음 카드를 만들 때 모델(약 5.7GB)을 자동으로 내려받아서 첫 실행만 오래 걸립니다.

### 4-B. Claude 구독 (품질이 가장 좋음, 전사 내용이 Anthropic 으로 전송됨)

1. [Claude Code](https://claude.com/claude-code) 를 설치하고 터미널에서 `claude` 를 한 번 실행해 로그인합니다.
2. 확인: `claude --version` 이 버전을 출력하면 됩니다.
3. `config.yaml` 을 열어 "(A) 로컬 모델" 아래 다섯 줄(`llm_provider` 부터 `llama_server_cmd` 까지)을 지우고, "(B) Claude 구독" 아래 두 줄 앞의 `#` 을 지웁니다.

## 5. 첫 실행

```bash
python app.py
```

브라우저에서 <http://127.0.0.1:7860> 을 엽니다.

1. **＋ 새 수업** → 녹음 파일을 올리고 수업 언어를 고른 뒤 **분석 시작**. 처음에는 음성 인식 모델을 내려받느라 한참 걸리고, 그다음부터는 10분짜리 수업 기준 몇 분입니다.
2. 두 화자의 발화가 나오면 **내가 한 말 쪽**을 고릅니다.
3. **카드 만들기** → 카드가 화면에 나오고 `.apkg` 파일을 내려받을 수 있습니다.
4. Anki 를 열고 **파일 → 가져오기**로 그 `.apkg` 를 고릅니다. `English Speaking` 덱에 카드가 들어옵니다. 폰에서 보려면 Anki 에서 **동기화**를 누릅니다.

## 막혔을 때

| 증상 | 원인과 해결 |
|---|---|
| 분석 시작 직후 `HF_TOKEN ... 라이선스 동의 필요` | 토큰이 등록되지 않았습니다. 3단계의 4번을 다시 하고 **터미널을 새로 연 뒤** 실행하세요. |
| `화자 구분 모델을 받지 못했습니다` | 두 모델 페이지 중 하나에 동의하지 않았거나 토큰을 잘못 붙여넣었습니다. 3단계의 2번을 두 페이지 모두 확인하세요. |
| `pip install` 이 `ResolutionImpossible` 로 실패 | `--no-deps` 가 빠졌습니다. |
| `CUDA를 찾지 못해 cpu로 폴백` 이라고 나오고 매우 느림 | GPU 를 못 잡은 상태입니다. `nvidia-smi` 가 동작하는지 확인하세요(WSL 은 0단계). |
| GPU 메모리 부족(`out of memory`) | `config.yaml` 에 `whisper_batch_size: 2` (또는 1)를 추가하세요. 로컬 LLM 을 따로 띄워 두었다면 끄세요(프로그램이 필요할 때만 알아서 띄웁니다). |
| `llama.cpp 서버에 연결하지 못했습니다` | `llama-server` 가 설치되지 않았거나 PATH 에 없습니다. `llama-server --version` 을 확인하세요. 실행 로그는 `/tmp/sounds-good-llama-server.log` 에 있습니다. |
| `ffmpeg를 PATH에서 찾을 수 없습니다` | 1단계의 `ffmpeg` 설치가 빠졌습니다. |
| `이미 처리한 녹음입니다` | 같은 파일을 다시 올린 경우입니다. 이력 탭에서 그 수업을 찾을 수 있습니다. |
| 카드가 한 장도 안 나옴 | 내 화자를 반대로 골랐을 가능성이 큽니다. 또는 그 수업에 고칠 만한 발화가 없었던 경우입니다. |
