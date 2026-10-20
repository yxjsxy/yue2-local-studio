# 逛逛GitHub · YuE2 本地复现

## 从 GitHub 安装

此仓库是 Apple Silicon 本地创作工作台，基于官方 YuE2 构建，并非官方模型仓库。已在 M4 Pro / 24 GiB 上验证短歌生成；4 分钟整曲、个人声音转换尚未实现或验收。风格描述是软条件，不能保证男低音、乐器等要求逐项生效。

```sh
git clone --recurse-submodules https://github.com/yxjsxy/yue2-local-studio.git
cd yue2-local-studio
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python download_models.py
.venv/bin/python studio_ctl.py start
```

如克隆时未加子模块选项，先执行 `git submodule update --init --recursive`。默认使用本机端口 8766，需保持空闲。模型下载约 7.8 GB（十进制），另需环境和生成结果空间；版本和权重 SHA-256 固定在 `models.lock.json`。下载脚本不执行模型仓库中的 Python 文件。后续生成使用本地模型，不需要云端 API 密钥。首次安装与下载需要网络；大模型下载可能耗时较长。

仓库包含工作台、生成/转谱脚本、原始测试输入、四段固定示例、曲谱修改与精选验证记录。`upstream/` 是固定提交的 Git 子模块。模型、虚拟环境、完整运行日志、个人输入和生成历史均不上传。报告中的完整 `outputs/` 和其他 `evidence/` 路径为原机运行记录，不保证在 GitHub 中存在。`public/` 内的四段固定示例可直接使用下方命令试听，无需下载模型：

```sh
python3 -m http.server 8765 --bind 127.0.0.1 --directory public
```

交互工作台只需 YuE2-3B 和 YuE2-Vae。`transcribe.py` 属于可选高级实验，还需独立的 SheetSage2/MERT2 模型与转谱环境；上面的快速安装不会安装这部分。请按固定子模块内的 `skills/yue2-music/references/models-and-setup.md` 配置，勿混装到生成环境。

代码、模型和样例的授权分别适用：官方代码许可见 `upstream/LICENSE`；模型及生成内容使用条件见 `upstream/MODEL_LICENSE`。本仓库不重新授权上游模型或第三方代码。


本项目使用官方 YuE2 权重真实生成音频，在 Apple M4 Pro / 24 GiB / macOS 上验证歌曲生成、和声编辑和录音转谱流程。结果与限制见 `REPRODUCTION_REPORT.md`，逐项事前评估见 `evidence/preflight-assessment.json`。

## 自定义歌词体验服务（追加交付）

打开 http://127.0.0.1:8766/ ：填写2至6行歌词（最多160字符），选择歌曲或说唱，填写声音、乐器、情绪与速度，点击“生成我的歌曲”。生成完成后可试听、下载音频、查看曲谱，并用原输入继续修改。原固定样例比较页仍在 http://127.0.0.1:8765/ 。

每次真实调用本地 YuE2，生成上限约48秒，不保证精确长度；一次只运行一个任务，重复提交会提示等待。可取消；任务最长20分钟后停止。页面只在本机可用，重启电脑后需重新启动。歌词、任务、音频与日志位于本目录 `studio-data/<任务ID>/`。

```sh
.venv/bin/python studio_ctl.py start
.venv/bin/python studio_ctl.py status
.venv/bin/python studio_ctl.py stop
```

停止服务会停止它正在执行的生成任务。只读试听服务仍由 `serve.py` 管理。`studio_server.py`负责真实任务运行、输入校验、并发限制和音频访问；`studio-ui/`为中文交互界面。

## 文件

- `upstream/`：官方仓库快照，commit `1647252d68b70cbead046ffd3f7caf036dde47ee`。
- `models/`：固定版本的 YuE2-3B、YuE2-Vae、SheetSage2 与 MERT2；不会自动下载新版本。
- `.venv/`：歌曲生成 Python 3.11 环境。
- `.venv-sheetsage/`：转谱独立环境，避免 Torch/Transformers 版本冲突。
- `requests/`：真实风格、歌词、随机种子；`edits/`：修改前后曲谱校验。
- `outputs/`：原始音频、曲谱、tokens、latents、配置、耗时与信号检查。
- `public/`：只包含可试听的结果白名单，不包含权重和内部环境。
- `evidence/`：来源、环境、锁定依赖、模型校验及每次执行日志。

## 复跑

先进入本目录。每次必须使用新的输出目录名，避免覆盖原始证据。以下命令均串行执行，模型加载期间可能发生大量内存交换。

```sh
.venv/bin/python run_bounded.py --timeout 1230 --log evidence/new-song.log -- .venv/bin/python -u run_song.py --request requests/chinese.json --output outputs/new-song --max-seconds 1200
.venv/bin/python run_bounded.py --timeout 1230 --log evidence/new-harmony.log -- .venv/bin/python -u run_song.py --request requests/harmony.json --abc-file edits/harmony.abc --output outputs/new-harmony --max-seconds 1200
.venv/bin/python run_bounded.py --timeout 1230 --log evidence/new-rap.log -- .venv/bin/python -u run_song.py --request requests/rap.json --output outputs/new-rap --max-seconds 1200
.venv/bin/python run_bounded.py --timeout 1230 --log evidence/new-transcription.log -- .venv-sheetsage/bin/python -u transcribe.py outputs/chinese-baseline/audio.flac outputs/new-transcription
```

歌曲生成使用 MPS、BF16、torch-eager、AR 卸载与分块 VAE；解码在 CPU 上运行。没有关闭 MPS 内存保护。默认最多 1000 个语义 token，适合约 40 秒以内的短片段；爵士改编重跑显式使用 1200 个 token 上限（约48秒）；这不是完整长曲压力测试。不同输入若触发截断，必须查看 `signal_metrics.json`，不能把截断输出当完整歌曲。

## 本地试听

```sh
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 --directory public
```

打开 http://127.0.0.1:8765 。这是本机音频试听与结果比较页面，模型生成仍通过上面的命令执行。服务退出或电脑休眠后链接可能不可用。

## 验证边界与授权

音频时长、采样率、有限值、振幅与模型截断状态是客观检查，不代表歌词发音、审美或旋律保持已经通过听感验收。和声修改只在曲谱层验证旋律/节奏不变；模型仍会重生成整段演奏。

官方代码为 Apache 2.0，模型权重另有 CC BY-NC 4.0 及 YuE2 创作者附加许可。公司商用需要向模型方取得适用授权；SheetSage2/MERT2 的独立许可也需分别核对。详见 `upstream/MODEL_LICENSE` 和各模型目录中的许可文件。所有歌词为本次原创测试输入。

## 爵士改编复跑

转谱完成后，可将其作为新的输入。首次1000 token版本与提高上限到1200 token的复核版本均未截断，两次音频哈希完全一致。以下为复核参数：

```sh
.venv/bin/python run_bounded.py --timeout 1230 --log evidence/new-cover.log -- .venv/bin/python -u run_song.py --request requests/cover.json --abc-file outputs/transcribed-baseline/transcription/score.abc --output outputs/new-cover --max-seconds 1200 --max-semantic-tokens 1200
```

## 后台试听服务管理

```sh
.venv/bin/python serve.py start
.venv/bin/python serve.py status
.venv/bin/python serve.py stop
```

脚本校验进程身份后才停止本项目的服务。端口自动选择8765—8784中的空闲项，实际地址见 `evidence/server.json`。服务只读取 `public/`，不运行模型。
