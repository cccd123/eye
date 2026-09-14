# -*- coding: utf-8 -*-
"""
main.py - 博物馆智能讲解眼镜 后端API服务
FastAPI三模块接口：
  POST /api/recognize  - 图片 → 文物名称
  POST /api/explain    - 文物名称+问题+人设 → 讲解文字
  POST /api/speak      - 文字+人设 → 音频文件(mp3)
  GET  /api/health     - 健康检查
"""
import os, re, io, asyncio, uuid, torch
from pathlib import Path
from collections import Counter

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from PIL import Image

# ── 路径配置（和你本地路径一致）─────────────────────────────
BASE = Path(r"D:\MuseumGlasses")
CLIP_PATH   = BASE / "models" / "clip-vit-base"
BGE_PATH    = BASE / "models" / "bge-m3"
LLM_PATH    = BASE / "models" / "qwen3-1.7b" / "Qwen3-1.7B-Q4_K_M.gguf"
IMG_DB_PATH = BASE / "vector_db" / "images"
TXT_DB_PATH = BASE / "vector_db" / "sculptures"
AUDIO_DIR   = BASE / "server" / "audio_cache"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

CONFIDENCE_THRESHOLD = 0.75
SUFFIXES = r'正面|背面|侧面|底面|内部|外部|另一[侧側]|証明|证明|后面'

# ── 人设配置 ─────────────────────────────────────────────────
PERSONAS = {
    "expert": {
        "zh": "专家",
        "prompt": "你是博物馆专业研究员，用准确术语讲解文物工艺、年代和历史背景，100字以内，严格基于参考资料，不添加资料外内容，不用Markdown格式。/no_think",
        "voice": "zh-CN-YunxiNeural"
    },
    "child": {
        "zh": "儿童",
        "prompt": "你是博物馆讲解员，用简单语言给8岁小朋友介绍文物。只说参考资料里有的内容，不超过60字，结尾一句简单问题，不用Markdown格式。/no_think",
        "voice": "zh-CN-XiaoxiaoNeural"
    },
    "worker": {
        "zh": "社畜",
        "prompt": "你是高效讲解员，3句话内讲完核心，可用职场类比，严格基于参考资料，不用Markdown格式。/no_think",
        "voice": "zh-CN-YunjianNeural"
    },
    "student": {
        "zh": "大学生",
        "prompt": "你是学长风格讲解员，讲重点+趣闻，语言轻松，150字以内，严格基于参考资料，不用Markdown格式。/no_think",
        "voice": "zh-CN-YunxiNeural"
    },
    "teacher": {
        "zh": "老师",
        "prompt": "你是循序渐进的讲解员：先讲历史背景，再讲文物本体，最后讲文化意义，适合亲子讲解，严格基于参考资料，不用Markdown格式。/no_think",
        "voice": "zh-CN-XiaoyiNeural"
    },
    "archaeologist": {
        "zh": "考古专家",
        "prompt": "你是考古研究员，直接给出年代、材质工艺、学界观点，不解释基础概念，假设对方有专业基础，严格基于参考资料，不用Markdown格式。/no_think",
        "voice": "zh-CN-YunxiNeural"
    },
}

# ── 全局模型（启动时加载一次）────────────────────────────────
print("正在加载模型，请稍候...")

from transformers import CLIPProcessor, CLIPModel
clip_model = CLIPModel.from_pretrained(str(CLIP_PATH))
clip_processor = CLIPProcessor.from_pretrained(str(CLIP_PATH))
clip_model.eval()
print("✅ CLIP加载完成")

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
emb = HuggingFaceEmbeddings(
    model_name=str(BGE_PATH),
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True}
)
txt_db = Chroma(
    persist_directory=str(TXT_DB_PATH),
    embedding_function=emb,
    collection_name="npm_sculptures"
)
print("✅ 文字知识库加载完成")

from llama_cpp import Llama
llm = Llama(model_path=str(LLM_PATH), n_ctx=4096, n_gpu_layers=28, verbose=False)
print("✅ Qwen3加载完成")

import chromadb
img_client = chromadb.PersistentClient(path=str(IMG_DB_PATH))
img_col = img_client.get_collection("artifact_images")
print(f"✅ 图像库加载完成 ({img_col.count()}条)")

import edge_tts
print("✅ 所有模型加载完成，服务启动")

# ── FastAPI应用 ───────────────────────────────────────────────
app = FastAPI(
    title="博物馆智能讲解眼镜 API",
    description="Museum Guide Glasses Backend",
    version="1.0.0"
)

# 允许跨域（Android APP需要）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 请求/响应数据结构 ─────────────────────────────────────────
class ExplainRequest(BaseModel):
    artifact_name: str          # 文物名称
    persona: str = "expert"     # 人设key
    question: str = ""          # 用户追问（可空）
    language: str = "zh"        # zh/en

class SpeakRequest(BaseModel):
    text: str
    persona: str = "expert"

# ── 工具函数 ──────────────────────────────────────────────────
def get_image_vector(pil_image):
    inputs = clip_processor(images=pil_image, return_tensors="pt")
    with torch.no_grad():
        outputs = clip_model.vision_model(**inputs)
        feat = outputs.pooler_output
        feat = feat / feat.norm(dim=-1, keepdim=True)
    return feat[0].detach().numpy().tolist()

def recognize_from_vector(vec):
    results = img_col.query(
        query_embeddings=[vec],
        n_results=min(6, img_col.count()),
        include=["metadatas", "distances"]
    )
    votes = Counter()
    scores = {}
    for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
        name = meta["artifact_name"]
        sim = 1 - dist
        votes[name] += 1
        scores[name] = max(scores.get(name, 0), sim)
    ranked = sorted(votes.keys(), key=lambda x: (votes[x], scores[x]), reverse=True)
    if not ranked:
        return None, 0.0
    top1 = ranked[0]
    return top1, scores[top1]

def clean_output(text):
    """清理LLM输出：去Markdown、去括号舞台指令、去think标签"""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    text = re.sub(r'#{1,6}\s+', '', text)
    text = re.sub(r'[`~]', '', text)
    text = re.sub(r'[（(][^）)]{1,20}[）)]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ── 接口1：图像识别 ───────────────────────────────────────────
@app.post("/api/recognize")
async def recognize(file: UploadFile = File(...)):
    """
    输入：图片文件（jpg/png）
    输出：{"artifact_name": "竹根雕牧童臥牛", "confidence": 0.95, "success": true}
         {"success": false, "message": "置信度不足，请移到展品正面重新拍摄", "confidence": 0.52}
    """
    try:
        contents = await file.read()
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception:
        raise HTTPException(400, "图片格式错误")

    vec = get_image_vector(image)
    artifact_name, confidence = recognize_from_vector(vec)

    if not artifact_name or confidence < CONFIDENCE_THRESHOLD:
        return JSONResponse({
            "success": False,
            "message": "识别置信度不足，请移到展品正面重新拍摄",
            "confidence": round(float(confidence), 3)
        })

    return {
        "success": True,
        "artifact_name": artifact_name,
        "confidence": round(float(confidence), 3)
    }

# ── 接口2：RAG讲解生成 ────────────────────────────────────────
@app.post("/api/explain")
async def explain(req: ExplainRequest):
    """
    输入：文物名称 + 人设 + 可选问题
    输出：{"artifact_name": "...", "explanation": "...", "persona": "expert"}
    """
    persona_cfg = PERSONAS.get(req.persona, PERSONAS["expert"])

    # 检索知识库
    query = f"{req.artifact_name} {req.question}".strip()
    docs = txt_db.similarity_search(query, k=2)
    context = "\n\n".join([d.page_content for d in docs])

    if not context:
        raise HTTPException(404, f"知识库中未找到文物：{req.artifact_name}")

    # 构造prompt
    if req.question:
        user_msg = f"参考资料：\n{context}\n\n文物名称：{req.artifact_name}\n用户问题：{req.question}"
    else:
        user_msg = f"参考资料：\n{context}\n\n请介绍这件文物：{req.artifact_name}"

    resp = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": persona_cfg["prompt"]},
            {"role": "user", "content": user_msg}
        ],
        max_tokens=200,
        temperature=0.5,
        stream=False
    )
    raw_text = resp["choices"][0]["message"]["content"]
    explanation = clean_output(raw_text)

    return {
        "artifact_name": req.artifact_name,
        "explanation": explanation,
        "persona": req.persona,
        "persona_zh": persona_cfg["zh"]
    }

# ── 接口3：语音合成 ───────────────────────────────────────────
@app.post("/api/speak")
async def speak(req: SpeakRequest):
    """
    输入：文字 + 人设
    输出：返回mp3音频文件
    """
    persona_cfg = PERSONAS.get(req.persona, PERSONAS["expert"])
    voice = persona_cfg["voice"]

    # 生成唯一文件名，避免并发冲突
    filename = f"{uuid.uuid4().hex}.mp3"
    audio_path = AUDIO_DIR / filename

    tts = edge_tts.Communicate(text=req.text, voice=voice)
    await tts.save(str(audio_path))

    return FileResponse(
        path=str(audio_path),
        media_type="audio/mpeg",
        filename="response.mp3"
    )

# ── 健康检查 ──────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "models": {
            "clip": True,
            "bge_m3": True,
            "qwen3": True,
            "image_db_count": img_col.count(),
            "text_db_count": txt_db._collection.count()
        },
        "personas": list(PERSONAS.keys())
    }

# ── 获取人设列表（APP下拉菜单用）─────────────────────────────
@app.get("/api/personas")
def get_personas():
    return [
        {"key": k, "name_zh": v["zh"], "name_en": k}
        for k, v in PERSONAS.items()
    ]