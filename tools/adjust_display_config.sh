#!/bin/bash -e

JQ=/opt/homebrew/bin/jq
DISPLAYPLACER=/opt/homebrew/bin/displayplacer

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 MACBOOK_DISPLAY_PERSISTENT_ID" 1>&2
    exit 1
fi

# TODO: 自動で取得する
macbook_display_persistent_id="$1"

res="$(system_profiler -json SPDisplaysDataType)"

# ディスプレイが2枚以外の場合は何もしない
count=$(echo "$res" | $JQ '.SPDisplaysDataType[].spdisplays_ndrvs | length')
[ $count -eq 2 ] || exit 0

# 意図したディスプレイサイズ(2304x1296を最適に表示できるサイズ≒4K)でない場合は何もしない
external_display_pixels="$(echo "$res" | $JQ -r '.SPDisplaysDataType[].spdisplays_ndrvs[] | select(._name != "Color LCD") | ._spdisplays_pixels')"
[ "$external_display_pixels" = "3840 x 2160" -o "$external_display_pixels" = "4608 x 2592" ] || exit 0

# MacBook Built-in Displayでは無い方のPersistent IDを取得
external_display_persistent_id=$($DISPLAYPLACER list | grep "Persistent screen id:" | grep -v "$macbook_display_persistent_id" | awk '{print $4}')

# ディスプレイを2304x1296に設定してミラーする（既に設定済みの場合は何もしない）
config="id:$external_display_persistent_id+$macbook_display_persistent_id res:2304x1296 hz:60 color_depth:8 enabled:true scaling:on origin:(0,0) degree:0"
$DISPLAYPLACER list | grep -q "$config" && exit 0
$DISPLAYPLACER "$config"
