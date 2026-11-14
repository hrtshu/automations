#!/usr/bin/env python3
"""Apple TV へのコマンド列を1接続でまとめて送る (pyatv API 直接利用)。

youtube_resume.py から呼ばれる。従来は atvremote をコマンドごとに
起動し直しており、プロセス起動 + pyatv import + 接続が毎回かさんで
いた (pause + menu x2 で実測6〜7秒)。このスクリプトは pyatv の API で
1回だけ接続し、コマンド列を順に送る。

使い方: atv_batch.py [--id IDENTIFIER] CMD [CMD ...]
  CMD には pause / menu 等のリモコンコマンドのほか、特殊コマンド
  "sleep:<秒>" (待機。リモコン連打の間隔用) を混ぜられる。
認証情報は ~/.pyatv.conf (ペアリング時に atvremote が保存したもの) を
pyatv のストレージ経由で読み込む。

stdout に JSON を1つだけ出す:
{
  "ok": bool,         # 接続できてコマンド列を実行できたか
  "connected": bool,
  "results": [{"command": str, "ok": bool, "error": str | null}],
  "error": str | null
}
接続自体に失敗した場合は ok=false で正常終了する。呼び出し側はその
場合、従来方式 (atvremote の個別実行) へフォールバックする。
個別コマンドの失敗 (menu の NotSupportedError 等) は results に記録
するだけで、残りのコマンドは続行する。
"""
import asyncio
import json
import os
import sys

CONF_PATH = os.path.expanduser("~/.pyatv.conf")
TIMEOUT_SEC = 30


async def _scan_with_storage(loop, identifier):
    """保存済み認証情報付きでデバイスをスキャンする。

    pyatv のバージョン差異で FileStorage / scan のシグネチャが違う
    場合は段階的にフォールバックする (最終的に認証情報なしの scan)。
    """
    import pyatv
    from pyatv.storage import file_storage

    storage = None
    try:
        try:
            storage = file_storage.FileStorage(CONF_PATH, loop)
        except TypeError:
            storage = file_storage.FileStorage(CONF_PATH)
        await storage.load()
    except Exception:
        storage = None
    if storage is not None:
        try:
            return await pyatv.scan(loop, identifier=identifier,
                                    storage=storage)
        except TypeError:
            pass
    return await pyatv.scan(loop, identifier=identifier)


async def _run(identifier, commands):
    import pyatv

    loop = asyncio.get_running_loop()
    atvs = await _scan_with_storage(loop, identifier)
    if not atvs:
        return {"ok": False, "connected": False, "results": [],
                "error": "device not found"}
    atv = None
    results = []
    try:
        # connect は必ず await する。当初 await を漏らした版では
        # 'coroutine' object has no attribute 'remote_control' となり
        # バッチ全体が使えず、個別実行へフォールバックしていた (実測)。
        atv = await pyatv.connect(atvs[0], loop)
        rc = atv.remote_control
        for cmd in commands:
            if cmd.startswith("sleep:"):
                try:
                    await asyncio.sleep(float(cmd.split(":", 1)[1]))
                    results.append({"command": cmd, "ok": True,
                                    "error": None})
                except Exception as e:
                    results.append({"command": cmd, "ok": False,
                                    "error": f"{type(e).__name__}: {e}"})
                continue
            fn = getattr(rc, cmd, None)
            if fn is None:
                results.append({"command": cmd, "ok": False,
                                "error": "unknown command"})
                continue
            try:
                await fn()
                results.append({"command": cmd, "ok": True, "error": None})
            except Exception as e:
                results.append({"command": cmd, "ok": False,
                                "error": f"{type(e).__name__}: {e}"})
        return {"ok": True, "connected": True, "results": results,
                "error": None}
    finally:
        if atv is not None:
            try:
                # close() は pyatv のバージョンによりコルーチンを返す
                # 場合があるため、両方に対応する。
                res = atv.close()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass


def main():
    args = sys.argv[1:]
    identifier = None
    if args[:1] == ["--id"] and len(args) >= 2:
        identifier = args[1]
        args = args[2:]
    if not args:
        print(json.dumps({"ok": False, "connected": False, "results": [],
                          "error": "no commands"}))
        return 0
    try:
        out = asyncio.run(
            asyncio.wait_for(_run(identifier, args), TIMEOUT_SEC))
    except Exception as e:
        out = {"ok": False, "connected": False, "results": [],
               "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
