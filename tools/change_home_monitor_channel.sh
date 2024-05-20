#!/bin/bash -e

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 DEVICE_INFO_LIST ADD_OR_REMOVE" 1>&2
    exit 1
fi

DEVICE_INFO_LIST="$1"
ADD_OR_REMOVE="$2"

[ "$ADD_OR_REMOVE" = "add" ] && CHANNEL=4
[ "$ADD_OR_REMOVE" = "remove" ] && CHANNEL=2

USB_HUB_ID="2109:2817"

# load SWITCHBOT_TOKEN, SWITCHBOT_SECRET
source ~/.profile_env

echo "$DEVICE_INFO_LIST" | grep -q "$USB_HUB_ID" || { echo "usb hub '$USB_HUB_ID' is not detected."; exit 0; }

echo "usb hub '$USB_HUB_ID' is detected."
~/automations/tools/set_tv_channel.py "01-202309241713-48053111" "$CHANNEL" # モニター
