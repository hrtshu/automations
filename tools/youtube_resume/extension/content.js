// YouTube Resume Handoff - content script
//
// ネイティブ側 (youtube_resume.py) からペイロードを受け取り、履歴ページの
// DOM からタイトルが一致する動画を特定し、再生位置付きで開き直す。
// 受け渡し経路は2つ:
//   - localhost (本線): 本体が引き継ぎ時だけ立てる
//     http://127.0.0.1:18765/payload から取得する。PWA は起動パラメータの
//     URL を無視して常にホームを開くため、URL では渡せない。
//   - URLハッシュ (ページ間の引き継ぎ): ホーム → 履歴 → 履歴内検索 →
//     全体検索とページを跨いで遷移する際、ペイロードを持ち回る手段が
//     他にないため、各遷移先URLの #ytresume=<URLエンコードしたJSON> に
//     載せて渡す。本体から直接開く場合の予備経路でもある。
//
// 特定は3段構え (再生中の動画はほぼ履歴の先頭にある前提で、軽い順に試す):
//   0. (ホームで開かれた場合のみ) 右下ミニプレイヤーの動画を照合し、無ければ履歴へ
//   1. 履歴の先頭付近を照合 (少しだけスクロールして遅延描画分を読む)
//   2. 履歴内検索 (履歴ページの検索欄と等価の ?query= 付きURL) で照合
//   3. YouTube 全体検索の最上位 (最終手段)
// ペイロード: {title, position, total, ts}  (position / total は秒、null 可)
//
// 再生位置の引き継ぎは2段構え:
//   1. pyatv のライブな再生位置が取れていれば URL の &t= で明示的に渡す
//      (YouTube 自身の進捗同期より新しいため優先する。3秒巻き戻し付き)
//   2. 位置が取れない場合 (ライブ等) は &t= を付けず、YouTube 自身の
//      レジューム (同一アカウントの再生進捗同期) に任せる
//
// ハッシュが無い通常の履歴閲覧では何もしない。
// Cookie の読み取りは一切しない (ログイン済みページ自体の DOM を読むだけ)。
(function () {
  "use strict";

  // バッジ: 注入されたら画面右上に6秒だけ表示する。自動で開かれた
  // ウィンドウだと目で見て分かるようにするための目印 (診断用に入れた
  // ものだが、2026-11-14 に本人の判断で残置が決まった)。
  try {
    var badge = document.createElement("div");
    badge.textContent = "YTRH active";
    badge.style.cssText =
      "position:fixed;top:8px;right:8px;z-index:99999;background:#c00;" +
      "color:#fff;padding:4px 8px;border-radius:4px;font-size:12px;" +
      "font-family:sans-serif;";
    document.documentElement.appendChild(badge);
    setTimeout(function () {
      badge.remove();
    }, 6000);
  } catch (e) {}

  var HASH_KEY = "#ytresume=";
  var HIST_KEY = "#ytresume-hist=";
  var SEARCH_KEY = "#ytresume-search=";
  var PHASE1_WAIT_MS = 6000;
  var PHASE2_WAIT_MS = 10000;
  var SEARCH_WAIT_MS = 15000;
  var MAX_ITEMS = 12;

  function parsePayload(prefix) {
    if (location.hash.indexOf(prefix) !== 0) return null;
    try {
      return JSON.parse(decodeURIComponent(location.hash.slice(prefix.length)));
    } catch (e) {
      return null;
    }
  }

  function normalize(t) {
    return (t || "").normalize("NFKC").replace(/\s+/g, " ").trim().toLowerCase();
  }

  function toast(msg) {
    var el = document.getElementById("ytrh-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "ytrh-toast";
      el.style.cssText =
        "position:fixed;left:16px;bottom:16px;z-index:9999;" +
        "background:rgba(0,0,0,.85);color:#fff;padding:10px 14px;" +
        "border-radius:8px;font-size:13px;font-family:sans-serif;";
      document.documentElement.appendChild(el);
    }
    el.textContent = msg;
  }

  function watchUrl(id, payload) {
    var url = "https://www.youtube.com/watch?v=" + encodeURIComponent(id);
    var pos = payload.position;
    var total = payload.total;
    // &t= を付ける条件: 冒頭 (10秒以下) では付ける意味がなく、終盤
    // (残り15秒未満) では終わり際から再生が始まってしまうため付けない。
    // その場合は YouTube 自身のレジュームに任せる。
    if (
      typeof pos === "number" &&
      typeof total === "number" &&
      total > 0 &&
      pos > 10 &&
      pos < total - 15
    ) {
      url += "&t=" + Math.max(pos - 3, 0) + "s";
    }
    return url;
  }

  // 動画ページへ遷移する。Mac側はTV側の状態に関わらず常に一時停止した
  // 状態で開くため、対象IDを控えて watch 側の runWatchPause に止めさせる
  // (同じタブ内の遷移では sessionStorage が引き継がれる)。
  function openWatch(id, payload) {
    try {
      sessionStorage.setItem("ytrh_pause_watch", id);
    } catch (e) {}
    location.replace(watchUrl(id, payload));
  }

  function collectItems() {
    // 履歴・検索の動画項目は旧レンダラ (ytd-video-renderer) と新レンダラ
    // (yt-lockup-view-model) が混在する。新レンダラには a#video-title が
    // 無く、旧セレクタだけでは目の前の項目を取りこぼす (実測: ショート棚の
    // 下に候補が表示されているのにフェーズ1が素通りした)。watch リンクを
    // 出所の確からしさ順に走査し、動画IDごとに最良のタイトルを残す。
    var specs = [
      { sel: "ytd-video-renderer a#video-title", pri: 3 },
      { sel: "yt-lockup-view-model a[href*='/watch?v=']", pri: 2 },
      { sel: "a[href*='/watch?v=']", pri: 1 }
    ];
    var byId = {};
    var order = [];
    specs.forEach(function (spec) {
      document.querySelectorAll(spec.sel).forEach(function (a) {
        var m = (a.getAttribute("href") || "").match(/[?&]v=([\w-]{6,})/);
        if (!m) return;
        var id = m[1];
        var title = (
          a.getAttribute("title") ||
          a.getAttribute("aria-label") ||
          a.textContent ||
          ""
        )
          .replace(/\s+/g, " ")
          .trim();
        if (!title) return;
        var cur = byId[id];
        if (!cur) {
          byId[id] = { id: id, title: title, pri: spec.pri };
          order.push(id);
        } else if (
          spec.pri > cur.pri ||
          (spec.pri === cur.pri && title.length > cur.title.length)
        ) {
          cur.title = title;
          cur.pri = spec.pri;
        }
      });
    });
    return order.map(function (id) {
      return byId[id];
    });
  }

  // タイトル照合は完全一致を最優先にしつつ前方一致まで許す。DOM 側の
  // タイトルは表示都合で末尾が省略されたり付帯情報が付いたりして、
  // pyatv のタイトルと完全には揃わない場合があるため。
  function titleMatches(candidateNorm, targetNorm) {
    return (
      !!candidateNorm &&
      (candidateNorm === targetNorm ||
        candidateNorm.indexOf(targetNorm) === 0 ||
        targetNorm.indexOf(candidateNorm) === 0)
    );
  }

  // 履歴ページでショートは通常の動画一覧 (ytd-video-renderer) ではなく
  // ショート棚 (ytd-reel-item-renderer、/shorts/ へのリンク) に並ぶ。
  // 対象タイトルがそちらに一致したらショートと判定できる。
  function findShort(targetNorm) {
    var nodes = document.querySelectorAll(
      'ytd-reel-item-renderer a[href*="/shorts/"],' +
        ' yt-lockup-view-model a[href*="/shorts/"]'
    );
    for (var i = 0; i < nodes.length; i++) {
      var label =
        nodes[i].getAttribute("aria-label") ||
        nodes[i].getAttribute("title") ||
        nodes[i].textContent ||
        "";
      if (titleMatches(normalize(label), targetNorm)) return label;
    }
    return null;
  }

  function findMatch(items, targetNorm) {
    var i, n;
    for (i = 0; i < items.length; i++) {
      if (normalize(items[i].title) === targetNorm) return items[i];
    }
    for (i = 0; i < items.length; i++) {
      n = normalize(items[i].title);
      if (n && (n.indexOf(targetNorm) === 0 || targetNorm.indexOf(n) === 0)) {
        return items[i];
      }
    }
    return null;
  }

  function runHistory(payload, phase) {
    var flag = "ytrh_done_" + phase + "_" + payload.ts;
    if (sessionStorage.getItem(flag)) return;
    toast(
      phase === 2
        ? "YouTube引き継ぎ: 履歴内検索で動画を特定しています…"
        : "YouTube引き継ぎ: 履歴から動画を特定しています…"
    );
    var targetNorm = normalize(payload.title);
    var start = Date.now();
    var lastScroll = 0;
    var scrolledOnce = false;
    var waitMs = phase === 2 ? PHASE2_WAIT_MS : PHASE1_WAIT_MS;
    var timer = setInterval(function () {
      // ショートは引き継がない (本体側の尺判定をすり抜けた場合の安全網)
      if (findShort(targetNorm)) {
        clearInterval(timer);
        sessionStorage.setItem(flag, "1");
        toast("YouTube引き継ぎ: ショートのためスキップしました");
        return;
      }
      var items = collectItems();
      var hit = findMatch(items, targetNorm);
      if (hit) {
        clearInterval(timer);
        sessionStorage.setItem(flag, "1");
        toast("YouTube引き継ぎ: 「" + hit.title + "」を開きます");
        openWatch(hit.id, payload);
        return;
      }
      // スクロールは最小限にする。フェーズ1では先頭が描画済みなのに見つからない
      // 場合に限り、遅延描画を促すため開始1.5秒後に1回だけ軽くスクロールする。
      // フェーズ2 (履歴内検索) は件数が絞られるため従来どおり読み込みを促す。
      var elapsed = Date.now() - start;
      if (
        phase === 1 &&
        items.length > 0 &&
        items.length < MAX_ITEMS &&
        elapsed > 1500 &&
        !scrolledOnce
      ) {
        scrolledOnce = true;
        window.scrollTo(0, document.documentElement.scrollHeight);
      } else if (
        phase === 2 &&
        items.length < MAX_ITEMS &&
        Date.now() - lastScroll > 1200
      ) {
        lastScroll = Date.now();
        window.scrollTo(0, document.documentElement.scrollHeight);
      }
      if (Date.now() - start > waitMs) {
        clearInterval(timer);
        sessionStorage.setItem(flag, "1");
        var q = encodeURIComponent(payload.title || "");
        var p = encodeURIComponent(JSON.stringify(payload));
        // 切り分け用に、この時点で先頭に見えていた動画をトーストへ出す
        var topTitle = items.length ? items[0].title : "(履歴0件)";
        if (phase === 1) {
          // 履歴内検索へ切り替え (履歴ページの検索欄と等価の ?query= 付きURL)
          toast(
            "YouTube引き継ぎ: 先頭の「" + topTitle + "」と一致せず、履歴内検索に切り替えます…"
          );
          location.replace(
            "https://www.youtube.com/feed/history?query=" + q + HIST_KEY + p
          );
        } else {
          toast(
            "YouTube引き継ぎ: 履歴内検索でも「" + topTitle + "」までしか見つからず、全体検索で特定します…"
          );
          location.replace(
            "https://www.youtube.com/results?search_query=" + q + SEARCH_KEY + p
          );
        }
      }
    }, 500);
  }

  // プレイヤー要素の API から現在の動画 (ID・完全タイトル) を取る。
  // ミニプレイヤー表示中は #movie_player がそのままミニプレイヤーになる。
  function miniplayerData() {
    var player = document.querySelector("#movie_player");
    if (player && typeof player.getVideoData === "function") {
      try {
        var d = player.getVideoData();
        if (d && d.video_id) return { id: d.video_id, title: d.title || "" };
      } catch (e) {}
    }
    return null;
  }

  function goHistory(payload, note) {
    if (note) toast("YouTube引き継ぎ: " + note + "。履歴で特定します…");
    var p = encodeURIComponent(JSON.stringify(payload));
    location.replace("https://www.youtube.com/feed/history" + HASH_KEY + p);
  }

  // 本体 (youtube_resume.py) が引き継ぎ時にだけ立てる localhost サーバーから
  // ペイロードを受け取る。取得自体は service worker (background.js) が
  // 拡張機能の文脈で行う。ページ文脈からの 127.0.0.1 への fetch は Chrome の
  // Local Network Access 制限でプリフライトすら送られず弾かれることがある
  // ため。サーバーが無い通常時は null が返るだけで無害。
  function fetchLocalPayload() {
    if (
      typeof chrome === "undefined" ||
      !chrome.runtime ||
      !chrome.runtime.sendMessage
    ) {
      return;
    }
    chrome.runtime.sendMessage({ type: "ytrh-get-payload" }, function (p) {
      if (chrome.runtime.lastError) {
        return;
      }
      if (!p || !p.title || !p.ts) return;
      if (Date.now() / 1000 - p.ts > 180) return; // 古いペイロードは無視
      var flag = "ytrh_local_" + p.ts;
      if (sessionStorage.getItem(flag)) return;
      sessionStorage.setItem(flag, "1");
      toast("YouTube引き継ぎ: 引き継ぎ情報を受け取りました");
      runHome(p);
    });
  }

  // ホーム表示時: 右下のミニプレイヤー (続きを見る) に対象動画が出ていれば
  // それが最速の特定経路 (候補が1つしかない)。出ていなければ履歴フェーズへ移る。
  function runHome(payload) {
    var flag = "ytrh_done_home_" + payload.ts;
    if (sessionStorage.getItem(flag)) return;
    toast("YouTube引き継ぎ: ホームで続きの動画を確認しています…");
    var targetNorm = normalize(payload.title);
    var start = Date.now();
    var timer = setInterval(function () {
      var data = miniplayerData();
      if (data) {
        clearInterval(timer);
        sessionStorage.setItem(flag, "1");
        if (titleMatches(normalize(data.title), targetNorm)) {
          toast("YouTube引き継ぎ: ミニプレイヤーの「" + data.title + "」を開きます");
          openWatch(data.id, payload);
        } else {
          goHistory(payload, "ミニプレイヤーは別の動画でした");
        }
        return;
      }
      if (Date.now() - start > 4000) {
        clearInterval(timer);
        sessionStorage.setItem(flag, "1");
        goHistory(payload, null);
      }
    }, 500);
  }

  function runSearch(payload) {
    var flag = "ytrh_done_search_" + payload.ts;
    if (sessionStorage.getItem(flag)) return;
    toast("YouTube引き継ぎ: 検索結果から動画を特定しています…");
    var start = Date.now();
    var timer = setInterval(function () {
      var items = collectItems();
      if (items.length > 0) {
        clearInterval(timer);
        sessionStorage.setItem(flag, "1");
        toast("YouTube引き継ぎ: 検索結果の先頭を開きます");
        openWatch(items[0].id, payload);
        return;
      }
      if (Date.now() - start > SEARCH_WAIT_MS) {
        clearInterval(timer);
        toast("YouTube引き継ぎ: 動画を特定できませんでした");
      }
    }, 500);
  }

  // watch ページ: 引き継いだ動画を一時停止した状態で開く (TV側の状態に
  // 依らず常に止める)。このスクリプトは watch では document_start で
  // 走り始め、ガード期間中はプレイヤーの再生を止め続ける。
  // 旧実装が止め損ねた原因の候補が2つあるため、両方を塞ぐ:
  // - 広告が流れると getVideoData の video_id が広告側になり、対象動画と
  //   一致しないことを理由にガードを早期終了していた疑い。そのため
  //   video_id では判定せず、プレイヤーの再生は無条件に止める。
  // - 「接触から10秒後に一時停止を確認したら終了」も早期終了の経路に
  //   なり得るため、終了条件はガード期間 (20秒) の満了だけにする。
  //   ただしユーザーがプレイヤーをクリック/キー操作したら、本人の
  //   再生意思としてガードを解除する。
  // ポーリングに加えて、再生開始イベント (play) を捕まえて即座に止める
  // ことで、tick の隙間に再生が進むのも防ぐ。
  function runWatchPause() {
    var armed = null;
    try {
      armed = sessionStorage.getItem("ytrh_pause_watch");
    } catch (e) {}
    if (!armed) return;
    var start = Date.now();
    var GUARD_MS = 20000;
    var done = false;
    var timer = setInterval(tick, 150);
    document.addEventListener("play", onPlay, true);
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    function finish(reason) {
      if (done) return;
      done = true;
      clearInterval(timer);
      document.removeEventListener("play", onPlay, true);
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
      try {
        sessionStorage.removeItem("ytrh_pause_watch");
      } catch (e) {}
    }
    function pausePlayer() {
      var player = document.querySelector("#movie_player");
      if (!player) return;
      try {
        if (typeof player.pauseVideo === "function") player.pauseVideo();
      } catch (e) {}
      var video = player.querySelector("video");
      if (video && !video.paused) {
        try {
          video.pause();
        } catch (e) {}
      }
    }
    function onPlay(ev) {
      if (ev.target && ev.target.tagName === "VIDEO") {
        try {
          ev.target.pause();
        } catch (e) {}
        pausePlayer();
      }
    }
    function onPointerDown(ev) {
      var player = document.querySelector("#movie_player");
      if (player && ev.target && player.contains(ev.target)) {
        finish("user-gesture");
      }
    }
    function onKeyDown(ev) {
      var t = ev.target;
      var editable =
        t && t.closest && t.closest("input, textarea, [contenteditable]");
      if (!editable && (ev.key === "k" || ev.key === " ")) {
        finish("user-gesture");
      }
    }
    function tick() {
      var player = document.querySelector("#movie_player");
      if (player && typeof player.pauseVideo === "function") {
        var st = -1;
        try {
          st = player.getPlayerState();
        } catch (e) {}
        if (st !== 2) pausePlayer();
      }
      if (Date.now() - start > GUARD_MS) finish("guard-elapsed");
    }
  }

  var payload = null;
  if (location.pathname.indexOf("/watch") === 0) {
    runWatchPause();
  } else if (location.pathname.indexOf("/results") === 0) {
    payload = parsePayload(SEARCH_KEY);
    if (payload) runSearch(payload);
  } else if (location.pathname.indexOf("/feed/history") === 0) {
    payload = parsePayload(HIST_KEY);
    if (payload) {
      runHistory(payload, 2);
    } else {
      payload = parsePayload(HASH_KEY);
      if (payload) runHistory(payload, 1);
    }
  } else if (location.pathname === "/") {
    // ホーム右下のミニプレイヤー (続きを見る) に対象動画が出ている場合の近道
    payload = parsePayload(HASH_KEY);
    if (payload) {
      runHome(payload);
    } else {
      // 起動URLではペイロードを渡せないため、localhost 経由の受け取りを試みる
      fetchLocalPayload();
    }
  }
})();
