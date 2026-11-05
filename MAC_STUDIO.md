# Mac Studio 部署与迁移

代码更新于 2026-11-06：包含本地歌曲库、收藏/删除、MP3 处理和每日八首串行队列。GitHub 不包含个人歌曲、歌词批次、模型权重或 WorkBuddy 账号。

## 新机安装

```sh
brew install python@3.11 ffmpeg
git clone --recurse-submodules https://github.com/yxjsxy/yue2-local-studio.git
cd yue2-local-studio
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python download_models.py
.venv/bin/python studio_ctl.py start
```

入口为 `http://127.0.0.1:8766/library`。不要复制旧机器虚拟环境；新机重建。模型可以使用下载脚本校验后下载，也可通过私有传输复制旧机 `models/YuE2-3B` 和 `models/YuE2-Vae` 后核对锁文件中的 SHA-256。

## 旧机数据

旧项目：`~/Documents/vibe-coding/2026-10-20-yue2-music`。

- `studio-data/`：实际音频、输入、生成结果与收藏/删除状态。
- `nightly-data/`：每日清单、已完成槽位、队列进度、停止和删除记录。
- `outputs/`、`public/`：如需保留旧机本地样例和早期结果，单独迁移。仓库原有公开示例不是私人歌曲全集。

切换时等待正在生成的任务完成或明确停止后再复制。保留 `STOP`、删除记录和完整元数据；不要仅复制 MP3 后期待队列去重与收藏状态完整恢复。不要迁移旧 PID、进程锁和启动状态文件。

## 午夜写歌

旧机排程目前由 WorkBuddy 的“午夜写歌”任务承担。clone 不会自动迁移 WorkBuddy 定时任务，也不会自动调用语言模型写词。

仓库包含 `nightly/AUTOMATION.md` 工作流与可移植的静态歌词检查器。风格指纹和个人创作 skill 仍需在新机单独安装；可设置 `YUE2_LYRIC_SKILL` 指向完整 skill 目录，未设置时检查器使用仓库自带版本。

新机先验证现有 manifest，再启动队列：

```sh
.venv/bin/python nightly/pipeline.py validate --manifest nightly-data/YYYY-MM-DD/manifest.json
.venv/bin/python nightly/pipeline.py start --manifest nightly-data/YYYY-MM-DD/manifest.json
```

重新建立北京时间 00:00 排程前，先停用旧机对应排程，避免双机重复生成。新机单曲与完整夜间批次尚需实测，本次代码上传没有启动音频生成。
