#!/usr/bin/env python3
"""Apple TV の再生状態を pyatv (atvscript / atvremote) で取得する。

stdout に JSON を1つだけ出す:
{
  "available": bool,          # Apple TV に到達し、再生情報が取れたか
  "is_youtube": bool,         # 再生中アプリが YouTube か
  "device_state": "playing" | "paused" | ... | null,
  "title": str | null,        # 再生中コンテンツのタイトル (動画IDは含まれない)
  "position": int | null,     # 再生位置 (秒)
  "total_time": int | null,   # 総尺 (秒)。ライブ等では null / 0 になり得る
  "error": str | null
}

注意:
- 事前に `atvremote pair` でペアリング済みであることが前提。
  未ペアリング / Apple TV がスリープ等で到達できない場合は available=false で正常終了する
  (呼び出し側の扱いは方式で異なる: 既定の拡張方式では履歴に触らず引き継ぎなしで
  終了し、代替の yt-dlp 方式でのみ履歴フォールバックへ進む)。
- 特定の Apple TV を固定したい場合は環境変数 YOUTUBE_RESUME_ATV_ID に
  `atvremote scan` で出る identifier を設定する。
"""
import json
import os
import shutil
import subprocess
import sys

TIMEOUT_SEC = 15
HERE = os.path.dirname(os.path.abspath(__file__))


def find_pyatv_tool(name):
    """pyatv の CLI を探す。Python 3.15 では pyatv が動かないため、
    Python 3.13 で作った専用 venv (tools/youtube_resume/.venv) を最優先する。"""
    env_dir = os.environ.get("YOUTUBE_RESUME_PYATV_BIN_DIR")
    candidates = []
    if env_dir:
        candidates.append(os.path.join(env_dir, name))
    candidates.append(os.path.join(HERE, ".venv", "bin", name))
    which = shutil.which(name)
    if which:
        candidates.append(which)
    candidates.append(os.path.expanduser(f"~/.local/bin/{name}"))
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return candidates[0]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC)


def main():
    result = {
        "available": False,
        "is_youtube": False,
        "device_state": None,
        "title": None,
        "position": None,
        "total_time": None,
        "error": None,
    }

    atvscript = find_pyatv_tool("atvscript")
    if not os.path.exists(atvscript):
        result["error"] = "atvscript not found"
        print(json.dumps(result, ensure_ascii=False))
        return 0

    cmd = [atvscript]
    atv_id = os.environ.get("YOUTUBE_RESUME_ATV_ID")
    if atv_id:
        cmd += ["--id", atv_id]
    cmd.append("playing")

    try:
        proc = run(cmd)
    except subprocess.TimeoutExpired:
        result["error"] = "atvscript timed out"
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except OSError as e:
        result["error"] = f"atvscript failed to run: {e}"
        print(json.dumps(result, ensure_ascii=False))
        return 0

    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        result["error"] = (proc.stderr or "").strip()[:500] or f"exit code {proc.returncode}"
        print(json.dumps(result, ensure_ascii=False))
        return 0

    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        result["error"] = f"invalid atvscript output: {e}"
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if data.get("result") != "success":
        result["error"] = f"atvscript result={data.get('result')}"
        print(json.dumps(result, ensure_ascii=False))
        return 0

    app = str(data.get("app") or "")
    app_id = str(data.get("app_id") or "")
    result["available"] = True
    result["is_youtube"] = "youtube" in app.lower() or "youtube" in app_id.lower()
    result["device_state"] = data.get("device_state")
    result["title"] = data.get("title")
    result["position"] = data.get("position")
    result["total_time"] = data.get("total_time")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
