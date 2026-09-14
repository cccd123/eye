# -*- coding: utf-8 -*-
"""
真实场景测试：用非正面图查询（模拟眼镜从任意角度拍）
低于置信度阈值时提示用户移到正面
"""
import os, re, torch
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
import chromadb
from collections import Counter

CLIP_PATH = r"D:\MuseumGlasses\models\clip-vit-base"
IMAGE_DIR = r"D:\MuseumGlasses\knowledge_base\images"
DB_PATH   = r"D:\MuseumGlasses\vector_db\images"
CONFIDENCE_THRESHOLD = 0.75  # 低于此值提示用户调整角度
SUFFIXES = r'正面|背面|侧面|底面|内部|外部|另一[侧側]|証明|证明|后面'

def get_artifact_name(filename):
    name = os.path.splitext(filename)[0]
    return re.sub(SUFFIXES, '', name).strip()

print("加载CLIP模型...")
model = CLIPModel.from_pretrained(CLIP_PATH)
processor = CLIPProcessor.from_pretrained(CLIP_PATH)
model.eval()
client = chromadb.PersistentClient(path=DB_PATH)
collection = client.get_collection("artifact_images")

def get_vector(image_path):
    image = Image.open(image_path).convert("RGB")
    inputs = processor(images=image, return_tensors="pt")
    with torch.no_grad():
        outputs = model.vision_model(**inputs)
        feat = outputs.pooler_output
        feat = feat / feat.norm(dim=-1, keepdim=True)
    return feat[0].detach().numpy().tolist()

def recognize(image_path):
    vec = get_vector(image_path)
    results = collection.query(
        query_embeddings=[vec], n_results=min(6, collection.count()),
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
    top1 = ranked[0] if ranked else None
    confidence = scores.get(top1, 0) if top1 else 0

    if confidence < CONFIDENCE_THRESHOLD:
        return None, confidence, "⚠️ 识别置信度不足，请移到展品正面重新拍摄"
    return top1, confidence, None

# 用所有非正面图测试
all_files = [f for f in os.listdir(IMAGE_DIR) if f.lower().endswith(('.jpg','.jpeg','.png'))]
query_files = [f for f in all_files if '正面' not in f]

print(f"\n=== 真实场景测试：用非正面图查询（共{len(query_files)}张）===\n")
correct, warned, total = 0, 0, 0

for qfile in query_files:
    qpath = os.path.join(IMAGE_DIR, qfile)
    expected = get_artifact_name(qfile)
    top1, conf, hint = recognize(qpath)
    total += 1

    if hint:
        warned += 1
        print(f"⚠️  {qfile}")
        print(f"   预期：{expected} | 置信度：{conf:.3f} | {hint}\n")
    elif top1 == expected:
        correct += 1
        print(f"✅ {qfile}")
        print(f"   识别：{top1}（置信度:{conf:.3f}）\n")
    else:
        print(f"❌ {qfile}")
        print(f"   预期：{expected} | 识别：{top1}（置信度:{conf:.3f}）\n")

print("="*50)
print(f"总计：{total}张 | 正确：{correct} | 触发提示：{warned} | 错误：{total-correct-warned}")
print(f"有效识别准确率：{correct}/{total-warned} = {correct/max(total-warned,1)*100:.0f}%")
print("="*50)