# youtube_resume — Apple TV の YouTube を Mac へ引き継ぐ

MacBook をドックに繋いで HDMI が Mac 側へ切り替わった直後に、
Apple TV で見ていた YouTube 動画を Mac の Chrome YouTube アプリ (PWA) で開き直す。

- 引き継ぐ動画がある場合: ディスプレイは拡張 (2画面) になり、
  YouTube アプリは内蔵ディスプレイ側に全面表示される
- ない場合: 従来通りミラーリングになる

## 構成

| ファイル | 役割 |
|---|---|
| `youtube_resume.py` | 本体。判定・一時停止・表示切替・アプリ起動を統括 |
| `get_now_playing.py` | pyatv (`atvscript`) で Apple TV の再生状態・タイトル・再生位置を取得 |
| `atv_batch.py` | pyatv API で Apple TV へ pause / menu 等のコマンド列を1接続で一括送信する |
| `set_display_mode.py` | `displayplacer` で mirror / extend を切替。解像度は現在の構成から読み取る |
| `extension/` | Chrome 拡張機能 (Manifest V3)。履歴ページの DOM から動画を特定して開き直す。動画IDの特定はこの方式のみ |

呼び出し元は `device_event_hooks/usb_added.sh`。
USB ハブ `2109:2817` の接続 (＝従来のチャンネル切替条件) を満たす場合のみ、
チャンネル切替後に非同期で本体が起動する。
なお `device_event_hooks/` は丸ごと `.gitignore` で管理外 (ローカルの
フック置き場という既存の設計) のため、フック側の変更はこのリポジトリには
含まれない。組み込みに必要な追加スニペットは、再現できるよう
「初期セットアップ」に正本として記載してある。

## 「再生していた」判定の仕組み

YouTube の視聴履歴には視聴時刻が無く、順序しか分からないため、
履歴だけでは「今さっき見ていた」かを判定できない。そこで:

1. pyatv がゲート: Apple TV で YouTube が playing / paused なら再生していたと確定。
   タイトルと再生位置 (秒) は取れるが動画IDは取れない
2. ID解決は Chrome 拡張機能が担当: PWA は起動パラメータの URL を
   無視して常にホームを開くため (実測で --app-url / open -b / open -a の
   3方式ともホームだった)、ペイロード (タイトル・再生位置) は本体が
   引き継ぎ時だけ立てる localhost サーバー (http://127.0.0.1:18765/payload)
   経由で拡張へ渡す (ペイロードは先着1回だけ配布し、2回目以降は 410 を
   返す。複数ウィンドウでの二重引き継ぎ防止)。拡張はホームでペイロードを受け取り、まず右下の
   ミニプレイヤーと照合し、無ければ履歴ページへ移って DOM からタイトル
   一致でIDを特定して開き直す。Cookie DB を読まないため、usb_watcher への
   TCC 許可は不要。
   特定は、ミニプレイヤー → 履歴の先頭付近の照合 → 履歴内検索
   (?query= 付きURL) → YouTube全体検索の最上位、の順で試す (再生中の動画は
   ほぼ履歴の先頭にある前提で、長いスクロールはしない)。
   ATV で再生が確定しなかった場合は引き継ぎなしで終了する。なお接続直後は
   再生情報が一時的に idle / 空で返ることがあるため、本体は未確定時に
   3秒・5秒の間隔で最大2回まで再取得する

   なお、yt-dlp + Chrome のログイン Cookie で視聴履歴から ID を取る方式も
   当初は併存させていたが、拡張方式だけで完結するため 2026-11-14 に廃止
   した。履歴取得のために Cookie DB を読む経路は無く、キーチェーン許可や
   usb_watcher への TCC 許可は不要。

再生位置の引き継ぎ:

- pyatv で位置と総尺が取れた通常動画は `&t=<位置-3>秒` 付きで開く
- ライブ等で尺が取れない場合は位置を付けず、YouTube 自身のレジューム (同一アカウントの
  再生進捗同期) に任せる
- 引き継ぎ時に Apple TV 側を `atvremote pause` で止める (裏で進み続けるのを防ぐ)

Mac 側の再生状態:

- Mac 側は TV 側の状態によらず常に一時停止で開く。拡張が watch ページで
  20秒間ガードし、プレイヤーの再生 (広告含む) を止め続ける。再生開始
  イベント (play) でも即座に止める。ユーザーがプレイヤーをクリックするか
  K / スペースキーを押したら、本人の再生意思としてガードを解除する

## 初期セットアップ (Mac のターミナルで1回だけ)

```sh
chmod +x ~/automations/tools/youtube_resume/*.py
chmod +x ~/automations/device_event_hooks/usb_added.sh
```

フックへの組み込み (`device_event_hooks/usb_added.sh` は管理外のため、
既存のチャンネル切替呼び出しの後ろに以下を追加する):

```sh
# Apple TV で見ていた YouTube を Mac 側に引き継ぐ (表示切替を遅らせないよう非同期で実行)
# 対象ハブ以外では youtube_resume 側の判定でミラーに倒れるだけだが、
# change_home_monitor_channel.sh と同じハブ条件を満たす場合のみ起動する
if echo "$DEVICE_INFO_LIST" | grep -q "2109:2817"; then
    (sleep 2; "$SCRIPT_DIR/../tools/youtube_resume/youtube_resume.py") \
        >> ~/Library/Logs/youtube_resume.hook.log 2>&1 &
fi
```

非同期 (`&`) なのは、引き継ぎ処理で本来の表示切替を遅らせないため。
起動前に2秒待つのは、チャンネル切替が先に完了してから引き継ぎ側の
ディスプレイ操作が始まるようにするため。

Chrome 拡張機能の読み込み (初回のみ):

1. Chrome で `chrome://extensions` を開き、右上の「デベロッパー モード」を ON にする
2. 「パッケージ化されていない拡張機能を読み込む」で
   `~/automations/tools/youtube_resume/extension` を選択する
3. 以後 content.js を書き換えたら、同画面で拡張の再読み込みボタンを押す

pyatv 用の専用 venv (必須):

既存の `~/.local/bin/atvremote` は mise の Python 3.15.2t (free-threaded) 上にあり、
`asyncio.get_event_loop()` が Python 3.15 で削除されたため起動直後に落ちる
(RuntimeError: There is no current event loop)。そのため pyatv は
Python 3.13 の専用 venv で動かす。スクリプト側は `.venv/bin` を最優先で参照する。

```sh
cd ~/automations/tools/youtube_resume
mise install python@3.13
mise exec python@3.13 -- python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/atvremote --version
```

Apple TV のペアリング (未実施の場合):

```sh
cd ~/automations/tools/youtube_resume
.venv/bin/atvremote scan
.venv/bin/atvremote --id <scanで出たidentifier> --protocol companion pair
.venv/bin/atvremote --id <scanで出たidentifier> --protocol airplay pair
```

`--protocol` を付けないと `No protocol specified` で落ちる (最近の pyatv の仕様)。
再生情報の取得と pause は companion が担い、airplay は補助として両方入れておく。
各回とも Apple TV の画面に表示される PIN を入力する。認証情報は `~/.pyatv.conf` に保存される。

ペアリング確認:

```sh
.venv/bin/atvscript playing
```

以降、手動で atvremote / atvscript を叩く場合も `.venv/bin/` のものを使うこと
(`~/.local/bin` のものは壊れたまま残置される)。

注意: pyatv 0.18.0 は srptools 2.x と非互換で、ペアリング時に
`SRPClientSession.__init__() takes 2 positional arguments but 3 were given`
という TypeError になる。そのため requirements.txt で `srptools<2` に固定してある。
venv を作り直す場合も素の `pip install pyatv` ではなく `-r requirements.txt` を使うこと。

特定の Apple TV に固定する場合は `~/.profile_env` 等に:

```sh
export YOUTUBE_RESUME_ATV_ID=<identifier>
```

Chrome 側の前提は、Apple TV と同一の Google アカウントで YouTube に
ログインしていることだけ (拡張機能がログイン済みページの DOM を読むため)。
Cookie DB の読み取りはしないので、キーチェーン許可やプロファイル指定は不要。

## 動作確認

判定だけ (表示もアプリも触らない):

```sh
~/automations/tools/youtube_resume/youtube_resume.py --dry-run
```

ログ: `~/Library/Logs/youtube_resume.log`

## 既知の制約・注意

- 同じ動画の再オープン抑制: `~/.local/state/youtube_resume/last_resume.json` に
  記録し、180秒以内の二重起動 (USB と display の両イベント等) は抑止する。
  拡張方式ではタイトル+再生位置で判定し、位置が15秒を超えて進んでいれば
  新しい引き継ぎとして通す (位置が取れない場合はタイトルだけで判定)
- ウィンドウ配置は AppleScript で内蔵ディスプレイへ移動する。
  window-placer 等の自動配置ツールが有効な場合、そちらの配置が優先されることがある
- 拡張時の内蔵ディスプレイ解像度は、ミラー中は構成文字列から読み取れないため
  MacBook Air の既定スケーリング (1470x956) を使う。普段と違うスケーリングを
  使っている場合は `set_display_mode.py` のフォールバック値を書き換えること
- Apple TV と Mac の YouTube が別アカウントだと履歴が一致せず引き継げない
- 拡張方式は YouTube の履歴ページ DOM に依存する。項目収集は旧レンダラ
  (`ytd-video-renderer`) と新レンダラ (`yt-lockup-view-model`) 等の
  複数出所に対応しているが、YouTube 側の DOM 変更で合わなくなったら
  `extension/content.js` の collectItems を直すこと
- 拡張方式では、再生開始直後で履歴への反映が済んでいない動画は履歴で拾えず、
  拡張側の検索フォールバックで開く (精度は落ちる)
- ショート (総尺180秒以下) は引き継がずミラーにする。尺での判定のため
  180秒以下の通常動画もスキップされる。閾値は `YOUTUBE_RESUME_SHORTS_MAX_SEC`
  (0で無効化)。拡張側でも、履歴のショート棚 (reel) にタイトルが一致したら
  開かずにスキップする安全網がある
- 引き継ぎ時は Apple TV へ pause に続けて menu (戻る) を送り、TV側の
  再生画面を抜ける。menu は2回送る (再生バー表示中は1回目がバーを消す
  だけで消費されるため)。送信は `atv_batch.py` が pyatv API で1接続に
  まとめて行い、使えない場合は atvremote の個別実行へフォールバック
  する。不要なら `YOUTUBE_RESUME_ATV_BACK=off`
- PWA の起動は Chrome の `--app-id` の1経路のみ (open -b / --app-url /
  --app は、起動パラメータの URL が無視されホームが開くか、遷移確認が
  成立せず複数経路が発火してウィンドウが増えたため廃止)。起動済みでも
  終了はせず、既存ウィンドウで再生中の watch / shorts には触らない。
  例外として、Chrome 本体が完全に終了している状態では `--app-id` では
  アプリウィンドウが開かないため、`open -a` でアプリバンドル
  (~/Applications/Chrome Apps.localized/YouTube.app) を開いてシムから
  冷間起動させる
- PWA のバンドルIDは環境依存 (PWA のインストール状態で変わり得る) ため
  ハードコードを正とせず、アプリバンドルの `Contents/Info.plist` の
  `CFBundleIdentifier` から解決する (読めない場合のみフォールバック値を
  使う)。`--app-id` に渡す値もバンドルIDの末尾から導出する
- 起動後の対象ウィンドウの選択・リロード・内蔵側への配置は、1回の
  AppleScript 実行の中で「YouTube ホームを表示しているタブ」を URL で
  見つけて完結させる。ウィンドウIDを別の osascript 実行へ持ち回すと
  `id of w` 比較で一致しない (実測) ため、ID は使わない。リロードが要る
  のは、読み込み済みのホームはペイロードを取りに行かず、冷間起動直後の
  ページは拡張の登録前に読み込まれて content script が注入されないため。
  配置は bounds を読み戻して照合し、ずれていたら System Events で
  PWA 先頭ウィンドウを配置する従来方式にフォールバックする
- 表示切替がガードで止まった場合は set_display_mode の出力に
  skipped_reason が付き、本体のログにも残る
