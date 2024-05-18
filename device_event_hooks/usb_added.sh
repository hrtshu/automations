#!/bin/bash -e

USB_HUB_ID="2109:2817"

# load SWITCHBOT_TOKEN, SWITCHBOT_SECRET
source ~/.profile_env

DEVICE_INFO_LIST="$1"

echo "$DEVICE_INFO_LIST" | grep -q "$USB_HUB_ID" || { echo "usb hub '$USB_HUB_ID' is not detected."; exit 0; }

echo "usb hub '$USB_HUB_ID' is detected."
~/automations/tools/set_tv_channel.py "モニター" 4
