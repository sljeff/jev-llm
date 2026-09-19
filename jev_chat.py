#!/usr/bin/env python3
"""
jev_chat.py — 把 TypeSafe Jev 当对话引擎用的实验(三层决策版)。

Jev 的 Choice 单次最多 255 个选项, 词表塞不下, 所以每个词分三层串行选:
  第一层  词性(noun/verb/adj/adv/pron/prep/conj/det, +end)
  第二层  首字母(只提供该词性下有候选词的字母)
  第三层  候选词 = 词性与首字母都匹配的词, 按词频排序, 每批最多 250 个;
          还有剩余时批内多一个 "more"(都不是, 翻下一批)选项
第一层选定的词性会写进第二/三层的 state, 让 Jev 知道自己前面的决定。

词表 data/vocab.tsv 由 build_vocab.py 从 Brown 语料库生成
(词 + 词性 + 词频), 与任何 GitHub 项目文件无关。

stream_reply() 是事件流生成器, CLI 和网页后端(server.py)共用。
事件: pos / letter / word / loop / final / error。

用法:
    python3 jev_chat.py                # 聊天
    echo "hi" | python3 jev_chat.py --debug
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

ALPHABET = "abcdefghijklmnopqrstuvwxyz"
MAX_WORDS = 30           # 单条回复的词数上限
MAX_WORD_OPTIONS = 250   # 第三层每批的选项上限

END = "end"
MORE = "more"

ROOT = os.path.dirname(os.path.abspath(__file__))

ROLE = ("You are a friendly, helpful chatbot replying to the last user "
        "message in `conversation`.")
POS_INSTRUCTIONS = (
    f"{ROLE} Your reply is written one word at a time; `reply_so_far` is "
    "what you have written and `chosen_words` lists those words with their "
    "parts of speech. What part of speech should the next word be? Choose "
    "so that the sentence continues to read as natural, correct English — "
    "follow basic grammar order (a determiner is usually followed by a "
    "noun or adjective; a verb usually needs a subject before it), and a "
    "reply to a factual question usually starts with a determiner like "
    "\"the\", a pronoun like \"i\", or a noun."
)
LETTER_INSTRUCTIONS = (
    f"{ROLE} Your reply is written one word at a time; `reply_so_far` is "
    "what you have written and the next word must be the part of speech "
    "given in `next_word_pos`. What letter should the next word start with?"
)
WORD_INSTRUCTIONS = (
    f"{ROLE} Your reply is written one word at a time; `reply_so_far` is "
    "what you have written. The next word must be the part of speech given "
    "in `next_word_pos` and start with the letter given in `first_letter`. "
    "Which word comes next?"
)

POS_LABELS = {
    "noun": "a noun — a person, place, thing, or idea",
    "verb": "a verb or auxiliary — an action or a state (is, have, can, go)",
    "adj": "an adjective — describes a noun (good, big, new)",
    "adv": "an adverb — modifies a verb or adjective (very, really, not)",
    "pron": "a pronoun — i, you, it, they, my",
    "prep": "a preposition — in, on, with, of",
    "conj": "a conjunction — and, but, because, if",
    "det": "a determiner or article — the, a, this, one",
}

_index = None    # (pos, 首字母) -> [词, ...] 按词频降序


def load_vocab():
    """读取 build_vocab.py 生成的 data/vocab.tsv, 建三层筛选用的索引。"""
    global _index
    if _index is not None:
        return _index
    _index = {}
    with open(os.path.join(ROOT, "data", "vocab.tsv")) as f:
        for line in f:
            word, pos, _ = line.rstrip("\n").split("\t")
            _index.setdefault((pos, word[0]), []).append(word)  # 文件本身按词频排序
    return _index


def letters_for(pos):
    """该词性下有候选词的首字母, 按字母序。"""
    return sorted({l for (p, l) in load_vocab() if p == pos})


def candidates(pos, letter):
    return load_vocab().get((pos, letter), [])


def load_env(path):
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def ask(api_key, state, question, usage, retries=4):
    body = {"state": state, "model": MODEL, "questions": {"next": question}}
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
            if _trace:
                _trace.write(json.dumps({"request": body, "response": data},
                                        ensure_ascii=False) + "\n")
                _trace.flush()
            for k, v in data.get("usage", {}).items():
                usage[k] = usage.get(k, 0) + v
            return data["answers"]["next"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
    raise RuntimeError("重试次数用尽")


_trace = None       # --trace 时打开的 trace.jsonl, 记录每次原始请求/响应


def top_of(answer, n):
    """top n 个概率 >= 0.5% 的选项, 0% 的凑数行不推给前端。"""
    ranked = sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])
    out = []
    for o, p in ranked:
        if len(out) >= n or p < 0.005:
            break
        out.append([o, round(p, 3)])
    return out


def trim_word_loop(words):
    """末尾同一词块(周期 1..4)出现两次时, 保留一份并标记复读。"""
    for p in range(1, 5):
        if len(words) >= 2 * p and words[-p:] == words[-2 * p:-p]:
            return True, words[:-p]
    return False, words


def stream_reply(api_key, history, usage, max_words=MAX_WORDS):
    """逐词生成(三层决策), 产出事件流:
    pos    第一层结果(词性, 或 end)
    letter 第二层结果(首字母)
    word   第三层每一批的结果(picked 可能是 more; word 字段为最终选中的词)
    loop   复读成环, 保留一份重复块后停止
    final  回复完成, 带最终文本、请求数与 token 用量
    error  请求失败
    """
    draft = ""
    words = []           # 本条回复已选的词
    chosen = []          # [{"word": w, "pos": p}] 语法轨迹, 进 state
    steps = 0
    try:
        for _ in range(max_words):
            state = {"conversation": history, "reply_so_far": draft,
                     "chosen_words": chosen}

            # ── 第一层: 词性 ──────────────────────────────
            criteria = dict(POS_LABELS)
            if words:
                criteria[END] = "the reply is finished"
            answer = ask(api_key, state,
                         {"type": "choice", "instructions": POS_INSTRUCTIONS,
                          "criteria": criteria}, usage)
            steps += 1
            pos = answer["choice"]
            yield {"type": "pos", "picked": pos, "top": top_of(answer, 4)}
            if pos not in POS_LABELS:          # end 或表外
                break

            # ── 第二层: 首字母(只列该词性下有候选词的字母) ──
            # 同(词性,字母)已连续两次 → 禁用该字母, 防变体复读
            letters = letters_for(pos)
            if len(chosen) >= 2 and chosen[-1]["pos"] == chosen[-2]["pos"] == pos:
                letters = [l for l in letters
                           if chosen[-1]["word"][0] != l or chosen[-2]["word"][0] != l]
            if not letters:
                break
            answer = ask(api_key, {**state, "next_word_pos": pos},
                         {"type": "choice", "instructions": LETTER_INSTRUCTIONS,
                          "criteria": {c: f'a {pos} word starting with "{c}"'
                                       for c in letters}}, usage)
            steps += 1
            letter = answer["choice"]
            yield {"type": "letter", "picked": letter, "top": top_of(answer, 4)}
            if letter not in letters:
                break

            # ── 第三层: 候选词分批, more 翻下一批 ──────────
            recent = set(words[-2:])            # 排除最近两个词, 防周期2复读
            pool = [w for w in candidates(pos, letter) if w not in recent]
            picked = None
            for start in range(0, len(pool), MAX_WORD_OPTIONS):
                batch = pool[start:start + MAX_WORD_OPTIONS]
                has_more = start + MAX_WORD_OPTIONS < len(pool)
                criteria = {w: w for w in batch}
                if has_more:
                    criteria[MORE] = "none of these is the right word — show more"
                answer = ask(api_key,
                             {**state, "next_word_pos": pos,
                              "first_letter": letter},
                             {"type": "choice", "instructions": WORD_INSTRUCTIONS,
                              "criteria": criteria}, usage)
                steps += 1
                pick = answer["choice"]
                yield {"type": "word", "picked": pick,
                       "word": pick if pick in batch else None,
                       "top": top_of(answer, 4)}
                if pick == MORE and has_more:
                    continue                    # Jev 说这批没有, 翻下一批
                if pick in batch:
                    picked = pick
                break
            if picked is None:
                break
            words.append(picked)
            chosen.append({"word": picked, "pos": pos})
            draft = " ".join(words)

            stuck, kept = trim_word_loop(words)
            if stuck:
                draft = " ".join(kept)
                yield {"type": "loop", "draft": draft}
                break
    except RuntimeError as e:
        yield {"type": "error", "message": str(e)}
        return
    yield {"type": "final", "reply": draft.strip(), "steps": steps,
           "usage": dict(usage)}


def run_cli(api_key, debug):
    usage = {}
    history = []
    print("Jev 逐词聊天(每词三层决策)。Ctrl-C 退出。")
    while True:
        try:
            user = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        history.append({"role": "user", "text": user})
        print("jev> ", end="", flush=True)
        t0 = time.time()
        reply = ""
        for ev in stream_reply(api_key, history, usage):
            if ev["type"] == "pos" and debug:
                print(f"\n    [词性 {ev['picked']}]", flush=True)
            elif ev["type"] == "letter" and debug:
                print(f"    [首字母 {ev['picked']}]", flush=True)
            elif ev["type"] == "word":
                if ev["word"]:
                    print(("" if debug else "") + ev["word"] + " ",
                          end="", flush=True)
                elif debug:
                    print(f"    [第三层: {ev['picked']}]", flush=True)
            elif ev["type"] == "loop":
                print("  [复读成环, 停止]", flush=True)
            elif ev["type"] == "error":
                print(f"\n[请求失败] {ev['message']}", flush=True)
            elif ev["type"] == "final":
                reply = ev["reply"]
        print(f"    ({time.time() - t0:.0f}s, {len(reply)} chars)")
        if reply:
            history.append({"role": "assistant", "text": reply})

    total = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    print(f"\n本次共 {total} tokens (in {usage.get('input_tokens', 0)} / "
          f"out {usage.get('output_tokens', 0)})")


def main():
    global _trace
    ap = argparse.ArgumentParser(description="Jev 逐词聊天实验")
    ap.add_argument("--debug", action="store_true", help="打印每层的选择")
    ap.add_argument("--trace", action="store_true",
                    help="把每次原始请求/响应写入 trace.jsonl 用于验真")
    args = ap.parse_args()

    load_env(os.path.join(ROOT, ".env"))
    api_key = os.environ.get("API_KEY")
    if not api_key:
        sys.exit("缺少 API_KEY(请在 .env 里配置)")
    load_vocab()
    if args.trace:
        _trace = open(os.path.join(ROOT, "trace.jsonl"), "w")
        print("已开启 trace, 原始请求/响应写入 trace.jsonl")
    run_cli(api_key, args.debug)


if __name__ == "__main__":
    main()
