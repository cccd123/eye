# -*- coding: utf-8 -*-
"""
build_vectordb.py
把663件台北故宫文物数据清洗后向量化，存入ChromaDB向量数据库

核心技术：
  - bge-m3：把文字变成1024维数字向量（"语义坐标"）
  - ChromaDB：存储这些向量，支持快速语义检索
  - 分块：把长文本切成小段，每段单独向量化

向量化原理：
  bge-m3是在海量中文语料上训练的神经网络。
  训练时它学到了"语义相近的文字在数字空间中位置也相近"。
  输入一段文字，它输出1024个小数（1024维向量）。
  检索时，用户的问题也变成向量，找距离最近的文物描述返回。
"""

import json
import os
import re

# langchain_text_splitters：专门负责把长文本切成小块的工具包
# RecursiveCharacterTextSplitter：递归字符分割器，
#   先尝试按换行符切，切不小就按句号切，再不行按逗号切，
#   这样保证每块都在自然断句处切开，不会把一句话切断
from langchain_text_splitters import RecursiveCharacterTextSplitter

# chromadb通过langchain接口使用，Chroma是向量数据库
# 可以理解为：存数字坐标的特殊数据库，支持"找最近邻居"的查询
from langchain_chroma import Chroma

# HuggingFaceEmbeddings：加载bge-m3模型的接口
# 它把SentenceTransformer模型包装成langchain能用的格式
from langchain_huggingface import HuggingFaceEmbeddings

# ── 路径配置 ──────────────────────────────────────────────────
RAW_JSON   = r"D:\MuseumGlasses\knowledge_base\raw_text\npm_artifacts.json"
CHUNKS_DIR = r"D:\MuseumGlasses\knowledge_base\chunks"
VECTOR_DB  = r"D:\MuseumGlasses\vector_db\sculptures"   # 向量库存放位置
MODEL_PATH = r"D:\MuseumGlasses\models\bge-m3"          # bge-m3本地路径

os.makedirs(CHUNKS_DIR, exist_ok=True)
os.makedirs(VECTOR_DB, exist_ok=True)

# ── 第一步：加载原始数据 ──────────────────────────────────────
print("=" * 50)
print("第一步：加载原始文物数据")
print("=" * 50)
with open(RAW_JSON, encoding="utf-8") as f:
    artifacts = json.load(f)
print(f"共加载 {len(artifacts)} 件文物")

# ── 第二步：数据清洗 ──────────────────────────────────────────
print("\n第二步：数据清洗")

def clean_name(name):
    """
    去掉文物名称里的英文部分，只保留中文。
    原始数据中名称格式为：'竹根雕牧童臥牛Bamboo-root carving...'
    我们只需要中文部分供RAG检索使用。
    """
    m = re.search(r'[A-Z][a-z]', name)  # 找第一个"大写+小写"的英文组合
    if m:
        name = name[:m.start()].strip()   # 截断英文之前的部分
    return name

def clean_text(text):
    """把多余的空白字符（空格、换行、制表符）压缩成单个空格"""
    return re.sub(r'\s+', ' ', text).strip()

def build_doc(a):
    """
    把一件文物的所有字段拼成一段结构化文本。
    
    为什么要拼成一段？
    因为bge-m3的输入是文字，我们要让它"看到"文物的所有信息，
    这样当用户问"清朝竹雕"时，向量才能准确匹配到相关文物。
    
    格式用标签标注（[name][era]等），方便调试时肉眼识别字段。
    """
    name  = clean_name(a.get("name", ""))
    attrs = a.get("attributes", {})
    desc  = clean_text(a.get("description", ""))

    # 从属性字典里取关键字段
    # 注意：台北故宫用繁体字，所以字段名是繁体
    aid  = attrs.get("\u6587\u7269\u7d71\u4e00\u7de8\u865f", "")  # 文物統一編號
    era  = attrs.get("\u6642\u4ee3", "")   # 時代
    size = attrs.get("\u5c3a\u5bf8", "")   # 尺寸

    parts = [f"[name]{name}"]
    if aid:  parts.append(f"[id]{aid}")
    if era:  parts.append(f"[era]{era}")
    if size: parts.append(f"[size]{size}")
    if desc: parts.append(f"[desc]{desc}")
    parts.append(f"[src]National Palace Museum Taipei CC-BY-4.0 {a.get('url','')}")

    return "\n".join(parts)

# 过滤掉内容太少的条目（没有描述且名字太短 = 数据不完整，没价值）
valid = [a for a in artifacts
         if len(a.get("description", "")) >= 20 or len(clean_name(a.get("name", ""))) >= 3]
print(f"有效文物：{len(valid)} 件（过滤掉 {len(artifacts)-len(valid)} 件内容过少的）")

# 构建文档
docs_text, docs_meta = [], []
for a in valid:
    docs_text.append(build_doc(a))
    # metadata是每个向量的"身份标签"，检索返回结果时会附带这些信息
    # 告诉我们"这个向量来自哪件文物"
    docs_meta.append({
        "artifact_id": str(a.get("id", "")),
        "name": clean_name(a.get("name", "")),
        "era": a.get("attributes", {}).get("\u6642\u4ee3", ""),
        "source": "NPM_Taipei"
    })

print(f"构建了 {len(docs_text)} 篇文档")

# ── 第三步：文本分块 ──────────────────────────────────────────
print("\n第三步：文本分块")
print("（把长文本切成小段，每段独立向量化，检索更精准）")

"""
为什么要分块？
bge-m3单次处理上限约512个token（约800中文字）。
超过这个长度，多出来的内容会被截断，信息丢失。
更重要的是：一件文物的描述如果很长，分成小块后，
检索时能精确定位到"哪一块"最相关，而不是返回整篇。

chunk_size=600：每块最多600字
chunk_overlap=80：相邻两块重叠80字
  重叠的目的：防止一句话被切断成两半，前后块各有一点上下文
  
例如：...竹黄工艺的历史稍不久远，| 竹黄器的普及性也不如陶瓷...
有重叠的话，第二块开头还有"竹黄工艺的历史稍不久远"，语义完整。
"""
splitter = RecursiveCharacterTextSplitter(
    chunk_size=600,
    chunk_overlap=80,
    separators=["\n", "\u3002", "\uff1b", "\uff0c", " "]
    # 分隔符优先级：换行 > 句号 > 分号 > 逗号 > 空格
    # 优先在自然断句处切，实在太长才在逗号处切
)

chunks, metas = [], []
for text, meta in zip(docs_text, docs_meta):
    text_chunks = splitter.split_text(text)
    for i, c in enumerate(text_chunks):
        chunks.append(c)
        # 记录这个chunk是第几件文物的第几块，方便追溯
        metas.append({**meta, "chunk_idx": i, "total_chunks": len(text_chunks)})

print(f"分块结果：{len(docs_text)} 篇文档 → {len(chunks)} 个chunk")
print(f"平均每篇 {len(chunks)/len(docs_text):.1f} 块")

# 保存分块结果到文件，方便人工检查质量
chunks_save_path = os.path.join(CHUNKS_DIR, "chunks_preview.json")
with open(chunks_save_path, "w", encoding="utf-8") as f:
    json.dump([{"text": c, "meta": m} for c, m in zip(chunks[:20], metas[:20])],
              f, ensure_ascii=False, indent=2)
print(f"前20个chunk已保存至 {chunks_save_path}（可打开检查质量）")

# ── 第四步：加载bge-m3向量模型 ───────────────────────────────
print("\n第四步：加载bge-m3向量模型")
print("（首次加载约30秒，模型需要从磁盘读取到显存）")

"""
bge-m3工作原理：
  输入：一段中文文字（最多约800字）
  内部：通过24层Transformer神经网络处理
       每一层都在提炼文字的语义信息
       最终把所有信息压缩到1024个数字
  输出：1024维向量（1024个浮点数）

为什么选bge-m3？
  - 专门针对中文优化，在C-MTEB中文评测榜单排名前列
  - 支持"多粒度检索"：既能匹配短词也能匹配长段落
  - 完全本地运行，不需要联网，不需要API key

normalize_embeddings=True：
  把向量长度归一化为1（变成单位向量）
  这样计算相似度时只看方向，不看长度
  效果：更稳定，两段文字语义越像，点积越接近1
"""
emb = HuggingFaceEmbeddings(
    model_name=MODEL_PATH,          # 本地模型路径，不联网
    model_kwargs={"device": "cuda"},# 用GPU（RTX 3050）运行，比CPU快10倍
    encode_kwargs={
        "normalize_embeddings": True,   # 归一化，提升检索精度
        "batch_size": 32                # 每批同时处理32个chunk，充分利用GPU并行
    }
)
print("bge-m3加载成功")

# ── 第五步：向量化并写入ChromaDB ─────────────────────────────
print("\n第五步：向量化并写入ChromaDB")
print("（预计3-8分钟，GPU正在把663篇文档变成数字坐标...）")

"""
这一步发生了什么：
  1. 把chunks列表分成若干批（每批32个）
  2. 每批送入bge-m3，输出32个1024维向量
  3. 把这些向量和对应的文字、metadata一起存入ChromaDB
  4. ChromaDB在磁盘上建立索引，以后检索时能快速找最近邻

ChromaDB存储结构（简化）：
  向量库 = {
    向量1（1024个数）: "竹根雕牧童臥牛...清十八世紀...",
    向量2（1024个数）: "全器作長橢圓形...",
    ...共672个
  }
  
查询时：把用户问题变成向量，找和它"方向最相似"的前K个向量，返回对应文字。
"""
db = Chroma.from_texts(
    texts=chunks,
    embedding=emb,
    metadatas=metas,
    persist_directory=VECTOR_DB,        # 持久化到磁盘，下次启动不用重建
    collection_name="npm_sculptures"    # 集合名，相当于数据库里的表名
)

total = db._collection.count()
print(f"\n{'='*50}")
print(f"向量库构建完成！")
print(f"  存储路径：{VECTOR_DB}")
print(f"  总向量数：{total}")
print(f"{'='*50}")