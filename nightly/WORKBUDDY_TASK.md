接管既有音乐流水线（不是重新开发项目），工作目录为本任务所配置的仓库根目录（包含 nightly/pipeline.py）。歌词由你（WorkBuddy）当前所选、无需VPN即可正常调用的模型创作；禁止调用 Codex、Codex CLI 或 OpenAI API。使用项目已有 .venv Python 环境、模型、队列和播放器。不联网搜索、不重新下载模型、不安装替代方案、不重启或中断正在生成歌曲的服务。

执行流程：
1. 阅读 nightly/AUTOMATION.md，以及 skills/cn-lyric-writer/SKILL.md 和该 skill 所需的风格指纹参考。调度由 WorkBuddy 承担：文档中关于旧 Codex 排程状态、GPT 创作额度的描述，改为本任务状态和实际所选模型。不要因为 Codex 排程暂停而拒绝执行，也不要修改或恢复 Codex 排程。其余生成、存储和安全规则保留。
2. 获取 Asia/Shanghai 当前日期，以日期为幂等键。检查 nightly-data/YYYY-MM-DD/manifest.json（YYYY-MM-DD 为真实日期）：已有清单绝不覆盖、不重新写词；已完成则直接退出；正在运行则不重复启动；未完成且 worker 已退出时按既有规则恢复。存在 STOP 文件时不要自行移除。仅从近 7 天清单读取标题和一句主题以避重复，不读历史聊天和大日志。
3. 若当天没有清单，一次集中创作 8 首不同的完整原创中文歌曲：温柔民谣、轻松爵士、中文说唱、明亮流行，每种各一首男声目标、一首女声目标，多抒发对生活小细节的热爱、对人生各种具体境况的感受或者对爱情的细腻体会。严格按 skill 写作及审稿：每首 40—56 行，每行 7—14 个汉字；具体场景、单一副歌钩子、桥段推进、自然收尾；重复副歌完整展开；先设计韵脚，避免高风险读音。英文音乐指令明确 Mandarin Chinese、唯一主唱性别与音区、乐器、BPM、情绪及演唱方式。男女声只标注目标，未经听验不能称已符合。
4. 按 AUTOMATION.md 的 JSON 结构保存当天 manifest。lyric_author 如实记录 WorkBuddy 及实际模型名称；模型名称未知就注明未知，不要沿用"GPT in Codex"。保存真实 fingerprint 和 editorial_review。folk/jazz/pop 使用 full，rap 使用 off；target_seconds 为 210；种子由日期和 slot 稳定生成。
5. 在项目目录执行：
.venv/bin/python nightly/pipeline.py validate --manifest nightly-data/YYYY-MM-DD/manifest.json
检查失败时，只读失败条目及对应 audit，集中修正，最多 3 轮；不得改检查器或降低要求绕过。全部通过后执行：
.venv/bin/python nightly/pipeline.py start --manifest nightly-data/YYYY-MM-DD/manifest.json
6. 启动几秒后只检查一次 state.json 和必要的 worker 状态。确认后台队列运行后即结束本次执行。不要在音频生成期间逐首调用模型、持续轮询或长时间等候。后台 Python 负责串行生成、固定次数重试、进度记录及 REPORT.md。没有真实完成证据时，只能报告已入队，不能报告 8 首已经生成。
7. 保留完整歌曲预算：360 秒音频预算、8192 曲谱 tokens，单曲 90 分钟、整批 14 小时保护。不得截短、循环拼接、补静音或变速凑时长。实际时长如实记录。
8. 每首成功后自动进入现有个人播放器（http://127.0.0.1:8766/library）。沿用现有歌词展示、真实进度、单曲循环、随机循环、收藏及"我喜欢的歌"。保留所有历史歌曲和删除记录，不得恢复用户已删除的歌曲、覆盖收藏或自动清理旧歌。
9. 沿用现有 320kbps MP3 转换流程。只有完整校验通过后才删除原 FLAC；失败保留原件。下载名为"小Rei-歌名.mp3"。磁盘不足按原规则停止，不要删除历史歌曲腾空间。
10. 正常后台生成期间不反复通知。每次启动时简要汇报上一批结果；本批写入本地状态和 REPORT.md。区分"歌词检查通过""后台运行""音频已完成""实际听感验收"。启动失败或需要用户处理时，报告具体原因。
