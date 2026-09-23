#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_hd_demo.py — 用内置几个单词样例生成 Tuwa type=1 二进制 .hd 词书。

自包含（仅标准库），不依赖任何样本/数据库文件。格式依据 A-M4 逆向结论：
  - 40B 全局头 + 96B 元数据描述符表 + 词索引表(N×u32) + 24B 结构块
    + 词描述符表 + 数据流。
  - 描述符 [u16 type][u16 tag][u32 length][u32 data_offset]；
    type: 0=id, 1=int(4B), 2=string(UTF-8+\\0, 4字节对齐)。
  - tag 映射、数据流顺序与 WordRepo.sqlite 字段对应，见 README §2.5/§3。
  - hash8 = MD5(载荷=文件[40:]) 的后 8 字节；hash4 为自定义校验和（算法未识别，
    示例置 0；真机读取如需通过，需反汇编 libSTBookGeneratorLib.so 补全）。

用法: python3 make_hd_demo.py [out.hd]
"""
import sys, struct, hashlib

def align4(n):
    return (n + 3) & ~3

BOOK_ID = 99001
NAME   = "示例词书"
MODULE = "英语"
TAG    = "单词"

# ---- 示例词（means 值 = 词性 + ' ' + 释义，与 WordRepo.sqlite 一致）----
SAMPLES = [
    {
        "word": "sibling",
        "symbol": "ˈsɪblɪŋ",
        "symbol_url": "/sound/word_symbol_v1/getlearn/z8095b.mp3",
        "means": ["n. 兄弟姊妹；民族成员"],
        "sentences": [
            {"en": "A calm child may respond better to different parenting than a younger sibling.",
             "zh": "一个冷静的孩子对不同的养育方式可能比弟弟妹妹反应更好。",
             "en_url": "/sound/word_sentence_v1/ttsljzuixin/cikuliju2835.mp3"},
        ],
    },
    {
        "word": "guzzle",
        "symbol": "ˈɡʌz(ə)l",
        "symbol_url": "/sound/word_symbol_v1/getlearn/z13209b.mp3",
        "means": ["vt. 狂饮；暴食", "vi. 狂饮；暴食；狼吞虎咽"],
        "sentences": [
            {"en": "Onlookers sip wine and guzzle beer.",
             "zh": "看客们或啜饮美酒、或豪饮冰啤。",
             "en_url": "/sound/word_sentence_v1/ttsljzuixin/2023320xwlj3374.mp3"},
        ],
    },
    {
        "word": "sting",
        "symbol": "stɪŋ",
        "symbol_url": "/sound/word_symbol_v1/getlearn/z15763b.mp3",
        "means": ["vt. 刺，蜇；刺痛", "vi. 刺，蜇", "n. 刺痛；刺"],
        "sentences": [
            {"en": "He was stung by a wasp.",
             "zh": "他被马蜂蜇了一下。",
             "en_url": "/sound/word_sentence_v1/hechengliju/202336lj250.mp3"},
            {"en": "The bee's sting is painful.",
             "zh": "蜜蜂的蜇刺很疼。",
             "en_url": ""},
        ],
    },
]
def word_fields(w):
    """单词对象 → .hd 字段列表（顺序与已验证生成器 hd_generator.py 一致）。"""
    fields = [
        {"type": 2, "tag": 0, "value": w["word"]},       # word
        {"type": 2, "tag": 1, "value": w["symbol"]},     # symbol
        {"type": 2, "tag": 2, "value": w["means"][0]},   # mean[0]
        {"type": 2, "tag": 3, "value": w["symbol_url"]}, # symbol_url
    ]
    for k in range(1, len(w["means"])):                  # mean[1..] → tag 1000+(k-1)
        fields.append({"type": 2, "tag": 1000 + (k - 1), "value": w["means"][k]})
    for k, s in enumerate(w["sentences"]):               # sentence[i]
        base = 2000 + (k - 1) if k else 0
        for j, key in enumerate(("en", "zh", "en_url")):
            fields.append({"type": 2, "tag": (4 + j) if k == 0 else base + j,
                           "value": s.get(key) or ""})
    n = len(w["sentences"])
    fields.append({"type": 1, "tag": 7, "value": len(w["means"])})   # meanCount
    fields.append({"type": 1, "tag": 8, "value": n})                 # sentenceCount en
    fields.append({"type": 1, "tag": 9, "value": n})                 # sentenceCount zh
    fields.append({"type": 1, "tag": 10, "value": n})                # sentenceCount url
    return fields

def build(book_id, meta_fields, words):
    """按逆向布局组装 .hd 二进制。返回 bytes。"""
    wc = len(words)
    meta_desc_start = 40
    word_idx_start = meta_desc_start + 96
    block_start = word_idx_start + wc * 4
    word_desc_start = block_start + 24
    wd_lens = [4 + 12 * len(w) for w in words]
    data_base = word_desc_start + sum(wd_lens)

    data = bytearray()
    def emit(type_, val):
        off = data_base + len(data)
        if type_ in (0, 1):
            data.extend(struct.pack("<I", val)); length = 4
        else:
            raw = val.encode("utf-8") + b"\x00"
            data.extend(raw + b"\x00" * (align4(len(raw)) - len(raw)))
            length = len(raw)
        return off, length

    meta_offs = []
    for (t, v) in meta_fields:
        off, ln = emit(t, v)
        meta_offs.append((t, ln, off))
    word_data_offs = [[emit(f["type"], f["value"]) for f in w] for w in words]

    out = bytearray()
    hdr = bytearray(40)
    struct.pack_into("<I", hdr, 0, book_id)
    struct.pack_into("<I", hdr, 20, 0x01000001)          # flag
    struct.pack_into("<I", hdr, 28, wc)                  # wordCount
    out += hdr
    for (t, ln, off) in meta_offs:                        # 8×12B 元数据描述符表
        out += struct.pack("<HHI", t, 0, 0)
        out += struct.pack("<I", off)
    for i, (t, ln, off) in enumerate(meta_offs):          # 回填 tag 0..7 / length
        struct.pack_into("<H", out, meta_desc_start + i * 12, t)
        struct.pack_into("<H", out, meta_desc_start + i * 12 + 2, i)
        struct.pack_into("<I", out, meta_desc_start + i * 12 + 4, ln)
    pos = word_desc_start
    for w in words:                                       # 词索引表 N×u32
        out += struct.pack("<I", pos); pos += 4 + 12 * len(w)
    out += struct.pack("<IIIIII", 0x00010000, 0, wc - 1, 0, 0, 0)  # 24B 结构块
    for wi, w in enumerate(words):                        # 词描述符表
        out += struct.pack("<I", len(w))
        for k, f in enumerate(w):
            off, ln = word_data_offs[wi][k]
            out += struct.pack("<HHI", f["type"], f["tag"], ln)
            out += struct.pack("<I", off)
    out += data                                           # 数据流
    struct.pack_into("<I", out, 16, 8)                    # 版本/常量
    struct.pack_into("<I", out, 32, len(out) - 40)        # payloadLen
    md5 = hashlib.md5(bytes(out[40:])).digest()
    out[8:16] = md5[8:16]                                 # hash8
    # hash4 @36：自定义校验和（算法未识别），示例置 0
    return bytes(out)

def parse(data):
    """回读自检：校验全局头字段、结构块、hash8。"""
    book_id, = struct.unpack_from("<I", data, 0)
    wc, = struct.unpack_from("<I", data, 28)
    payload_len, = struct.unpack_from("<I", data, 32)
    block = struct.unpack_from("<IIIIII", data, 40 + 96 + wc * 4)
    fields_per_word = []
    for i in range(wc):
        pos = struct.unpack_from("<I", data, 40 + 96 + i * 4)[0]
        fc, = struct.unpack_from("<I", data, pos)
        fields_per_word.append(fc)
    return {"book_id": book_id, "word_count": wc, "payload_len": payload_len,
            "block": block, "fields_per_word": fields_per_word,
            "hash8_ok": data[8:16] == hashlib.md5(data[40:]).digest()[8:16]}

def main():
    outfn = sys.argv[1] if len(sys.argv) > 1 else "demo.hd"
    meta = [(0, BOOK_ID), (1, 0), (1, 1), (2, NAME), (2, MODULE), (2, ""), (1, 1), (2, TAG)]
    words = [word_fields(w) for w in SAMPLES]
    data = build(BOOK_ID, meta, words)
    with open(outfn, "wb") as f:
        f.write(data)
    print("生成:", outfn, len(data), "字节,", len(words), "词")
    print("自检:", parse(data))

if __name__ == "__main__":
    main()

