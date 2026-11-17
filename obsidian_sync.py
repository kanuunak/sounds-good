"""Obsidian Remotely Save 동기화를 외부에서 트리거한다 (Advanced URI 플러그인 사용).

로컬 파이프라인이 vault에 뷰 파일을 쓴 뒤 호출하면 PC의 Obsidian이 원격으로 밀어올리고,
폰에서 Remotely Save로 받을 수 있다.

- Remotely Save는 Obsidian 안에서만 도는 플러그인이라 CLI/API가 없다. 그래서
  Advanced URI(`obsidian://adv-uri?...&commandid=remotely-save:start-sync`)로 커맨드를 때린다.
- **Obsidian이 꺼져 있으면 URI가 Obsidian을 실행시킨다.** 그게 싫으면 SG_OBSIDIAN_SYNC=0.
- 실패해도 카드 생성에는 영향을 주지 않는다(경고만 찍고 False 반환).

- Hyprland이면, **우리가 띄운 경우에만** 새 Obsidian 창을 특수 워크스페이스로 조용히 보낸다.
  전역 windowrule을 쓰지 않는 이유: 사용자가 직접 Obsidian을 열었을 때도 숨겨져 혼란스럽다.

환경변수:
  SG_OBSIDIAN_SYNC=0        끄기 (기본은 켜짐)
  SG_OBSIDIAN_VAULT=이름     vault 이름 강제 (기본: vault_path에서 자동 탐지)
  SG_OBSIDIAN_HIDE=0        창 숨기기 끄기 (기본은 켜짐)
  SG_OBSIDIAN_WORKSPACE=... 보낼 워크스페이스 (기본 special:obsidian)

외부 의존: 없음 (표준 라이브러리 + xdg-open, hyprctl은 있으면 사용).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote

_COMMAND_ID = "remotely-save:start-sync"
_WORKSPACE = os.getenv("SG_OBSIDIAN_WORKSPACE", "special:obsidian")


def _hypr_env() -> dict | None:
    """hyprctl 실행용 환경을 만든다. 없으면 None(=Hyprland 아님/못 찾음).

    systemd 사용자 서비스에는 HYPRLAND_INSTANCE_SIGNATURE가 상속되지 않으므로
    $XDG_RUNTIME_DIR/hypr/ 에서 실행 중인 인스턴스를 직접 찾는다.
    """
    if not shutil.which("hyprctl"):
        return None
    sig = os.getenv("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        base = Path(os.getenv("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "hypr"
        cands = [d for d in base.iterdir() if d.is_dir()] if base.is_dir() else []
        if not cands:
            return None
        sig = max(cands, key=lambda d: d.stat().st_mtime).name
    env = dict(os.environ)
    env["HYPRLAND_INSTANCE_SIGNATURE"] = sig
    return env


def _desktop_env() -> dict:
    """xdg-open이 동작하려면 세션(디스플레이) 환경이 필요하다.

    systemd 사용자 서비스에는 WAYLAND_DISPLAY 등이 상속되지 않아 xdg-open이 응답하지 않고
    멈추는 사고가 있었다(실측: 15초 타임아웃). 런타임 디렉터리에서 직접 찾아 채워준다.
    """
    env = dict(os.environ)
    rt = Path(os.getenv("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    if not env.get("WAYLAND_DISPLAY"):
        socks = sorted(f.name for f in rt.glob("wayland-*") if not f.name.endswith(".lock"))
        if socks:
            env["WAYLAND_DISPLAY"] = socks[0]
    if not env.get("XDG_CURRENT_DESKTOP") and (rt / "hypr").is_dir():
        env["XDG_CURRENT_DESKTOP"] = "Hyprland"
    hy = _hypr_env()
    if hy and hy.get("HYPRLAND_INSTANCE_SIGNATURE"):
        env["HYPRLAND_INSTANCE_SIGNATURE"] = hy["HYPRLAND_INSTANCE_SIGNATURE"]
    return env


def _obsidian_windows(env: dict) -> dict[str, str]:
    """현재 열린 Obsidian 창: {주소: 워크스페이스 이름}."""
    try:
        r = subprocess.run(["hyprctl", "-j", "clients"], capture_output=True, text=True,
                           timeout=5, env=env)
        if r.returncode != 0:
            return {}
        return {c["address"]: str(c.get("workspace", {}).get("name", ""))
                for c in json.loads(r.stdout or "[]")
                if "obsidian" in str(c.get("class", "")).lower()}
    except Exception:
        return {}


def _move(env: dict, addr: str, ws: str) -> None:
    subprocess.run(["hyprctl", "dispatch", "movetoworkspacesilent", f"{ws},address:{addr}"],
                   capture_output=True, timeout=5, env=env)


def _restore_existing(env: dict, before: dict[str, str], timeout: float = 8.0) -> None:
    """URI를 받으면 Obsidian이 기존 창을 스스로 앞으로 끌어낸다(실측).

    사용자가 보고 있던 화면을 방해하지 않도록, 원래 있던 워크스페이스로 되돌린다.
    """
    deadline = time.time() + timeout
    pending = dict(before)
    while time.time() < deadline and pending:
        cur = _obsidian_windows(env)
        for addr, ws in list(pending.items()):
            now = cur.get(addr)
            if now and now != ws:
                _move(env, addr, ws)
                print(f"[obsidian_sync] 기존 Obsidian 창을 원래 워크스페이스({ws})로 되돌렸습니다")
                pending.pop(addr, None)
                # 새 창과 같은 이유로 다시 튀어나올 수 있어 잠시 붙잡아 둔다.
                threading.Thread(target=_keep_pinned, args=(env, addr, ws), daemon=True).start()
        time.sleep(0.5)


def _keep_pinned(env: dict, addr: str, ws: str, seconds: float = 30.0) -> None:
    """창을 ws에 붙잡아 둔다.

    한 번 옮기는 것만으로는 부족했다(실측): 창을 옮긴 뒤 Obsidian이 URI를 처리하면서
    Electron single-instance 동작으로 창을 스스로 다시 끌어올린다. 그래서 잠시 지켜보다가
    빠져나오면 도로 넣는다.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(0.5)
        cur = _obsidian_windows(env).get(addr)
        if cur is None:          # 창이 닫혔다
            return
        if cur != ws:
            _move(env, addr, ws)
            print(f"[obsidian_sync] Obsidian이 다시 튀어나와 {ws} 로 되돌렸습니다")


def _hide_new_window(env: dict, before: dict[str, str], timeout: float = 25.0) -> bool:
    """새로 생긴 Obsidian 창만 특수 워크스페이스로 조용히 보낸다(포커스 뺏지 않음)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        new = set(_obsidian_windows(env)) - set(before)
        if new:
            addr = sorted(new)[0]
            try:
                _move(env, addr, _WORKSPACE)
                print(f"[obsidian_sync] 새 Obsidian 창을 {_WORKSPACE} 로 보냈습니다 (화면 방해 없음)")
                _keep_pinned(env, addr, _WORKSPACE)
                return True
            except Exception as e:
                print(f"[obsidian_sync] 창 이동 실패(무시): {e}")
                return False
        time.sleep(0.5)
    return False


def find_vault_root(path: str | Path) -> Path | None:
    """vault_path에서 위로 올라가며 `.obsidian`이 있는 실제 vault 루트를 찾는다.

    config의 vault_path는 vault 안쪽 하위 폴더(예: .../MyVault/어학/전화영어 복습)이므로
    Obsidian이 아는 vault 이름을 얻으려면 루트를 따로 찾아야 한다.
    """
    p = Path(path).expanduser().resolve()
    for cand in [p, *p.parents]:
        if (cand / ".obsidian").is_dir():
            return cand
    return None


def build_uri(vault_name: str, command_id: str = _COMMAND_ID) -> str:
    return (f"obsidian://adv-uri?vault={quote(vault_name, safe='')}"
            f"&commandid={quote(command_id, safe='')}")


def trigger(vault_path: str | Path, timeout: float = 15.0) -> bool:
    """Remotely Save 동기화를 트리거한다. 성공 여부를 bool로 반환(예외를 던지지 않는다)."""
    if os.getenv("SG_OBSIDIAN_SYNC", "1").strip().lower() in ("0", "false", "no"):
        return False

    name = os.getenv("SG_OBSIDIAN_VAULT", "").strip()
    if not name:
        root = find_vault_root(vault_path)
        if root is None:
            print(f"[obsidian_sync] vault 루트(.obsidian)를 찾지 못했습니다: {vault_path} — 동기화 생략")
            return False
        name = root.name

    opener = shutil.which("xdg-open")
    if not opener:
        print("[obsidian_sync] xdg-open이 없어 URI를 열 수 없습니다 — 동기화 생략")
        return False

    # 이미 떠 있던 창은 건드리지 않는다. 우리가 새로 띄운 창만 나중에 숨긴다.
    hide = os.getenv("SG_OBSIDIAN_HIDE", "1").strip().lower() not in ("0", "false", "no")
    henv = _hypr_env() if hide else None
    before = _obsidian_windows(henv) if henv else set()

    uri = build_uri(name)
    try:
        # ★ 기다리지 않는다(fire-and-forget). xdg-open은 세션에 따라 반환하지 않을 수 있어
        #   run()으로 기다리면 카드 생성이 통째로 멈춘 것처럼 보인다(실측 15초 지연).
        subprocess.Popen([opener, uri], env=_desktop_env(), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"[obsidian_sync] Remotely Save 동기화 요청 (vault={name})")
        if henv:
            # 꺼져 있었으면 새 창을 숨기고, 켜져 있었으면 튀어나온 창을 원위치로 되돌린다.
            target = _hide_new_window if not before else _restore_existing
            threading.Thread(target=target, args=(henv, before), daemon=True).start()
        return True
    except Exception as e:  # 동기화 실패가 카드 생성을 망치면 안 된다
        print(f"[obsidian_sync] 동기화 트리거 실패(무시하고 계속): {e}")
        return False
