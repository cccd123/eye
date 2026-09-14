# -*- coding: utf-8 -*-
"""
test_tts.py
用Edge-TTS把文字转成语音文件
Edge-TTS是微软提供的免费在线TTS，中文效果很好
不需要本地模型，联网调用即可（后期可换成本地CosyVoice）
"""
import asyncio
import edge_tts

# 不同人设用不同声音
VOICES = {
    "专家":  "zh-CN-YunxiNeural",    # 成熟男声，适合专业讲解
    "儿童":  "zh-CN-XiaoxiaoNeural", # 活泼女声，适合儿童模式
    "社畜":  "zh-CN-YunjianNeural",  # 轻松男声，适合社畜模式
}

async def text_to_speech(text, persona="专家", output_file="D:/output.mp3"):
    voice = VOICES.get(persona, "zh-CN-YunxiNeural")
    tts = edge_tts.Communicate(text=text, voice=voice, rate="+0%")
    await tts.save(output_file)
    print(f"已生成：{output_file}（人设：{persona}，声音：{voice}）")

# 测试三种人设
texts = {
    "专家": "竹根雕牧童臥牛采用竹根圆雕技法，以高超工艺呈现人牛互动，为清代十八世纪竹雕精品。",
    "儿童": "小朋友你看！这个小牧童骑在牛背上，手里还牵着绳子，是不是超级可爱？你猜猜他们要去哪里呀？",
    "社畜": "竹根雕，清朝，圆雕工艺，技法纯熟。核心亮点：用竹子纹理表现牛毛质感，性价比极高的宫廷收藏。"
}

async def main():
    for persona, text in texts.items():
        await text_to_speech(text, persona, f"D:/output_{persona}.mp3")
        print(f"  文字：{text[:30]}...")

asyncio.run(main())
print("\n完成！在D盘根目录查看三个mp3文件")