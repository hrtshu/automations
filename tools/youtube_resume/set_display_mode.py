#!/usr/bin/env python3
"""displayplacer でミラーリング / 拡張 (2画面) を切り替える。

使い方:
  set_display_mode.py mirror   # 従来の adjust_display_config.sh と同じ 2560x1440 ミラー
  set_display_mode.py extend   # 外部をメイン (0,0)、内蔵をその下に中央寄せで配置する拡張

stdout に JSON を出す:
{
  "ok": bool, "mode": "mirror"|"extend", "changed": bool,
  "internal": {"id": str, "res": [w, h], "origin": [x, y]} | null,
  "external": {"id": str, "res": [w, h], "origin": [x, y]} | null,
  "error": str | null
}

方針:
- ディスプレイが2枚でない場合は何もしない (ok=true, changed=false)。
- 解像度は推測で決めず、`displayplacer list` 末尾に出る現在の構成文字列から
  各ディスプレイの res / hz / color_depth / scaling を読み取って再利用する。
  ただしミラー時は従来スクリプトと同じ 2560x1440@60 / scaling:on に固定する。
- 外部ディスプレイのピクセル数が 4K 相当でない場合は何もしない (従来スクリプトのガード踏襲)。
"""
import json
import os
import re
import subprocess
import sys

DISPLAYPLACER = "/opt/homebrew/bin/displayplacer"
JQ = "/opt/homebrew/bin/jq"
MIRROR_RES = (2560, 1440)


def parse_displayplacer_list(text):
    """list 出力を {persistent_id: {...}} と現在の構成トークン列に分解する。"""
    screens = {}
    current_id = None
    for line in text.splitlines():
        m = re.match(r"Persistent screen id: (\S+)", line.strip())
        if m:
            current_id = m.group(1)
            screens[current_id] = {"id": current_id, "type": ""}
            continue
        if current_id is None:
            continue
        m = re.match(r"Type: (.+)", line.strip())
        if m:
            screens[current_id]["type"] = m.group(1).strip()
    # 末尾の `displayplacer "id:..." "id:..."` が現在の構成
    config_tokens = []
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("displayplacer "):
            config_tokens = re.findall(r'"([^"]+)"', line)
            break
    modes = {}
    for token in config_tokens:
        fields = dict(
            kv.split(":", 1) for kv in token.split() if ":" in kv
        )
        res_m = re.match(r"(\d+)x(\d+)", fields.get("res", ""))
        res = (int(res_m.group(1)), int(res_m.group(2))) if res_m else None
        mode = {
            "res": res,
            "hz": fields.get("hz", "60"),
            "color_depth": fields.get("color_depth", "8"),
            "scaling": fields.get("scaling", "off"),
        }
        # ミラー中は id:A+B の1トークンに複数IDが入る
        for sid in fields.get("id", "").split("+"):
            if sid:
                modes[sid] = mode
    return screens, modes


def external_pixels_ok():
    """system_profiler で外部ディスプレイのピクセル数を確認する (従来ガード)。

    (ok, detail) を返す。detail は判定に使った実値で、スキップ理由の表示用。
    """
    try:
        proc = subprocess.run(
            ["system_profiler", "-json", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(proc.stdout)
        displays = data["SPDisplaysDataType"][0]["spdisplays_ndrvs"]
        if len(displays) != 2:
            return None, f"displays={len(displays)}"
        for d in displays:
            if d.get("_name") != "Color LCD":
                pixels = d.get("_spdisplays_pixels")
                # 5120x2880 は S2721QS を「2560x1440相当」の Retina スケーリングで
                # 使う時のレンダリング解像度 (2026-11-14 実測で許可値に追加)
                return pixels in ("3840 x 2160", "4608 x 2592", "5120 x 2880"), \
                    f"name={d.get('_name')!r} pixels={pixels!r}"
    except Exception as e:
        return False, f"profiler error: {e}"
    return False, "no external display entry"


def format_entry(sid, mode, origin):
    w, h = mode["res"]
    return (
        f"id:{sid} res:{w}x{h} hz:{mode['hz']} color_depth:{mode['color_depth']} "
        f"enabled:true scaling:{mode['scaling']} origin:({origin[0]},{origin[1]}) degree:0"
    )


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("mirror", "extend"):
        print(json.dumps({"ok": False, "error": "usage: set_display_mode.py mirror|extend"}))
        return 1
    mode_arg = sys.argv[1]
    result = {"ok": True, "mode": mode_arg, "changed": False,
              "internal": None, "external": None, "error": None}

    if not os.path.exists(DISPLAYPLACER):
        result.update(ok=False, error="displayplacer not found")
        print(json.dumps(result))
        return 0

    try:
        list_out = subprocess.run([DISPLAYPLACER, "list"], capture_output=True,
                                  text=True, timeout=30).stdout
    except Exception as e:
        result.update(ok=False, error=f"displayplacer list failed: {e}")
        print(json.dumps(result))
        return 0

    screens, modes = parse_displayplacer_list(list_out)
    if len(screens) != 2:
        result["skipped_reason"] = f"screens={len(screens)} (need 2)"
        print(json.dumps(result))  # 2枚以外は何もしない
        return 0

    pixels_ok, pixels_detail = external_pixels_ok()
    if pixels_ok is not True:
        result["skipped_reason"] = f"external display pixels guard failed ({pixels_detail})"
        print(json.dumps(result))  # 対象外の外部ディスプレイでは何もしない
        return 0

    internal_id = next(
        (s["id"] for s in screens.values() if "built in" in s["type"].lower()), None)
    if internal_id is None:
        result["skipped_reason"] = "internal display not identified"
        print(json.dumps(result))  # 内蔵を特定できない場合は触らない
        return 0
    external_id = next(sid for sid in screens if sid != internal_id)

    int_mode_cur = modes.get(internal_id) or {"res": (1470, 956), "hz": "60",
                                              "color_depth": "8", "scaling": "on"}
    ext_mode_cur = modes.get(external_id) or {"res": MIRROR_RES, "hz": "60",
                                              "color_depth": "8", "scaling": "on"}

    if mode_arg == "mirror":
        desired = (f"id:{external_id}+{internal_id} res:{MIRROR_RES[0]}x{MIRROR_RES[1]} "
                   f"hz:60 color_depth:8 enabled:true scaling:on origin:(0,0) degree:0")
        commands = [desired]
        result["internal"] = {"id": internal_id, "res": list(MIRROR_RES), "origin": [0, 0]}
        result["external"] = {"id": external_id, "res": list(MIRROR_RES), "origin": [0, 0]}
    else:
        # ミラー中は構成トークンが id:EXT+INT の1本になり、両IDに同一モードが入る。
        # その場合、内蔵本来の解像度は構成文字列からは分からないため、
        # 外部はミラー解像度を正とし、内蔵は MacBook Air の既定スケーリングを使う。
        mirrored = (
            internal_id in modes and external_id in modes
            and modes[internal_id] is modes[external_id]
        )
        if mirrored or modes.get(external_id) is None:
            ext_mode = {"res": MIRROR_RES, "hz": "60", "color_depth": "8", "scaling": "on"}
        else:
            ext_mode = dict(ext_mode_cur)
        if mirrored or modes.get(internal_id) is None:
            int_mode = {"res": (1470, 956), "hz": "60", "color_depth": "8", "scaling": "on"}
        else:
            int_mode = dict(int_mode_cur)
        # 内蔵は外部の下に、横方向は中央寄せで置く (本人の希望する配置)
        int_origin = ((ext_mode["res"][0] - int_mode["res"][0]) // 2,
                      ext_mode["res"][1])
        ext_entry = format_entry(external_id, ext_mode, (0, 0))
        int_entry = format_entry(internal_id, int_mode, int_origin)
        commands = [ext_entry, int_entry]
        result["internal"] = {"id": internal_id, "res": list(int_mode["res"]),
                              "origin": list(int_origin)}
        result["external"] = {"id": external_id, "res": list(ext_mode["res"]), "origin": [0, 0]}

    # 既に希望の構成なら何もしない
    current_tokens = set(re.findall(r'"([^"]+)"', list_out.splitlines()[-1])) if list_out else set()
    normalized_current = set()
    for line in reversed(list_out.splitlines()):
        if line.strip().startswith("displayplacer "):
            normalized_current = set(re.findall(r'"([^"]+)"', line.strip()))
            break
    if set(commands) == normalized_current:
        print(json.dumps(result))
        return 0

    try:
        proc = subprocess.run([DISPLAYPLACER] + commands, capture_output=True,
                              text=True, timeout=30)
        if proc.returncode != 0:
            result.update(ok=False, error=(proc.stderr or proc.stdout or "")[:500])
            print(json.dumps(result))
            return 0
    except Exception as e:
        result.update(ok=False, error=f"displayplacer apply failed: {e}")
        print(json.dumps(result))
        return 0

    result["changed"] = True
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
