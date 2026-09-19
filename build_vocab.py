#!/usr/bin/env python3
"""
build_vocab.py — 从 Brown 语料库生成带词性标注的词频表。

数据来源: Brown Corpus(布朗大学, NLP 经典公开语料, 经 NLTK 官方数据
渠道分发), 与任何 GitHub 项目文件无关。词性用 Brown 原生标签按前缀
映射到 8 个大类。产物 data/vocab.tsv: 每行 "word<TAB>pos<TAB>count",
按词频降序, 仅保留纯字母词, 取前 10000。

一次性构建(需 nltk 与 brown 语料, 运行时不再需要):
    python3 build_vocab.py
"""
import os
import re
from collections import Counter

from nltk.corpus import brown

TOP_N = 10000
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vocab.tsv")

# Brown 原生标签 → 8 个词性大类(前缀匹配)
# 注意: 单元素元组必须带尾逗号, 否则是字符串会被逐字符匹配
POS_RULES = [
    (("PRP", "PRPS", "PP", "PP$", "PPO", "PPL", "WP", "WP$"), "pron"),
    (("AT", "DT", "DTI", "DTS", "DTX", "OD", "CD", "WDT"), "det"),
    (("IN", "TO"), "prep"),
    (("CC", "CS"), "conj"),
    (("JJ", "JJR", "JJT"), "adj"),
    (("RB", "RBR", "RBT", "QL", "RN", "WRB"), "adv"),
    (("VB", "VBD", "VBG", "VBN", "VBZ", "MD", "BE", "BED", "BEDZ",
      "BEG", "BEM", "BEN", "BER", "BEZ", "DO", "DOD", "DOZ", "HV",
      "HVD", "HVG", "HVN", "HVZ"), "verb"),
    (("NN", "NNS", "NN$", "NNS$", "NP", "NPS", "NR"), "noun"),
]
ALPHA = re.compile(r"^[a-z]+$")


def map_pos(tag):
    for prefixes, pos in POS_RULES:
        for p in prefixes:
            if tag.startswith(p):
                return pos
    return None     # 其余(PRT/. ,标点等)丢弃


def main():
    counts = {}      # word -> {pos: count}
    for word, tag in brown.tagged_words():
        w = word.lower()
        if not ALPHA.match(w):
            continue
        pos = map_pos(tag)
        if pos is None:
            continue
        d = counts.setdefault(w, {})
        d[pos] = d.get(pos, 0) + 1

    # 每个词取最高频的词性; 总频次排序
    ranked = sorted(
        ((sum(d.values()), w, max(d, key=d.get)) for w, d in counts.items()),
        reverse=True,
    )[:TOP_N]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        for total, w, pos in ranked:
            f.write(f"{w}\t{pos}\t{total}\n")
    print(f"{len(ranked)} words -> {OUT}")

    # 各词性覆盖情况
    stat = Counter(pos for _, _, pos in ranked)
    for pos, n in stat.most_common():
        print(f"  {pos:5} {n}")


if __name__ == "__main__":
    main()
