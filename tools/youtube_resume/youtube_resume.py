#!/usr/bin/env python3
"""MacBook 接続時に Apple TV で見ていた YouTube を Mac 側へ引き継ぐ。

呼び出し元: device_event_hooks/usb_added.sh (USBハブ検出 = チャンネルが Mac へ
切り替わった直後)。単体実行も可能。

動作:
  1. pyatv で Apple TV の再生状態を確認する (get_now_playing.py)
     - YouTube が playing / paused なら「再生していた」と確定できる。
       タイトルと再生位置 (秒) が取れるが動画IDは取れない。
  2. 動画IDの特定は Chrome 拡張機能 (extension/ フォルダ) へ委譲する。
     PWA は起動パラメータの URL を無視して常にホームを開くため、ペイロード
     (タイトル・再生位置) は引き継ぎ時だけ立てる localhost サーバー
     (127.0.0.1:18765) 経由で拡張へ渡し、拡張がログイン済みページの DOM から
     タイトル一致で ID を特定して開き直す。Cookie DB の読み取りは一切せず、
     usb_watcher への TCC 許可も不要。pyatv でタイトルが確定している場合のみ。
     (当初は yt-dlp + Chrome のログイン Cookie で視聴履歴から ID を取る方式も
     併存させていたが、拡張方式だけで完結するため 2026-11-14 に廃止し、
     関連コード・環境変数・セットアップ手順は一掃した)
  3. 候補があれば:
     - 再生中だった Apple TV を一時停止し、戻る (menu) も送って再生画面を抜ける
       (裏で進み続けるのを防ぎ、TVへ戻した時に再生画面に居残らないようにする)
     - ショート (総尺180秒以下) は候補にせず、ミラーのままにする
     - ディスプレイを拡張 (2画面) にし、内蔵ディスプレイ側に
       Chrome の YouTube アプリ (PWA) で動画を開く
     - pyatv で位置が取れた通常動画は &t=<位置-3>秒 を付け、その位置から開く
       (ライブ / 尺不明の場合は付けず、YouTube 自身のレジュームに任せる)
  4. 候補がなければ: 従来通りミラーリングにする (set_display_mode.py mirror)

オプション:
  --dry-run   判定だけ行い、表示切替・アプリ起動・一時停止をしない

ログ: ~/Library/Logs/youtube_resume.log
状態: ~/.local/state/youtube_resume/last_resume.json (二重起動ガード)
"""
import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.expanduser("~/Library/Logs/youtube_resume.log")
STATE_PATH = os.path.expanduser("~/.local/state/youtube_resume/last_resume.json")
PWA_APP_BUNDLE = os.path.expanduser(
    "~/Applications/Chrome Apps.localized/YouTube.app")
_PWA_BUNDLE_ID_CACHE = None


def pwa_bundle_id():
    """YouTube アプリ (PWA) のバンドルIDをアプリバンドル自身から解決する。

    バンドルIDは PWA のインストール状態に依存する環境依存値のため、
    ハードコードはせず、アプリバンドル (冷間起動の open -a でも使う
    PWA_APP_BUNDLE) の Contents/Info.plist にある CFBundleIdentifier を
    正とする (無い場合は CrAppModeShortcutID から組み立てる)。
    解決できない場合は、環境依存の決め打ち値でごまかさず RuntimeError を
    送出する (呼び出し側は引き継ぎを中止してエラーを返す)。結果は
    プロセス内にキャッシュする。
    """
    global _PWA_BUNDLE_ID_CACHE
    if _PWA_BUNDLE_ID_CACHE:
        return _PWA_BUNDLE_ID_CACHE
    bundle_id = None
    try:
        with open(os.path.join(PWA_APP_BUNDLE, "Contents", "Info.plist"),
                  "rb") as f:
            info = plistlib.load(f)
        bundle_id = info.get("CFBundleIdentifier")
        if not bundle_id and info.get("CrAppModeShortcutID"):
            bundle_id = "com.google.Chrome.app." + info["CrAppModeShortcutID"]
    except Exception:
        bundle_id = None
    if not bundle_id:
        raise RuntimeError(
            "YouTube アプリのバンドルIDを解決できません ("
            + os.path.join(PWA_APP_BUNDLE, "Contents", "Info.plist")
            + " が読めないか、CFBundleIdentifier / CrAppModeShortcutID が"
            "ありません)。Chrome で YouTube の PWA がインストールされて"
            "いるか確認してください")
    _PWA_BUNDLE_ID_CACHE = bundle_id
    return _PWA_BUNDLE_ID_CACHE


def pwa_app_id():
    """Chrome の --app-id に渡す値 (バンドルIDの末尾 = CrAppModeShortcutID)。"""
    return pwa_bundle_id().rsplit(".", 1)[-1]
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEDUPE_WINDOW_SEC = 180
# 二重起動ガードで「同じ引き継ぎ」とみなす再生位置のずれの許容幅 (秒)。
# 同じ動画でも位置がこれを超えて進んでいれば、TV側で見続けた結果の
# 新しい引き継ぎとして扱う。
DEDUPE_POSITION_TOLERANCE_SEC = 15
# 2枚目のディスプレイが認識されるまで待つ予算 (秒)
DISPLAY_WAIT_SEC = 8
PAYLOAD_PORT = int(os.environ.get("YOUTUBE_RESUME_PAYLOAD_PORT", "18765"))
# ペイロード取得待ちの上限 (秒)。拡張の service worker のリトライ上限
# (25秒) に、ウィンドウ選択・リロードにかかる時間を加えて余裕を持たせる。
PAYLOAD_WAIT_SEC = 75


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def run_json(script, timeout=100):
    try:
        proc = subprocess.run([sys.executable, os.path.join(HERE, script)],
                              capture_output=True, text=True, timeout=timeout)
        return json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception as e:
        return {"available": False, "error": f"{script}: {e}"}


def normalize_title(t):
    t = unicodedata.normalize("NFKC", t or "")
    t = re.sub(r"\s+", " ", t).strip().casefold()
    return t


def wait_for_two_displays():
    """2枚目のディスプレイが認識されるまで待つ (予算 DISPLAY_WAIT_SEC 秒)。

    判定は displayplacer list のスクリーン数 (set_display_mode.py と
    同じ判定源)。system_profiler より遥かに速く返るので 0.5 秒間隔で
    ポーリングでき、認識された瞬間に抜けられる。予算を超えたら False
    を返し、呼び出し側は1画面のまま続行する。
    """
    deadline = time.time() + DISPLAY_WAIT_SEC
    while True:
        try:
            proc = subprocess.run(["/opt/homebrew/bin/displayplacer", "list"],
                                  capture_output=True, text=True, timeout=10)
            n = sum(1 for line in (proc.stdout or "").splitlines()
                    if line.strip().startswith("Persistent screen id:"))
            if n >= 2:
                return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def set_display_mode(mode, dry_run):
    if dry_run:
        log(f"dry-run: set_display_mode {mode} (skipped)")
        return {"ok": True, "changed": False, "internal": None, "external": None}
    try:
        proc = subprocess.run([sys.executable, os.path.join(HERE, "set_display_mode.py"), mode],
                              capture_output=True, text=True, timeout=60)
        res = json.loads((proc.stdout or "").strip().splitlines()[-1])
        log(f"display mode {mode}: {json.dumps(res, ensure_ascii=False)}")
        return res
    except Exception as e:
        log(f"display mode {mode} failed: {e}")
        return {"ok": False, "error": str(e)}


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


_ATV_ID_CACHE = None


def resolve_atv_id():
    """Apple TV の identifier を解決する (環境変数 → atvscript scan の順)。

    atvremote は ID 未指定だと失敗することがある (実測で pause が rc=1) ため、
    失敗時のリトライ用に使う。結果はプロセス内だけにキャッシュする。
    """
    global _ATV_ID_CACHE
    if _ATV_ID_CACHE:
        return _ATV_ID_CACHE
    env_id = os.environ.get("YOUTUBE_RESUME_ATV_ID")
    if env_id:
        _ATV_ID_CACHE = env_id
        return env_id
    atvscript = find_pyatv_tool("atvscript")
    try:
        proc = subprocess.run([atvscript, "scan"], capture_output=True,
                              text=True, timeout=25)
        data = json.loads((proc.stdout or "").strip().splitlines()[-1])
        devices = data.get("devices") or []
        if devices and devices[0].get("identifier"):
            _ATV_ID_CACHE = devices[0]["identifier"]
    except Exception as e:
        log(f"atvscript scan (for id) failed: {e}")
    return _ATV_ID_CACHE


def run_atvremote_once(command, atv_id):
    atvremote = find_pyatv_tool("atvremote")
    cmd = [atvremote]
    if atv_id:
        cmd += ["--id", atv_id]
    cmd.append(command)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15)


def run_atvremote(command, dry_run):
    if dry_run:
        log(f"dry-run: atvremote {command} (skipped)")
        return
    try:
        # ID未指定の atvremote は実測で毎回失敗する (rc=1 でデバイス一覧を吐く) ため、
        # 先に identifier を解決して --id 付きで実行するのを正規ルートにする。
        atv_id = resolve_atv_id()
        proc = run_atvremote_once(command, atv_id)
        err = " ".join((proc.stderr or proc.stdout or "").split())[-200:]
        log(f"atvremote {command}: rc={proc.returncode}"
            + (f" {err}" if proc.returncode != 0 else ""))
    except Exception as e:
        log(f"atvremote {command} failed: {e}")


def _remote_steps(now_playing):
    """Apple TV へ送る操作列を返す: ("cmd", 名前) | ("sleep", 秒)。

    再生中なら pause、続けて menu を2回 (再生バー表示中は1回目がバーを
    消すだけで消費されるため)。menu を送らない場合は環境変数
    YOUTUBE_RESUME_ATV_BACK=off。
    """
    steps = []
    if now_playing.get("device_state") == "playing":
        steps.append(("cmd", "pause"))
    if os.environ.get("YOUTUBE_RESUME_ATV_BACK", "on").lower() != "off":
        if steps:
            steps.append(("sleep", 0.5))
        steps += [("cmd", "menu"), ("sleep", 1.0), ("cmd", "menu")]
    return steps


def run_atv_batch(steps):
    """操作列を atv_batch.py (pyatv API、1接続) で一括送信する。

    接続できて実行できたら results (コマンド単位の成否) を返す。
    バッチが使えない (接続失敗・スクリプト異常) 場合は None を返し、
    呼び出し側が従来方式へフォールバックする。
    """
    python = find_pyatv_tool("python3")
    script = os.path.join(HERE, "atv_batch.py")
    if not os.path.exists(script) or not os.path.exists(python):
        return None
    args = []
    for kind, value in steps:
        args.append(f"sleep:{value}" if kind == "sleep" else value)
    cmd = [python, script]
    atv_id = resolve_atv_id()
    if atv_id:
        cmd += ["--id", atv_id]
    cmd += args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=45)
        out = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception as e:
        log(f"atv batch failed to run: {e}")
        return None
    for r in out.get("results") or []:
        status = "ok" if r.get("ok") else f"error: {r.get('error')}"
        log(f"atv batch {r.get('command')}: {status}")
    if not out.get("ok"):
        log(f"atv batch unavailable: {out.get('error')}")
        return None
    return out.get("results") or []


def pause_apple_tv(now_playing, dry_run):
    """Apple TV 側を止める: 再生中なら pause、加えて戻る (menu) で再生画面を抜ける。

    表示は既に Mac 側へ切り替わっているため TV 画面は見えないが、次に
    Apple TV へ戻した時に再生画面に居残っていない状態にする。
    既に paused だった場合も menu は送る (再生画面を抜けるため)。
    menu は2回送る: 再生バー (シークバー等の OSD) が表示されていると
    1回目はバーを消すだけで消費され、再生画面を抜けられないため
    (2026-11-14 本人指摘)。バーが無い場合は2回分戻ることになるが、
    表示は既に Mac 側へ切り替わっているため実害は小さいという判断。
    menu を送らない場合は環境変数 YOUTUBE_RESUME_ATV_BACK=off。
    送信は atv_batch.py で1接続にまとめる (コマンドごとの atvremote
    起動だと接続が毎回かさむため)。バッチが使えない場合は従来方式
    (atvremote 個別実行) へフォールバックする。また menu はセッション
    ごとに NotSupported になることがある (Companion 接続の成否依存と
    みられる) ため、バッチで menu が失敗した分だけ、セッションを
    作り直す atvremote で再送する。
    """
    steps = _remote_steps(now_playing)
    if not steps:
        return
    if dry_run:
        log(f"dry-run: atv remote steps {steps} (skipped)")
        return
    results = run_atv_batch(steps)
    if results is not None:
        failed_menus = [r for r in results
                        if r.get("command") == "menu" and not r.get("ok")]
        if failed_menus:
            log(f"menu failed in batch x{len(failed_menus)}; "
                "retrying menu via atvremote")
            for i in range(len(failed_menus)):
                if i:
                    time.sleep(1.0)
                run_atvremote("menu", dry_run)
        return
    log("atv batch unavailable; falling back to per-command atvremote")
    for kind, value in steps:
        if kind == "sleep":
            time.sleep(value)
        else:
            run_atvremote(value, dry_run)


def youtube_app_window_title():
    """PWA (YouTube アプリ) の先頭ウィンドウのタイトルを返す。無ければ空文字。

    プロセスの特定は名前ではなくバンドルIDで行う (名前は環境で変わり得る)。
    """
    script = f'''
tell application "System Events"
    set p to (first process whose bundle identifier is "{pwa_bundle_id()}")
    if (count of windows of p) is 0 then return ""
    return name of window 1 of p
end tell
'''
    try:
        proc = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=10)
        return proc.stdout.strip()
    except Exception:
        return ""


def wait_for_youtube_app(expect_title_part, timeout_sec):
    """PWAウィンドウが現れ、履歴ページ or 対象動画のタイトルになれば True。

    open -b でアプリだけ起動して URL が渡らないケースがあるため、
    ウィンドウの存在ではなくタイトルで遷移を確認する。
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        title = youtube_app_window_title()
        if title:
            if expect_title_part and expect_title_part in title:
                return True
            if "履歴" in title or "History" in title:
                return True
        time.sleep(1)
    return False


def youtube_app_pids():
    """起動中の YouTube アプリ (PWA) のプロセスID集合を返す。

    PWA の複数ウィンドウは1プロセスに同居する (実測: 1プロセスで
    windows=2 のため、ウィンドウの増減はプロセスでは数えられない)。
    ここでは起動しているかどうかの把握にだけ使う。
    """
    script = ('tell application "System Events" to get unix id of '
              f'(every process whose bundle identifier is "{pwa_bundle_id()}")')
    try:
        proc = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=10)
        return {int(x) for x in proc.stdout.replace(",", " ").split()
                if x.strip().isdigit()}
    except Exception:
        return set()


def chrome_is_running():
    """Google Chrome 本体のプロセスが存在するか (System Events 経由なので
    Chrome を新たに起動してしまう副作用はない)。"""
    script = ('tell application "System Events" to exists '
              '(first process whose name is "Google Chrome")')
    try:
        proc = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=10)
        return proc.stdout.strip() == "true"
    except Exception:
        return False


def chrome_window_tabs():
    """Chrome の全ウィンドウについて {window_id: アクティブタブのURL} を返す。

    アプリ (PWA) のウィンドウも通常ウィンドウと一緒に列挙でき、IDも
    採れる (実測)。Chrome が起動していない状態で呼ぶと Chrome が起動して
    しまうため、呼び出し側で起動済みを確認すること。
    """
    script = '''
tell application "Google Chrome"
    set out to ""
    repeat with w in every window
        set out to out & (id of w) & "\t" & (URL of active tab of w) & linefeed
    end repeat
    return out
end tell
'''
    try:
        # タイムアウトは短めにする。起動直後は Chrome が忙しく AppleScript
        # が数秒固まることがあり、長いタイムアウトだと呼び出し側の探索
        # 予算を1回で食い潰してしまう (実測)。
        proc = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=4)
    except Exception:
        return {}
    result = {}
    for line in proc.stdout.splitlines():
        wid, sep, url = line.partition("\t")
        if sep and wid.strip().lstrip("-").isdigit():
            result[int(wid.strip())] = url.strip()
    return result


def log_window_positions(tag):
    """診断用: Chrome の各ウィンドウの左上座標とアクティブタブURLをログする。

    移動時の読み戻し照合 (reloaded+moved) は移動直後の状態しか保証
    しないため、引き継ぎ完了時点でもウィンドウが内蔵ディスプレイに
    留まっているかを、後からログで確認できるようにする。
    """
    script = '''
tell application "Google Chrome"
    set out to ""
    repeat with w in every window
        set b to bounds of w
        set out to out & (item 1 of b) & "," & (item 2 of b) & " " & (URL of active tab of w) & linefeed
    end repeat
    return out
end tell
'''
    try:
        proc = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=4)
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        log(f"window positions ({tag}): {' | '.join(lines)}")
    except Exception as e:
        log(f"window positions ({tag}): failed: {e}")
def handoff_window_reload_and_move(internal, timeout_sec=30):
    """YouTube ホームを開いているウィンドウを選び、リロードして内蔵側へ配置する。

    選択・リロード・配置は1回の AppleScript 実行の中で完結させる。
    ウィンドウIDを別の osascript 実行へ持ち回す方式は使わない: 列挙で
    得たIDは別の実行の `id of w` 比較では一致しない (2026-11-14 17:26 の
    実測。列挙では見えていた新規ホームウィンドウが、リロード側では
    window-not-found になり続け、手動リロードが必要だった)。対象は
    「YouTube ホームのタブを持つウィンドウ」で、アクティブタブがホームの
    ウィンドウを優先する。watch / shorts のタブには触らない。配置は
    bounds をセットした後に読み戻して照合し、ずれていたら "reloaded"
    を返して呼び出し側の従来方式 (System Events) にフォールバックさせる。
    制限時間内にホームが見つからなければ False。
    """
    home = "https://www.youtube.com/"
    if internal:
        x, y = internal["origin"]
        w, h = internal["res"]
        after_reload = f'''
                try
                    set bounds of w to {{{x}, {y}, {x + w}, {y + h}}}
                    set b to bounds of w
                    if (item 1 of b is {x}) and (item 2 of b is {y}) then
                        return "reloaded+moved"
                    end if
                end try
                return "reloaded"'''
    else:
        after_reload = '\n                return "reloaded"'
    script = f'''
tell application "Google Chrome"
    repeat with w in every window
        set t to active tab of w
        if (URL of t) is "{home}" then
            reload t{after_reload}
        end if
    end repeat
    repeat with w in every window
        repeat with t in every tab of w
            if (URL of t) is "{home}" then
                reload t{after_reload}
            end if
        end repeat
    end repeat
    return "no-home-tab"
end tell
'''
    deadline = time.time() + timeout_sec
    last_result = None
    while time.time() < deadline:
        try:
            proc = subprocess.run(["osascript", "-e", script],
                                  capture_output=True, text=True, timeout=8)
            result = proc.stdout.strip() or proc.stderr.strip() or "(no output)"
        except Exception as e:
            result = f"error: {e}"
        if result != last_result:
            log(f"handoff window (reload+move): {result}")
            last_result = result
        if result == "reloaded+moved":
            return True
        if result == "reloaded":
            # リロードはできたが配置が合わなかった。従来方式で配置する。
            move_youtube_to_internal(internal, False)
            return True
        time.sleep(1)
    return False


def open_youtube_app(url, dry_run, expect_title=""):
    if dry_run:
        log(f"dry-run: open YouTube app {url} (skipped)")
        return True
    # 起動は Chrome の --app-id の1経路だけにする。
    # 以前は open -b → --app-url → --app の3経路を順に試したが、PWAウィンドウの
    # タイトルが System Events 経由で取得できない (missing value が返る) ため
    # 遷移確認が常に失敗し、3経路すべてが発火してウィンドウが3つ開いていた。
    # open -b は URL を渡さずホームを開く実測があり、--app は別窓 (サブウィンドウ)
    # になるため、アプリ起動シムと同じ --app-id に一本化する。URL は渡さない
    # (渡しても無視されホームが開く。ペイロードは localhost 経由で渡す)。
    # アプリ起動済みの場合は、既存ウィンドウが前面化されるだけのことも
    # 新しいウィンドウが開くこともある (どちらになるかは状態次第で読め
    # ない)。終了はせず、どちらの結果でも呼び出し側が「ホームを表示して
    # いるウィンドウ」を選んで引き継ぎに使う。既存ウィンドウの watch /
    # shorts ページには触らない。
    # 例外: Chrome 本体が完全に終了している状態では、バイナリへの
    # --app-id 指定ではアプリウィンドウが開かない (2026-11-14 17:36 の
    # 実測: 起動ログは出るがホームタブが最後まで現れない)。その場合は
    # LaunchServices 経由でアプリバンドルを開き、シムに Chrome を
    # アプリモードで冷間起動させる。
    app_bundle = PWA_APP_BUNDLE
    cold = not chrome_is_running() and not youtube_app_pids()
    try:
        if cold and os.path.isdir(app_bundle):
            subprocess.Popen(["open", "-a", app_bundle],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            log("launched YouTube app via open -a (Chrome was not running)")
        else:
            subprocess.Popen([CHROME_BIN, f"--app-id={pwa_app_id()}"],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            log("launched YouTube app via Chrome --app-id "
                "(payload is served via the localhost server)")
    except Exception as e:
        log(f"YouTube app launch failed: {e}")
        return False
    # ウィンドウ生成の待ちは呼び出し側の探索ループが担うため、ここでは
    # 固定の sleep を置かない (以前は5秒待っていた)。
    return True


def move_youtube_to_internal(internal, dry_run):
    """YouTube アプリのウィンドウを内蔵ディスプレイ (拡張時は外部の下) へ全面配置する。

    handoff_window_reload_and_move の配置 (Chrome の bounds 指定) が
    合わなかった場合のフォールバック。対象は PWA プロセスの先頭
    ウィンドウになる。引き継ぎ完了時の最終配置にも使う: Chrome の
    bounds 指定は読み戻し照合が一致しても、ページ読み込み完了時に
    アプリウィンドウの保存位置へ戻されることがあるため (2026-11-14
    18:06 の実測)、実ウィンドウを掴む System Events 経由で締める。
    """
    if not internal:
        log("move window: internal display info missing, skipped")
        return
    x, y = internal["origin"]
    w, h = internal["res"]
    if dry_run:
        log(f"dry-run: move YouTube window to ({x},{y}) {w}x{h} (skipped)")
        return
    script = f'''
tell application "System Events"
    set procList to (every process whose bundle identifier is "{pwa_bundle_id()}")
    if (count of procList) is 0 then return "no process"
    tell first item of procList
        if (count of windows) is 0 then return "no window"
        tell window 1
            set position to {{{x}, {y}}}
            set size to {{{w}, {h}}}
        end tell
    end tell
end tell
return "ok"
'''
    for _ in range(5):
        try:
            proc = subprocess.run(["osascript", "-e", script],
                                  capture_output=True, text=True, timeout=10)
            if proc.stdout.strip() == "ok":
                log(f"moved YouTube window to internal display ({x},{y}) {w}x{h}")
                return
        except Exception as e:
            log(f"move window error: {e}")
        time.sleep(1)
    log("move window: gave up (window-placer等の配置と競合する場合はそちらが優先される)")


class _PayloadHandler(BaseHTTPRequestHandler):
    """拡張機能へペイロードを渡すための使い捨て HTTP ハンドラ。

    PWA は起動パラメータの URL を無視して常にホームを開く (実測で3方式とも
    ホームだった) ため、ペイロードは localhost 経由で渡す。https ページから
    http://127.0.0.1 への取得は CORS / Private Network Access の対象になる
    ため、プリフライト (OPTIONS) にも応答して必要なヘッダーを返す。
    """

    payload_body = b""
    fetched = threading.Event()
    # ペイロードは先着1回だけ配る。YouTube アプリのウィンドウが複数ある
    # 状態で引き継ぐと、対象外のウィンドウの拡張まで取得に来ることがあり、
    # 二重に引き継ぎが走るのを防ぐため、2回目以降は 410 を返す。
    served = False
    serve_lock = threading.Lock()

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        log(f"payload server: OPTIONS {self.path}")
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        log(f"payload server: GET {self.path}")
        if self.path.split("?")[0] != "/payload":
            self.send_response(404)
            self.end_headers()
            return
        with type(self).serve_lock:
            if type(self).served:
                self.send_response(410)
                self.end_headers()
                return
            type(self).served = True
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(self.payload_body)
        type(self).fetched.set()

    def log_message(self, *args):
        pass


def start_payload_server(payload):
    """ペイロードを 127.0.0.1 で配る使い捨てサーバーを別スレッドで起動する。

    (server, fetched_event) を返す。起動に失敗したら (None, None)。
    """
    _PayloadHandler.payload_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _PayloadHandler.fetched = threading.Event()
    _PayloadHandler.served = False
    _PayloadHandler.serve_lock = threading.Lock()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PAYLOAD_PORT), _PayloadHandler)
    except OSError as e:
        log(f"payload server failed to start on port {PAYLOAD_PORT}: {e}")
        return None, None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log(f"payload server listening on 127.0.0.1:{PAYLOAD_PORT}")
    return server, _PayloadHandler.fetched


def extension_handoff(now_playing, dry_run):
    """Chrome 拡張機能へ動画特定を委譲する経路 (唯一の方式)。

    PWA を起動し (常にホームが開く)、ペイロードは localhost の使い捨て
    サーバー経由で拡張機能 (extension/content.js) へ渡す。拡張はホームで
    ペイロードを受け取り、ミニプレイヤー照合 → 履歴ページの DOM 照合へ進み、
    タイトル一致で動画IDを特定して再生位置付きで開き直す。位置が取れない
    場合は YouTube 自身のレジュームに任せる (拡張側の仕様)。

    アプリ起動済みでも終了はしない。起動後に「YouTube ホームを表示して
    いるウィンドウ」を1つ選んでそのタブをリロードし、ペイロードの取得と
    引き継ぎをそのウィンドウで進める (選択・リロード・内蔵側への配置は
    handoff_window_reload_and_move が1回の AppleScript 実行で完結させる。
    ウィンドウIDの持ち回しは別の実行では一致しないため使わない)。watch /
    shorts 等のページには触らないため、既存ウィンドウで再生中のものは
    守られる。
    """
    # バンドルIDは最初に解決を確定させる。解決できなければ以降の起動・
    # 配置はすべて失敗するので、ここで潔くエラーで返す。
    try:
        pwa_bundle_id()
    except RuntimeError as e:
        log(f"handoff aborted: {e}")
        return 1
    payload = {
        "title": now_playing.get("title") or "",
        "position": now_playing.get("position"),
        "total": now_playing.get("total_time"),
        # TV側の再生状態 (参考情報。Mac側は状態に依らず常に一時停止で開く)
        "state": now_playing.get("device_state"),
        "ts": int(time.time()),
    }
    # 二重起動ガード (タイトル+再生位置。IDは拡張側で決まるため本体では
    # 持ち回らない)。同じ動画でも再生位置が許容幅を超えて進んでいれば、
    # TV側で見続けた結果の新しい引き継ぎとして扱い、抑止しない。位置が
    # 取れていない場合 (ライブ等) はタイトルだけで判定する。
    state = load_state()
    if (state.get("source") == "atv+extension"
            and normalize_title(state.get("title") or "") == normalize_title(payload["title"])
            and time.time() - float(state.get("resumed_at", 0)) < DEDUPE_WINDOW_SEC):
        prev_pos = state.get("position")
        cur_pos = payload["position"]
        if (prev_pos is None or cur_pos is None
                or abs(float(cur_pos) - float(prev_pos))
                <= DEDUPE_POSITION_TOLERANCE_SEC):
            log(f"duplicate handoff suppressed: {payload['title']!r} "
                f"(position {prev_pos} -> {cur_pos})")
            return 0
        log(f"same title but position moved ({prev_pos} -> {cur_pos}); "
            "not suppressed")

    log(f"candidate: source=atv+extension title={payload['title']!r} "
        f"position={payload['position']}")

    pause_apple_tv(now_playing, dry_run)

    wait_for_two_displays()
    disp = set_display_mode("extend", dry_run)
    time.sleep(0 if dry_run else 1)  # 表示構成が落ち着くのを待つ
    server = None
    fetched = None
    if not dry_run:
        server, fetched = start_payload_server(payload)
    # 起動前のウィンドウ一覧をログ・診断用に控える (選択ロジック自体は
    # ウィンドウIDを使わない)。Chrome が起動していない状態で Chrome へ
    # AppleScript を送ると起動してしまうため、PWA か Chrome のプロセスが
    # ある時だけ取得する。
    before = {}
    if not dry_run and (youtube_app_pids() or chrome_is_running()):
        before = chrome_window_tabs()
        log(f"windows before launch: {before}")
    if open_youtube_app("https://www.youtube.com/", dry_run,
                        expect_title=payload["title"]):
        if dry_run:
            log("dry-run: handoff window reload/move (skipped)")
        elif not handoff_window_reload_and_move(disp.get("internal")):
            log("handoff window: no YouTube home tab found; nothing touched")
    else:
        log("YouTube app launch failed")

    if server is not None:
        if fetched.wait(PAYLOAD_WAIT_SEC):
            log("payload fetched by extension")
        else:
            log(f"payload NOT fetched within {PAYLOAD_WAIT_SEC}s "
                "(extension not loaded / not reloaded?)")
        server.shutdown()
        server.server_close()

    # 最終配置: リロード直後の bounds 指定は、読み込み完了時に Chrome
    # がアプリウィンドウの保存位置へ戻してしまうことがある (2026-11-14
    # 18:06 の実測: 照合OKの2秒後には外部側の座標へ戻っていた)。
    # 遷移が落ち着いてから System Events 経由で再配置して締める。
    if not dry_run and disp.get("internal"):
        time.sleep(2)
        move_youtube_to_internal(disp["internal"], dry_run)

    if not dry_run and (youtube_app_pids() or chrome_is_running()):
        log_window_positions("after handoff")

    save_state({"video_id": None, "title": payload["title"],
                "position": payload["position"],
                "source": "atv+extension", "resumed_at": time.time()})
    log("=== youtube_resume done (extension handoff) ===")
    return 0


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dry_run = args.dry_run

    log(f"=== youtube_resume start (dry_run={dry_run}) ===")

    # --- 1. Apple TV の再生状態 ---
    now_playing = run_json("get_now_playing.py", timeout=30)
    # 接続直後は再生情報が一時的に idle / 空で返ることがある (Apple TV
    # 側の Now Playing メタデータが載るまでのラグ) ため、間隔を空けて
    # 最大2回まで再試行する。
    for retry_wait in (3, 5):
        if now_playing.get("available") and now_playing.get("title"):
            break
        log(f"now_playing not confirmed (will retry in {retry_wait}s): "
            f"{json.dumps(now_playing, ensure_ascii=False)}")
        time.sleep(retry_wait)
        now_playing = run_json("get_now_playing.py", timeout=30)
    log(f"now_playing: {json.dumps(now_playing, ensure_ascii=False)}")
    atv_youtube = (
        now_playing.get("available")
        and now_playing.get("is_youtube")
        and now_playing.get("device_state") in ("playing", "paused")
        and bool(now_playing.get("title"))
    )

    # ショートは引き継がない: 総尺が短い動画は対象外としてミラーにする。
    # ショートは最大3分 (180秒)。尺だけでは通常動画と区別できないため、
    # 180秒以下の通常動画も巻き込んでスキップする (閾値は環境変数
    # YOUTUBE_RESUME_SHORTS_MAX_SEC で変更可、0で無効化)。
    if atv_youtube:
        total = now_playing.get("total_time")
        try:
            shorts_max = int(os.environ.get("YOUTUBE_RESUME_SHORTS_MAX_SEC", "180"))
        except ValueError:
            shorts_max = 180
        if shorts_max > 0 and isinstance(total, int) and 0 < total <= shorts_max:
            log(f"shorts-like video (total={total}s <= {shorts_max}s) -> skip handoff")
            wait_for_two_displays()
            set_display_mode("mirror", dry_run)
            return 0

    # --- 2. 動画IDの特定は拡張機能へ委譲する ---
    if atv_youtube:
        return extension_handoff(now_playing, dry_run)
    # Apple TV で再生が確定しなければ引き継ぐ対象は無い。動画IDの特定は
    # 拡張方式のみで、Cookie や履歴取得には一切触らずミラーで終わる。
    log("no confirmed ATV playback -> mirror")
    wait_for_two_displays()
    set_display_mode("mirror", dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
