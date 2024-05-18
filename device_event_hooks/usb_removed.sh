#!/bin/bash -e

SCRIPT_PATH=$(readlink -f "$0")
SCRIPT_DIR=$(dirname "$SCRIPT_PATH")

DEVICE_INFO_LIST="$1"

$SCRIPT_DIR/../tools/change_home_monitor_channel.sh "$DEVICE_INFO_LIST" 2
