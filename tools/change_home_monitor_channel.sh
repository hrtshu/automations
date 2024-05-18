#!/bin/bash -e

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 DEVICE_INFO_LIST CHANNEL" 1>&2
    exit 1
fi

DEVICE_INFO_LIST="$1"
CHANNEL="$2"

USB_HUB_ID="2109:2817"

# load SWITCHBOT_TOKEN, SWITCHBOT_SECRET
source ~/.profile_env

echo "$DEVICE_INFO_LIST" | grep -q "$USB_HUB_ID" || { echo "usb hub '$USB_HUB_ID' is not detected."; exit 0; }

echo "usb hub '$USB_HUB_ID' is detected."
~/automations/tools/set_tv_channel.py "モニター" "$CHANNEL"
