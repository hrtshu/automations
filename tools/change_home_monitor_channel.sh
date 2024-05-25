#!/bin/bash -e

LSUSB="/opt/homebrew/bin/lsusb"

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 DEVICE_INFO_LIST ADD_OR_REMOVE" 1>&2
    exit 1
fi

DEVICE_INFO_LIST="$1"
ADD_OR_REMOVE="$2"

USB_HUB_ID="2109:2817"

# load SWITCHBOT_TOKEN, SWITCHBOT_SECRET
source ~/.profile_env

echo "$DEVICE_INFO_LIST" | grep -q "$USB_HUB_ID" || { echo "$ADD_OR_REMOVE: home usb hub is not detected"; exit 0; }

# removeの場合は数秒間抜かれた状態が継続している場合のみ処理を続行(接触不良で反応するのを防ぐため)
if [ "$ADD_OR_REMOVE" = "remove" ]; then
    sleep 5
    "$LSUSB" | grep -q "$USB_HUB_ID" && { echo "$ADD_OR_REMOVE: home usb hub is detected, but still added"; exit 0; }
fi

echo "$ADD_OR_REMOVE: home usb hub is detected"


echo "usb hub '$USB_HUB_ID' is detected."

[ "$ADD_OR_REMOVE" = "add" ] && CHANNEL=4
[ "$ADD_OR_REMOVE" = "remove" ] && CHANNEL=2
echo "changing channel to $CHANNEL"
~/automations/tools/set_tv_channel.py "01-202309241713-48053111" "$CHANNEL" # モニター
