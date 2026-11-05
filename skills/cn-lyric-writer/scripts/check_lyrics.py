#!/usr/bin/env python3
"""歌词发音风险检查（面向 YuE2 等无拼音层的音乐生成模型）。

背景：YuE2 的歌词只经过字符级 BPE，没有拼音/G2P 层，读音靠模型隐式对齐推断。
后果是低频字会被映射到字形或发音相近的高频字（例：獠 liáo 被念成 luǒ），
且生成后无法局部修改。此脚本在写词阶段拦截三类风险。

用法:
    python3 check_lyrics.py lyrics.txt
    python3 check_lyrics.py --text "只好把獠牙藏进吻你的角度"

风险分类:
    [押韵]   逐段韵脚审计 —— 韵部是否统一、出韵行、韵脚字自身是否安全
    [高风险] 非 GB2312 一级常用字（低频字）—— 读音无保障，必须换词
    [搭配漂移] 组合不在常用词表 —— 模型按先验补成高频词（低吠→低垂）
    [少数派读音] 词内多音字必须读低频音 —— 必被高频读音覆盖（知了→知le），必须换词
    [读音确认] 常见多音字 —— 人工读音确认，或换同音高频字
    [叠词]   连续同字 —— 采样重复惩罚可能压制或替换
    [数字/字母] 阿拉伯数字或拉丁字母 —— 建议改写为中文数字
    [超长行] 单行超过 16 字 —— 易吞字/黏字

押韵判定用中华新韵十四韵（严）＋十三辙（宽），韵母由 GB2312 拼音表推导。
韵脚是押韵与读音唯一的共同着力点：韵脚字读错，韵也一起散，所以单独审计。

退出码: 存在高风险问题时为 1，否则为 0。
"""
import argparse
import os
import re
import sys
import unicodedata
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
TABLES = os.path.join(HERE, os.pardir, "references", "char-tables")

MAX_LINE = 16
CJK = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF]")
SECTION = re.compile(r"^\s*\[.+\]\s*$")

# 现代汉语里真正容易读错的多音字（人工筛选，避免全量字典带来的噪声）
POLY_CONFIRM = {
    "行": "xíng/háng", "长": "cháng/zhǎng", "重": "zhòng/chóng", "还": "hái/huán",
    "得": "dé/de/děi", "了": "le/liǎo", "着": "zhe/zháo/zhuó", "和": "hé/huò/hú",
    "差": "chā/chà/chāi", "干": "gān/gàn", "发": "fā/fà", "觉": "jué/jiào",
    "假": "jiǎ/jià", "教": "jiāo/jiào", "结": "jié/jiē", "解": "jiě/jiè/xiè",
    "落": "luò/là/lào", "为": "wéi/wèi", "相": "xiāng/xiàng", "兴": "xīng/xìng",
    "应": "yīng/yìng", "与": "yǔ/yù", "载": "zǎi/zài", "只": "zhǐ/zhī",
    "中": "zhōng/zhòng", "种": "zhǒng/zhòng", "转": "zhuǎn/zhuàn", "处": "chǔ/chù",
    "数": "shù/shǔ/shuò", "少": "shǎo/shào", "好": "hǎo/hào", "空": "kōng/kòng",
    "曲": "qū/qǔ", "折": "zhé/shé/zhē", "血": "xuè/xiě", "薄": "báo/bó/bò",
    "塞": "sāi/sài/sè", "传": "chuán/zhuàn", "弹": "tán/dàn", "调": "diào/tiáo",
    "更": "gèng/gēng", "供": "gōng/gòng", "号": "hào/háo", "划": "huá/huà",
    "会": "huì/kuài", "几": "jǐ/jī", "卷": "juǎn/juàn", "看": "kàn/kān",
    "累": "lèi/lěi/léi", "量": "liàng/liáng", "露": "lù/lòu", "难": "nán/nàn",
    "便": "biàn/pián", "强": "qiáng/qiǎng/jiàng", "散": "sàn/sǎn", "扫": "sǎo/sào",
    "似": "sì/shì", "宿": "sù/xiǔ/xiù", "提": "tí/dī", "挑": "tiāo/tiǎo",
    "系": "xì/jì", "鲜": "xiān/xiǎn", "咽": "yàn/yān/yè", "要": "yào/yāo",
    "钥": "yào/yuè", "挣": "zhèng/zhēng", "症": "zhèng/zhēng", "扒": "bā/pá",
    "背": "bèi/bēi", "奔": "bēn/bèn", "臂": "bì/bei", "别": "bié/biè",
    "剥": "bō/bāo", "藏": "cáng/zàng", "曾": "céng/zēng", "冲": "chōng/chòng",
    "答": "dá/dā", "倒": "dǎo/dào", "都": "dōu/dū", "恶": "è/wù/ě",
    "分": "fēn/fèn", "缝": "féng/fèng", "父": "fù/fǔ", "杆": "gān/gǎn",
    "给": "gěi/jǐ", "冠": "guān/guàn", "过": "guò/guo", "横": "héng/hèng",
    "还": "hái/huán", "混": "hùn/hún", "夹": "jiā/jiá", "间": "jiān/jiàn",
    "将": "jiāng/jiàng", "降": "jiàng/xiáng", "劲": "jìn/jìng", "尽": "jìn/jǐn",
    "禁": "jìn/jīn", "菌": "jūn/jùn", "卡": "kǎ/qiǎ", "壳": "ké/qiào",
    "埋": "mái/mán", "蒙": "méng/mēng/měng", "磨": "mó/mò", "模": "mó/mú",
    "宁": "níng/nìng", "弄": "nòng/lòng", "片": "piàn/piān", "铺": "pū/pù",
    "翘": "qiào/qiáo", "切": "qiē/qiè", "亲": "qīn/qìng", "圈": "quān/juàn",
    "任": "rèn/rén", "丧": "sāng/sàng", "色": "sè/shǎi", "煞": "shā/shà",
    "省": "shěng/xǐng", "盛": "shèng/chéng", "石": "shí/dàn", "属": "shǔ/zhǔ",
    "说": "shuō/shuì", "缩": "suō/sù", "弹": "tán/dàn", "汤": "tāng/shāng",
    "趟": "tàng/tāng", "帖": "tiē/tiě/tiè", "吐": "tǔ/tù", "瓦": "wǎ/wà",
    "吓": "xià/hè", "削": "xiāo/xuē", "旋": "xuán/xuàn", "压": "yā/yà",
    "烟": "yān/yīn", "沿": "yán/yàn", "应": "yīng/yìng", "佣": "yōng/yòng",
    "晕": "yūn/yùn", "扎": "zhā/zā/zhá", "炸": "zhà/zhá", "占": "zhàn/zhān",
    "涨": "zhǎng/zhàng", "爪": "zhǎo/zhuǎ", "正": "zhèng/zhēng", "殖": "zhí/shi",
    "轴": "zhóu/zhòu", "著": "zhù/zhe", "钻": "zuān/zuàn", "作": "zuò/zuō",
    "巷": "xiàng/hàng", "模": "mó/mú",
}


def load_level1(path=os.path.join(TABLES, "level1-3755.txt")):
    with open(path, encoding="utf-8") as fh:
        return set(fh.read().strip())


def load_collocations(path=os.path.join(TABLES, "collocations-45k.txt")):
    with open(path, encoding="utf-8") as fh:
        return set(fh.read().split())


def load_char_freq(path=os.path.join(TABLES, "char-freq.tsv")):
    table = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if "\t" in line:
                char, freq = line.rstrip("\n").split("\t", 1)
                table[char] = int(freq)
    return table


def load_prefix_top(path=os.path.join(TABLES, "prefix-top.tsv")):
    table = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if "\t" not in line:
                continue
            char, body = line.rstrip("\n").split("\t", 1)
            items = []
            for part in body.split("|"):
                if ":" in part:
                    freq, word = part.split(":", 1)
                    items.append((int(freq), word))
            table[char] = items
    return table


def load_homophone(path=os.path.join(TABLES, "char-homophone.tsv")):
    """非一级字的同音兜底强度：{字: (拼音, 该音下一级常用字数量)}。

    读成同音字时听感无损（筝 zhēng → 争/征/睁 仍是 zhēng）；
    同音字越少，越可能落到不同音的邻居上（攥 zuàn 全表仅 3 字）。
    """
    table = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                table[parts[0]] = (parts[1], int(parts[2]))
    return table


# 同音一级常用字少于此值 → 读错必歪，判为必换
HOMOPHONE_MIN = 3


def load_polyphone_words(path=os.path.join(TABLES, "polyphone-words.tsv")):
    """词内多音字必须读少数派读音的词表。

    返回 {词: (字, 应读, 常被读成, 改法)}，按词长降序，保证长词先匹配。
    """
    table = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 5 and parts[0] != "词":
                table[parts[0]] = tuple(parts[1:])
    return dict(sorted(table.items(), key=lambda kv: -len(kv[0])))


def detect_rare_reading(lines, words, collocations):
    """检出「词认识、音会选错」的词：多音字的低频读音被高频读音覆盖。

    机制不同于低频字（没有读音锚点）和搭配漂移（组合概率低被替换）：
    这里字认得、词也认得，只是该词要求的读音在语料中占比极低，
    模型按单字最高频读音取音。例：「知了」的「了」应读 liǎo，语料中 le 占 99% 以上。

    再按词频分两档：常用词表内的词模型见过足够样本，多数能学对读音（长大、还给），
    只降级为「可留听」；词表外的词没有样本，只能逐字取音（知了、弹珠），必换。
    """
    found = {}
    for ln in lines:
        if not ln or SECTION.match(ln):
            continue
        for word, info in words.items():
            if word in ln and word not in found:
                found[word] = (info, ln, word in collocations)
    return found


# 虚词/代词结尾的搭配不参与漂移检测，避免"吻你""爱我"这类正常跨词误报
FUNC_TAIL = set("你我他她它的了着是在就都也还不没吗呢吧啊很太又把被给和与这那个么一二三四五六七八九十")

# 首字是纯虚词时，后一个字必然是新词的开始（夏天的|蝉），属于跨词边界，不判漂移
FUNC_HEAD = set("的地得了着过就也都还很太又把被给和与这那个么不没在是有")

# 末字本身能独立成词且使用频繁时（说、疼、我），前两字只是正常的词+词组合，不判为漂移
STANDALONE_MAX = 800


# 亲属称呼与固定叠词本身是高频词，模型不会压制，避免误报
DUP_WHITELIST = {"爸爸", "妈妈", "爷爷", "奶奶", "外公", "外婆", "哥哥", "姐姐", "弟弟",
                 "妹妹", "叔叔", "阿姨", "婆婆", "公公", "宝宝", "星星", "娃娃",
                 "天天", "年年", "人人", "刚刚", "渐渐"}


def detect_drift(segments, collocations, prefix_top, char_freq):
    """检出词尾非常用搭配：模型可能改用高频搭配的读音（低吠 → 低垂/低声）。

    判定条件三选三：末字不常独立成词、该二元组不在常用词表、首字有极高频搭配。
    """
    found = {}
    for seg in segments:
        chars = [c for c in seg if CJK.match(c)]
        if len(chars) < 2:
            continue
        pair = chars[-2] + chars[-1]
        if chars[-1] in FUNC_TAIL or pair in collocations:
            continue
        if chars[-2] in FUNC_HEAD:
            continue
        if char_freq.get(chars[-1], 0) >= STANDALONE_MAX:
            continue
        candidates = prefix_top.get(chars[-2])
        if not candidates:
            continue
        best_freq, best_word = candidates[0]
        if best_freq >= 800 and best_word != pair:
            found[pair] = [best_word] + [w for _, w in candidates[1:3]]
    return found


# ---------------- 押韵分析 ----------------
# 用中华新韵十四韵做严判，十三辙做宽判（齐/支合并为一七辙）。
# 严判用于"这条韵线散没散"，宽判用于"算不算过得去的通押"。

PINYIN_TONE_MAP = {"ü": "v", "ǖ": "v", "ǘ": "v", "ǚ": "v", "ǜ": "v"}

Y_MAP = {"u": "v", "ue": "ve", "uan": "van", "un": "vn", "i": "i", "in": "in",
         "ing": "ing", "a": "ia", "e": "ie", "ao": "iao", "ou": "iu", "an": "ian",
         "ang": "iang", "ong": "iong", "o": "o"}
W_MAP = {"u": "u", "a": "ua", "o": "uo", "ai": "uai", "ei": "ui", "an": "uan",
         "en": "un", "ang": "uang", "eng": "ueng"}
INITIALS = ["zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l",
            "g", "k", "h", "j", "q", "x", "r", "z", "c", "s"]
FINAL_NORM = {"uei": "ui", "iou": "iu", "uen": "un"}

RHYME_14 = {
    "麻": {"a", "ia", "ua"},
    "波": {"o", "e", "uo"},
    "皆": {"ie", "ve"},
    "开": {"ai", "uai"},
    "微": {"ei", "ui"},
    "豪": {"ao", "iao"},
    "尤": {"ou", "iu"},
    "寒": {"an", "ian", "uan", "van"},
    "文": {"en", "in", "un", "vn"},
    "唐": {"ang", "iang", "uang"},
    "庚": {"eng", "ing", "ong", "iong", "ueng"},
    "齐": {"i", "v", "er"},
    "支": {"-i"},
    "姑": {"u"},
}
FINAL2RHYME = {f: name for name, fs in RHYME_14.items() for f in fs}
# 十三辙：齐、支同归一七辙
KUO = {"齐": "一七", "支": "一七"}

# 韵脚落在轻声虚词上时，读音弱化，押韵听感打折
WEAK_RHYME = set("了的着过呢吗啊吧么嘛呗")

# 句末语气词不承载韵脚，回退到前一个字取韵（"日子啊"的韵在"子"上）
RHYME_PARTICLE = set("啊呀哇哦嘛哟啦吧呢吗么")

# 人为判定「这个字是不是真多音字」用 curated 表，不用字典全量读音：
# 字典里"年 nìng""她 jiě""方 páng"属古音或异读，实际歌词里不会出现
def is_polyphonic(ch):
    return ch in POLY_CONFIRM


def toneless(py):
    for src, dst in PINYIN_TONE_MAP.items():
        py = py.replace(src, dst)
    out = []
    for ch in py:
        base = unicodedata.normalize("NFD", ch)
        if unicodedata.combining(base[0]) if base else False:
            continue
        out.append(base[0])
    return "".join(out)


def final_of(py):
    p = toneless(py)
    if not p:
        return ""
    if p.startswith("y"):
        return Y_MAP.get(p[1:], p[1:])
    if p.startswith("w"):
        return W_MAP.get(p[1:], p[1:])
    for ini in INITIALS:
        if p.startswith(ini) and len(p) > len(ini):
            rest = p[len(ini):]
            if ini in ("zh", "ch", "sh", "r", "z", "c", "s") and rest == "i":
                return "-i"
            # j/q/x 后面的 u 实际读 ü（雪 xue → üe，去 qu → ü）
            if ini in ("j", "q", "x") and rest.startswith("u"):
                rest = "v" + rest[1:]
            return FINAL_NORM.get(rest, rest)
    return FINAL_NORM.get(p, p)


def load_pinyin(path=os.path.join(TABLES, "pinyin-gb2312.tsv")):
    table = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if "\t" not in line:
                continue
            ch, body = line.rstrip("\n").split("\t", 1)
            table[ch] = [p for p in body.split(",") if p]
    return table


def last_cjk(line):
    for ch in reversed(line):
        if CJK.match(ch):
            return ch
    return ""


def rhyme_char_of(line):
    """取韵脚：跳过句末语气词，返回 (韵脚字, 语气词或空串)。"""
    chars = [c for c in line if CJK.match(c)]
    particle = ""
    while chars and chars[-1] in RHYME_PARTICLE:
        particle = chars[-1] + particle
        chars.pop()
    return (chars[-1] if chars else ""), particle


def split_blocks(text):
    """按 [Verse]/[Chorus] 标签切段；无标签时整首视为一段。"""
    blocks, cur_name, cur_lines = [], "全曲", []
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln:
            continue
        if SECTION.match(ln):
            if cur_lines:
                blocks.append((cur_name, cur_lines))
            cur_name, cur_lines = ln.strip("[] "), []
        else:
            cur_lines.append(ln)
    if cur_lines:
        blocks.append((cur_name, cur_lines))
    return blocks


def rhyme_audit(text, level1, pinyin):
    """逐段算韵脚。

    中文歌词默认「对句押韵」：偶数行必须同韵，奇数行（含首句）自由。
    所以主判只统计押韵行（第 2、4、6…行，单数行数时末行也算），
    奇数行同韵只是加分项，不同韵不算错——这样才不会把正常的叙事体判成散韵。
    """
    out = []
    for name, lines in split_blocks(text):
        entries = []
        for idx, ln in enumerate(lines, 1):
            ch, particle = rhyme_char_of(ln)
            pys = pinyin.get(ch)
            if not pys:
                continue
            finals = [final_of(p) for p in pys]
            groups = [FINAL2RHYME.get(f, "?") for f in finals]
            if "?" in groups:
                continue
            entries.append({
                "line": ln, "idx": idx, "char": ch, "particle": particle,
                "pinyin": pys[0], "group": groups[0],
                "poly": is_polyphonic(ch),
            })
        if len(entries) < 2:
            continue
        last_idx = entries[-1]["idx"]
        if len(entries) == 2:
            # 只有两行时，这一对就是完整对句，两行都要押
            rhyme_lines = list(entries)
        else:
            rhyme_lines = [e for e in entries if e["idx"] % 2 == 0]
        if not rhyme_lines:
            rhyme_lines = [entries[-1]]
        odd_lines = [e for e in entries if e not in rhyme_lines]
        counter = Counter(e["group"] for e in rhyme_lines)
        main, main_n = counter.most_common(1)[0]
        loose = Counter(KUO.get(e["group"], e["group"]) for e in rhyme_lines)
        loose_main, loose_n = loose.most_common(1)[0]
        odd_match = sum(1 for e in odd_lines if e["group"] == main)
        out.append({
            "name": name, "entries": entries, "rhyme_lines": rhyme_lines,
            "odd_lines": odd_lines, "main": main, "main_n": main_n,
            "loose_main": loose_main, "loose_n": loose_n,
            "total": len(rhyme_lines), "odd_match": odd_match,
            "odd_total": len(odd_lines),
        })
    return out


def analyse(text, level1, collocations, prefix_top, char_freq, words):
    lines = [ln.strip() for ln in text.splitlines()]
    rare, poly, dup, digits, latin, longlines = {}, {}, {}, {}, {}, {}
    segments = []
    for ln in lines:
        if not ln or SECTION.match(ln):
            continue
        segments.extend(s for s in re.split(r"[\s，。、！？,.!?]+", ln) if s)
        chars = [c for c in ln if CJK.match(c)]
        if len(chars) > MAX_LINE:
            longlines[ln] = len(chars)
        for ch in chars:
            if ch not in level1:
                rare.setdefault(ch, []).append(ln)
            elif ch in POLY_CONFIRM:
                poly.setdefault(ch, set()).add(POLY_CONFIRM[ch])
        for m in re.finditer(r"(.)\1", "".join(chars)):
            if CJK.match(m.group(1)) and m.group(0) not in DUP_WHITELIST:
                dup.setdefault(m.group(1) * 2, []).append(ln)
        for m in re.finditer(r"[0-9]+", ln):
            digits.setdefault(m.group(), []).append(ln)
        for m in re.finditer(r"[A-Za-z]+", ln):
            latin.setdefault(m.group(), []).append(ln)
    drift = detect_drift(segments, collocations, prefix_top, char_freq)
    rare_reading = detect_rare_reading(lines, words, collocations)
    return rare, poly, dup, digits, latin, longlines, drift, rare_reading


def report(text):
    level1 = load_level1()
    collocations = load_collocations()
    prefix_top = load_prefix_top()
    char_freq = load_char_freq()
    words = load_polyphone_words()
    homophone = load_homophone()
    pinyin = load_pinyin()
    rare, poly, dup, digits, latin, longlines, drift, rare_reading = analyse(
        text, level1, collocations, prefix_top, char_freq, words)
    rhymes = rhyme_audit(text, level1, pinyin)
    bar = "=" * 52
    print(bar)
    print("歌词发音风险检查")
    print(bar)

    rare_hard = {c: w for c, w in rare.items() if homophone.get(c, ("", 0))[1] < HOMOPHONE_MIN}
    rare_soft = {c: w for c, w in rare.items() if homophone.get(c, ("", 0))[1] >= HOMOPHONE_MIN}

    # ---- 押韵审计：韵脚同时决定「押不押韵」和「念不念错」，所以先看 ----
    print("\n[押韵] 对句押韵审计（严判 中华新韵十四韵 / 宽判 十三辙）")
    loose_blocks = []
    if not rhymes:
        print("  可分析的韵脚不足两行。")
    for blk in rhymes:
        n, tot = blk["main_n"], blk["total"]
        ratio = n / tot
        if n == tot:
            verdict = "成立"
        elif ratio >= 0.67:
            verdict = "有出韵"
        else:
            verdict = "不成立"
            loose_blocks.append(blk["name"])
        print(f"  {blk['name']}  押韵行 {n}/{tot} 同「{blk['main']}」 · {verdict}")
        seq = " ".join(
            f"{e['char']}({e['group']})" + (f"+{e['particle']}" if e["particle"] else "")
            for e in blk["rhyme_lines"])
        print(f"    押韵行韵脚：{seq}")
        if blk["odd_total"]:
            extra = "，句句押韵，更紧。" if blk["odd_match"] == blk["odd_total"] else "。"
            print(f"    奇数行（自由）：{blk['odd_match']}/{blk['odd_total']} 也落在同韵{extra}")
        if verdict != "成立":
            if blk["loose_n"] > n:
                print(f"    宽判：{blk['loose_n']}/{tot} 归入「{blk['loose_main']}」——通押，可接受。")
            else:
                print(f"    宽判：{blk['loose_n']}/{tot} 归入「{blk['loose_main']}」——不是通押，属换韵或跑韵。")
        for e in blk["rhyme_lines"]:
            if e["group"] != blk["main"]:
                print(f"    出韵：{e['line']}   （{e['char']} {e['pinyin']} → {e['group']}）")
        seen = set()
        for e in blk["rhyme_lines"]:
            risks = []
            if e["char"] not in level1:
                risks.append("低频字，读错则韵一起散")
            if e["poly"]:
                risks.append("多音字，韵不唯一")
            if e["particle"]:
                risks.append(f"句末「{e['particle']}」不承载韵，实际靠前一字")
            if risks and e["char"] not in seen:
                seen.add(e["char"])
                print(f"    韵脚风险：{e['char']}（{e['pinyin']}）—— {'；'.join(risks)}")

    if rare_hard:
        print("\n[高风险] 低频字·同音无兜底——必错，必须换词")
        for ch, where in rare_hard.items():
            py, n = homophone.get(ch, ("?", 0))
            print(f"  {ch}({py}，同音一级字仅 {n} 个)  出现于：{where[0]}")
        print("  说明：模型把字映射到最近的高频邻居，读成别的音（攥 zuàn → zhuàn/zuān）。改提示词和重跑都无效，只能换词。")

    if rare_soft:
        print("\n[低频字·可留听] 有同音字兜底——可能无损，建议保留后实听一次")
        for ch, where in rare_soft.items():
            py, n = homophone.get(ch, ("?", 0))
            print(f"  {ch}({py}，同音一级字 {n} 个)  出现于：{where[0]}")
        print("  说明：这类字读错也多半落在同音字上，听感不变（筝 zhēng → 争/征/睁）。但「獠 liáo → luǒ」是已证实的反例：同音 11 个仍然读歪，所以此档只是可留，不是安全。")

    if drift:
        print("\n[搭配漂移] 词尾非常用搭配——模型可能改念成常用词")
        for pair, cands in drift.items():
            print(f"  {pair} → 可能读成 {' / '.join(cands)}")
        print("  说明：字本身常见，但组合不在常用词表，模型会按语言先验补成高频词（低吠→低垂）。换成词表内的固定搭配即可。")

    reading_hard = {w: v for w, v in rare_reading.items() if not v[2]}
    reading_soft = {w: v for w, v in rare_reading.items() if v[2]}

    if reading_hard:
        print("\n[少数派读音·必换] 词表里没有这个词——只能逐字取音，必错")
        for word, ((ch, should, wrong, fix), where, _) in reading_hard.items():
            print(f"  {word}：{ch} 应读 {should}，会被读成 {wrong}  → {fix}")
            print(f"      出现在：{where}")
        print("  说明：词不在常用词表，模型没见过这个词的读音样本，只能按单字最高频读音取音（知了→知le）。")

    if reading_soft:
        print("\n[少数派读音·可留听] 词在常用词表内——模型有读音样本，多半能读对")
        for word, ((ch, should, wrong, fix), where, _) in reading_soft.items():
            print(f"  {word}：{ch} 应读 {should}（若读成 {wrong} 再改 {fix}）")
            print(f"      出现在：{where}")
        print("  说明：这类词在语料中高频出现，模型的读音统计多半已经学对。为保歌词质量，优先保留，实听后再定。")

    if poly:
        print("\n[读音确认] 常见多音字——逐个人工确认读音")
        print("  " + "  ".join(f"{c}({'/'.join(sorted(r))})" for c, r in poly.items()))

    if dup:
        print("\n[叠词] 连续同字——可能被采样重复惩罚压制")
        print("  " + "  ".join(f"{d}×{len(w)}" for d, w in dup.items()))

    if digits or latin:
        print("\n[数字/字母] 建议改写")
        for d in digits:
            print(f"  数字 {d} → 改写为中文数字")
        for w in latin:
            print(f"  拉丁字母 {w} → 模型可能逐字念或跳过")

    if longlines:
        print(f"\n[超长行] 超过 {MAX_LINE} 字——易吞字")
        for ln, n in longlines.items():
            print(f"  {n} 字：{ln}")

    issues = (len(rare_hard) * 3 + len(reading_hard) * 3 + len(drift) * 2
              + len(rare_soft) + len(reading_soft) + len(dup) + len(digits) + len(latin) + len(longlines))
    if not rhymes:
        rhyme_flag = "无可审段落（不足两行）"
    elif loose_blocks:
        rhyme_flag = f"散韵段 {len(loose_blocks)}（{'/'.join(loose_blocks)}）"
    else:
        rhyme_flag = f"{len(rhymes)} 段全部成立"
    print("\n" + "-" * 52)
    if not any([rare, rare_reading, drift, poly, dup, digits, latin, longlines]):
        print(f"押韵 {rhyme_flag}。未发现发音风险项。")
        return 0
    print(f"押韵 {rhyme_flag} / "
          f"高风险 {len(rare_hard) + len(reading_hard)} 项 / "
          f"可留听 {len(rare_soft) + len(reading_soft)} 项 / "
          f"搭配漂移 {len(drift)} 项 / 多音字 {len(poly)} 项 / 叠词 {len(dup)} 项 / "
          f"数字字母 {len(digits) + len(latin)} 项 / 超长行 {len(longlines)} 项")
    print("处置顺序：先保韵（散韵段先整段统一韵部）→ 再换必换项 → 可留听项保留实听。")
    print("冲突时：韵脚字要换，就整段换韵部，不要单独换掉一个韵脚字——单行出韵比念错一个字更刺耳。")
    return 1 if (rare_hard or reading_hard) else 0


def main():
    ap = argparse.ArgumentParser(description="检查中文歌词在无拼音层音乐模型中的发音风险")
    ap.add_argument("file", nargs="?", help="歌词文件路径（省略则读 stdin）")
    ap.add_argument("--text", help="直接传入歌词文本")
    args = ap.parse_args()
    if args.text:
        text = args.text
    elif args.file:
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()
    sys.exit(report(text))


if __name__ == "__main__":
    main()
