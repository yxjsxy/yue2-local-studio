# YuE2 Local Studio

基于 YuE2 的本地音乐创作工作台。在 Apple Silicon Mac 上输入歌词与风格描述，生成歌曲、试听并下载音频，也可以通过命令行使用曲谱进行音乐生成与改编。

本项目提供中文网页界面、任务管理、模型下载校验和 MPS 适配，不是 YuE2 官方模型仓库。安装与模型下载完成后，歌曲生成在本机运行，不需要云端 API 密钥。

## 功能

- **自定义歌词生成**：输入分段歌词，描述声音、乐器、情绪与速度，生成一份连续歌曲。
- **三种原生生成方式**：`full`（旋律与和弦）、`melody`（旋律与自由伴奏）、`off`（直接音频，也可用于歌曲）。
- **时长与运行控制**：选择约1、2、3、3.5或4分钟目标，查看任务状态、取消生成；一次仅运行一个任务。
- **试听与下载**：生成后在网页播放、下载音频，查看曲谱（曲谱模式生成成功时），沿用原输入继续修改。
- **曲谱输入**：命令行支持传入 ABC 曲谱，用于和声编辑或改编实验。
- **本地结果管理**：保存输入、配置、音频与日志；另附四段可直接试听的固定示例。
- **可选录音转谱**：提供独立的转谱脚本，需要额外模型与环境，不包含在快速安装流程中。

个人声音转换/声纹克隆尚未实现。风格描述是软条件，不保证指定音色、乐器或歌手身份逐项实现。

## 环境要求

- Apple Silicon Mac；本仓库的适配与验证环境为 **M4 Pro / 24 GiB / macOS**，不代表其他设备已验证。
- Python **3.11**、Git；安装和首次下载模型需要网络。
- 生成模型下载约 **7.8 GB**（十进制），另需为 Python 环境、下载缓存和生成结果预留空间。
- 工作台使用本机端口 **8766**，启动前需确保端口空闲。

本机记录中，3分19秒和4分39秒的连续歌曲分别耗时约25分钟和40分钟。生成不是实时过程，实际耗时和内存占用随输入变化，可能发生大量内存交换。完整记录见 [长歌验证报告](FULL_SONG_REPORT.md)。

## 快速开始

```sh
git clone --recurse-submodules https://github.com/yxjsxy/yue2-local-studio.git
cd yue2-local-studio
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python download_models.py
.venv/bin/python studio_ctl.py start
```

打开 <http://127.0.0.1:8766/>。

如果克隆时没有拉取子模块，先在仓库目录执行：

```sh
git submodule update --init --recursive
```

模型版本和权重 SHA-256 固定在 [models.lock.json](models.lock.json)。下载脚本会校验权重大小和哈希，不执行模型仓库中的 Python 文件。已有模型可单独校验：

```sh
.venv/bin/python download_models.py --verify-only
```

### 不下载模型，先试听示例

```sh
python3 -m http.server 8765 --bind 127.0.0.1 --directory public
```

打开 <http://127.0.0.1:8765/>，试听并比较仓库中的四段固定示例。此页面只展示已有结果，不生成新歌曲。

## 使用工作台

1. 输入歌词，可用 `[Verse 1]`、`[Chorus]`、`[Bridge]` 等标记分段；也可加载页面中的完整歌词示例。
2. 选择目标时长和生成方式，填写音色、乐器、情绪、速度等风格描述。
3. 点击“生成我的歌曲”，等待本地模型运行；需要时可取消任务。
4. 完成后试听、下载音频；曲谱模式可查看生成的曲谱，使用原输入继续调整。

歌词最多 **4000字符**。运行保护默认 **60分钟**，可在 **10–120分钟**内调整。目标时长不是硬性裁切长度，模型可能提前自然结束。

管理服务：

```sh
.venv/bin/python studio_ctl.py status
.venv/bin/python studio_ctl.py stop
.venv/bin/python studio_ctl.py start
```

停止服务也会停止其正在执行的生成任务。工作台仅在本机可用，重启电脑后需要重新启动。任务输入、音频和日志保存在 `studio-data/<任务ID>/`；启动日志位于 `evidence/studio-server.log`。启动失败时先检查8766端口和该日志。

## 命令行生成

使用仓库内的三分钟示例：

```sh
.venv/bin/python run_song.py --request requests/continuous-180.json --output outputs/my-full-song --target-seconds 180 --max-seconds 3600
```

每次使用新的输出目录，避免覆盖历史结果。`--max-seconds` 是运行超时，不是音频长度。

### 自定义输入

在仓库目录新建 `my-song.json`，例如：

```json
{
  "style": "Mandarin Chinese, acoustic folk pop, warm vocal, acoustic guitar, soft piano, 88 BPM",
  "lyrics": "[Verse 1]\n晚风轻轻吹过街巷\n路灯照亮回家的方向\n\n[Chorus]\n让歌声陪你走过远方\n把今天唱成温柔的光",
  "cot": "full",
  "seed": 42
}
```

```sh
.venv/bin/python run_song.py --request my-song.json --output outputs/my-song --target-seconds 120 --max-seconds 3600
```

`cot` 可选 `full`、`melody`、`off`；`--target-seconds` 支持 `0`（不指定）、`60`、`120`、`180`、`210`、`240`。实际时长由模型生成结果决定。

### 使用 ABC 曲谱

```sh
.venv/bin/python run_song.py --request requests/harmony.json --abc-file edits/harmony.abc --output outputs/my-harmony --max-seconds 3600
```

模型会重新生成整段演奏，并非对已有音频进行无损局部编辑。和声修改的旋律/节奏保持检查只针对曲谱层，不代表输出音频已经通过听感验收。

### 可选：录音转谱

[transcribe.py](transcribe.py) 需要独立的 SheetSage2/MERT2 模型及 `.venv-sheetsage` 环境。请先按照子模块内的 `upstream/skills/yue2-music/references/models-and-setup.md` 配置，避免与生成环境混装：

```sh
.venv-sheetsage/bin/python transcribe.py outputs/my-song/audio.flac outputs/my-transcription
```

## 项目结构

- [studio-ui/](studio-ui/)：中文工作台界面。
- [studio_server.py](studio_server.py)：输入校验、任务运行、并发控制和音频访问。
- [studio_ctl.py](studio_ctl.py)：工作台启动、状态查询与停止。
- [run_song.py](run_song.py)：歌曲生成命令行入口。
- [mps_attention.py](mps_attention.py)：Apple Silicon 长序列 attention 适配。
- [requests/](requests/)：示例歌词与生成参数。
- [public/](public/)：四段固定音频示例及比较页面。
- [edits/](edits/)：ABC 曲谱编辑与校验记录。
- [evidence/](evidence/)：精选验证结果；不包含全部原机日志。
- `upstream/`：固定版本的官方 YuE 仓库子模块。
- `models/`、`outputs/`、`studio-data/`：下载或运行时产生的本地文件，不随仓库上传。

更多实验过程见 [复现报告](REPRODUCTION_REPORT.md) 和 [完整歌曲报告](FULL_SONG_REPORT.md)。报告中的部分 `outputs/`、`evidence/` 路径指向原机记录，不保证随仓库提供。

## 限制与许可

- **长度与上下文**：默认音频预算为9000个语义 token，约360秒上限；曲谱预算默认4096、可扩展到8192，模型上下文为24576。歌词4000字符是输入保护边界，不代表任意4000字歌词都能成功生成。
- **连续生成**：不通过独立片段拼接、循环、补静音或时间拉伸凑长度。VAE分块解码处理的是同一份连续声学表示。曲谱结构校验失败时停止并保留原谱。
- **本机适配**：默认使用显式 FP32 attention 与查询分块，保留全上下文和未量化权重；可用 `--attention native` 做原生实现对照。本机适配不等于官方验证。
- **结果质量**：时长、采样率、振幅、有限值和截断状态检查不等同于歌词发音、旋律、风格或审美验收。请试听后判断，发生截断时检查输出中的 `signal_metrics.json`。
- **授权范围**：上游代码为 Apache 2.0；模型权重另有 CC BY-NC 4.0 及 YuE2 创作者附加许可。商业使用需向模型方取得适用授权；SheetSage2/MERT2 许可需分别核对。详见子模块内 `upstream/LICENSE`、`upstream/MODEL_LICENSE` 及各模型许可文件。本仓库不重新授权上游模型或第三方代码。
