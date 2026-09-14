# -*- coding: utf-8 -*-
"""
test_full_pipeline.py
完整流程测试：用户问题 -> 向量检索 -> 组装上下文 -> Qwen3生成回答
这是系统的核心闭环，验证RAG真的能用
"""

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from llama_cpp import Llama

VECTOR_DB  = r"D:\MuseumGlasses\vector_db\sculptures"
MODEL_PATH = r"D:\MuseumGlasses\models\bge-m3"
LLM_PATH   = r"D:\MuseumGlasses\models\qwen3-1.7b\Qwen3-1.7B-Q4_K_M.gguf"

print("加载向量模型...")
emb = HuggingFaceEmbeddings(
    model_name=MODEL_PATH,
    model_kwargs={"device": "cpu"},   # 改成CPU，给Qwen3让出显存
    encode_kwargs={"normalize_embeddings": True}
)

print("加载向量库...")
db = Chroma(
    persist_directory=VECTOR_DB,
    embedding_function=emb,
    collection_name="npm_sculptures"
)

print("加载Qwen3...")
llm = Llama(
    model_path=LLM_PATH,
    n_ctx=4096,
    n_gpu_layers=28,
    verbose=False
)

# 三种人设的System Prompt
PERSONAS = {
    "儿童": "你是博物馆的童趣讲解员，用简单有趣的语言给小朋友讲解文物，多用比喻，语气活泼，每次结尾问一个小问题引发好奇心。/no_think",
    "专家": "你是博物馆的专业研究员，用准确严谨的术语讲解文物的工艺、年代和历史背景，简洁但信息密度高。/no_think",
    "社畜": "你是博物馆的高效讲解员，用最精简的语言，3句话内讲完核心信息，可以用职场类比让人会心一笑。/no_think"
}

def ask(question, persona="专家", k=2):
    print(f"\n{'='*60}")
    print(f"【问题】{question}  【人设】{persona}")
    print('='*60)

    # 第一步：向量检索，找最相关的k个chunk
    docs = db.similarity_search(question, k=k)
    context = "\n\n".join([d.page_content for d in docs])

    print(f"\n[检索到的文物]")
    for d in docs:
        print(f"  - {d.metadata.get('name','')}")

    # 第二步：组装prompt，让Qwen3基于检索内容回答
    system_prompt = PERSONAS[persona]
    user_prompt = f"参考资料：\n{context}\n\n请根据上面的资料回答：{question}"

    # 第三步：调用Qwen3生成回答
    print(f"\n[Qwen3回答]")
    response = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        max_tokens=300,
        temperature=0.7,
        stream=True
    )
    for chunk in response:
        delta = chunk["choices"][0]["delta"].get("content", "")
        if delta:
            print(delta, end="", flush=True)
    print()

# ── 测试用例 ──────────────────────────────────────
ask("竹根雕牧童臥牛是什么材质做的，有什么特点？", persona="专家")
ask("竹黃臥瓜式盒为什么会成为国交礼物？", persona="儿童")
ask("沉香木雕山水筆筒的工艺怎么样？", persona="社畜")