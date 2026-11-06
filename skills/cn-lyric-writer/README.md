# 中文写词 skill

WorkBuddy 从 [SKILL.md](SKILL.md) 开始，按其中的步骤写词、审稿，再用随仓库提供的脚本检查韵脚和读音。

- [风格指纹](references/style-fingerprints.md) 提供曲风分析和创作参考。
- [样章与技法](references/worked-examples.md) 说明如何修改意象、段落和韵脚。
- 从仓库根目录运行 `.venv/bin/python skills/cn-lyric-writer/scripts/check_lyrics.py 歌词文件.txt` 可检查单首文本。夜间批次使用 [完整清单校验](../../nightly/AUTOMATION.md)。

夜间程序默认使用此目录及其字表。如需自定义检查器，可显式设置 `YUE2_LYRIC_SKILL`。
