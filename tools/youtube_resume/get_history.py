#!/usr/bin/env python3
"""YouTube の視聴履歴 (新しい順) を yt-dlp + Chrome のログイン Cookie で取得する。

stdout に JSON を1つだけ出す:
{
  "available": bool,
  "entries": [{"id": str, "title": str, "url": str}, ...],  # 新しい順、最大10件
  "error": str | null
}

前提・注意:
- Chrome で YouTube にログインしていること (Apple TV と同一アカウント)。
- 初回実行時に macOS キーチェーン「Chrome Safe Storage」へのアクセス許可ダイアログが
  出る場合がある。その場合は許可する。
- 履歴エントリには視聴時刻が含まれない。「直近か」の判定は呼び出し側が
  pyatv の再生状態ゲートで行う。本スクリプトは順序 (最新が先頭) のみを保証する。
- 既定プロファイル以外を使っている場合は環境変数 YOUTUBE_RESUME_CHROME_PROFILE に
  プロファイル名 (例: "Profile 1") を設定すると chrome::<profile> 指定で取得する。
"""
import json
import os
import shutil
import subprocess
import sys

TIMEOUT_SEC = 90
HISTORY_URL = "https://www.youtube.com/feed/history"
MAX_ENTRIES = 10


def main():
    result = {"available": False, "entries": [], "error": None}

    # ~/.local/bin/yt-dlp は削除済み anaconda を指す壊れた shim なので、
    # Homebrew の実体を最優先する (既存スクリプト群の /opt/homebrew/bin 決め打ちに合わせる)
    yt_dlp = next(
        (p for p in ["/opt/homebrew/bin/yt-dlp", shutil.which("yt-dlp")]
         if p and os.path.exists(p)),
        None,
    )
    if not yt_dlp:
        result["error"] = "yt-dlp not found"
        print(json.dumps(result, ensure_ascii=False))
        return 0

    browser_spec = "chrome"
    profile = os.environ.get("YOUTUBE_RESUME_CHROME_PROFILE")
    if profile:
        browser_spec = f"chrome::{profile}"

    cmd = [
        yt_dlp,
        "--cookies-from-browser", browser_spec,
        "--flat-playlist",
        "--playlist-end", str(MAX_ENTRIES),
        "--no-warnings",
        "-J",
        HISTORY_URL,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        result["error"] = "yt-dlp timed out"
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except OSError as e:
        result["error"] = f"yt-dlp failed to run: {e}"
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
        result["error"] = f"invalid yt-dlp output: {e}"
        print(json.dumps(result, ensure_ascii=False))
        return 0

    entries = []
    for e in data.get("entries") or []:
        vid = e.get("id")
        if not vid:
            continue
        entries.append({
            "id": vid,
            "title": e.get("title") or "",
            "url": e.get("url") or f"https://www.youtube.com/watch?v={vid}",
        })

    result["available"] = True
    result["entries"] = entries
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
