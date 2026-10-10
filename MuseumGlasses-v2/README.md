# 观物 · 博物馆智能讲解眼镜

看向展品 → 眼镜拍照 → 识别文物 → RAG 检索知识库 → 按人设生成讲解 → 眼镜播放语音；长按按键可以语音追问。

```
 眼镜（全志 V821）──Wi-Fi──▶ 电脑：FastAPI ◀──Wi-Fi── 手机 APP
 拍照 / 录音 / 放音           CLIP 识别 · BGE-M3 + Chroma 检索     切换人设 · 实时看讲解文字
                              Qwen3-1.7B 生成 · Whisper 听写        手机拍照/语音提问（备用）
                              edge-tts 合成
```

硬件识别结果见 **[docs/HARDWARE.md](docs/HARDWARE.md)**。

## 目录

| 路径 | 内容 |
|---|---|
| `server/` | 后端：`main.py` 接口、`engine.py` 模型、`personas.py` 人设、`config.py` 路径 |
| `app/` | 手机 APP（网页版，由后端直接提供，打开 `http://电脑IP:8000`） |
| `glasses/glasses_client.sh` | 跑在眼镜上的客户端（短按拍照讲解，长按语音追问） |
| `glasses/glasses_sim.py` | 在电脑上模拟眼镜，板子没好之前联调用 |
| `glasses/probe_glasses.bat` | 读取眼镜主板信息，发给 Claude 做适配 |
| `scripts/` | 爬数据、建库、测试脚本（原有） |

## 快速开始

双击 `server\start_server.bat`。它会自动找到装了 torch 的 conda 环境，第一次会自动装新增的包。窗口里会打印三个地址：

| 谁用 | 地址 | 说明 |
|---|---|---|
| 电脑浏览器 | `http://localhost:8000` | 语音提问也能用 |
| 手机 | `https://电脑IP:8443` | 第一次提示「不安全」→ 高级 → 继续访问。必须用这个 https 地址才能按住说话 |
| 眼镜 | `http://电脑IP:8000` | 填到 `glasses_client.sh` 的 SERVER |

手机、眼镜要和电脑连**同一个 Wi-Fi 或手机热点**。`start_mock.bat` 是模拟模式（不加载模型，几秒启动）。

## 讲解变聪明：可选的在线大模型

本地 Qwen3-1.7B 很小，追问复杂问题时能力有限。复制 `server\llm_api.example.json` 为 `llm_api.json`，
填上 DeepSeek（或通义千问、Kimi 等 OpenAI 兼容接口）的 key，重启即可切换；删掉文件就回到本地模型。
不管用哪个模型，都会经过 `qa.py` 的同一套处理：资料清洗、照抄检测、追问记忆、繁转简、去英文。

## 接口

全部接口在 `http://localhost:8000/docs` 有可交互文档。核心是一个：

**`POST /api/ask`**（multipart 表单）—— 眼镜按一次键就调一次

| 字段 | 说明 |
|---|---|
| `image` | 照片（可选）。有 → 先识别文物；没有 → 针对该设备上一次识别的文物追问 |
| `audio` | 录音（可选，wav/webm/m4a 均可），自动转文字当作问题 |
| `question` | 文字问题（可选） |
| `device_id` | 设备编号，默认 `glasses-01` |
| `persona` | 人设 key；不传则用 APP 上设置的 |
| `audio_format` | `mp3`（默认）或 `wav`（16kHz 单声道，眼镜 aplay 直接播） |
| `response` | `json`（默认）或 `audio`（直接返回音频，文字在 `X-Answer` 响应头，方便 shell 脚本） |

识别失败时也会返回一段语音（「没看清，请靠近再拍」），眼镜端不用处理任何错误分支。

其他：`/api/asr`、`/api/device/heartbeat`、`/api/device/command`、`/api/settings`、`/api/history`、`/ws`（APP 实时推送），旧的 `/api/recognize`、`/api/explain`、`/api/speak` 保持不变。

## 这一版改了什么

**v2.1**
- 修复「答非所问 / 照抄资料」：资料先整理成干净中文再给模型；回答若在照抄资料会自动换严格提示重试
- 修复「语音夹英文」：去掉资料里的来源网址和英文，输出统一简体中文
- 追问会记住同一件展品的前两轮对话；换展品自动清空
- 识别不太确定时给出「可能是…」，手机上点「就是它」确认；也可以手动搜索选择展品
- 手机端 HTTPS（8443 端口）→ 可以按住说话提问
- 可选在线大模型（`llm_api.json`）

**v2.0**

- **新增语音提问**（faster-whisper），原来只能文字提问
- **新增 `/api/ask` 一站式接口** + 设备心跳/远程指令 + WebSocket 推送，眼镜和 APP 联动
- **修复 RAG 检索串文物**：原来 `similarity_search` 不按文物名过滤，问「黃楊木雕山水筆筒」可能检索到别的筆筒的资料；现在先按 `name` 精确过滤
- **修复并发卡死**：Qwen/CLIP 是同步调用，原来写在 `async` 接口里会卡住整个服务（APP 心跳、WebSocket 全部停住）；现在放到线程池并加锁
- 路径不再写死 `D:\MuseumGlasses`，换电脑可直接跑；`audio_cache` 自动清理，不再无限增长
- `MUSEUM_MOCK=1` 模拟模式
- `.gitignore`：模型权重、音频缓存、`__pycache__` 不再提交
