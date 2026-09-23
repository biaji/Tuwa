#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tuwa .hd 词书二进制格式解析器（A-M4，基于真实样本 wordbook_23536.hd 逆向）
用法: python3 hd_parser.py <xxx.hd> [--dump]
输出: 结构化 JSON（每词字段解码）到 <xxx>.parsed.json，--dump 打印到终端
"""
import sys, json, struct

def u16(b, o): return struct.unpack_from('<H', b, o)[0]
def u32(b, o): return struct.unpack_from('<I', b, o)[0]

def read_cstr(b, o, maxlen=512):
    e = b.find(b'\x00', o, o + maxlen)
    if e == -1: e = o + maxlen
    return b[o:e].decode('utf-8', 'replace')

def parse(fn, dump=False):
    b = open(fn, 'rb').read()
    total = len(b)
    out = {'file': fn, 'size': total}

    # ---- 40 字节全局头 ----
    hdr = {
        'bookId': u32(b, 0),
        'reserved1': u32(b, 4),
        'hash8': b[8:16].hex(),
        'u16_@16': u16(b, 16),
        'flag': u32(b, 20),
        'reserved2': u32(b, 24),
        'wordCount': u32(b, 28),
        'payloadLen': u32(b, 32),
        'hash4_@36': u32(b, 36),
    }
    out['global_header'] = hdr
    wc = hdr['wordCount']
    assert hdr['payloadLen'] == total - 40, (hdr['payloadLen'], total - 40)

    def parse_desc_table(start, n):
        """n 条描述符，每条 12 字节: [u16 type][u16 tag][u32 len][u32 off]"""
        recs = []
        for i in range(n):
            o = start + i * 12
            recs.append({
                'type': u16(b, o), 'tag': u16(b, o + 2),
                'length': u32(b, o + 4), 'offset': u32(b, o + 8),
            })
        return recs

    # ---- 元数据描述符表（40 起，8 条）----
    meta_desc = parse_desc_table(40, 8)
    meta = {}
    for d in meta_desc:
        o = d['offset']
        if d['type'] == 2:
            val = read_cstr(b, o, d['length'])
        elif d['type'] in (0, 1):
            val = u32(b, o)
        else:
            val = b[o:o + d['length']].hex()
        meta['tag%d' % d['tag']] = val
    out['metadata'] = meta

    # ---- 词索引表（136 起，wc 条 u32 偏移）----
    word_offsets = [u32(b, 136 + i * 4) for i in range(wc)]
    out['word_index_offset'] = 136

    # ---- 逐词解析 ----
    words = []
    for wi, wstart in enumerate(word_offsets):
        nfields = u32(b, wstart)
        descs = parse_desc_table(wstart + 4, nfields)
        fields = []
        for d in descs:
            o = d['offset']
            if d['type'] == 2:
                val = read_cstr(b, o, max(d['length'], 4))
            elif d['type'] in (0, 1):
                val = u32(b, o)
            else:
                val = b[o:o + max(d['length'], 1)].hex()
            fields.append({'tag': d['tag'], 'type': d['type'], 'length': d['length'], 'offset': o, 'value': val})
        words.append({'index': wi, 'offset': wstart, 'field_count': nfields, 'fields': fields})
    out['words'] = words

    if dump:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return out

if __name__ == '__main__':
    fn = sys.argv[1]
    res = parse(fn, dump='--dump' in sys.argv)
    outfn = fn.rsplit('.', 1)[0] + '.parsed.json'
    json.dump(res, open(outfn, 'w'), ensure_ascii=False, indent=1)
    wc = res['global_header']['wordCount']
    print('OK 全局头: bookId=%s 词数=%s 载荷=%s' % (
        res['global_header']['bookId'], wc, res['global_header']['payloadLen']))
    print('元数据: %s' % json.dumps(res['metadata'], ensure_ascii=False))
    print('解码词数: %d -> 已写入 %s' % (len(res['words']), outfn))
    # 打印前 3 个词
    for w in res['words'][:3]:
        print('-- word#%d @%d fields=%d' % (w['index'], w['offset'], w['field_count']))
        for f in w['fields']:
            print('    tag=%-6s type=%s len=%s off=%s : %r' % (f['tag'], f['type'], f['length'], f['offset'], f['value']))
