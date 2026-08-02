# LynxAct Coach — 实时战术分析 demo(lablab AI Factory 黑客松作品)

> 比赛片段 → (Speechmatics 实时转录 + LynxMove CV 融合标注)→ LLM 流式战术事件卡 + 全场报告 + PNG 分享卡。
> LynxAct Phase 1.5(战术报告层)的 7 天冲刺版。状态:D1–D5 全部落地,replay/live 双模式实测通过。

LynxAct Coach turns a football clip into a **live tactical analysis stream**:
commentary transcription plus computer-vision annotations from the LynxMove
fusion pipeline are fed to an LLM, which streams tactical event cards and a
full-match report in real time.

## 功能 / Features

- **三栏实时页**:视频 + 解说流 + 战术事件卡(reception/setup/dribble/burst/finish,1–10 评分)
- **CV 融合 grounding**:三源加权投票(2×VLM + player-prior)注入 prompt;无证据的卡诚实标 ⚠ speculative
- **live 模式**:6s 窗口化模型生成,严格 JSON Lines 解析 + 整窗重试;任意异常回退 replay,演示不死
- **上传通道**:拖入 mp4 → ffprobe 时长 → ffmpeg 抽 16k 音轨 → Speechmatics 实时转录 → live 分析
- **全场报告**:live=模型生成 markdown;replay=零成本模板(时间线/高光/事件分布/教练要点)
- **PNG 导出**:一键下载 Top 3 时刻品牌分享卡(客户端 canvas,零依赖)
- **回放兜底**:`data/baked/` 预烘焙 3 条金标 clip,断网无 key 也能完整演示

## 快速开始 / Quickstart

```bash
cp .env.example .env      # 默认 replay 模式,零 key 可跑
python3 app.py            # http://127.0.0.1:6901
```

- `COACH_MODE=replay`(默认):回放预烘焙数据,断网/无 key 也能完整演示。
- `COACH_MODE=live`:走 OpenAI 兼容端点(`COACH_API_BASE/KEY/MODEL`),窗口化实时生成。
- 上传件的实时转录需 `SPEECHMATICS_API_KEY`(Speechmatics Real-time API)。

## 架构

| 文件 | 作用 |
|------|------|
| `app.py` | 路由:/ 选片、/coach/<id> 实时页、/video/<f> Range 流、/api/stream SSE、/api/report、/api/upload |
| `coach/clips.py` | clip 发现(VAULT_ROOT 文件名=金标)+ 预烘焙 + 上传注册表 |
| `coach/stream.py` | SSE 时间线回放引擎(转录流 + 事件卡交错,REPLAY_SPEED 变速) |
| `coach/claude.py` | live 引擎:6s 窗口 prompt + JSON Lines 解析 + grounding/speculative + 持久化 |
| `coach/speechmatics.py` | Speechmatics Real-time 客户端(wav 按真实语速推流,句读出窗) |
| `coach/report.py` | 报告引擎:模板(零模型)/ live(模型 markdown)双模式 |
| `coach/video.py` | mp4 Range 拖进度流 |
| `data/baked/*.json` | 预烘焙:转录 + 事件卡 + CV 融合上下文(3 条金标 clip) |

致谢 / Thanks: Linux.do 社区 · OpenClaw 生态 · lablab.ai · Speechmatics
