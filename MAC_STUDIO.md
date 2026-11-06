# Mac Studio 部署与迁移

代码更新于 2026-11-06：包含歌曲库程序、WorkBuddy 写词 skill 和每日八首串行队列。GitHub 不包含个人歌曲、歌词批次、模型权重或 WorkBuddy 账号。

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

## 用 WorkBuddy 接管午夜写歌

1. 在新机安装并登录 WorkBuddy，确认所选模型在平时使用的网络下可用。工作目录选刚 clone 的仓库根目录，里面应能看到 `nightly/pipeline.py` 和 `.venv/bin/python`。完整[写词 skill](skills/cn-lyric-writer/SKILL.md)、参考文件、检查器和字表都在仓库内，任务直接按相对路径读取。
2. 如已私下复制模型，跳过上方下载步骤，运行 `.venv/bin/python download_models.py --verify-only`。启动服务后用 `curl -fsS -o /dev/null http://127.0.0.1:8766/library` 确认播放器可达；若已带回历史数据，试听一首旧歌，核对收藏与日期。选一个真实的已有批次，运行下面的命令做静态校验（会刷新该批校验报告，须在副本或新机执行）：

   ```sh
   read -r 'batch?输入已有批次的 YYYY-MM-DD 日期：'   # macOS zsh
   .venv/bin/python nightly/pipeline.py validate --manifest "nightly-data/$batch/manifest.json"
   ```

3. 正式交接时，先暂停旧 MacBook 上 WorkBuddy 的“午夜写歌”，等在跑的批次收尾，再补齐最后一次数据。若 WorkBuddy 账号在新机已有同名任务，先检查其工作目录与生效设备，避免两台机器同时触发。Codex 的“午夜八首”保持暂停。
4. 在新机 WorkBuddy 的定时任务中配置“午夜写歌”。将触发时间设为**每天北京时间 00:00**，核对界面显示的下次触发时间。沿用旧机“快速”模型设置和中等推理强度（界面提供时）；实际可用模型以新机账号为准。允许任务读写此项目并运行其中的 Python 命令。用下面的命令复制[完整任务指令](nightly/WORKBUDDY_TASK.md)，粘贴到任务正文并保存，再启用新机任务。旧机 WorkBuddy 保持停用。

   ```sh
   pbcopy < nightly/WORKBUDDY_TASK.md
   ```

5. 保持 Mac Studio 开机、用户已登录、WorkBuddy 可执行定时任务，避免午夜前自动睡眠。首次触发先核对当天的 `manifest.json`、`validation.json` 与 `state.json`。如果有同日已完成批次，应直接跳过；如果有 `STOP`，保留停止状态。等后台队列结束，再查同目录的 `REPORT.md` 和播放器实际可播放的歌曲。真正的写词模型名称、八首产出及听感以新机当晚记录和试听为准。
