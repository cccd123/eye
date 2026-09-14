# -*- coding: utf-8 -*-
"""
交叉验证测试：用一张图识别，不能用自己识别自己
策略：把每件文物的正面图当作"查询图"，
      把数据库里除正面以外的其他角度当作"已知库"
      看能不能用其他角度的图认出同一件文物
"""
import os, re, torch
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
import chromadb
from collections import Counter

CLIP_PATH  = r"D:\MuseumGlasses\models\clip-vit-base"
IMAGE_DIR  = r"D:\MuseumGlasses\knowledge_base\images"
DB_PATH    = r"D:\MuseumGlasses\vector_db\images"

SUFFIXES = r'正面|背面|侧面|底面|内部|外部|另一[侧側]|証明|证明|后面'

def get_artifact_name(filename):
    name = os.path.splitext(filename)[0]
    return re.sub(SUFFIXES, '', name).strip()

print("加载CLIP模型...")
model = CLIPModel.from_pretrained(CLIP_PATH)
processor = CLIPProcessor.from_pretrained(CLIP_PATH)
model.eval()

client = chromadb.PersistentClient(path=DB_PATH)

def get_vector(image_path):
    image = Image.open(image_path).convert("RGB")
    inputs = processor(images=image, return_tensors="pt")
    with torch.no_grad():
        outputs = model.vision_model(**inputs)
        feat = outputs.pooler_output
        feat = feat / feat.norm(dim=-1, keepdim=True)
    return feat[0].detach().numpy().tolist()

all_files = [f for f in os.listdir(IMAGE_DIR)
             if f.lower().endswith(('.jpg','.jpeg','.png'))]

# 找出有正面图的文物，用正面图作为查询
query_files = [f for f in all_files if '正面' in f]

print(f"\n=== 交叉验证：用正面图查询，排除自身 ===")
print(f"查询图数量：{len(query_files)}\n")

correct, total = 0, 0

for qfile in query_files:
    qpath = os.path.join(IMAGE_DIR, qfile)
    expected = get_artifact_name(qfile)
    qvec = get_vector(qpath)

    # 重建一个排除自身的临时集合
    temp_client = chromadb.EphemeralClient()
    try:
        temp_client.delete_collection("temp")
    except:
        pass
    temp_col = temp_client.create_collection("temp")

    ids, vecs, metas, docs = [], [], [], []
    for i, fname in enumerate(all_files):
        if fname == qfile:  # 排除查询图自身
            continue
        fpath = os.path.join(IMAGE_DIR, fname)
        artifact = get_artifact_name(fname)
        vec = get_vector(fpath)
        ids.append(f"img_{i}")
        vecs.append(vec)
        metas.append({"artifact_name": artifact, "filename": fname})
        docs.append(artifact)

    temp_col.add(ids=ids, embeddings=vecs, metadatas=metas, documents=docs)

    # 查询Top5
    results = temp_col.query(
        query_embeddings=[qvec],
        n_results=min(5, len(ids)),
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
    top1 = ranked[0] if ranked else "无结果"
    top3 = ranked[:3]

    match = "✅" if top1 == expected else "❌"
    total += 1
    if top1 == expected:
        correct += 1

    print(f"{match} 查询：{qfile}")
    print(f"   预期：{expected}")
    print(f"   Top1：{top1}（票:{votes.get(top1,0)} 相似度:{scores.get(top1,0):.3f}）")
    if top1 != expected:
        print(f"   Top3：{top3}")
    print()

print(f"{'='*50}")
print(f"交叉验证准确率：{correct}/{total} = {correct/total*100:.0f}%")
print(f"{'='*50}")