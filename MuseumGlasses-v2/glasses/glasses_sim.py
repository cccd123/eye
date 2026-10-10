# -*- coding: utf-8 -*-
"""
glasses_sim.py - 在电脑上「假装是眼镜」，走和真眼镜完全一样的协议。

板子还没烧好之前，用它把 服务器 ⇄ 眼镜 ⇄ APP 整条链路跑通：
  - 用电脑摄像头（或指定图片）代替眼镜摄像头
  - 用电脑麦克风代替眼镜麦克风（需 pip install sounddevice）
  - 每 5 秒发心跳 → APP 上显示「眼镜在线」；APP 点「让眼镜拍一张」这里会自动拍

用法：
  pip install requests opencv-python sounddevice
  python glasses_sim.py --server http://127.0.0.1:8000
  python glasses_sim.py --image ../knowledge_base/images/銅貫耳瓶.png   # 没有摄像头时

命令：
  回车        拍照并讲解（= 眼镜短按）
  r           录音 4 秒提问（= 眼镜长按）
  t 问题文字   文字追问
  q           退出
"""
import argparse, io, os, sys, time, wave, threading, tempfile, subprocess
from urllib.parse import unquote

import requests

ap = argparse.ArgumentParser()
ap.add_argument("--server", default="http://127.0.0.1:8000")
ap.add_argument("--device", default="glasses-01")
ap.add_argument("--image", help="不用摄像头，固定发送这张图片")
ap.add_argument("--camera", type=int, default=0)
ap.add_argument("--seconds", type=float, default=4)
args = ap.parse_args()
S = args.server.rstrip("/")


def play_wav(data: bytes):
    path = os.path.join(tempfile.gettempdir(), "mg_reply.wav")
    with open(path, "wb") as f:
        f.write(data)
    try:
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME)
        elif sys.platform == "darwin":
            subprocess.run(["afplay", path])
        else:
            subprocess.run(["aplay", "-q", path])
    except Exception as e:
        print("  (播放失败，音频已存到", path, ")", e)


def grab_photo() -> bytes:
    if args.image:
        with open(args.image, "rb") as f:
            return f.read()
    import cv2
    cap = cv2.VideoCapture(args.camera)
    for _ in range(5):          # 丢掉前几帧，等自动曝光稳定
        ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError("摄像头打不开，用 --image 指定图片")
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes()


def record(seconds) -> bytes:
    import sounddevice as sd
    print(f"  🎤 录音 {seconds:.0f} 秒，请说话…")
    pcm = sd.rec(int(seconds * 16000), samplerate=16000, channels=1, dtype="int16")
    sd.wait()
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    return bio.getvalue()


def ask(files=None, data=None):
    t = time.time()
    form = {"device_id": args.device, "source": "glasses", "audio_format": "wav", "response": "audio"}
    form.update(data or {})
    r = requests.post(S + "/api/ask", files=files, data=form, timeout=120)
    if r.status_code != 200:
        print("  ❌", r.status_code, r.text[:200]); return
    print(f"  [{r.headers.get('x-status')}] {unquote(r.headers.get('x-artifact', ''))}  ({time.time()-t:.1f}s)")
    print("  ", unquote(r.headers.get("x-answer", "")))
    play_wav(r.content)


lock = threading.Lock()


def do_photo():
    with lock:
        print("  📷 拍照…")
        ask(files={"image": ("photo.jpg", grab_photo(), "image/jpeg")})


def heartbeat():
    while True:
        try:
            r = requests.post(S + "/api/device/heartbeat",
                              json={"device_id": args.device, "battery": 88, "fw": "sim"}, timeout=5).json()
            if "capture" in r.get("commands", []):
                print("\n  ← APP 远程拍照指令"); do_photo()
        except Exception:
            pass
        time.sleep(5)


print(requests.get(S + "/api/health", timeout=5).json())
threading.Thread(target=heartbeat, daemon=True).start()
print(__doc__.split("命令：")[1])
while True:
    cmd = input("> ").strip()
    try:
        if cmd == "":
            do_photo()
        elif cmd == "r":
            with lock:
                ask(files={"audio": ("q.wav", record(args.seconds), "audio/wav")})
        elif cmd.startswith("t "):
            with lock:
                ask(data={"question": cmd[2:]})
        elif cmd == "q":
            break
    except Exception as e:
        print("  ❌", e)
