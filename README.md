# LynxAct Coach — 实时战术分析 demo(lablab AI Factory 黑客松作品)

> 比赛片段 → (Speechmatics 实时转录 + LynxMove CV 融合标注)→ Claude 流式战术事件卡 + 全场报告。
> LynxAct Phase 1.5(战术报告层)的 7 天冲刺版。D1 状态:骨架 + 预烘焙回放 + SSE 事件卡流。

LynxAct Coach turns a football clip into a **live tactical analysis stream**:
commentary transcription plus computer-vision annotations from the LynxMove
fusion pipeline are fed to Claude, which streams tactical event cards and a
full-match report in real time.

## 快速开始 / Quickstart

```bash
cp .env.example .env      # 默认 replay 模式,零 key 可跑
python3 app.py            # http://127.0.0.1:6901
```

- `COACH_MODE=replay`(默认):回放预烘焙数据,断网/无 key 也能完整演示。
- `COACH_MODE=live`(D2 接):走 OpenAI 兼容端点(本地 CPA 或赛方 Claude key)实时生成。

## 架构

| 文件 | 作用 |
|------|------|
| `app.py` | 路由:/ 选片、/coach/<id> 实时页、/video/<f> Range 流、/api/stream/<id> SSE |
| `coach/clips.py` | clip 发现(VAULT_ROOT 文件名=金标)+ 预烘焙注册表 |
| `coach/stream.py` | SSE 时间线回放引擎(转录流 + 事件卡交错,REPLAY_SPEED 变速) |
| `coach/claude.py` | live 模式:prompt 构建 + 流式调用(D2 接线) |
| `coach/video.py` | mp4 Range 拖进度流 |
| `data/baked/*.json` | 预烘焙:转录 + 事件卡 + CV 融合上下文 |

致谢 / Thanks: Linux.do 社区 · OpenClaw 生态 · lablab.ai
