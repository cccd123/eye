# -*- coding: utf-8 -*-
"""
full_pipeline.py
完整闭环：图像识别 → RAG知识库检索 → 多人设讲解生成 → 语音播放
这是系统的核心演示脚本

流程：
  输入图片
    → CLIP提取特征向量
    → ChromaDB图像库找最相似文物
    → 确认文物名称
    → bge-m3把问题向量化
    → ChromaDB文字库检索相关知识
    → Qwen3根据人设生成讲解
    → Edge-TTS合成语音
    → 播放mp3
"""
import os, re, sys, asyncio, subprocess, torch
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from llama_cpp import Llama
import chromadb
from collections import Counter
import edge_tts

# ── 路径配置 ──────────────────────────────────────────────────
CLIP_PATH   = r"D:\MuseumGlasses\models\clip-vit-base"
BGE_PATH    = r"D:\MuseumGlasses\models\bge-m3"
LLM_PATH    = r"D:\MuseumGlasses\models\qwen3-1.7b\Qwen3-1.7B-Q4_K_M.gguf"
IMG_DB_PATH = r"D:\MuseumGlasses\vector_db\images"
TXT_DB_PATH = r"D:\MuseumGlasses\vector_db\sculptures"
OUTPUT_DIR  = r"D:\MuseumGlasses\test_output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

CONFIDENCE_THRESHOLD = 0.75  # 图像识别置信度阈值
SUFFIXES = r'正面|背面|侧面|底面|内部|外部|另一[侧側]|証明|证明|后面'

# ── 人设配置 ──────────────────────────────────────────────────
PERSONAS = {
    "专家": {
        "prompt": "你是博物馆专业研究员，用准确术语讲解文物工艺、年代和历史背景，100字以内，严格基于参考资料。/no_think",
        "voice": "zh-CN-YunxiNeural"
    },
    "儿童": {
        "prompt": "你是博物馆讲解员，用简单语言给8岁小朋友介绍文物。规则：1.只说参考资料里有的内容，不添加任何资料里没有的描述；2.不超过60字；3.结尾用一句简单问题引发好奇；4.不要括号里的舞台指令；5.不要用Markdown格式。/no_think",
        "voice": "zh-CN-XiaoxiaoNeural"
    },
    "社畜": {
        "prompt": "你是高效讲解员，3句话内讲完核心，可用职场类比。严格基于参考资料。/no_think",
        "voice": "zh-CN-YunjianNeural"
    },
    "大学生": {
        "prompt": "你是学长风格讲解员，讲重点+趣闻，偶尔带梗，150字以内。严格基于参考资料。/no_think",
        "voice": "zh-CN-YunxiNeural"
    },
    "老师": {
        "prompt": "你是循序渐进的讲解员：先讲历史背景，再讲文物本体，最后讲文化意义，适合亲子讲解。严格基于参考资料。/no_think",
        "voice": "zh-CN-XiaoyiNeural"
    },
    "考古专家": {
        "prompt": "你是考古研究员，直接给出年代、出土信息、材质工艺、学界观点，不解释基础概念，假设对方有专业基础。严格基于参考资料。/no_think",
        "voice": "zh-CN-YunxiNeural"
    },
}

print("="*60)
print("博物馆智能讲解眼镜 - 完整闭环系统")
print("="*60)

# ── 加载所有模型 ──────────────────────────────────────────────
print("\n[1/4] 加载CLIP图像识别模型...")
clip_model = CLIPModel.from_pretrained(CLIP_PATH)
clip_processor = CLIPProcessor.from_pretrained(CLIP_PATH)
clip_model.eval()

print("[2/4] 加载bge-m3向量检索模型...")
emb = HuggingFaceEmbeddings(
    model_name=BGE_PATH,
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True}
)

print("[3/4] 加载文字知识库...")
txt_db = Chroma(persist_directory=TXT_DB_PATH,
                embedding_function=emb,
                collection_name="npm_sculptures")

print("[4/4] 加载Qwen3语言模型...")
llm = Llama(model_path=LLM_PATH, n_ctx=4096, n_gpu_layers=28, verbose=False)

img_client = chromadb.PersistentClient(path=IMG_DB_PATH)
img_col = img_client.get_collection("artifact_images")

print(f"\n✅ 所有模型加载完成")
print(f"   图像库：{img_col.count()} 条向量")
print(f"   文字库：{txt_db._collection.count()} 条向量")

# ── 核心功能函数 ──────────────────────────────────────────────
def recognize_artifact(image_path):
    """
    图像识别：输入图片路径，返回文物名称和置信度
    低置信度时返回None并给出提示
    """
    image = Image.open(image_path).convert("RGB")
    inputs = clip_processor(images=image, return_tensors="pt")
    with torch.no_grad():
        outputs = clip_model.vision_model(**inputs)
        feat = outputs.pooler_output
        feat = feat / feat.norm(dim=-1, keepdim=True)
    vec = feat[0].detach().numpy().tolist()

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

    ranked = sorted(votes.keys(), key=lambda x:(votes[x], scores[x]), reverse=True)
    if not ranked:
        return None, 0
    top1, conf = ranked[0], scores[ranked[0]]
    return (top1, conf) if conf >= CONFIDENCE_THRESHOLD else (None, conf)

def retrieve_knowledge(artifact_name, user_question=""):
    """
    知识检索：根据文物名称+用户问题从文字库检索最相关的资料
    """
    query = f"{artifact_name} {user_question}".strip()
    docs = txt_db.similarity_search(query, k=2)
    return "\n\n".join([d.page_content for d in docs])

def generate_explanation(artifact_name, context, persona="专家", user_question=""):
    persona_cfg = PERSONAS.get(persona, PERSONAS["专家"])
    if user_question:
        user_msg = f"参考资料：\n{context}\n\n文物名称：{artifact_name}\n用户问题：{user_question}"
    else:
        user_msg = f"参考资料：\n{context}\n\n请介绍这件文物：{artifact_name}"

    resp = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": persona_cfg["prompt"]},
            {"role": "user", "content": user_msg}
        ],
        max_tokens=200, temperature=0.5, stream=False
    )
    text = resp["choices"][0]["message"]["content"]
    
    # 清理所有Markdown格式
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)  # 去掉**加粗**和*斜体*
    text = re.sub(r'#{1,6}\s+', '', text)                   # 去掉标题#
    text = re.sub(r'[`~]', '', text)                         # 去掉代码符号
    
    # 清理括号内的舞台指令（停顿，看小朋友等）
    text = re.sub(r'[（(][^）)]*[）)]', '', text)
    
    # 清理多余空白
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

async def text_to_speech(text, persona="专家", output_path="output.mp3"):
    """语音合成：文字转语音"""
    voice = PERSONAS.get(persona, PERSONAS["专家"])["voice"]
    tts = edge_tts.Communicate(text=text, voice=voice)
    await tts.save(output_path)

def play_audio(path):
    """播放音频（Windows）"""
    os.startfile(path)

# ── 完整流程 ──────────────────────────────────────────────────
def run_pipeline(image_path, persona="专家", user_question=""):
    """
    完整闭环：图片 → 识别 → 检索 → 生成 → 语音
    """
    print(f"\n{'='*60}")
    print(f"输入图片：{os.path.basename(image_path)}")
    print(f"人设模式：{persona}")
    if user_question:
        print(f"用户问题：{user_question}")
    print('='*60)

    # 第一步：图像识别
    print("\n[Step 1] 图像识别...")
    artifact_name, confidence = recognize_artifact(image_path)
    if not artifact_name:
        msg = f"识别置信度不足（{confidence:.2f}），请移到展品正面重新拍摄"
        print(f"⚠️  {msg}")
        return None, msg, None

    print(f"✅ 识别结果：{artifact_name}（置信度：{confidence:.3f}）")

    # 第二步：知识检索
    print("\n[Step 2] 检索知识库...")
    context = retrieve_knowledge(artifact_name, user_question)
    print(f"✅ 检索到相关资料（{len(context)}字）")

    # 第三步：生成讲解
    print("\n[Step 3] 生成讲解...")
    explanation = generate_explanation(artifact_name, context, persona, user_question)
    # 去掉<think>标签（Qwen3的思考链输出）
    explanation = re.sub(r'<think>.*?</think>', '', explanation, flags=re.DOTALL).strip()
    print(f"✅ 讲解文字：\n{explanation}")

    # 第四步：语音合成
    print("\n[Step 4] 语音合成...")
    audio_path = os.path.join(OUTPUT_DIR, f"output_{persona}.mp3")
    asyncio.run(text_to_speech(explanation, persona, audio_path))
    print(f"✅ 语音已生成：{audio_path}")

    # 第五步：播放
    print("\n[Step 5] 播放语音...")
    play_audio(audio_path)

    return artifact_name, explanation, audio_path

# ── 测试入口 ──────────────────────────────────────────────────
if __name__ == "__main__":
    IMAGE_DIR = r"D:\MuseumGlasses\knowledge_base\images"

    # 测试三件文物 × 三种人设
    test_cases = [
        ("竹根雕牧童臥牛正面.png", "儿童",   "这个小牛是什么做的？"),
        ("竹黃臥瓜式盒正面.png",  "社畜",   ""),
        ("黃楊木雕山水筆筒正面.png", "专家", "工艺有什么特点？"),
    ]

    for img_file, persona, question in test_cases:
        img_path = os.path.join(IMAGE_DIR, img_file)
        if not os.path.exists(img_path):
            print(f"⚠️ 图片不存在：{img_file}，跳过")
            continue

        run_pipeline(img_path, persona=persona, user_question=question)
        print("\n" + "─"*60)
        input("按回车继续下一个测试...")