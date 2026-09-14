# -*- coding: utf-8 -*-
"""
test_image_recognition.py
用已有文物图片测试CLIP图像识别效果
输入：任意一张图片路径
输出：最匹配的文物名称（Top3）
"""
import sys, torch
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
import chromadb
from collections import Counter

CLIP_PATH = r"D:\MuseumGlasses\models\clip-vit-base"
DB_PATH   = r"D:\MuseumGlasses\vector_db\images"

print("加载模型...")
model = CLIPModel.from_pretrained(CLIP_PATH)
processor = CLIPProcessor.from_pretrained(CLIP_PATH)
model.eval()

client = chromadb.PersistentClient(path=DB_PATH)
collection = client.get_collection("artifact_images")
print(f"图像库中共 {collection.count()} 条向量")

def recognize(image_path, top_k=3):
    image = Image.open(image_path).convert("RGB")
    inputs = processor(images=image, return_tensors="pt")
    
    with torch.no_grad():
        outputs = model.vision_model(**inputs)
        feat = outputs.pooler_output
        feat = feat / feat.norm(dim=-1, keepdim=True)
        vec = feat[0].detach().numpy().tolist()
    
    # 检索最相似的5条向量
    results = collection.query(
        query_embeddings=[vec],
        n_results=min(5, collection.count()),
        include=["metadatas", "distances"]
    )
    
    # 投票：哪件文物被多个角度都匹配到就胜出
    votes = Counter()
    scores = {}
    for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
        name = meta["artifact_name"]
        similarity = 1 - dist  # ChromaDB返回距离，转换为相似度
        votes[name] += 1
        scores[name] = max(scores.get(name, 0), similarity)
    
    # 综合排序：票数优先，相似度次之
    ranked = sorted(votes.keys(),
                    key=lambda x: (votes[x], scores[x]),
                    reverse=True)
    
    return [(name, votes[name], scores[name]) for name in ranked[:top_k]]

# 测试：用库里的图片测试（自识别应该准确）
import os
IMAGE_DIR = r"D:\MuseumGlasses\knowledge_base\images"
test_images = os.listdir(IMAGE_DIR)[:5]  # 取前5张测试

print("\n=== 自识别测试（库内图片）===")
correct, total = 0, 0
for fname in test_images:
    fpath = os.path.join(IMAGE_DIR, fname)
    expected = fname.replace('正面','').replace('背面','').replace('侧面','')
    expected = expected.replace('底面','').replace('内部','').replace('外部','')
    expected = expected.replace('另一側','').replace('証明','').replace('后面','')
    expected = os.path.splitext(expected)[0].strip()
    
    results = recognize(fpath)
    top1 = results[0][0] if results else "无结果"
    match = "✅" if top1 == expected else "❌"
    total += 1
    if top1 == expected:
        correct += 1
    
    print(f"{match} {fname}")
    print(f"   预期：{expected}")
    print(f"   识别：{top1}（票数:{results[0][1]} 相似度:{results[0][2]:.3f}）")

print(f"\n自识别准确率：{correct}/{total} = {correct/total*100:.0f}%")