#!/usr/bin/env bash
# sounds good — 원커맨드 실행 (S11 Track B)
#
#   ./run.sh                     폰(adb)에서 최신 통화 녹음을 가져와 처리
#   ./run.sh 파일.m4a            지정한 파일 처리
#   ./run.sh --lang zh           중국어 수업 (파일 인자 없이도 가능; 나머지 인자는 main.py로 전달)
#
# 기존 5~9단계(adb 파일 찾기 → pull → venv → export → 실행)를 이 한 줄로 줄인다.
# 화자 선택 프롬프트 1회 개입은 정상이다.
set -euo pipefail

# 이 스크립트가 있는 폴더 = 프로젝트 폴더 (예전엔 개인 홈 경로가 박혀 있었다)
PROJECT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# 폰의 통화 녹음 위치 — 기기마다 다르면 SG_REMOTE_DIR로 덮어쓴다.
REMOTE_DIR="${SG_REMOTE_DIR:-/sdcard/Recordings/Call}"

cd "$PROJECT_DIR"

# shellcheck disable=SC1091
source .venv/bin/activate

# API 키는 셸 환경(~/.bashrc 등)에서 가져온다. 스크립트에 값을 적지 않는다.
# 2026-09-29부터 기본 분석은 Claude 구독(claude -p)이라 Gemini 키는 폴백용 — 없어도 실행은 된다.
if [ -z "${GEMINI_API_KEY:-}" ]; then
  echo "[run.sh] 경고: GEMINI_API_KEY가 없어 Claude 실패 시 Gemini 폴백이 동작하지 않습니다." >&2
fi

# 첫 번째 비옵션 인자 = 파일 경로, 나머지(--lang 등)는 main.py로 그대로 전달.
# 값을 받는 옵션(--lang zh / --force analyze / --config x)은 다음 토큰까지 한 쌍으로 넘긴다 —
# 예전엔 `./run.sh --lang zh`의 "zh"를 파일 경로로 오인해 "파일이 없습니다: zh"로 죽었다(2026-11-07).
TARGET=""
PASS_ARGS=()
expect_value=0
for a in "$@"; do
  if [ "$expect_value" = 1 ]; then
    PASS_ARGS+=("$a"); expect_value=0
  elif [[ "$a" == --lang || "$a" == --force || "$a" == --config ]]; then
    PASS_ARGS+=("$a"); expect_value=1
  elif [[ "$a" != -* && -z "$TARGET" ]]; then
    TARGET="$a"
  else
    PASS_ARGS+=("$a")
  fi
done

if [ -z "$TARGET" ]; then
  # 파일 인자 없음 → 폰에서 최신 녹음 자동 반입
  if ! command -v adb >/dev/null 2>&1; then
    echo "[run.sh] adb가 없습니다. 파일 경로를 직접 넘기세요: ./run.sh \"파일.m4a\"" >&2
    exit 1
  fi
  if [ -z "$(adb devices | awk 'NR>1 && $2=="device"')" ]; then
    echo "[run.sh] 연결된 안드로이드 기기가 없습니다." >&2
    echo "         USB 연결 + 화면 잠금 해제 + USB 디버깅 허용을 확인하세요 (adb devices)." >&2
    echo "         또는 파일 경로를 직접 넘기세요: ./run.sh \"파일.m4a\"" >&2
    exit 1
  fi

  # 기기 쪽에서 글롭을 확장해야 한다(adb pull은 글롭을 확장하지 않음).
  # 안드로이드가 붙이는 CR을 제거한다.
  REMOTE=$(adb shell "ls -t $REMOTE_DIR/*.m4a 2>/dev/null | head -1" | tr -d '\r')
  if [ -z "$REMOTE" ]; then
    echo "[run.sh] 폰에서 녹음 파일을 찾지 못했습니다: $REMOTE_DIR" >&2
    echo "         경로가 다르면 SG_REMOTE_DIR=/실제/경로 ./run.sh 로 지정하세요." >&2
    exit 1
  fi

  echo "[run.sh] 가져오는 중: $REMOTE"
  mkdir -p ./sounds
  adb pull "$REMOTE" ./sounds/
  TARGET="./sounds/$(basename "$REMOTE")"
fi

if [ ! -f "$TARGET" ]; then
  echo "[run.sh] 파일이 없습니다: $TARGET" >&2
  exit 1
fi

echo "[run.sh] 처리 시작: $TARGET"
python main.py "$TARGET" ${PASS_ARGS[@]+"${PASS_ARGS[@]}"}
