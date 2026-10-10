# -*- coding: utf-8 -*-
"""
engine.py - 所有 AI 能力封装在这里：
  recognize(PIL图片)                    -> (文物名, 置信度)
  explain(文物名, 问题, 人设, 对话历史)   -> 讲解文字（干净的简体中文，可直接朗读）
  transcribe(音频字节)                  -> 问题文字
  synthesize(文字, 人设, 格式)           -> 音频文件路径
  artifact_names()                      -> 知识库里所有展品名（APP 手动选择用）

讲解质量相关的逻辑在 qa.py。MOCK 模式不需要模型和显卡。
"""
import io, json, math, wave, struct, uuid, shutil, sqlite3, asyncio, threading, subprocess, urllib.request
from collections import Counter
from pathlib import Path

import config
import qa
from personas import PERSONAS

_llm_lock = threading.Lock()     # llama.cpp 不是线程安全的
_clip_lock = threading.Lock()
MAX_CONTEXT_CHARS = 900          # 资料太长时只取和问题最相关的几段，小模型上下文有限


def _find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:  # pip install imageio-ffmpeg 会自带一个 ffmpeg.exe
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = _find_ffmpeg()


def to_wav16k(src: Path) -> Path:
    """mp3 -> 16kHz 单声道 16bit wav（眼镜端 aplay 直接能放）"""
    dst = src.with_suffix(".wav")
    if not FFMPEG:
        raise RuntimeError("需要 ffmpeg 才能输出 wav：pip install imageio-ffmpeg")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(src),
                    "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", str(dst)], check=True)
    return dst


def prune_dir(d: Path, keep: int):
    files = sorted(d.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[keep:]:
        try:
            p.unlink()
        except OSError:
            pass


class _Base:
    """真实引擎和模拟引擎共用的讲解流程"""

    def chat(self, messages, temperature=0.3, max_tokens=300) -> str:
        raise NotImplementedError

    def retrieve(self, artifact, question) -> str:
        raise NotImplementedError

    def explain(self, artifact, question, persona_key, history=()):
        p = PERSONAS.get(persona_key, PERSONAS["expert"])
        context = self.retrieve(artifact, question)
        if not context:
            return "知识库里还没有这件展品的资料。"
        msgs = qa.build_messages(p, artifact, context, question, history)
        raw = self.chat(msgs, temperature=0.3)
        if qa.is_echo(raw, context):
            print(f"↻ 回答在照抄资料，换严格提示重试：{raw[:40]}…")
            msgs = qa.build_messages(p, artifact, context, question, history, strict=True)
            raw = self.chat(msgs, temperature=0.6)
        text = qa.finalize(raw, p["max_chars"] + 30)
        if len(text) < 6 or qa.is_echo(text, context):
            text = qa.fallback_answer(artifact, question)
        return text

    async def tts_save(self, text, voice, path):
        import edge_tts
        await edge_tts.Communicate(text=text, voice=voice, rate="+5%").save(str(path))

    async def synthesize(self, text, persona_key, fmt="mp3") -> Path:
        voice = PERSONAS.get(persona_key, PERSONAS["expert"])["voice"]
        path = config.AUDIO_DIR / f"{uuid.uuid4().hex}.mp3"
        await self.tts_save(text, voice, path)
        if fmt == "wav":
            path = await asyncio.to_thread(to_wav16k, path)
        return path


def _api_chat(messages, temperature, max_tokens):
    """OpenAI 兼容接口（DeepSeek / 通义千问 / Kimi 等），见 config.LLM_API"""
    api = config.LLM_API
    body = json.dumps({"model": api["model"], "messages": messages, "temperature": temperature,
                       "max_tokens": max_tokens, "stream": False}).encode()
    req = urllib.request.Request(api["base_url"].rstrip("/") + "/chat/completions", data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {api['api_key']}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["choices"][0]["message"]["content"]


# ═════════════════════════ 真实引擎 ═════════════════════════
class RealEngine(_Base):
    def __init__(self):
        import torch
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"正在加载模型（{self.device}），请稍候...")

        from transformers import CLIPProcessor, CLIPModel
        self.clip_model = CLIPModel.from_pretrained(str(config.CLIP_PATH)).to(self.device).eval()
        self.clip_processor = CLIPProcessor.from_pretrained(str(config.CLIP_PATH))
        print("✅ CLIP加载完成")

        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_chroma import Chroma
        emb = HuggingFaceEmbeddings(model_name=str(config.BGE_PATH),
                                    model_kwargs={"device": self.device},
                                    encode_kwargs={"normalize_embeddings": True})
        self.txt_db = Chroma(persist_directory=str(config.TXT_DB_PATH),
                             embedding_function=emb, collection_name="npm_sculptures")
        print("✅ 文字知识库加载完成")

        if config.LLM_API:
            self.llm = None
            print(f"✅ 使用在线大模型：{config.LLM_API['model']}（{config.LLM_API['base_url']}）")
        else:
            from llama_cpp import Llama
            self.llm = Llama(model_path=str(config.LLM_PATH), n_ctx=4096,
                             n_gpu_layers=config.LLM_GPU_LAYERS, verbose=False)
            print("✅ Qwen3加载完成")

        import chromadb
        self.img_col = chromadb.PersistentClient(path=str(config.IMG_DB_PATH)) \
                               .get_collection("artifact_images")
        print(f"✅ 图像库加载完成 ({self.img_col.count()}条)")

        self._asr = None          # 第一次用到语音时再加载，不拖慢启动
        self._asr_error = None
        self._names = None
        print("✅ 所有模型加载完成")

    # ── 图像识别 ──
    def recognize(self, pil_image):
        with _clip_lock:
            inputs = self.clip_processor(images=pil_image, return_tensors="pt").to(self.device)
            with self.torch.no_grad():
                feat = self.clip_model.vision_model(**inputs).pooler_output
                feat = feat / feat.norm(dim=-1, keepdim=True)
            vec = feat[0].cpu().numpy().tolist()
        res = self.img_col.query(query_embeddings=[vec],
                                 n_results=min(6, self.img_col.count()),
                                 include=["metadatas", "distances"])
        votes, scores = Counter(), {}
        for meta, dist in zip(res["metadatas"][0], res["distances"][0]):
            name, sim = meta["artifact_name"], 1 - dist
            votes[name] += 1
            scores[name] = max(scores.get(name, 0), sim)
        if not votes:
            return None, 0.0
        top = max(votes, key=lambda n: (votes[n], scores[n]))
        return top, float(scores[top])

    # ── 检索 ──
    def retrieve(self, artifact, question):
        if artifact:
            # 已知展品：直接按名字取出它的全部资料（比向量检索更准，不会混进别的展品）
            got = self.txt_db._collection.get(where={"name": artifact}, include=["documents", "metadatas"])
            pairs = sorted(zip(got["metadatas"], got["documents"]), key=lambda x: x[0].get("chunk_idx") or 0)
            ctx = qa.clean_context([d for _, d in pairs])
            if ctx and len(ctx) <= MAX_CONTEXT_CHARS:
                return ctx
            if ctx:   # 太长：保留第一段（名称年代）+ 和问题最相关的两段
                hits = self.txt_db.similarity_search(f"{artifact} {question}", k=2, filter={"name": artifact})
                first = pairs[0][1] if pairs else ""
                chunks = [first] + [h.page_content for h in hits if h.page_content != first]
                return qa.clean_context(chunks)[:MAX_CONTEXT_CHARS]
        hits = self.txt_db.similarity_search(question or artifact or "", k=3)
        return qa.clean_context([h.page_content for h in hits])[:MAX_CONTEXT_CHARS]

    def artifact_names(self):
        if self._names is None:
            metas = self.txt_db._collection.get(include=["metadatas"])["metadatas"]
            in_img = {m["artifact_name"] for m in self.img_col.get(include=["metadatas"])["metadatas"]}
            names = sorted({m.get("name") for m in metas if m.get("name")})
            self._names = [{"name": n, "has_image": n in in_img} for n in names]
        return self._names

    # ── 大模型 ──
    def chat(self, messages, temperature=0.3, max_tokens=300):
        if config.LLM_API:
            return _api_chat(messages, temperature, max_tokens)
        with _llm_lock:
            resp = self.llm.create_chat_completion(messages=messages, max_tokens=max_tokens,
                                                   temperature=temperature, repeat_penalty=1.1)
        return resp["choices"][0]["message"]["content"]

    # ── 语音识别 ──
    def _load_asr(self):
        if self._asr or self._asr_error:
            return
        try:
            from faster_whisper import WhisperModel
            dev = config.ASR_DEVICE if config.ASR_DEVICE != "auto" else self.device
            ct = "float16" if dev == "cuda" else "int8"
            print(f"加载语音识别模型 {config.ASR_MODEL} ({dev})，第一次会下载约 480MB...")
            self._asr = WhisperModel(config.ASR_MODEL, device=dev, compute_type=ct)
            print("✅ 语音识别加载完成")
        except Exception as e:
            self._asr_error = f"{type(e).__name__}: {e}"
            print("❌ 语音识别加载失败：", self._asr_error)

    def transcribe(self, audio_bytes: bytes) -> str:
        self._load_asr()
        if not self._asr:
            raise RuntimeError(f"语音识别不可用：{self._asr_error}")
        segs, _ = self._asr.transcribe(io.BytesIO(audio_bytes), language="zh",
                                       vad_filter=True, beam_size=1,
                                       initial_prompt="以下是普通话的句子，关于博物馆文物。")
        return qa.to_simplified("".join(s.text for s in segs).strip())

    @property
    def asr_status(self):
        return "loaded" if self._asr else ("error: " + self._asr_error if self._asr_error else "lazy")

    def stats(self):
        return {"image_db_count": self.img_col.count(),
                "text_db_count": self.txt_db._collection.count(),
                "asr": self.asr_status, "device": self.device,
                "llm": config.LLM_API["model"] if config.LLM_API else "qwen3-1.7b (本地)"}


# ═════════════════════════ 模拟引擎（MOCK） ═════════════════════════
class MockEngine(_Base):
    """不加载模型，但用真实知识库（直接读 sqlite）走一遍检索和清洗流程。
    「大模型」用资料第一句话代替，用来调 APP 和眼镜联调。"""

    def __init__(self):
        print("⚠️  MOCK 模式：未加载任何模型")
        self.docs = {}
        db = config.TXT_DB_PATH / "chroma.sqlite3"
        if db.exists():
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            rows = con.execute("""select a.id, a.string_value, b.string_value from embedding_metadata a
                                  join embedding_metadata b on a.id=b.id
                                  where a.key='name' and b.key='chroma:document' order by a.id""").fetchall()
            for _, name, doc in rows:
                self.docs.setdefault(name, []).append(doc)
        self.names = sorted(self.docs) or ["竹根雕牧童臥牛", "黃楊木雕山水筆筒", "竹雕梅花綬帶", "銅貫耳瓶"]
        self.demo = [n for n in ["竹根雕牧童臥牛", "黃楊木雕山水筆筒", "竹雕梅花綬帶", "黃楊木雕松竹梅筆筒"] if n in self.names] or self.names[:4]

    def recognize(self, pil_image):
        g = pil_image.convert("L").resize((8, 8))
        avg = sum(g.getdata()) / 64
        if avg < 20:
            return self.demo[0], 0.62        # 全黑图 → 模拟「不太确定」
        return self.demo[int(avg) % len(self.demo)], 0.9

    def retrieve(self, artifact, question):
        if artifact in self.docs:
            return qa.clean_context(self.docs[artifact])[:MAX_CONTEXT_CHARS]
        return ""

    def chat(self, messages, temperature=0.3, max_tokens=300):
        user = messages[-1]["content"]
        desc = next((l[3:] for l in user.splitlines() if l.startswith("介绍：")), "")
        first = desc.split("。")[0][:35]
        q = next((l[len("【游客现在问】"):] for l in user.splitlines() if l.startswith("【游客现在问】")), "")
        return (f"【模拟回答】关于“{q}”，资料里没有直接记载。" if q else "【模拟回答】") + f"{first}。"

    def artifact_names(self):
        return [{"name": n, "has_image": n in self.demo} for n in self.names]

    def transcribe(self, audio_bytes: bytes) -> str:
        return "这件文物是什么年代的"

    async def tts_save(self, text, voice, path):
        try:                               # 有网络就用真的 edge-tts，方便试听音色
            await super().tts_save(text, voice, path)
            return
        except Exception:
            pass
        sr = 16000                          # 没网：0.6 秒提示音
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / sr)))
                                   for i in range(int(sr * 0.6))))

    async def synthesize(self, text, persona_key, fmt="mp3") -> Path:
        voice = PERSONAS.get(persona_key, PERSONAS["expert"])["voice"]
        path = config.AUDIO_DIR / f"{uuid.uuid4().hex}.mp3"
        await self.tts_save(text, voice, path)
        with open(path, "rb") as f:
            is_wav = f.read(4) == b"RIFF"
        if is_wav:
            path = path.rename(path.with_suffix(".wav"))
        elif fmt == "wav":
            path = await asyncio.to_thread(to_wav16k, path)
        return path

    def stats(self):
        return {"image_db_count": 0, "text_db_count": sum(map(len, self.docs.values())),
                "asr": "mock", "device": "none", "llm": "mock"}


def create_engine():
    return MockEngine() if config.MOCK else RealEngine()
