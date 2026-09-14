# -*- coding: utf-8 -*-
import torch
import json
import re
from PIL import Image
from transformers import CLIPProcessor, CLIPModel

MODEL_PATH = r"D:\MuseumGlasses\models\clip-vit-base"
TEST_IMAGE = r"D:\test.jpg"

print("加载CLIP模型...")
model = CLIPModel.from_pretrained(MODEL_PATH)
processor = CLIPProcessor.from_pretrained(MODEL_PATH)
model.eval()

# 加载知识库文物名称
with open(r"D:\MuseumGlasses\knowledge_base\raw_text\npm_artifacts.json", encoding="utf-8") as f:
    artifacts = json.load(f)

def clean_name(name):
    m = re.search(r'[A-Z][a-z]', name)
    return name[:m.start()].strip() if m else name

# 取前100件作为候选
candidates = [clean_name(a["name"]) for a in artifacts[:100]]
print(f"候选文物数量：{len(candidates)}")

# 加载测试图片
image = Image.open(TEST_IMAGE).convert("RGB")
print(f"图片加载成功：{image.size}")

# CLIP推理
inputs = processor(text=candidates, images=image, return_tensors="pt", padding=True, truncation=True)
with torch.no_grad():
    outputs = model(**inputs)

probs = outputs.logits_per_image.softmax(dim=1)[0]
top5 = probs.topk(5)

print("\n=== 识别结果 Top5 ===")
for score, idx in zip(top5.values, top5.indices):
    print(f"  {candidates[idx.item()]:<20s}  置信度：{score.item():.4f}")