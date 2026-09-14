# -*- coding: utf-8 -*-
"""
build_image_db.py
用CLIP对文物图片提取图像向量，存入ChromaDB图像库
原理：CLIP把图片压缩成512维向量，语义相似的图片向量距离近
识别时：输入新图片 → 提取向量 → 找最近的已知文物向量 → 返回文物名称
"""
import os, re
from PIL import Image
import torch
import numpy as np
from transformers import CLIPProcessor, CLIPModel
import chromadb

IMAGE_DIR  = r"D:\MuseumGlasses\knowledge_base\images"
CLIP_PATH  = r"D:\MuseumGlasses\models\clip-vit-base"
DB_PATH    = r"D:\MuseumGlasses\vector_db\images"

os.makedirs(DB_PATH, exist_ok=True)

# 加载CLIP模型（CPU，不占GPU显存）
print("加载CLIP模型...")
model = CLIPModel.from_pretrained(CLIP_PATH)
processor = CLIPProcessor.from_pretrained(CLIP_PATH)
model.eval()

# 初始化ChromaDB图像向量库
client = chromadb.PersistentClient(path=DB_PATH)
# 每次重建，清除旧数据
try:
    client.delete_collection("artifact_images")
except:
    pass
collection = client.create_collection("artifact_images")

# 文物名称清理：去掉角度后缀，得到纯文物名
SUFFIXES = r'正面|背面|侧面|底面|内部|外部|另一[侧側]|証明|后面'

def get_artifact_name(filename):
    """从文件名提取文物名称，去掉角度描述"""
    name = os.path.splitext(filename)[0]  # 去掉.png/.jpg
    name = re.sub(SUFFIXES, '', name).strip()
    return name

# 提取所有图片的CLIP向量
print(f"\n开始处理图片...")
image_files = [f for f in os.listdir(IMAGE_DIR)
               if f.lower().endswith(('.jpg', '.jpeg', '.png'))]

ids, embeddings, metadatas, documents = [], [], [], []

for i, fname in enumerate(image_files, 1):
    fpath = os.path.join(IMAGE_DIR, fname)
    artifact_name = get_artifact_name(fname)
    
    try:
        image = Image.open(fpath).convert("RGB")
        inputs = processor(images=image, return_tensors="pt")
        
        with torch.no_grad():
            # 提取图像特征向量（512维）
            outputs = model.vision_model(**inputs)
            feat = outputs.pooler_output
            feat = feat / feat.norm(dim=-1, keepdim=True)
            vec = feat[0].detach().numpy().tolist()
        
        img_id = f"img_{i:04d}"
        ids.append(img_id)
        embeddings.append(vec)
        metadatas.append({
            "artifact_name": artifact_name,
            "filename": fname,
            "angle": re.search(SUFFIXES, os.path.splitext(fname)[0]) and
                     re.search(SUFFIXES, os.path.splitext(fname)[0]).group() or "未知"
        })
        documents.append(artifact_name)
        
        print(f"  [{i}/{len(image_files)}] ✅ {fname} → {artifact_name}")
        
    except Exception as e:
        print(f"  [{i}/{len(image_files)}] ❌ {fname}: {e}")

# 批量写入ChromaDB
collection.add(ids=ids, embeddings=embeddings,
               metadatas=metadatas, documents=documents)

print(f"\n图像库构建完成！共 {collection.count()} 条向量")
print(f"存储路径：{DB_PATH}")