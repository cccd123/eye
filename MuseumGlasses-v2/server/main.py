# -*- coding: utf-8 -*-
"""
main.py - 博物馆智能讲解眼镜 后端API服务

启动（在仓库根目录）：
    cd server
    uvicorn main:app --host 0.0.0.0 --port 8000
没有显卡/模型、只想调 APP：
    set MUSEUM_MOCK=1   (PowerShell: $env:MUSEUM_MOCK="1")
    uvicorn main:app --host 0.0.0.0 --port 8000

手机浏览器打开 http://<电脑IP>:8000  就是配套 APP。

接口一览（详见 docs/PROTOCOL.md）：
  POST /api/ask              ★ 眼镜/APP 一次调用：图片+语音/文字 → 识别+讲解+语音
  POST /api/asr              语音 → 文字
  POST /api/recognize        图片 → 文物名称        （旧接口，保留）
  POST /api/explain          文物名称+问题+人设 → 讲解（旧接口，保留）
  POST /api/speak            文字+人设 → 音频        （旧接口，保留）
  POST /api/device/heartbeat 眼镜心跳，返回当前人设和待执行指令
  POST /api/device/command   APP 给眼镜下指令（如远程拍照）
  GET/PUT /api/settings      读取/修改某台设备的人设
  GET  /api/history          最近问答记录
  WS   /ws                   APP 实时接收眼镜的问答结果
  GET  /api/health, /api/personas
"""
import io, time, uuid
from collections import deque
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from PIL import Image

import config
from personas import PERSONAS
import qa
from engine import create_engine, prune_dir

config.describe()
engine = create_engine()

app = FastAPI(title="博物馆智能讲解眼镜 API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/audio", StaticFiles(directory=str(config.AUDIO_DIR)), name="audio")
app.mount("/captures", StaticFiles(directory=str(config.CAPTURE_DIR)), name="captures")
if config.APP_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(config.APP_DIR), html=True), name="app")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app/")


# ═════════════════════════ 设备状态 & 实时推送 ═════════════════════════
DEFAULT_DEVICE = "glasses-01"
devices: dict = {}                       # device_id -> 状态
history: deque = deque(maxlen=config.HISTORY_KEEP)
ws_clients: set = set()


def dev(device_id: str) -> dict:
    if device_id not in devices:
        devices[device_id] = {"device_id": device_id, "persona": "expert", "last_artifact": None,
                              "last_seen": None, "battery": None, "ip": None, "fw": None,
                              "commands": [], "dialog": deque(maxlen=2)}
    return devices[device_id]


def set_artifact(d: dict, name: str):
    """换了展品就清空追问上下文，免得把上一件的对话带进来"""
    if d["last_artifact"] != name:
        d["dialog"].clear()
    d["last_artifact"] = name


def public_dev(d: dict) -> dict:
    online = d["last_seen"] is not None and time.time() - d["last_seen"] < 30
    return {k: v for k, v in d.items() if k not in ("commands", "dialog")} | {"online": online}


async def broadcast(msg: dict):
    dead = []
    for ws in list(ws_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    await ws.send_json({"type": "hello", "history": list(history),
                        "devices": [public_dev(d) for d in devices.values()]})
    try:
        while True:
            await ws.receive_text()      # APP 端只收不发，这里只是保持连接
    except WebSocketDisconnect:
        ws_clients.discard(ws)


# ═════════════════════════ ★ 一站式问答 ═════════════════════════
async def _read_image(upload: UploadFile):
    data = await upload.read()
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        raise HTTPException(400, "图片格式错误")
    name = f"{uuid.uuid4().hex}.jpg"
    img.save(config.CAPTURE_DIR / name, quality=85)
    return img, name


@app.post("/api/ask")
async def ask(request: Request,
              image: Optional[UploadFile] = File(None),
              audio: Optional[UploadFile] = File(None),
              question: str = Form(""),
              artifact_name: str = Form(""),
              persona: str = Form(""),
              device_id: str = Form(DEFAULT_DEVICE),
              source: str = Form("glasses"),
              audio_format: str = Form("mp3"),
              response: str = Form("json")):
    """
    眼镜按一下键调用一次即可：
      - 带 image：先识别文物，再讲解（可同时带 audio/question 当作提问）
      - 不带 image：针对该设备上一次识别的文物继续追问
      - audio：录音文件（wav/mp3/webm/m4a 都行），会先转文字
    response=json  → 返回 JSON（含 audio_url）
    response=audio → 直接返回音频文件，文字放在响应头里（眼镜端 shell 脚本不用解析 JSON）
    """
    t0 = time.time()
    d = dev(device_id)
    persona = persona if persona in PERSONAS else d["persona"]
    timing, status = {}, "ok"
    confidence, capture_url, guess = None, None, None
    artifact = artifact_name or None
    if artifact:                                   # APP 上手动选了 / 点了「就是它」
        set_artifact(d, artifact)

    if image is not None and image.filename:
        img, cap = await _read_image(image)
        capture_url = f"/captures/{cap}"
        name, confidence = await run_in_threadpool(engine.recognize, img)
        timing["recognize"] = round(time.time() - t0, 2)
        if name and confidence >= config.CONFIDENCE_THRESHOLD:
            artifact = name
            set_artifact(d, name)
        elif name and confidence >= config.GUESS_THRESHOLD:
            status, guess = "uncertain", name
        else:
            status = "not_recognized"
    elif not artifact:
        artifact = d["last_artifact"]

    if audio is not None and audio.filename:
        t = time.time()
        raw = await audio.read()
        try:
            question = await run_in_threadpool(engine.transcribe, raw)
        except Exception as e:
            raise HTTPException(503, str(e))
        timing["asr"] = round(time.time() - t, 2)
        if not question:
            status = "no_speech"

    question = (question or "").strip()
    if status == "no_speech":
        answer = "没听清你的问题，请靠近一点再说一次。"
    elif status == "uncertain":
        answer = f"我不太确定，这件看起来像{qa.to_simplified(guess)}。请正对展品再拍一次，或者在手机上确认。"
    elif status == "not_recognized":
        answer = "没看清这件展品，请正对展品，靠近一点再拍一次。"
    elif not artifact and not question:
        status, answer = "no_artifact", "请先对准一件展品拍照，我再为你讲解。"
    else:
        t = time.time()
        answer = await run_in_threadpool(engine.explain, artifact, question, persona, list(d["dialog"]))
        timing["llm"] = round(time.time() - t, 2)
        d["dialog"].append((question or "介绍一下这件展品", answer))

    t = time.time()
    fmt = "wav" if audio_format == "wav" else "mp3"
    audio_path = await engine.synthesize(answer, persona, fmt)
    timing["tts"] = round(time.time() - t, 2)
    timing["total"] = round(time.time() - t0, 2)

    result = {
        "id": uuid.uuid4().hex[:8], "time": time.strftime("%H:%M:%S"),
        "status": status, "source": source, "device_id": device_id,
        "artifact_name": artifact, "artifact_display": qa.to_simplified(artifact) if artifact else None,
        "guess": guess, "guess_display": qa.to_simplified(guess) if guess else None,
        "confidence": None if confidence is None else round(confidence, 3),
        "question": question, "answer": answer, "persona": persona,
        "persona_zh": PERSONAS[persona]["zh"],
        "audio_url": f"/audio/{audio_path.name}", "image_url": capture_url, "timing": timing,
    }
    history.appendleft(result)
    await broadcast({"type": "ask_result", "item": result})
    prune_dir(config.AUDIO_DIR, config.CACHE_KEEP)
    prune_dir(config.CAPTURE_DIR, config.CACHE_KEEP)

    if response == "audio":
        media = "audio/wav" if audio_path.suffix == ".wav" else "audio/mpeg"
        return FileResponse(str(audio_path), media_type=media, headers={
            "X-Status": status,
            "X-Artifact": quote(artifact or ""),
            "X-Answer": quote(answer[:300]),
        })
    return result


@app.post("/api/asr")
async def asr(audio: UploadFile = File(...)):
    raw = await audio.read()
    try:
        text = await run_in_threadpool(engine.transcribe, raw)
    except Exception as e:
        raise HTTPException(503, str(e))
    return {"text": text}


# ═════════════════════════ 设备管理 ═════════════════════════
class Heartbeat(BaseModel):
    device_id: str = DEFAULT_DEVICE
    battery: Optional[int] = None      # 电量百分比，没有就不传
    fw: Optional[str] = None


@app.post("/api/device/heartbeat")
async def heartbeat(hb: Heartbeat, request: Request):
    """眼镜每 5~10 秒调用一次。返回值里的 commands 是 APP 下发的指令，眼镜执行完就丢掉。"""
    d = dev(hb.device_id)
    was_online = public_dev(d)["online"]
    d.update(last_seen=time.time(), battery=hb.battery, fw=hb.fw,
             ip=request.client.host if request.client else None)
    cmds, d["commands"] = d["commands"], []
    if not was_online:
        await broadcast({"type": "device", "device": public_dev(d)})
    return {"persona": d["persona"], "commands": cmds,
            "cmd": cmds[0] if cmds else ""}   # cmd：给 shell 脚本用的单个指令，免解析数组


class Command(BaseModel):
    device_id: str = DEFAULT_DEVICE
    command: str                       # capture / ask / reboot ...


@app.post("/api/device/command")
async def device_command(c: Command):
    dev(c.device_id)["commands"].append(c.command)
    return {"queued": c.command}


@app.get("/api/devices")
def list_devices():
    return [public_dev(d) for d in devices.values()]


class Settings(BaseModel):
    device_id: str = DEFAULT_DEVICE
    persona: str


@app.get("/api/settings")
def get_settings(device_id: str = DEFAULT_DEVICE):
    d = dev(device_id)
    return {"device_id": device_id, "persona": d["persona"], "last_artifact": d["last_artifact"]}


@app.put("/api/settings")
async def put_settings(s: Settings):
    if s.persona not in PERSONAS:
        raise HTTPException(400, f"未知人设：{s.persona}")
    d = dev(s.device_id)
    d["persona"] = s.persona
    await broadcast({"type": "device", "device": public_dev(d)})
    return {"ok": True, "persona": s.persona}


@app.get("/api/artifacts")
def list_artifacts():
    """知识库里的全部展品（APP「手动选择」用）。has_image=True 的才能被拍照识别。"""
    return [a | {"display": qa.to_simplified(a["name"])} for a in engine.artifact_names()]


@app.get("/api/history")
def get_history():
    return list(history)


# ═════════════════════════ 旧接口（保持兼容） ═════════════════════════
class ExplainRequest(BaseModel):
    artifact_name: str
    persona: str = "expert"
    question: str = ""
    language: str = "zh"


class SpeakRequest(BaseModel):
    text: str
    persona: str = "expert"


@app.post("/api/recognize")
async def recognize(file: UploadFile = File(...)):
    img, _ = await _read_image(file)
    name, conf = await run_in_threadpool(engine.recognize, img)
    if not name or conf < config.CONFIDENCE_THRESHOLD:
        return {"success": False, "message": "识别置信度不足，请移到展品正面重新拍摄",
                "confidence": round(float(conf), 3)}
    return {"success": True, "artifact_name": name, "confidence": round(float(conf), 3)}


@app.post("/api/explain")
async def explain(req: ExplainRequest):
    p = req.persona if req.persona in PERSONAS else "expert"
    text = await run_in_threadpool(engine.explain, req.artifact_name, req.question, p)
    return {"artifact_name": req.artifact_name, "explanation": text,
            "persona": p, "persona_zh": PERSONAS[p]["zh"]}


@app.post("/api/speak")
async def speak(req: SpeakRequest, format: str = "mp3"):
    path = await engine.synthesize(req.text, req.persona, "wav" if format == "wav" else "mp3")
    media = "audio/wav" if path.suffix == ".wav" else "audio/mpeg"
    return FileResponse(str(path), media_type=media, filename="response" + path.suffix)


@app.get("/api/health")
def health():
    return {"status": "ok", "mock": config.MOCK, "models": engine.stats(),
            "personas": list(PERSONAS.keys()), "devices": len(devices)}


@app.get("/api/personas")
def get_personas():
    return [{"key": k, "name_zh": v["zh"], "name_en": k} for k, v in PERSONAS.items()]
