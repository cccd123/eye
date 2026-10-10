# -*- coding: utf-8 -*-
"""
qa.py - 讲解质量的关键都在这里（纯函数，不依赖模型，可单独测试）

原来的问题：
  1. 知识库原文带 [era] [src] 网址等标签，直接喂给 1.7B 小模型，它经常整段照抄 →
     答非所问，而且把英文网址念出来
  2. 追问不带上下文，「它是谁做的」里的「它」模型不知道指什么
  3. 资料里没有的信息（比如作者），小模型要么照抄、要么瞎编

现在的流程：
  clean_context()   把资料整理成「名称/年代/尺寸/介绍」的中文，删掉网址和编号，繁体转简体
  build_messages()  人设风格 + 硬规则 + 一组示范问答（小模型很吃示范）+ 最近两轮对话
  is_echo()         检查回答是不是在照抄资料，是的话换更严格的提示重试
  finalize()        去掉标签/网址/英文/Markdown，截到字数上限，得到适合朗读的文字
"""
import re
from difflib import SequenceMatcher

try:                                   # 繁体 → 简体（pip install zhconv，没有也能跑）
    from zhconv import convert as _zhconv
    def to_simplified(s): return _zhconv(s, "zh-cn")
except Exception:                      # pragma: no cover
    def to_simplified(s): return s

# 台湾故宫用语 → 大陆习惯说法（朗读更自然）
_TERMS = [("西元", "公元"), ("公分", "厘米"), ("公釐", "毫米"), ("紀", "纪")]
_LABELS = {"name": "名称", "era": "年代", "size": "尺寸", "desc": "介绍"}

URL_RE = re.compile(r"https?://\S+|www\.\S+")
TAG_RE = re.compile(r"\[(name|id|era|size|desc|src)\]", re.I)


def clean_context(chunks):
    """chunks: 同一件或多件文物的原始文本块列表 → 整理好的中文资料"""
    out = []
    for raw in chunks:
        for line in raw.splitlines():
            line = line.strip()
            m = re.match(r"\[(\w+)\](.*)", line)
            if m:
                key, val = m.group(1).lower(), m.group(2).strip()
                if key in ("id", "src") or not val:
                    continue
                line = f"{_LABELS.get(key, key)}：{val}"
            line = URL_RE.sub("", line)
            if "National Palace Museum" in line or not line:
                continue
            out.append(line)
    text = "\n".join(out)
    for a, b in _TERMS:
        text = text.replace(a, b)
    return to_simplified(text)


EXAMPLE = [
    {"role": "user", "content":
        "【展品资料】\n名称：青玉雕荷叶洗\n年代：清，公元18世纪\n尺寸：长12厘米\n"
        "介绍：以青玉雕成一片翻卷的荷叶，叶脉清晰，叶边立雕一只小青蛙，用来盛水洗笔。\n\n"
        "【游客现在问】这是谁做的？"},
    {"role": "assistant", "content":
        "资料里没有记载作者是谁。能确定的是，它是清代18世纪的玉器，用青玉雕成卷起来的荷叶，叶边还趴着一只小青蛙，是文人洗毛笔用的。"},
]

RULES = (
    "你是博物馆智能导览眼镜里的讲解员，游客正站在展品前，通过耳机听你说话。\n"
    "必须遵守：\n"
    "1. 第一句话就直接回答游客的问题，然后最多补充一两句相关内容。\n"
    "2. 只能用【展品资料】里的事实。资料里没有的，就说“资料里没有记载……”，再讲资料里有的相关内容。绝不编造人名、年代、数字。\n"
    "3. 用自己的话口语化地讲，不要照抄资料原文，不要念“名称、年代、尺寸”这些标签，不要说网址和英文。\n"
    "4. 用简体中文，不用Markdown，不用括号。全文不超过{limit}字。\n"
    "讲解风格：{style}\n/no_think"
)


def build_messages(persona, artifact, context, question, history=(), strict=False):
    """persona: personas.PERSONAS 里的一项；history: [(问, 答), ...] 最近几轮"""
    system = RULES.format(limit=persona["max_chars"], style=persona["style"])
    parts = [f"【展品资料】\n{context}" if context else "【展品资料】\n（没有找到相关资料）"]
    if not artifact:
        parts.append("（还不确定游客在看哪一件展品，上面是按问题检索到的可能相关资料）")
    if history:
        parts.append("【之前的对话】\n" + "\n".join(f"游客：{q}\n讲解员：{a}" for q, a in history))
    q = question.strip() if question else ""
    parts.append(f"【游客现在问】{q}" if q else f"【游客的请求】请介绍一下这件展品：{artifact}")
    if strict:
        parts.append("注意：不要复述资料原文，直接用一两句自己的话回答上面的问题。")
    return [{"role": "system", "content": system}, *EXAMPLE,
            {"role": "user", "content": "\n\n".join(parts)}]


def longest_copy(answer, context):
    if not answer or not context:
        return 0
    answer = to_simplified(answer)
    m = SequenceMatcher(None, answer, context, autojunk=False).find_longest_match(0, len(answer), 0, len(context))
    return m.size


def is_echo(answer, context):
    """回答里有标签/网址，或者连续照抄资料超过 40 个字 → 认为是在念资料"""
    if TAG_RE.search(answer) or URL_RE.search(answer) or "名称：" in answer:
        return True
    return longest_copy(answer, context) > 40


def finalize(text, max_chars=200):
    """模型输出 → 屏幕显示 & 朗读用的干净中文"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"</?think>", "", text)
    text = URL_RE.sub("", text)
    text = TAG_RE.sub("", text)
    text = re.sub(r"(名称|年代|尺寸|介绍)：", "", text)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"#{1,6}\s*|[`~>|〈〉]", "", text)
    text = re.sub(r"[（(][^）)]{0,30}[）)]", "", text)            # 括号注释/舞台指令
    text = re.sub(r"(CC-BY[\w.-]*|National Palace Museum\s*\w*)", "", text, flags=re.I)
    text = re.sub(r"(?<![A-Za-z])[A-Za-z]{3,}(?![A-Za-z])", "", text)                 # 剩下的英文单词（TTS 会念出英文）
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", text)
    text = re.sub(r"\s+", "", text)
    for a, b in _TERMS:
        text = text.replace(a, b)
    text = to_simplified(text).strip("，、；：")
    if len(text) > max_chars:                                     # 按句号截断，不截半句
        cut = max(text.rfind(p, 0, max_chars) for p in "。！？")
        text = text[:cut + 1] if cut > max_chars // 3 else text[:max_chars] + "。"
    return text


def fallback_answer(artifact, question):
    if question:
        return f"关于“{question}”，这件{artifact or '展品'}的资料里没有找到明确的说法。你可以换个问法，或者让我先整体介绍一下它。"
    return f"这件展品是{artifact}，暂时没能生成讲解，请再试一次。"
