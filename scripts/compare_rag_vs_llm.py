# -*- coding: utf-8 -*-
"""
对比测试：我们的RAG系统 vs 纯LLM（无知识库）
用同样的问题分别问两个系统，对比回答质量
"""
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from llama_cpp import Llama

VECTOR_DB  = r"D:\MuseumGlasses\vector_db\sculptures"
BGE_PATH   = r"D:\MuseumGlasses\models\bge-m3"
LLM_PATH   = r"D:\MuseumGlasses\models\qwen3-1.7b\Qwen3-1.7B-Q4_K_M.gguf"

print("加载模型中...")
emb = HuggingFaceEmbeddings(
    model_name=BGE_PATH,
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True}
)
db = Chroma(persist_directory=VECTOR_DB, embedding_function=emb,
            collection_name="npm_sculptures")
llm = Llama(model_path=LLM_PATH, n_ctx=4096, n_gpu_layers=28, verbose=False)

def ask_rag(question):
    """RAG模式：先检索知识库，再生成回答"""
    docs = db.similarity_search(question, k=2)
    context = "\n\n".join([d.page_content for d in docs])
    retrieved = [d.metadata.get("name","") for d in docs]
    
    resp = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": "你是博物馆专业讲解员，严格根据以下资料回答，不要添加资料中没有的内容。/no_think"},
            {"role": "user", "content": f"参考资料：\n{context}\n\n问题：{question}"}
        ],
        max_tokens=200, temperature=0.3, stream=False
    )
    return resp["choices"][0]["message"]["content"], retrieved

def ask_pure_llm(question):
    """纯LLM模式：没有知识库，完全靠模型自身"""
    resp = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": "你是博物馆讲解员。/no_think"},
            {"role": "user", "content": question}
        ],
        max_tokens=200, temperature=0.3, stream=False
    )
    return resp["choices"][0]["message"]["content"]

# 测试问题（选冷门文物，大模型训练数据里不太可能有）
questions = [
    "竹根雕牧童臥牛是几世纪的作品，尺寸是多少？",
    "竹黃臥瓜式盒的内胎是什么材质做的？",
    "犀角雕蓬瀛仙侶杯有什么历史背景？",
]

print("\n" + "="*60)
print("RAG系统 vs 纯LLM 对比测试")
print("="*60)

for q in questions:
    print(f"\n【问题】{q}")
    
    rag_ans, retrieved = ask_rag(q)
    print(f"\n✅ RAG系统回答（检索自：{retrieved}）：")
    print(f"   {rag_ans}")
    
    llm_ans = ask_pure_llm(q)
    print(f"\n❌ 纯LLM回答（无知识库）：")
    print(f"   {llm_ans}")
    print("-"*60)