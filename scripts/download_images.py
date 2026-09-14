# -*- coding: utf-8 -*-
"""
批量下载台北故宫文物官方图片
图片URL格式：固定路径+文物统一编号
下载后用于建立CLIP图像向量库
"""
import json, os, time, re, requests
from pathlib import Path

IMG_DIR = r"D:\MuseumGlasses\knowledge_base\images"
RAW_JSON = r"D:\MuseumGlasses\knowledge_base\raw_text\npm_artifacts.json"
os.makedirs(IMG_DIR, exist_ok=True)

with open(RAW_JSON, encoding="utf-8") as f:
    artifacts = json.load(f)

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "Mozilla/5.0"

def get_image_url(artifact):
    """从属性里取文物统一编号，构造图片URL"""
    attrs = artifact.get("attributes", {})
    aid = attrs.get("\u6587\u7269\u7d71\u4e00\u7de8\u865f", "")  # 文物統一編號
    if not aid:
        return None
    # 台北故宫图片URL格式
    return f"https://digitalarchive.npm.gov.tw/opendata/FileDownload/Images/L/{aid}.jpg"

success, failed = 0, 0
for i, a in enumerate(artifacts, 1):
    # 清理文物名称用于文件名
    name = a.get("name", "")
    m = re.search(r'[A-Z][a-z]', name)
    name = name[:m.start()].strip() if m else name
    name_safe = re.sub(r'[\\/:*?"<>|]', '_', name)[:40]
    
    save_path = os.path.join(IMG_DIR, f"{name_safe}.jpg")
    if os.path.exists(save_path):
        success += 1
        continue
    
    url = get_image_url(a)
    if not url:
        failed += 1
        continue
    
    try:
        r = SESSION.get(url, timeout=15)
        if r.status_code == 200 and len(r.content) > 1000:
            with open(save_path, "wb") as f:
                f.write(r.content)
            success += 1
            print(f"[{i}/{len(artifacts)}] ✅ {name[:20]}")
        else:
            failed += 1
            print(f"[{i}/{len(artifacts)}] ⚠️ {name[:20]} 图片无效")
    except Exception as e:
        failed += 1
        print(f"[{i}/{len(artifacts)}] ❌ {name[:20]}: {e}")
    
    time.sleep(0.3)

print(f"\n完成：成功{success}张，失败{failed}张")
print(f"图片保存至：{IMG_DIR}")