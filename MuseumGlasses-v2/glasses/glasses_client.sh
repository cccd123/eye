#!/bin/sh
# ════════════════════════════════════════════════════════════════════
#  glasses_client.sh - 跑在眼镜主板（全志 V821 / Tina Linux）上的客户端
#
#  交互：
#    短按按键  → 拍照 → 服务器识别+讲解 → 眼镜播放
#    长按按键  → 按住期间录音，松开 → 针对当前展品追问 → 播放回答
#    APP 点「让眼镜拍一张」→ 下次心跳时自动执行一次短按流程
#
#  只依赖 busybox + curl + (arecord/aplay 或 tinycap/tinyplay) + 一个拍照命令。
#  ⚠️ 下面「需要按 probe_result.txt 修改」的几项，先跑 probe_glasses.bat 拿到结果再填。
#
#  上传到板子并运行：
#    adb push glasses_client.sh /root/
#    adb shell "chmod +x /root/glasses_client.sh && /root/glasses_client.sh"
#  （Windows 上编辑后保存必须是 LF 换行，否则会报 ^M 错误）
# ════════════════════════════════════════════════════════════════════

# ── 基本配置 ──────────────────────────────────────────
SERVER="${SERVER:-http://192.168.1.10:8000}"   # 跑 uvicorn 的电脑 IP（同一个 Wi-Fi / 手机热点）
DEVICE_ID="${DEVICE_ID:-glasses-01}"           # 和 APP 设置里一致
LONG_PRESS_CS=80                               # 长按阈值，单位 1/100 秒（0.8s）
HEARTBEAT_SEC=5

# ── 需要按 probe_result.txt 修改 ──────────────────────
KEY_DEV="${KEY_DEV:-/dev/input/event0}"        # 按键对应的 event 设备（看 ===INPUT=== 段）
KEY_CODE="${KEY_CODE:-}"                       # 只响应某个键码，空=任意键。电源键通常是 116
EVENT_SIZE="${EVENT_SIZE:-16}"                 # 32位内核=16，64位内核=24（V821 是 32 位 RISC-V → 16）
AUDIO_TOOL="${AUDIO_TOOL:-auto}"               # auto / alsa(arecord+aplay) / tiny(tinycap+tinyplay)
MIC_CARD="${MIC_CARD:-0}"
SPK_CARD="${SPK_CARD:-0}"
LED="${LED:-}"                                 # 例如 /sys/class/leds/xxx/brightness，空=不用指示灯
# 拍照命令：必须在 $IMG_DIR 下生成一张 jpg/bmp/png。空 = 自动尝试 camerademo
CAPTURE_CMD="${CAPTURE_CMD:-}"

TMP=/tmp/mg
IMG_DIR=$TMP/img
mkdir -p $IMG_DIR

log() { echo "[$(date +%H:%M:%S)] $*"; }
led() { [ -n "$LED" ] && echo "$1" > "$LED" 2>/dev/null; }
now_cs() { awk '{printf "%d", $1*100}' /proc/uptime; }

if [ "$AUDIO_TOOL" = auto ]; then
  if command -v arecord >/dev/null 2>&1; then AUDIO_TOOL=alsa; else AUDIO_TOOL=tiny; fi
fi
command -v curl >/dev/null 2>&1 || { echo "缺少 curl：需要在 Tina SDK 里 make menuconfig 勾选 curl 后重新烧录"; exit 1; }

# ── 音频 ──────────────────────────────────────────────
rec_start() {   # 后台录音，直到 rec_stop
  rm -f $TMP/q.wav
  if [ "$AUDIO_TOOL" = alsa ]; then
    arecord -D hw:$MIC_CARD,0 -f S16_LE -r 16000 -c 1 $TMP/q.wav >/dev/null 2>&1 &
  else
    tinycap $TMP/q.wav -D $MIC_CARD -d 0 -c 1 -r 16000 -b 16 >/dev/null 2>&1 &
  fi
  REC_PID=$!
}
rec_stop() {   # 先发 INT（arecord/tinycap 收到会把 wav 头写完整），1 秒没退出再 TERM，不会卡死
  [ -n "$REC_PID" ] || return 0
  kill -INT $REC_PID 2>/dev/null
  for i in 1 2 3 4 5; do kill -0 $REC_PID 2>/dev/null || break; sleep 0.2; done
  kill -TERM $REC_PID 2>/dev/null
  wait $REC_PID 2>/dev/null; REC_PID=
}
play() {
  [ -s "$1" ] || return
  if [ "$AUDIO_TOOL" = alsa ]; then aplay -D hw:$SPK_CARD,0 "$1" >/dev/null 2>&1
  else tinyplay "$1" -D $SPK_CARD >/dev/null 2>&1; fi
}

# ── 拍照 ──────────────────────────────────────────────
capture() {
  rm -f $IMG_DIR/*
  if [ -n "$CAPTURE_CMD" ]; then
    sh -c "$CAPTURE_CMD"
  elif command -v camerademo >/dev/null 2>&1; then
    # Tina 自带的测试程序：格式 宽 高 帧率 存储格式 目录 张数
    camerademo NV21 1280 720 30 bmp $IMG_DIR 1 >/dev/null 2>&1
  else
    log "没有可用的拍照命令，请设置 CAPTURE_CMD"; return 1
  fi
  PHOTO=$(ls -t $IMG_DIR/*.jpg $IMG_DIR/*.bmp $IMG_DIR/*.png 2>/dev/null | head -1)
  [ -n "$PHOTO" ]
}

# ── 和服务器通信 ──────────────────────────────────────
ask() {   # 参数：额外的 curl -F 选项
  led 1
  code=$(curl -s -m 90 -o $TMP/reply.wav -w "%{http_code}" \
         -F device_id=$DEVICE_ID -F source=glasses -F audio_format=wav -F response=audio \
         "$@" "$SERVER/api/ask")
  led 0
  if [ "$code" = 200 ]; then play $TMP/reply.wav; else log "服务器返回 $code"; fi
}

do_photo() {
  log "拍照..."
  if capture; then log "上传 $PHOTO"; ask -F "image=@$PHOTO"; fi
}
do_question() {
  log "上传语音提问"
  ask -F "audio=@$TMP/q.wav"
}

# ── 心跳：上报在线/电量，接收 APP 指令 ─────────────────
heartbeat_loop() {
  while true; do
    bat=$(cat /sys/class/power_supply/battery/capacity 2>/dev/null)
    [ -n "$bat" ] && b="\"battery\":$bat," || b=""
    resp=$(curl -s -m 5 -H 'content-type: application/json' \
           -d "{${b}\"device_id\":\"$DEVICE_ID\",\"fw\":\"sh-1.0\"}" "$SERVER/api/device/heartbeat")
    cmd=$(echo "$resp" | sed -n 's/.*"cmd":"\([a-z]*\)".*/\1/p')
    [ "$cmd" = capture ] && touch $TMP/trigger
    sleep $HEARTBEAT_SEC
  done
}
trigger_loop() {   # APP 远程拍照
  while true; do
    if [ -f $TMP/trigger ]; then rm -f $TMP/trigger; do_photo; fi
    sleep 1
  done
}

# ── 按键：解析 /dev/input/eventX 的 input_event 结构 ────
# 32位：timeval(8字节) + type(2) + code(2) + value(4) = 16 字节 → 按 u16 拆成 8 个数，type/code/value 在第 5/6/7 个
# 用 dd 每次只读一个事件（od 直接读设备会攒满缓冲区才输出，按键时长就测不准了）
key_loop() {
  if [ "$EVENT_SIZE" = 24 ]; then IT=9; IC=10; IV=11; else IT=5; IC=6; IV=7; fi
  exec 3< "$KEY_DEV" || { log "打不开按键设备 $KEY_DEV"; exit 1; }
  while true; do
    line=$(dd bs=$EVENT_SIZE count=1 <&3 2>/dev/null | od -An -v -tu2)
    [ -z "$line" ] && { sleep 1; continue; }
    set -- $line
    eval "type=\${$IT} code=\${$IC} val=\${$IV}"
    [ "$type" = 1 ] || continue                       # EV_KEY
    [ -n "$KEY_CODE" ] && [ "$code" != "$KEY_CODE" ] && continue
    if [ "$val" = 1 ]; then                            # 按下：先开始录音（如果是短按再丢掉）
      DOWN=$(now_cs); rec_start; led 1
    elif [ "$val" = 0 ] && [ -n "$DOWN" ]; then        # 松开
      held=$(( $(now_cs) - DOWN )); DOWN=; rec_stop; led 0
      if [ $held -ge $LONG_PRESS_CS ]; then do_question; else do_photo; fi
    fi
  done
}

log "眼镜客户端启动  server=$SERVER  device=$DEVICE_ID  key=$KEY_DEV  audio=$AUDIO_TOOL"
curl -s -m 5 "$SERVER/api/health" >/dev/null || log "⚠️ 连不上服务器，检查 Wi-Fi 和 SERVER 地址"
heartbeat_loop &
trigger_loop &
trap 'kill 0' INT TERM
key_loop
