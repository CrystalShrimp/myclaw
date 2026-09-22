#!/bin/bash
# 重启 MyClaw：停旧 → 起新 → 等健康检查（MyClaw.command 双击即调它）。
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/mac_common.sh"

"$DIR/stop_mac.sh"
sleep 1
mac_start_service
mac_wait_healthy 60
