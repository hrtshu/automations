// YouTube Resume Handoff - service worker
//
// content script からの依頼で localhost のペイロードサーバーへ取得に行く。
// ページ文脈 (content script) からの 127.0.0.1 への fetch は、Chrome の
// Local Network Access 制限でプリフライトすら送られずに弾かれることが
// あるため、取得は拡張機能の文脈 (host_permissions の範囲) で行う。
"use strict";

var PAYLOAD_URL = "http://127.0.0.1:18765/payload";
var RETRY_MS = 1000;
// 取得リトライの上限。本体がサーバーを立てるのは引き継ぎ時だけで、
// 通常のホーム表示ではサーバーが存在しないため、その場合はこの時間で
// 諦めて null を返し、ページ側は何もしないまま終わる (無害)。
var WAIT_MS = 25000;

function tryFetch(deadline) {
  return fetch(PAYLOAD_URL, { cache: "no-store" })
    .then(function (r) {
      return r.ok ? r.json() : null;
    })
    .catch(function () {
      return null;
    })
    .then(function (p) {
      if (p && p.title && p.ts) {
        return p;
      }
      if (Date.now() > deadline) return null;
      return new Promise(function (resolve) {
        setTimeout(resolve, RETRY_MS);
      }).then(function () {
        return tryFetch(deadline);
      });
    });
}

chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  if (!msg || msg.type !== "ytrh-get-payload") return false;
  tryFetch(Date.now() + WAIT_MS).then(function (p) {
    sendResponse(p);
  });
  return true; // 非同期で応答する
});
