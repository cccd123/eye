# -*- coding: utf-8 -*-
"""
config.py - 所有路径和参数集中在这里，可用环境变量覆盖。

BASE（放 models/ 和 vector_db/ 的目录）自动查找：
  1. 环境变量 MUSEUM_BASE
  2. 代码所在目录的上一级（正常情况：D:\\MuseumGlasses）
  3. 再上一级（解压时多套了一层文件夹，如 D:\\MuseumGlasses\\MuseumGlasses-v2）
  4. D:\\MuseumGlasses
"""
import os, tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent.parent      # app/ glasses/ server/ 所在目录


def _find_base():
    if os.environ.get("MUSEUM_BASE"):
        return Path(os.environ["MUSEUM_BASE"])
    for cand in (CODE_ROOT, CODE_ROOT.parent, Path(r"D:\MuseumGlasses")):
        if (cand / "models").is_dir() and (cand / "vector_db").is_dir():
            return cand
    return CODE_ROOT


BASE = _find_base()

CLIP_PATH   = Path(os.environ.get("MUSEUM_CLIP", BASE / "models" / "clip-vit-base"))
BGE_PATH    = Path(os.environ.get("MUSEUM_BGE", BASE / "models" / "bge-m3"))
LLM_PATH    = Path(os.environ.get("MUSEUM_LLM", BASE / "models" / "qwen3-1.7b" / "Qwen3-1.7B-Q4_K_M.gguf"))
IMG_DB_PATH = Path(os.environ.get("MUSEUM_IMG_DB", BASE / "vector_db" / "images"))
TXT_DB_PATH = Path(os.environ.get("MUSEUM_TXT_DB", BASE / "vector_db" / "sculptures"))

SERVER_DIR = Path(__file__).resolve().parent
APP_DIR    = CODE_ROOT / "app"


def _writable_dir(preferred: Path, name: str) -> Path:
    """优先用 server/ 下的目录；没有写权限（被杀毒软件/受控文件夹拦截）就退到系统临时目录。"""
    for d in (preferred, Path(tempfile.gettempdir()) / "MuseumGlasses" / name):
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".write_test"
            probe.write_text("ok")
            probe.unlink()
            return d
        except OSError:
            continue
    raise RuntimeError(f"找不到可写的目录来存放 {name}")


AUDIO_DIR   = _writable_dir(SERVER_DIR / "audio_cache", "audio_cache")
CAPTURE_DIR = _writable_dir(SERVER_DIR / "captures", "captures")


def _can_write_db(d: Path) -> bool:
    """Chroma 即使只读也要在库目录里写锁文件/日志，并以写方式打开 chroma.sqlite3。"""
    try:
        probe = d / ".write_test"
        probe.write_text("ok"); probe.unlink()
        db = d / "chroma.sqlite3"
        if db.exists():
            with open(db, "ab"):
                pass
        for sub in d.iterdir():                       # HNSW 索引文件也要能写
            if sub.is_dir():
                p = sub / ".write_test"; p.write_text("ok"); p.unlink()
        return True
    except OSError:
        return False


def _usable_db(src: Path, name: str) -> Path:
    """向量库目录没有写权限时，复制一份到用户目录再用（只复制一次，源库更新后会重新复制）。"""
    if not src.exists() or _can_write_db(src):
        return src
    import shutil
    dst = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "MuseumGlasses" / "vector_db" / name
    src_mtime = max(p.stat().st_mtime for p in src.rglob("*"))
    if not dst.exists() or max(p.stat().st_mtime for p in dst.rglob("*")) < src_mtime:
        print(f"⚠️ {src} 没有写入权限，复制一份到 {dst} 使用")
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
        for p in dst.rglob("*"):                      # 去掉从源文件带过来的「只读」属性
            try:
                os.chmod(p, 0o777 if p.is_dir() else 0o666)
            except OSError:
                pass
    return dst


IMG_DB_PATH = _usable_db(IMG_DB_PATH, "images")
TXT_DB_PATH = _usable_db(TXT_DB_PATH, "sculptures")

# MUSEUM_MOCK=1：不加载任何模型，返回假数据。用来在没有显卡/模型的电脑上调 APP 和眼镜联调。
MOCK = os.environ.get("MUSEUM_MOCK", "0") == "1"

# GPU 层数：RTX 显卡用 28；纯 CPU 设为 0
LLM_GPU_LAYERS = int(os.environ.get("MUSEUM_LLM_GPU_LAYERS", "28"))

# 语音识别（ASR）：faster-whisper 模型名或本地路径。
#   small = 中文够用、RTX 上 <1 秒；CPU 上建议 base
#   国内下载慢：先 set HF_ENDPOINT=https://hf-mirror.com
ASR_MODEL  = os.environ.get("MUSEUM_ASR_MODEL", "small")
ASR_DEVICE = os.environ.get("MUSEUM_ASR_DEVICE", "auto")   # auto / cuda / cpu

# ── 可选：在线大模型（比本地 1.7B 聪明很多，追问效果好）────────────
# 新建 server/llm_api.json（不会上传 GitHub），内容例如 DeepSeek：
#   {"base_url": "https://api.deepseek.com", "api_key": "sk-...", "model": "deepseek-chat"}
# 没有这个文件就用本地 Qwen3-1.7B。
def _load_llm_api():
    import json
    f = SERVER_DIR / "llm_api.json"
    if os.environ.get("MUSEUM_LLM_API_KEY"):
        return {"base_url": os.environ.get("MUSEUM_LLM_API_BASE", "https://api.deepseek.com"),
                "api_key": os.environ["MUSEUM_LLM_API_KEY"],
                "model": os.environ.get("MUSEUM_LLM_API_MODEL", "deepseek-chat")}
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8-sig"))
            if d.get("api_key") and not d["api_key"].startswith("sk-在这里"):
                return d
        except Exception as e:
            print("⚠️ llm_api.json 格式错误，改用本地模型：", e)
    return None


LLM_API = _load_llm_api()

CONFIDENCE_THRESHOLD = float(os.environ.get("MUSEUM_CONF", "0.75"))
GUESS_THRESHOLD = float(os.environ.get("MUSEUM_GUESS", "0.55"))   # 介于两者之间：给出「可能是…」让用户确认
CACHE_KEEP = 200          # audio_cache / captures 各保留最近多少个文件
HISTORY_KEEP = 50         # APP 里显示的历史条数


def describe():
    print(f"代码目录：{CODE_ROOT}")
    print(f"模型目录：{BASE}" + ("" if (BASE / 'models').is_dir() else "  ⚠️ 这里没有 models 文件夹"))
    print(f"向量库：  {IMG_DB_PATH.parent}" + ("" if IMG_DB_PATH.parent == BASE / "vector_db" else "（原目录没写权限，用的是副本）"))
    print(f"音频缓存：{AUDIO_DIR}")
