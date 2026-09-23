# 途蛙记忆卡逆向

## 设备说明

本设备并非Android设备。 最新系统固件为： http://p.s3.tuwa.starot.com/firmware/study_v2_channel/01.02.02.61/xr_system_gen2.img
定制设备固件基本没改造可能。可以理解为换了墨水屏幕的MP4。

防止厂家倒闭、跑路、消失，故进行逆向。省的白花钱。

---

# 一、APP 端（词书生成与上传）

## 1. 词书数据模型（getMeta / getWord）

保存时 native 回调 Java 层 `getMeta()` / `getWord(i)`，各返回一段 JSON：

**getMeta()**
```json
{"id":<bookId>,"grade":0,"category":1,"name":"词书名","module":"英语","press":"","type":1,"tag":"单词"}
```
**getWord(i)**（`means` 值 = `getProperty()+' '+getMean()`，空字段不写入）
```json
{"word":"...","symbols":[{"symbol":"音标","url":"音标mp3"}],
 "means":[{"mean":"词性. 释义"}],
 "sentences":[{"en":"英文例句","zh":"中文翻译","en_url":"例句mp3"}]}
```

## 2. 上传与云端存储（S3）

- 保存后经 AWS S3 SDK 上传，**公有读**（PublicRead，无需 token 即可下载任何人上传的书）。
- Bucket：`p.s3.public.tuwa.starot.com`（region `cn-north-1`）。
- 对象 key（路径）：`book/custom_v1/<userId>/<bookId>_<时间戳>.hd`
- 下载两种等价形式：
  - `https://p.s3.public.tuwa.starot.com/book/custom_v1/<uid>/<bid>_<ts>.hd`
  - `http://p.s3.tuwa.starot.com/book/custom_v1/<uid>/<bid>_<ts>.hd`（CDN，即推送返回的 base）
- bucket 对对象 PublicRead 但**禁止 ListBucket 枚举**（只能凭已知 key 探测）。

### 本地存储
- 本地 `.hd`：`getExternalFilesDir(null)` = `/storage/emulated/0/Android/data/<包名>/files/<bookId>.hd`（点「保存」时才写）。
- 词库缓存：`databases/WordRepo.sqlite`（Room；STWordRepo + mean/symbol/sentence 关系表）。
- 账号/token：`databases/tuwa.db` 的 `stusermodel` 表。

### 本地词库 SQLite（WordRepo.sqlite，真机 dump）

词库缓存 `databases/WordRepo.sqlite`（Room），4 张业务表 + 2 张 Room 元数据表。

**STWordRepo**（词主表；`word` 唯一；`type`：1=单词、2=词条，语义未细分）
```sql
CREATE TABLE "STWordRepo" (
  "id" INTEGER NOT NULL PRIMARY KEY,
  "type" INTEGER NOT NULL,
  "word" VARCHAR(255) NOT NULL,
  "prefix" TEXT NOT NULL,
  "showStatus" INTEGER NOT NULL
);
CREATE UNIQUE INDEX "stword_word" ON "STWordRepo" ("word");
```

**STWordMeanRelation**（释义；`wordId`→STWordRepo.id，`property`=词性[n./vt./vi.]，即生成 .hd 时 `getProperty()` 前缀来源）
```sql
CREATE TABLE "STWordMeanRelation" (
  "id" INTEGER NOT NULL PRIMARY KEY,
  "wordId" INTEGER NOT NULL,
  "property" TEXT NOT NULL,
  "mean" TEXT NOT NULL,
  "soundUrl" TEXT NOT NULL,
  "showStatus" INTEGER NOT NULL,
  FOREIGN KEY ("wordId") REFERENCES "STWordRepo" ("id")
);
CREATE INDEX "stmean_wordId" ON "STWordMeanRelation" ("wordId");
```

**STWordSymbolRelation**（音标/发音；`type`=发音类型[样本恒 0]，`symbol`=音标，`soundUrl`=mp3）
```sql
CREATE TABLE "STWordSymbolRelation" (
  "id" INTEGER NOT NULL PRIMARY KEY,
  "wordId" INTEGER NOT NULL,
  "type" INTEGER NOT NULL,
  "symbol" TEXT NOT NULL,
  "soundUrl" TEXT NOT NULL,
  "showStatus" INTEGER NOT NULL,
  FOREIGN KEY ("wordId") REFERENCES "STWordRepo" ("id")
);
CREATE INDEX "stsymbol_wordId" ON "STWordSymbolRelation" ("wordId");
```

**STWordMeanSentenceRelation**（例句；`enContent`/`chContent`=英/中，`enSoundUrl`/`chSoundUrl`=例句 mp3；`meanId`→STWordMeanRelation.id，样本恒 0）
```sql
CREATE TABLE "STWordMeanSentenceRelation" (
  "id" INTEGER NOT NULL PRIMARY KEY,
  "wordId" INTEGER NOT NULL,
  "meanId" INTEGER NOT NULL,
  "enContent" TEXT NOT NULL,
  "chContent" TEXT NOT NULL,
  "enSoundUrl" TEXT NOT NULL,
  "chSoundUrl" TEXT NOT NULL,
  "showStatus" INTEGER NOT NULL,
  FOREIGN KEY ("wordId") REFERENCES "STWordRepo" ("id"),
  FOREIGN KEY ("meanId") REFERENCES "STWordMeanRelation" ("id")
);
CREATE INDEX "stsentence_wordId" ON "STWordMeanSentenceRelation" ("wordId");
CREATE INDEX "stsentence_meanId" ON "STWordMeanSentenceRelation" ("meanId");
```

**Room 元数据表**（非业务）：`android_metadata`（locale）、`room_master_table`（identity_hash）。
- 说明：`showStatus` 全表恒 1（可见）；soundUrl 为相对路径（`/sound/word_symbol_v1/...`、`/sound/word_sentence_v1/...`），对应 S3/CDN 对象。
- 生成 .hd 的字段对应关系：word←`STWordRepo.word`；symbol/symbol_url←`STWordSymbolRelation`；mean[i]←`STWordMeanRelation`（`property+' '+mean`）；sentence[i].en/zh/url←`STWordMeanSentenceRelation`（enContent/chContent/enSoundUrl）。

## 2.5 鉴权格式（APP 侧）

- 厂商按 **`'Bearer '<token>`（带字面单引号）** 前缀解析。标准 `Bearer `（无引号）→ 401；`'Bearer '` → 200。
- token 为 **JWT(HS512)**（真机 `tuwa.db/stusermodel` 与抓包双样本确认）：
  `{"alg":"HS512"}` + `{"created":<毫秒>,"id":<userId>,"sn":null,"rid":null,"type":"android","exp":<秒>}`
- 来源：读设备 `databases/tuwa.db` 的 `stusermodel` 表。

## 3. type=1 二进制词书 `.hd` 格式（生成端）

结论源于真实二进制样本逆向，并经独立生成器**逐字节复现**验证（含 hash4@36 校验和，见 §3.8）。

### 3.1 两种 .hd
- **type=2 电子书 `.hd`**：纯文本（UTF-8），仅扩展名改为 `.hd`。
- **type=1 自定义词书 `.hd`**：下述二进制格式。

### 3.2 总体布局（小端）
```
偏移      大小   内容
0         40     全局头
40        96     元数据描述符表（8 条 × 12 字节）
136       472    词索引表（N 条 × u32，N=词数，指向各词描述符表）
608       24     结构块（值 0x00010000, 0, 词数-1, 0, 0, 0；语义未完全确认）
632       ~      词描述符表（每词：[u32 字段数] + 字段数×12 字节描述符）
~         ~      数据流（元数据字段 + 各词字段，顺序打包；紧随词描述符表之后）
```
> 词索引表大小 = 词数×4，故其后各段偏移随词数变化；数据流偏移为文件内绝对偏移。

### 3.3 全局头（40 字节）
```
偏移  字段
0     bookId (u32)
4     reserved (u32) = 0
8     hash8 (8B)      = MD5(载荷=文件[40:]) 的后 8 字节
16    u32             = 8（版本/常量）
20    flag (u32)      = 0x01000001（= generateHashRecord 首参 16777217）
24    reserved (u32)  = 0
28    wordCount (u32)
32    payloadLen (u32)= 总大小 − 40
36    hash4 (u32)     = 标准 CRC-32/ISO-HDLC(zlib) 作用于全局头前 36 字节（见 §3.8）
```

### 3.4 描述符（元数据与词通用，每条 12 字节）
```
[u16 type][u16 tag][u32 length][u32 data_offset]
type: 0=bookId(id)，1=int(u32 4字节)，2=string(UTF-8，length=字节数+1含\0，数据4字节对齐补零)
```

### 3.5 元数据描述符（tag 0-7，对应 getMeta）
```
tag0 type0 → id    tag1 type1 → grade    tag2 type1 → category
tag3 type2 → name  tag4 type2 → module   tag5 type2 → press
tag6 type1 → type  tag7 type2 → tag
```

### 3.6 词字段标签（对应 getWord，全词通用）
```
tag0  type2  word          单词
tag1  type2  symbol        音标
tag3  type2  symbol_url    音标发音 mp3
tag2  type2  mean[0]       第一释义（"词性. 释义"）
tag7  type1  meanCount     释义数量
tag4/5/6  type2  sentence[0].en/.zh/.en_url
tag8/9/10 type1  sentenceCount（en/zh/url 三数组各计数，恒相等）
附加元素(i>=1)：
  mean[i]         = tag 1000+(i-1)
  sentence[i].en  = tag 2000+(i-1)
  sentence[i].zh  = tag 3000+(i-1)
  sentence[i].en_url = tag 4000+(i-1)
```

### 3.7 数据流打包
- 顺序 = 元数据字段(按 tag0..7) → word0 字段 → word1 字段 → …
- 字符串：UTF-8 + `\0`，补零到 4 字节对齐；length=len+1。
- int：4 字节。描述符 `data_offset` = 该字段在文件中的绝对偏移。

### 3.8 校验
- **hash8** = `MD5(文件[40:])` 的后 8 字节。
- **hash4** = 标准 CRC-32/ISO-HDLC（= `zlib.crc32`）作用于**全局头前 36 字节** `文件[0:36]`（含已回填的 hash8，须在 hash8 之后计算）。`zlib.crc32(b[0:36])` 与真实样本 `0x8d4c073c` 逐字节吻合；`hd_generator.py` 已据此回填，再生成与原文件 0 差异。

### 3.9 示例生成脚本
`work/tools/make_hd_demo.py`：自包含（仅标准库、不依赖样本/数据库）示例生成器，代码内内置几个单词（含多释义、多例句），按上述布局生成 `.hd` 并回读自检（bookId/词数/结构块/hash8）。

```shell
python3 work/tools/make_hd_demo.py demo.hd     # 默认输出 demo.hd
# 生成 1612B / 3 词，hash8_ok=True；用 hd_parser.py 交叉解析字段与 tag 均正确
```
- 数据模型与 `word_fields()`：`word / symbol / symbol_url / means[] / sentences[]` 直接映射 §2.5 的 WordRepo.sqlite 表；`means` 值 = 词性 + 空格 + 释义。
- 说明：`hash4`@36 已破解（= 标准 CRC-32/ISO-HDLC(zlib) 作用于全局头前 36 字节，须在 hash8 写入后再算）。`make_hd_demo.py` 已按此回填，并在回读自检中校验 `hash4_ok`。



---

# 二、设备端（词书读取）

## 4. 协议

### 设备注册

```shell
curl --location --request POST 'http://prod.study.tuwa.starot.com/wms/token?sn=xxxxx&code=CD919f&secret=xxxxxxx'
```
 - sn 设备序列号
 - code 未知
 - secret 未知

返回：

```json
{
    "code": 200,
    "message": "请求成功",
    "data": {
        "token": "此处可以获取token",
        "expired": 1753947348,
        "base": "http://p.s3.tuwa.starot.com"
    }
}
```

### 资源推送

```shell
curl --location --request GET 'http://prod.study.tuwa.starot.com/wms/wait/download?cId=6&offset=0&size=3' \
--header 'Authorization: '\''Bearer '\''有效token' \
```
 - cId 待下载类别 1 单词本 6 电子书
 - offset
 - size

当存在待下载项目时，返回结果示例：

```json
{
    "code": 200,
    "message": "请求成功",
    "data": {
        "total": 1,
        "list": [
            {
                "pushId": 263334,
                "bookId": 23402,
                "url": "/book/custom_v1/<userid>/ebook_<timestamp>.hd"
                "size": 584854,
                "planId": 0,
                "study": null,
                "review": null,
                "updateTime": 1751354646,
                "pressId": null,
                "press": null,
                "time": 1751354654,
                "type": 2,
                "name": "威尔历险记",
                "count": 200726,
                "studyMode": 0
            }
        ]
    }
}
```
则下载链接为：
```
http://p.s3.tuwa.starot.com/book/custom_v1/<userid>/ebook_<timestamp>.hd
```
书籍格式为txt，仅仅扩展名改为了".hd"。下载不需要token验证（亦即说不定可以下载别人上传的书）


## 5. MQTT 主题
APP↔设备实时通信/推书走 `tuwa.study.machine.*` 主题，如 `...custom.book.push.device`、`...custom.book.word.info`、`...book.plan.info`、`...wifi.password`、`...wifi.ssid`。


---

# 三、通用

## 6. 真机 .hd 获取路径

前置：设备已安装并登录目标 App；设备自带网络工具（curl）且可直连（不经中转代理）。

> 6.1（读 sqlite）需 root；6.1.1（logcat）**无需 root**。

### 6.1 取 token
读设备数据库 `databases/tuwa.db` 的 `stusermodel` 表（HS512 JWT）：
```
adb shell sqlite3 /data/data/<包名>/databases/tuwa.db 'SELECT token FROM stusermodel'
```

### 6.1.1 非 root：logcat 抓 token（无需 root）

App 的 `LogUtil.init(..., true)`（`TuwaApplication`）默认开启日志，token 相关日志经 **LogUtil 双写**，logcat 出现两个 tag：

| tag | 来源 | 说明 |
|---|---|---|
| `PRETTY_LOGGER` | orhanobut Logger 框架（`u9.g`→`u9.i`→`l9.a`） | 传 tag=null 落到默认值 `PRETTY_LOGGER`（**下划线**），消息包成 `┌──│└` 方框，级别 DEBUG |
| `System.out` | `LogUtil.print()` 的 `System.out.println("Log>>>> [文件:行]...")` | 原始一行文本，级别 INFO |

**含 token 文本的关键日志**：
- `刷新token成功, token = <JWT>, expired = ...`（`viewmodel/g.java`，`LogUtil.d`）
- `用户登录成功：<STUserModel>`（`viewmodel/d.java`，含完整用户模型与 token）
- 失败路径 `刷新token失败：...`（`viewmodel/h.java`，`LogUtil.e`）

**捕获命令**（任意一种即可）：
```
adb logcat -s PRETTY_LOGGER:System.out          # 按两个 tag 过滤
adb logcat | grep -E "刷新token|用户登录成功"    # 按内容抓，覆盖两个 tag（最稳）
```

**磁盘兜底**：Logger 框架同时把带时间戳的行写文件
`/sdcard/logger/logs_%d.csv`（默认路径 `Environment.getExternalStorageDirectory()/logger`，单文件上限 512000 字节），
行格式 `毫秒 时间 DEBUG/INFO <tag> <消息>`，`adb pull /sdcard/logger` 可取回离线翻查。

### 6.2 词书列表（prod.app）
```
GET /wms/custom/book/list
Header: Authorization: 'Bearer '<token>
→ data:[{id, name, downloadUrl, fileSize, ...}]
```

### 6.3 资源列表（prod.study，电子书等）
```
GET /wms/wait/download?cId=<1单词本|6电子书>&offset=0&size=N
Header: Authorization: 'Bearer '<token>
→ data:{total, list:[{bookId, url, size, ...}]}
```

### 6.4 下载 .hd（S3/CDN 公有读，无需 token）
```
curl -o out.hd 'http://p.s3.tuwa.starot.com<downloadUrl | url>'
```
- 自定义词书（type=1）：`/book/custom_v1/<userId>/<bookId>_<ts>.hd`（二进制）
- 电子书（type=2）：`/book/custom_v1/<userId>/ebook_<ts>.hd`（纯文本）

### 6.5 本地生成（触发「保存」）
在 App 内创建/保存自定义词书后，`.hd` 写入
`getExternalFilesDir(null)/<bookId>.hd` = `/storage/emulated/0/Android/data/<包名>/files/<bookId>.hd`，经 `adb pull` 取回。

### 6.6 预置词书（系统词书）全集

预置词书全集**只存在于 App 侧（prod.app `/ums/study/*`）**，由 App「资源」页下发；设备侧只接收 App 推送的单本（`/ums/study/push`，只传 bookId），不持有全集。本次实测（rId=18663，2026-09-23）共 **441 本**，合计约 **5.79 GB**。下载 URL 前缀 `http://p.s3.tuwa.starot.com`。采集方法/调用链见 `README.intel.md §13.6`。

| 分类 | 二级分类 | 词书名称 | 下载地址 | 大小 |
|---|---|---|---|---|
| 背单词 | 小学 | KET词汇正序版 | `http://p.s3.tuwa.starot.com/book/v1/750_1687916651407.hd` | 44.1 MB |
| 背单词 | 小学 | PET词汇正序版(下) | `http://p.s3.tuwa.starot.com/book/v1/751_1685929968788.hd` | 42.9 MB |
| 背单词 | 小学 | 2022新课标词汇小学 | `http://p.s3.tuwa.starot.com/book/v1/752_1686619690193.hd` | 13.3 MB |
| 背单词 | 小学 | 一年级(上) | `http://p.s3.tuwa.starot.com/book/v1/855_1664155019083.hd` | 1.3 MB |
| 背单词 | 小学 | 一年级(下) | `http://p.s3.tuwa.starot.com/book/v1/856_1686023848929.hd` | 1.2 MB |
| 背单词 | 小学 | 二年级(上) | `http://p.s3.tuwa.starot.com/book/v1/857_1686273934610.hd` | 1.2 MB |
| 背单词 | 小学 | 二年级(下) | `http://p.s3.tuwa.starot.com/book/v1/858_1667823576415.hd` | 1.2 MB |
| 背单词 | 小学 | 三年级(上) | `http://p.s3.tuwa.starot.com/book/v1/859_1663924016644.hd` | 3.0 MB |
| 背单词 | 小学 | 三年级(下) | `http://p.s3.tuwa.starot.com/book/v1/860_1686030373064.hd` | 2.0 MB |
| 背单词 | 小学 | 四年级(上) | `http://p.s3.tuwa.starot.com/book/v1/861_1686210461885.hd` | 2.9 MB |
| 背单词 | 小学 | 四年级(下) | `http://p.s3.tuwa.starot.com/book/v1/862_1663924066743.hd` | 2.9 MB |
| 背单词 | 小学 | 五年级(上) | `http://p.s3.tuwa.starot.com/book/v1/863_1664175637861.hd` | 4.3 MB |
| 背单词 | 小学 | 五年级(下) | `http://p.s3.tuwa.starot.com/book/v1/864_1664175559235.hd` | 1.6 MB |
| 背单词 | 小学 | 六年级(上) | `http://p.s3.tuwa.starot.com/book/v1/865_1664327751638.hd` | 6.6 MB |
| 背单词 | 小学 | 六年级(下) | `http://p.s3.tuwa.starot.com/book/v1/866_1664248641256.hd` | 6.4 MB |
| 背单词 | 小学 | PET词汇正序版(上) | `http://p.s3.tuwa.starot.com/book/v1/875_1683511468196.hd` | 43.2 MB |
| 背单词 | 小学 | KET词汇乱序版 | `http://p.s3.tuwa.starot.com/book/v1/1120_1687916674231.hd` | 44.1 MB |
| 背单词 | 小学 | PET词汇乱序版(上) | `http://p.s3.tuwa.starot.com/book/v1/1121_1683513240590.hd` | 43.2 MB |
| 背单词 | 小学 | PET词汇乱序版(下) | `http://p.s3.tuwa.starot.com/book/v1/1122_1685930029319.hd` | 42.9 MB |
| 背单词 | 小学 | 小学新课标词汇图文版 | `http://p.s3.tuwa.starot.com/book/v1/1933_1685516401063.hd` | 14.3 MB |
| 背单词 | 小学 | FCE核心词汇 | `http://p.s3.tuwa.starot.com/book/v1/4036_1678188100382.hd` | 62.2 MB |
| 背单词 | 小学 | KET词汇英英释义版（公测版） | `http://p.s3.tuwa.starot.com/book/v1/11239_1689849855947.hd` | 49.2 MB |
| 背单词 | 小学 | KET考试必背词组 | `http://p.s3.tuwa.starot.com/book/v1/11566_1691479801148.hd` | 13.1 MB |
| 背单词 | 小学 | PET考试必背词组 | `http://p.s3.tuwa.starot.com/book/v1/11567_1691724822954.hd` | 27.0 MB |
| 背单词 | 小学 | 小学考试必背词组400个 | `http://p.s3.tuwa.starot.com/book/v1/11568_1691480145377.hd` | 16.4 MB |
| 背单词 | 初中 | 初中词汇汇总上册 | `http://p.s3.tuwa.starot.com/book/v1/753_1685699637466.hd` | 26.3 MB |
| 背单词 | 初中 | 中考必背词汇 | `http://p.s3.tuwa.starot.com/book/v1/754_1687766146829.hd` | 44.3 MB |
| 背单词 | 初中 | 初中词汇汇总下册 | `http://p.s3.tuwa.starot.com/book/v1/1034_1685699654032.hd` | 27.6 MB |
| 背单词 | 初中 | 2022新课标词汇初中 | `http://p.s3.tuwa.starot.com/book/v1/1442_1687143854383.hd` | 43.7 MB |
| 背单词 | 初中 | 中考必备词汇正序版 | `http://p.s3.tuwa.starot.com/book/v1/1455_1663159417631.hd` | 43.7 MB |
| 背单词 | 初中 | 六年级(上) | `http://p.s3.tuwa.starot.com/book/v1/1586_1686563253526.hd` | 5.8 MB |
| 背单词 | 初中 | 六年级(下) | `http://p.s3.tuwa.starot.com/book/v1/1587_1686034546818.hd` | 7.4 MB |
| 背单词 | 初中 | 七年级(上) | `http://p.s3.tuwa.starot.com/book/v1/1588_1684727582848.hd` | 5.9 MB |
| 背单词 | 初中 | 七年级(下) | `http://p.s3.tuwa.starot.com/book/v1/1589_1673427309200.hd` | 5.6 MB |
| 背单词 | 初中 | 八年级(上) | `http://p.s3.tuwa.starot.com/book/v1/1590_1687768627794.hd` | 6.9 MB |
| 背单词 | 初中 | 八年级(下) | `http://p.s3.tuwa.starot.com/book/v1/1591_1673427330147.hd` | 6.6 MB |
| 背单词 | 初中 | 九年级(上) | `http://p.s3.tuwa.starot.com/book/v1/1592_1673083047504.hd` | 5.9 MB |
| 背单词 | 初中 | 九年级(下) | `http://p.s3.tuwa.starot.com/book/v1/1593_1673332449811.hd` | 3.1 MB |
| 背单词 | 初中 | 初中新课标词汇图文版 | `http://p.s3.tuwa.starot.com/book/v1/1934_1661344231607.hd` | 37.7 MB |
| 背单词 | 初中 | 上海中考考纲词汇 | `http://p.s3.tuwa.starot.com/book/v1/3471_1685963128997.hd` | 60.5 MB |
| 背单词 | 初中 | 上海中考考纲词汇乱序版 | `http://p.s3.tuwa.starot.com/book/v1/7223_1685963248402.hd` | 60.5 MB |
| 背单词 | 高中 | 高中必修1 | `http://p.s3.tuwa.starot.com/book/v1/339_1686206995967.hd` | 9.0 MB |
| 背单词 | 高中 | 高中必修2 | `http://p.s3.tuwa.starot.com/book/v1/340_1686559918216.hd` | 9.7 MB |
| 背单词 | 高中 | 高中必修3 | `http://p.s3.tuwa.starot.com/book/v1/341_1686560421457.hd` | 11.0 MB |
| 背单词 | 高中 | 高中选修1 | `http://p.s3.tuwa.starot.com/book/v1/342_1674012363195.hd` | 8.7 MB |
| 背单词 | 高中 | 高中选修2 | `http://p.s3.tuwa.starot.com/book/v1/343_1669968334866.hd` | 7.8 MB |
| 背单词 | 高中 | 高中选修3 | `http://p.s3.tuwa.starot.com/book/v1/344_1686207702491.hd` | 8.5 MB |
| 背单词 | 高中 | 高中选修4 | `http://p.s3.tuwa.starot.com/book/v1/345_1676254230361.hd` | 6.5 MB |
| 背单词 | 高中 | 高考必背3500词A-E | `http://p.s3.tuwa.starot.com/book/v1/761_1685946575976.hd` | 36.8 MB |
| 背单词 | 高中 | 高考必背3500词F-Q | `http://p.s3.tuwa.starot.com/book/v1/762_1686018197951.hd` | 38.1 MB |
| 背单词 | 高中 | 高考必背3500词R-Z | `http://p.s3.tuwa.starot.com/book/v1/763_1685522406846.hd` | 34.3 MB |
| 背单词 | 高中 | 高中必修4 | `http://p.s3.tuwa.starot.com/book/v1/764_1670377205913.hd` | 7.4 MB |
| 背单词 | 高中 | 高中必修5 | `http://p.s3.tuwa.starot.com/book/v1/765_1686193080547.hd` | 10.3 MB |
| 背单词 | 高中 | 高考必背3500词乱序版(上) | `http://p.s3.tuwa.starot.com/book/v1/1563_1686017483511.hd` | 36.4 MB |
| 背单词 | 高中 | 高考必背3500词乱序版(中) | `http://p.s3.tuwa.starot.com/book/v1/1564_1685522268102.hd` | 36.5 MB |
| 背单词 | 高中 | 高考必背3500词乱序版(下) | `http://p.s3.tuwa.starot.com/book/v1/1565_1686018177906.hd` | 36.4 MB |
| 背单词 | 高中 | 2022年上海高考词汇 | `http://p.s3.tuwa.starot.com/book/v1/1930_1681091009753.hd` | 81.4 MB |
| 背单词 | 大学 | 英语四级词汇 | `http://p.s3.tuwa.starot.com/book/v1/354_1686548474561.hd` | 38.8 MB |
| 背单词 | 大学 | 英语四级词汇乱序版 | `http://p.s3.tuwa.starot.com/book/v1/1840_1686548493821.hd` | 39.3 MB |
| 背单词 | 大学 | 四级离线正序版A-L | `http://p.s3.tuwa.starot.com/book/v1/8296_1686548656530.hd` | 66.7 MB |
| 背单词 | 大学 | 四级离线正序版M-Z | `http://p.s3.tuwa.starot.com/book/v1/8297_1686548690838.hd` | 61.5 MB |
| 背单词 | 大学 | 四级离线乱序版上 | `http://p.s3.tuwa.starot.com/book/v1/8298_1686548716058.hd` | 64.4 MB |
| 背单词 | 大学 | 四级离线乱序版下 | `http://p.s3.tuwa.starot.com/book/v1/8299_1686548740733.hd` | 63.8 MB |
| 背单词 | 大学 | 自考英语核心词汇 | `http://p.s3.tuwa.starot.com/book/v1/8312_1678156479318.hd` | 51.1 MB |
| 背单词 | 大学 | 六级离线正序版01 | `http://p.s3.tuwa.starot.com/book/v1/8639_1679023544568.hd` | 86.2 MB |
| 背单词 | 大学 | 六级离线正序版02 | `http://p.s3.tuwa.starot.com/book/v1/8640_1679023623664.hd` | 85.4 MB |
| 背单词 | 大学 | 六级离线乱序版01 | `http://p.s3.tuwa.starot.com/book/v1/8641_1679023699809.hd` | 86.2 MB |
| 背单词 | 大学 | 六级离线乱序版02 | `http://p.s3.tuwa.starot.com/book/v1/8642_1679023763759.hd` | 85.4 MB |
| 背单词 | 大学 | 2024专升本词汇大纲 | `http://p.s3.tuwa.starot.com/book/v1/14141_1713422774252.hd` | 92.7 MB |
| 背单词 | 大学 | 专升本真题核心词汇 | `http://p.s3.tuwa.starot.com/book/v1/14142_1713423163411.hd` | 53.7 MB |
| 背单词 | 考研 | 考研词汇上册 | `http://p.s3.tuwa.starot.com/book/v1/8651_1679040359003.hd` | 85.3 MB |
| 背单词 | 考研 | 考研词汇下册 | `http://p.s3.tuwa.starot.com/book/v1/8652_1679040415382.hd` | 84.9 MB |
| 背单词 | 其他 | 新概念词汇1 | `http://p.s3.tuwa.starot.com/book/v1/1459_1687142572588.hd` | 24.6 MB |
| 背单词 | 其他 | 新概念词汇2 | `http://p.s3.tuwa.starot.com/book/v1/1460_1687767899514.hd` | 24.8 MB |
| 背单词 | 其他 | 新概念词汇3 | `http://p.s3.tuwa.starot.com/book/v1/1645_1678352846333.hd` | 32.9 MB |
| 背单词 | 其他 | 新概念词汇4 | `http://p.s3.tuwa.starot.com/book/v1/1775_1678352866069.hd` | 26.1 MB |
| 背单词 | 日语 | 新标准日本语初级上册 | `http://p.s3.tuwa.starot.com/book/v1/14140_1713412065497.hd` | 32.7 MB |
| 听力练习 | 小学 | 小学生阅读100篇1 | `http://p.s3.tuwa.starot.com/book/v1/1092_1676374078545.hd` | 7.7 MB |
| 听力练习 | 小学 | 小学生阅读100篇2 | `http://p.s3.tuwa.starot.com/book/v1/1093_1676374090546.hd` | 7.6 MB |
| 听力练习 | 小学 | 小学生阅读100篇3 | `http://p.s3.tuwa.starot.com/book/v1/1256_1676374121379.hd` | 7.5 MB |
| 听力练习 | 小学 | 小学生阅读100篇4 | `http://p.s3.tuwa.starot.com/book/v1/1257_1676374132907.hd` | 6.7 MB |
| 听力练习 | 小学 | 小学生阅读100篇5 | `http://p.s3.tuwa.starot.com/book/v1/1258_1676374144604.hd` | 7.2 MB |
| 听力练习 | 小学 | 读故事记单词 小学 | `http://p.s3.tuwa.starot.com/book/v1/1259_1676374156363.hd` | 51.4 MB |
| 听力练习 | 初中 | 读故事记单词 初中 | `http://p.s3.tuwa.starot.com/book/v1/1260_1676374181583.hd` | 40.7 MB |
| 听力练习 | 初中 | 奥运英语 | `http://p.s3.tuwa.starot.com/book/v1/1261_1676374204626.hd` | 39.5 MB |
| 听力练习 | 初中 | 英语大世界阅读版1 | `http://p.s3.tuwa.starot.com/book/v1/1262_1676374228893.hd` | 27.0 MB |
| 听力练习 | 初中 | 英语大世界阅读版2 | `http://p.s3.tuwa.starot.com/book/v1/1263_1676374247238.hd` | 33.4 MB |
| 听力练习 | 初中 | 英语大世界阅读版3 | `http://p.s3.tuwa.starot.com/book/v1/1264_1676374279832.hd` | 23.1 MB |
| 听力练习 | 高中 | 伊索寓言 | `http://p.s3.tuwa.starot.com/book/v1/1107_1676374101506.hd` | 23.7 MB |
| 听力练习 | 高中 | 成语经典用词上 | `http://p.s3.tuwa.starot.com/book/v1/1265_1676374296065.hd` | 27.4 MB |
| 听力练习 | 高中 | 成语经典用词下 | `http://p.s3.tuwa.starot.com/book/v1/1266_1676374315428.hd` | 24.5 MB |
| 听力练习 | 高中 | 读故事记单词 高中 | `http://p.s3.tuwa.starot.com/book/v1/1267_1676374332814.hd` | 45.5 MB |
| 听力练习 | 大学 | 总统演讲1 | `http://p.s3.tuwa.starot.com/book/v1/1269_1676374355877.hd` | 33.7 MB |
| 听力练习 | 大学 | 总统演讲2 | `http://p.s3.tuwa.starot.com/book/v1/1270_1676374376016.hd` | 31.2 MB |
| 口语通关 | 小学 | 口语这些就够了 基础1 | `http://p.s3.tuwa.starot.com/book/v1/1095_1657855105902.hd` | 11 KB |
| 口语通关 | 小学 | 口语这些就够了 基础2 | `http://p.s3.tuwa.starot.com/book/v1/1096_1657855305525.hd` | 12 KB |
| 口语通关 | 小学 | 小学生阅读100篇1 | `http://p.s3.tuwa.starot.com/book/v1/1102_1657179034987.hd` | 51 KB |
| 口语通关 | 小学 | 小学生阅读100篇2 | `http://p.s3.tuwa.starot.com/book/v1/1103_1657179052498.hd` | 50 KB |
| 口语通关 | 小学 | 互动儿童口语Book1 | `http://p.s3.tuwa.starot.com/book/v1/1204_1663750102137.hd` | 8 KB |
| 口语通关 | 小学 | 互动儿童口语Book2 | `http://p.s3.tuwa.starot.com/book/v1/1205_1663750126518.hd` | 8 KB |
| 口语通关 | 小学 | 互动儿童口语Book3 | `http://p.s3.tuwa.starot.com/book/v1/1206_1663750141672.hd` | 7 KB |
| 口语通关 | 小学 | 互动儿童口语Book4 | `http://p.s3.tuwa.starot.com/book/v1/1207_1663750161141.hd` | 7 KB |
| 口语通关 | 小学 | 互动儿童口语Book5 | `http://p.s3.tuwa.starot.com/book/v1/1208_1663750175783.hd` | 8 KB |
| 口语通关 | 小学 | 互动儿童口语Book6 | `http://p.s3.tuwa.starot.com/book/v1/1209_1663750189047.hd` | 7 KB |
| 口语通关 | 小学 | 互动儿童口语Book7 | `http://p.s3.tuwa.starot.com/book/v1/1210_1663750206403.hd` | 7 KB |
| 口语通关 | 小学 | 互动儿童口语Book8 | `http://p.s3.tuwa.starot.com/book/v1/1211_1663750227247.hd` | 8 KB |
| 口语通关 | 小学 | 互动儿童口语Book9 | `http://p.s3.tuwa.starot.com/book/v1/1212_1663750258770.hd` | 6 KB |
| 口语通关 | 小学 | 互动儿童口语Book10 | `http://p.s3.tuwa.starot.com/book/v1/1213_1663750275404.hd` | 5 KB |
| 口语通关 | 小学 | 互动儿童口语Book11 | `http://p.s3.tuwa.starot.com/book/v1/1214_1663750289595.hd` | 7 KB |
| 口语通关 | 小学 | 互动儿童口语Book12 | `http://p.s3.tuwa.starot.com/book/v1/1215_1663750306160.hd` | 7 KB |
| 口语通关 | 小学 | 口语这些就够了 基础3 | `http://p.s3.tuwa.starot.com/book/v1/1216_1657856292679.hd` | 14 KB |
| 口语通关 | 小学 | 口语这些就够了 基础4 | `http://p.s3.tuwa.starot.com/book/v1/1217_1657856315296.hd` | 14 KB |
| 口语通关 | 小学 | 口语这些就够了 基础5 | `http://p.s3.tuwa.starot.com/book/v1/1218_1657856339074.hd` | 13 KB |
| 口语通关 | 小学 | 口语这些就够了 基础6 | `http://p.s3.tuwa.starot.com/book/v1/1219_1657856363127.hd` | 14 KB |
| 口语通关 | 初中 | 口语这些就够了 提升1 | `http://p.s3.tuwa.starot.com/book/v1/1097_1657855337301.hd` | 15 KB |
| 口语通关 | 初中 | 口语这些就够了 提升2 | `http://p.s3.tuwa.starot.com/book/v1/1098_1657855371210.hd` | 14 KB |
| 口语通关 | 初中 | 口语这些就够了 基础1 | `http://p.s3.tuwa.starot.com/book/v1/1104_1657179073567.hd` | 11 KB |
| 口语通关 | 初中 | 口语这些就够了 基础2 | `http://p.s3.tuwa.starot.com/book/v1/1105_1657179089281.hd` | 12 KB |
| 口语通关 | 初中 | 零起点 口语突破1 | `http://p.s3.tuwa.starot.com/book/v1/1124_1657855564739.hd` | 79 KB |
| 口语通关 | 初中 | 疯狂英语 PART1 | `http://p.s3.tuwa.starot.com/book/v1/1220_1657856407244.hd` | 15 KB |
| 口语通关 | 初中 | 疯狂英语 PART2 | `http://p.s3.tuwa.starot.com/book/v1/1221_1657856437812.hd` | 15 KB |
| 口语通关 | 初中 | 疯狂英语 PART3 | `http://p.s3.tuwa.starot.com/book/v1/1222_1657856461624.hd` | 14 KB |
| 口语通关 | 初中 | 疯狂英语 PART4 | `http://p.s3.tuwa.starot.com/book/v1/1223_1657856482669.hd` | 15 KB |
| 口语通关 | 初中 | 口语这些就够了 提升3 | `http://p.s3.tuwa.starot.com/book/v1/1224_1657856508755.hd` | 14 KB |
| 口语通关 | 初中 | 口语这些就够了 提升4 | `http://p.s3.tuwa.starot.com/book/v1/1225_1657856535614.hd` | 12 KB |
| 口语通关 | 初中 | 口语这些就够了 提升5 | `http://p.s3.tuwa.starot.com/book/v1/1226_1657856568708.hd` | 13 KB |
| 口语通关 | 初中 | 口语这些就够了 提升6 | `http://p.s3.tuwa.starot.com/book/v1/1227_1657856605287.hd` | 12 KB |
| 口语通关 | 初中 | 零起点 口语突破2 | `http://p.s3.tuwa.starot.com/book/v1/1228_1657856629622.hd` | 49 KB |
| 口语通关 | 初中 | 零起点 口语突破3 | `http://p.s3.tuwa.starot.com/book/v1/1229_1657856726345.hd` | 29 KB |
| 口语通关 | 初中 | 零起点 口语突破4 | `http://p.s3.tuwa.starot.com/book/v1/1230_1657856756579.hd` | 18 KB |
| 口语通关 | 初中 | 零起点 口语突破5 | `http://p.s3.tuwa.starot.com/book/v1/1231_1657856782818.hd` | 17 KB |
| 口语通关 | 高中 | 读故事记单词  | `http://p.s3.tuwa.starot.com/book/v1/1106_1659348284172.hd` | 398 KB |
| 口语通关 | 高中 | 英语900句 基础语句1 | `http://p.s3.tuwa.starot.com/book/v1/1232_1657856811919.hd` | 23 KB |
| 口语通关 | 高中 | 英语900句 基础语句2 | `http://p.s3.tuwa.starot.com/book/v1/1233_1657856859091.hd` | 25 KB |
| 口语通关 | 高中 | 英语900句 基础语句3 | `http://p.s3.tuwa.starot.com/book/v1/1234_1657856925061.hd` | 25 KB |
| 口语通关 | 高中 | 英语900句 基础语句4 | `http://p.s3.tuwa.starot.com/book/v1/1235_1657856956846.hd` | 25 KB |
| 口语通关 | 高中 | 英语900句 基础语句5 | `http://p.s3.tuwa.starot.com/book/v1/1236_1657857004553.hd` | 26 KB |
| 口语通关 | 高中 | 英语900句 基础语句6 | `http://p.s3.tuwa.starot.com/book/v1/1237_1657857033551.hd` | 27 KB |
| 口语通关 | 大学 | 旅游英语300句1 | `http://p.s3.tuwa.starot.com/book/v1/1108_1663750081468.hd` | 16 KB |
| 口语通关 | 大学 | 旅游英语300句2 | `http://p.s3.tuwa.starot.com/book/v1/1109_1657179153406.hd` | 10 KB |
| 口语通关 | 大学 | 口语大师 突破英语会话 | `http://p.s3.tuwa.starot.com/book/v1/1243_1658397414720.hd` | 126 KB |
| 口语通关 | 大学 | 旅游英语300句3 | `http://p.s3.tuwa.starot.com/book/v1/1244_1657864315640.hd` | 9 KB |
| 口语通关 | 大学 | 旅游英语300句4 | `http://p.s3.tuwa.starot.com/book/v1/1245_1657864482849.hd` | 10 KB |
| 口语通关 | 大学 | 旅游英语300句5 | `http://p.s3.tuwa.starot.com/book/v1/1246_1657864514577.hd` | 8 KB |
| 口语通关 | 大学 | 旅游英语300句6 | `http://p.s3.tuwa.starot.com/book/v1/1247_1657864537093.hd` | 14 KB |
| 口语通关 | 大学 | 如何说一口标准地道的美国英语 | `http://p.s3.tuwa.starot.com/book/v1/1248_1658455863824.hd` | 251 KB |
| 口语通关 | 大学 | 英语900句 进阶表达1 | `http://p.s3.tuwa.starot.com/book/v1/1249_1657864668198.hd` | 29 KB |
| 口语通关 | 大学 | 英语900句 进阶表达2 | `http://p.s3.tuwa.starot.com/book/v1/1250_1657864731862.hd` | 30 KB |
| 口语通关 | 大学 | 英语900句 进阶表达3 | `http://p.s3.tuwa.starot.com/book/v1/1251_1657864772601.hd` | 28 KB |
| 口语通关 | 大学 | 英语900句 进阶表达4 | `http://p.s3.tuwa.starot.com/book/v1/1252_1657864794120.hd` | 27 KB |
| 口语通关 | 大学 | 英语900句 进阶表达5 | `http://p.s3.tuwa.starot.com/book/v1/1253_1657864822643.hd` | 30 KB |
| 古诗文 | 全部 | 一年级(上) | `http://p.s3.tuwa.starot.com/book/v1/987_1675942739414.hd` | 1.5 MB |
| 古诗文 | 全部 | 一年级(下) | `http://p.s3.tuwa.starot.com/book/v1/988_1675942752119.hd` | 1.2 MB |
| 古诗文 | 全部 | 二年级(上) | `http://p.s3.tuwa.starot.com/book/v1/989_1675942762914.hd` | 1.3 MB |
| 古诗文 | 全部 | 二年级(下) | `http://p.s3.tuwa.starot.com/book/v1/990_1675942773642.hd` | 1.3 MB |
| 古诗文 | 全部 | 三年级(上) | `http://p.s3.tuwa.starot.com/book/v1/991_1675942784682.hd` | 1.7 MB |
| 古诗文 | 全部 | 三年级(下) | `http://p.s3.tuwa.starot.com/book/v1/992_1675942794464.hd` | 475 KB |
| 古诗文 | 全部 | 四年级(上) | `http://p.s3.tuwa.starot.com/book/v1/993_1675942808405.hd` | 2.1 MB |
| 古诗文 | 全部 | 四年级(下） | `http://p.s3.tuwa.starot.com/book/v1/994_1675942818216.hd` | 2.3 MB |
| 古诗文 | 全部 | 五年级(上) | `http://p.s3.tuwa.starot.com/book/v1/995_1692239251849.hd` | 2.0 MB |
| 古诗文 | 全部 | 五年级(下) | `http://p.s3.tuwa.starot.com/book/v1/996_1676274029261.hd` | 2.6 MB |
| 古诗文 | 全部 | 六年级(上) | `http://p.s3.tuwa.starot.com/book/v1/997_1675942853624.hd` | 2.7 MB |
| 古诗文 | 全部 | 六年级(下) | `http://p.s3.tuwa.starot.com/book/v1/998_1676009218791.hd` | 4.1 MB |
| 古诗文 | 全部 | 高中必背古诗词 | `http://p.s3.tuwa.starot.com/book/v1/1959_1673512802840.hd` | 1.1 MB |
| 古诗文 | 全部 | 初中必背古诗词 | `http://p.s3.tuwa.starot.com/book/v1/1976_1675942950606.hd` | 35.7 MB |
| 古诗文 | 全部 | 小学生必背古诗词75首 | `http://p.s3.tuwa.starot.com/book/v1/11496_1691138211724.hd` | 13.7 MB |
| 古诗文 | 全部 | 小学生必背古诗词80首【拓展】 | `http://p.s3.tuwa.starot.com/book/v1/11497_1692769674602.hd` | 23.3 MB |
| 古诗文 | 全部 | 小学生必背文言文 | `http://p.s3.tuwa.starot.com/book/v1/11641_1692008511761.hd` | 43.3 MB |
| 古诗文 | 古文观止 | 第一卷：人物的印记 | `http://p.s3.tuwa.starot.com/book/v1/12714_1699586967573.hd` | 47.3 MB |
| 古诗文 | 古文观止 | 第二卷：历史的回响 | `http://p.s3.tuwa.starot.com/book/v1/12715_1699587044044.hd` | 41.0 MB |
| 古诗文 | 古文观止 | 第三卷：书信的魅力 | `http://p.s3.tuwa.starot.com/book/v1/12716_1699587096420.hd` | 49.3 MB |
| 古诗文 | 古文观止 | 第四卷：游记的盛宴 | `http://p.s3.tuwa.starot.com/book/v1/12717_1699587155028.hd` | 40.7 MB |
| 古诗文 | 古文观止 | 第五卷：论辩的艺术 | `http://p.s3.tuwa.starot.com/book/v1/12718_1699587229452.hd` | 43.3 MB |
| 知识卡片 | 英语 | 生活英语(上) | `http://p.s3.tuwa.starot.com/book/v1/3177_1663245549387.tc` | 12.4 MB |
| 知识卡片 | 英语 | 生活英语(中) | `http://p.s3.tuwa.starot.com/book/v1/3178_1663245701501.tc` | 11.6 MB |
| 知识卡片 | 英语 | 生活英语(下) | `http://p.s3.tuwa.starot.com/book/v1/3179_1665477630431.tc` | 13.4 MB |
| 知识卡片 | 英语 | 情景英语 | `http://p.s3.tuwa.starot.com/book/v1/3180_1663245909481.tc` | 7.1 MB |
| 知识卡片 | 英语 | 情景英语少儿篇 | `http://p.s3.tuwa.starot.com/book/v1/3181_1663674650945.tc` | 3.7 MB |
| 知识卡片 | 英语 | 音标学习 | `http://p.s3.tuwa.starot.com/book/v1/4619_1668598536291.tc` | 3.4 MB |
| 知识卡片 | 英语 | 三年级上(人教版PEP) | `http://p.s3.tuwa.starot.com/book/v1/6418_1672994681729.tc` | 25.4 MB |
| 知识卡片 | 英语 | 三年级下(人教版PEP) | `http://p.s3.tuwa.starot.com/book/v1/6419_1672994998742.tc` | 43.2 MB |
| 知识卡片 | 英语 | 四年级上(人教版PEP) | `http://p.s3.tuwa.starot.com/book/v1/6420_1672995089520.tc` | 29.9 MB |
| 知识卡片 | 英语 | 四年级下(人教版PEP) | `http://p.s3.tuwa.starot.com/book/v1/6421_1672995175613.tc` | 39.8 MB |
| 知识卡片 | 英语 | 五年级上(人教版PEP) | `http://p.s3.tuwa.starot.com/book/v1/6422_1672995248503.tc` | 36.5 MB |
| 知识卡片 | 英语 | 五年级下(人教版PEP) | `http://p.s3.tuwa.starot.com/book/v1/6423_1672995327406.tc` | 39.8 MB |
| 知识卡片 | 英语 | 六年级上(人教版PEP) | `http://p.s3.tuwa.starot.com/book/v1/6424_1672995448523.tc` | 41.7 MB |
| 知识卡片 | 英语 | 六年级下(人教版PEP) | `http://p.s3.tuwa.starot.com/book/v1/6425_1672995536156.tc` | 38.9 MB |
| 知识卡片 | 英语 | 七年级上(人教版) | `http://p.s3.tuwa.starot.com/book/v1/6426_1672995634912.tc` | 49.6 MB |
| 知识卡片 | 英语 | 七年级下(人教版) | `http://p.s3.tuwa.starot.com/book/v1/6427_1672995730098.tc` | 52.5 MB |
| 知识卡片 | 英语 | 八年级上(人教版) | `http://p.s3.tuwa.starot.com/book/v1/6428_1672995812365.tc` | 47.2 MB |
| 知识卡片 | 英语 | 八年级下(人教版) | `http://p.s3.tuwa.starot.com/book/v1/6429_1672995899039.tc` | 50.5 MB |
| 知识卡片 | 英语 | 九年级全册(人教版) | `http://p.s3.tuwa.starot.com/book/v1/6430_1672996204635.tc` | 77.9 MB |
| 知识卡片 | 英语 | 24新版三年级上(人教版PEP) | `http://p.s3.tuwa.starot.com/book/v1/16127_1727427560903.bin` | 19.1 MB |
| 知识卡片 | 英语 | 24版七年级上(人教版) | `http://p.s3.tuwa.starot.com/book/v1/16356_1728702010413.bin` | 24.4 MB |
| 知识卡片 | 国学 | 成语故事 | `http://p.s3.tuwa.starot.com/book/v1/3182_1663674671522.tc` | 1.5 MB |
| 知识卡片 | 国学 | 声律启蒙 | `http://p.s3.tuwa.starot.com/book/v1/3183_1663246080634.tc` | 1.1 MB |
| 知识卡片 | 国学 | 笠翁对韵 | `http://p.s3.tuwa.starot.com/book/v1/3184_1663246129124.tc` | 1.1 MB |
| 知识卡片 | 语文 | 100典故（高考篇） | `http://p.s3.tuwa.starot.com/book/v1/3671_1666919600702.tc` | 1.2 MB |
| 知识卡片 | 语文 | 成语错别字823例 | `http://p.s3.tuwa.starot.com/book/v1/3673_1665476005611.tc` | 1.6 MB |
| 知识卡片 | 语文 | 仿句联句164组 | `http://p.s3.tuwa.starot.com/book/v1/3675_1665537325229.tc` | 1.3 MB |
| 知识卡片 | 语文 | 高考语文成语 | `http://p.s3.tuwa.starot.com/book/v1/3676_1665476158663.tc` | 1.5 MB |
| 知识卡片 | 语文 | 文学文化常识 | `http://p.s3.tuwa.starot.com/book/v1/3678_1665476252762.tc` | 1.3 MB |
| 知识卡片 | 语文 | 小学成语200条 | `http://p.s3.tuwa.starot.com/book/v1/3681_1665476489740.tc` | 1.1 MB |
| 知识卡片 | 语文 | 歇后语儿童版(100句) | `http://p.s3.tuwa.starot.com/book/v1/3682_1665476536606.tc` | 1.1 MB |
| 知识卡片 | 语文 | 歇后语千句 | `http://p.s3.tuwa.starot.com/book/v1/3683_1667198751545.tc` | 1.5 MB |
| 知识卡片 | 政治 | 人教版道德与法治(七年级上) | `http://p.s3.tuwa.starot.com/book/v1/3684_1665476703285.tc` | 1.2 MB |
| 知识卡片 | 政治 | 人教版道德与法治(七年级下) | `http://p.s3.tuwa.starot.com/book/v1/3685_1665476755795.tc` | 1.2 MB |
| 知识卡片 | 政治 | 人教版道德与法治(八年级上) | `http://p.s3.tuwa.starot.com/book/v1/3686_1666920594172.tc` | 1.1 MB |
| 知识卡片 | 政治 | 人教版道德与法治(八年级下) | `http://p.s3.tuwa.starot.com/book/v1/3687_1666920603381.tc` | 1.3 MB |
| 知识卡片 | 政治 | 人教版道德与法治(九年级上) | `http://p.s3.tuwa.starot.com/book/v1/3688_1666920612287.tc` | 1.2 MB |
| 知识卡片 | 政治 | 人教版道德与法治(九年级下) | `http://p.s3.tuwa.starot.com/book/v1/3689_1666920621178.tc` | 1.1 MB |
| 知识卡片 | 历史 | 历史七年级上 | `http://p.s3.tuwa.starot.com/book/v1/3661_1665475374716.tc` | 1.1 MB |
| 知识卡片 | 历史 | 历史七年级下 | `http://p.s3.tuwa.starot.com/book/v1/3662_1665475442171.tc` | 1.1 MB |
| 知识卡片 | 历史 | 历史八年级上 | `http://p.s3.tuwa.starot.com/book/v1/3663_1665475480299.tc` | 1.1 MB |
| 知识卡片 | 历史 | 历史八年级下 | `http://p.s3.tuwa.starot.com/book/v1/3664_1665475539731.tc` | 1.1 MB |
| 知识卡片 | 历史 | 历史九年级上 | `http://p.s3.tuwa.starot.com/book/v1/3665_1665475575688.tc` | 1.1 MB |
| 知识卡片 | 历史 | 历史九年级下 | `http://p.s3.tuwa.starot.com/book/v1/3666_1665475630730.tc` | 1.1 MB |
| 知识卡片 | 历史 | 典故 | `http://p.s3.tuwa.starot.com/book/v1/3667_1665475699841.tc` | 1.1 MB |
| 知识卡片 | 历史 | 人物 | `http://p.s3.tuwa.starot.com/book/v1/3668_1665475745367.tc` | 1.3 MB |
| 知识卡片 | 物理 | 初中物理 | `http://p.s3.tuwa.starot.com/book/v1/3670_1665475863604.tc` | 1.2 MB |
| 知识卡片 | 生物 | 初中生物 | `http://p.s3.tuwa.starot.com/book/v1/3669_1666920517039.tc` | 1.3 MB |
| 知识卡片 | 百科问答 | 十万个为什么(动物世界) | `http://p.s3.tuwa.starot.com/book/v1/3690_1666920644462.tc` | 1.1 MB |
| 知识卡片 | 百科问答 | 十万个为什么(浩瀚宇宙) | `http://p.s3.tuwa.starot.com/book/v1/3691_1666920660066.tc` | 1.1 MB |
| 知识卡片 | 百科问答 | 十万个为什么(军事交通) | `http://p.s3.tuwa.starot.com/book/v1/3692_1666920671443.tc` | 1.1 MB |
| 知识卡片 | 百科问答 | 十万个为什么(人体奥秘) | `http://p.s3.tuwa.starot.com/book/v1/3693_1665477152668.tc` | 1.1 MB |
| 知识卡片 | 百科问答 | 十万个为什么(数理化) | `http://p.s3.tuwa.starot.com/book/v1/3694_1665477189452.tc` | 1.2 MB |
| 知识卡片 | 百科问答 | 十万个为什么(体育与国家) | `http://p.s3.tuwa.starot.com/book/v1/3695_1665477226850.tc` | 1.1 MB |
| 知识卡片 | 百科问答 | 十万个为什么(文化艺术) | `http://p.s3.tuwa.starot.com/book/v1/3696_1665477273141.tc` | 1.2 MB |
| 知识卡片 | 百科问答 | 十万个为什么(我们的地球) | `http://p.s3.tuwa.starot.com/book/v1/3697_1665477309899.tc` | 1.2 MB |
| 知识卡片 | 百科问答 | 十万个为什么(信息科技) | `http://p.s3.tuwa.starot.com/book/v1/3698_1665477347850.tc` | 1.2 MB |
| 知识卡片 | 百科问答 | 十万个为什么(营养与健康) | `http://p.s3.tuwa.starot.com/book/v1/3699_1665477388841.tc` | 1.2 MB |
| 知识卡片 | 百科问答 | 十万个为什么(植物王国) | `http://p.s3.tuwa.starot.com/book/v1/3700_1665477430682.tc` | 1.1 MB |
| 知识卡片 | 百科问答 | 十万个为什么(中外历史) | `http://p.s3.tuwa.starot.com/book/v1/3701_1665477470698.tc` | 1.2 MB |
| 知识卡片 | 百科问答 | 推理百例 | `http://p.s3.tuwa.starot.com/book/v1/3702_1665477508802.tc` | 1.3 MB |
| 知识卡片 | 高考宝典 | 2025高考复习英语宝典 | `http://p.s3.tuwa.starot.com/book/v1/15399_1722849681292.bin` | 95 KB |
| 听书馆 | 儿童文学 | “下次开船”港 | `http://p.s3.tuwa.starot.com/book/v1/7666_1688117415632.hd` | 8.3 MB |
| 听书馆 | 儿童文学 | 萝卜回来了 | `http://p.s3.tuwa.starot.com/book/v1/10008_1700645191253.hd` | 2.3 MB |
| 听书馆 | 儿童文学 | “下次开船”港 | `http://p.s3.tuwa.starot.com/book/v1/10011_1700645167875.hd` | 8.3 MB |
| 听书馆 | 儿童文学 | "爱丽丝梦游仙境 ——一个女孩的梦境奇遇" | `http://p.s3.tuwa.starot.com/book/v1/10012_1700645139918.hd` | 7.7 MB |
| 听书馆 | 儿童文学 | 宝葫芦的秘密 | `http://p.s3.tuwa.starot.com/book/v1/10014_1700645050341.hd` | 8.7 MB |
| 听书馆 | 儿童文学 | 笨狼的故事 | `http://p.s3.tuwa.starot.com/book/v1/10016_1700645029875.hd` | 7.8 MB |
| 听书馆 | 儿童文学 | 吃黑夜的大象 | `http://p.s3.tuwa.starot.com/book/v1/10021_1700645007066.hd` | 4.4 MB |
| 听书馆 | 儿童文学 | "窗边的小豆豆 ——每一个孩子都值得被尊重" | `http://p.s3.tuwa.starot.com/book/v1/10022_1700644984578.hd` | 9.5 MB |
| 听书馆 | 儿童文学 | "大林和小林 ——两兄弟的不同人生" | `http://p.s3.tuwa.starot.com/book/v1/10023_1700644905795.hd` | 12.7 MB |
| 听书馆 | 儿童文学 | 稻草人 | `http://p.s3.tuwa.starot.com/book/v1/10026_1700722126917.hd` | 8.7 MB |
| 听书馆 | 儿童文学 | "哈利波特与魔法石 ——充满爱与勇敢的魔法世界" | `http://p.s3.tuwa.starot.com/book/v1/10039_1700644812850.hd` | 16.3 MB |
| 听书馆 | 儿童文学 | 了不起的狐狸爸爸 | `http://p.s3.tuwa.starot.com/book/v1/10056_1700644691052.hd` | 7.9 MB |
| 听书馆 | 儿童文学 | 列那狐的故事 | `http://p.s3.tuwa.starot.com/book/v1/10057_1700644663121.hd` | 12.5 MB |
| 听书馆 | 儿童文学 | 木偶奇遇记 | `http://p.s3.tuwa.starot.com/book/v1/10062_1700644643090.hd` | 12.5 MB |
| 听书馆 | 儿童文学 | 男生贾里全传 | `http://p.s3.tuwa.starot.com/book/v1/10064_1700644621625.hd` | 12.9 MB |
| 听书馆 | 儿童文学 | 苹果树上的外婆 | `http://p.s3.tuwa.starot.com/book/v1/10067_1700644599494.hd` | 8.0 MB |
| 听书馆 | 儿童文学 | 天空在脚下 | `http://p.s3.tuwa.starot.com/book/v1/10078_1700644579537.hd` | 5.7 MB |
| 听书馆 | 儿童文学 | 铁丝网上的小花 | `http://p.s3.tuwa.starot.com/book/v1/10079_1700644557938.hd` | 4.8 MB |
| 听书馆 | 儿童文学 | 我有友情要出租 | `http://p.s3.tuwa.starot.com/book/v1/10082_1700644536781.hd` | 4.0 MB |
| 听书馆 | 儿童文学 | 夏洛的网 | `http://p.s3.tuwa.starot.com/book/v1/10085_1700644511392.hd` | 8.6 MB |
| 听书馆 | 儿童文学 | 小布头奇遇记 | `http://p.s3.tuwa.starot.com/book/v1/10086_1700644485429.hd` | 9.8 MB |
| 听书馆 | 儿童文学 | "爱的教育 ——一个孩子写在日记里的生活与爱" | `http://p.s3.tuwa.starot.com/book/v1/10097_1700723378411.hd` | 11.1 MB |
| 听书馆 | 儿童文学 | 草房子 | `http://p.s3.tuwa.starot.com/book/v1/10099_1700644460779.hd` | 10.8 MB |
| 听书馆 | 儿童文学 | "今天我是升旗手 ——关于不放弃的启蒙书" | `http://p.s3.tuwa.starot.com/book/v1/10108_1700644431906.hd` | 5.5 MB |
| 听书馆 | 儿童文学 | "雷锋的故事 ——党和人民的好儿子雷锋" | `http://p.s3.tuwa.starot.com/book/v1/10111_1700723832845.hd` | 14.7 MB |
| 听书馆 | 儿童文学 | 没头脑和不高兴 | `http://p.s3.tuwa.starot.com/book/v1/10115_1700644282177.hd` | 5.2 MB |
| 听书馆 | 儿童文学 | 孙悟空在我们村里 | `http://p.s3.tuwa.starot.com/book/v1/10122_1700644259553.hd` | 5.2 MB |
| 听书馆 | 儿童文学 | 弗朗兹的故事 | `http://p.s3.tuwa.starot.com/book/v1/10135_1700644234782.hd` | 12.4 MB |
| 听书馆 | 儿童文学 | 你是我的妹 | `http://p.s3.tuwa.starot.com/book/v1/10145_1700644211152.hd` | 11.0 MB |
| 听书馆 | 儿童文学 | 小巴掌童话 | `http://p.s3.tuwa.starot.com/book/v1/10156_1700644187926.hd` | 6.0 MB |
| 听书馆 | 儿童文学 | 芝麻开门 | `http://p.s3.tuwa.starot.com/book/v1/10158_1700644164298.hd` | 8.9 MB |
| 听书馆 | 儿童文学 | 八十天环游地球 | `http://p.s3.tuwa.starot.com/book/v1/10165_1700644076871.hd` | 12.6 MB |
| 听书馆 | 儿童文学 | 格兰特船长的儿女——坚持不懈的寻人探险之旅 | `http://p.s3.tuwa.starot.com/book/v1/10166_1700644052344.hd` | 14.4 MB |
| 听书馆 | 儿童文学 | 荒野的呼唤——关于一条狗回归野性和自然的故事 | `http://p.s3.tuwa.starot.com/book/v1/10167_1700643962154.hd` | 13.5 MB |
| 听书馆 | 儿童文学 | 柳林风声——四个好朋友的成长故事 | `http://p.s3.tuwa.starot.com/book/v1/10171_1700643833604.hd` | 16.4 MB |
| 听书馆 | 儿童文学 | 尼尔斯骑鹅旅行记——顽皮男孩的旅行和成长故事 | `http://p.s3.tuwa.starot.com/book/v1/10172_1700643727848.hd` | 15.9 MB |
| 听书馆 | 儿童文学 | 三毛流浪记 | `http://p.s3.tuwa.starot.com/book/v1/10174_1700643617667.hd` | 10.9 MB |
| 听书馆 | 儿童文学 | 长袜子皮皮——一个与众不同小女孩的童年故事 | `http://p.s3.tuwa.starot.com/book/v1/10181_1700643590222.hd` | 14.1 MB |
| 听书馆 | 儿童文学 | 吹小号的天鹅 | `http://p.s3.tuwa.starot.com/book/v1/10185_1700643465749.hd` | 11.5 MB |
| 听书馆 | 儿童文学 | 存梦银行 | `http://p.s3.tuwa.starot.com/book/v1/10186_1700643436087.hd` | 12.1 MB |
| 听书馆 | 儿童文学 | 大森林里的小木屋 | `http://p.s3.tuwa.starot.com/book/v1/10187_1700643409440.hd` | 10.4 MB |
| 听书馆 | 儿童文学 | 借东西的小人——人类男孩与地板下小人的友谊 | `http://p.s3.tuwa.starot.com/book/v1/10189_1700643318247.hd` | 11.8 MB |
| 听书馆 | 儿童文学 | 精灵鼠小弟——关于友谊和成长的故事 | `http://p.s3.tuwa.starot.com/book/v1/10190_1700643196274.hd` | 9.2 MB |
| 听书馆 | 儿童文学 | 罗伯特的三次报复行动 | `http://p.s3.tuwa.starot.com/book/v1/10191_1700642935331.hd` | 9.3 MB |
| 听书馆 | 儿童文学 | 秘密花园——一座花园带来的救赎和成长 | `http://p.s3.tuwa.starot.com/book/v1/10192_1700642898853.hd` | 13.4 MB |
| 听书馆 | 儿童文学 | 桥下一家人 | `http://p.s3.tuwa.starot.com/book/v1/10193_1700642746349.hd` | 8.2 MB |
| 听书馆 | 儿童文学 | 巧克力男孩 | `http://p.s3.tuwa.starot.com/book/v1/10194_1700642721871.hd` | 7.6 MB |
| 听书馆 | 儿童文学 | 生气的小茉莉 | `http://p.s3.tuwa.starot.com/book/v1/10195_1700642695687.hd` | 10.8 MB |
| 听书馆 | 儿童文学 | 暑假里的大生意 | `http://p.s3.tuwa.starot.com/book/v1/10196_1700642659708.hd` | 7.5 MB |
| 听书馆 | 儿童文学 | 水孩子 | `http://p.s3.tuwa.starot.com/book/v1/10197_1700642632017.hd` | 14.7 MB |
| 听书馆 | 儿童文学 | 瓦尔登湖 | `http://p.s3.tuwa.starot.com/book/v1/10198_1700642608155.hd` | 6.7 MB |
| 听书馆 | 儿童文学 | 我们的母亲叫中国 | `http://p.s3.tuwa.starot.com/book/v1/10199_1700642580393.hd` | 11.2 MB |
| 听书馆 | 儿童文学 | 我生活的故事 | `http://p.s3.tuwa.starot.com/book/v1/10200_1700642554309.hd` | 9.7 MB |
| 听书馆 | 儿童文学 | 小海蒂——女孩海蒂和家人、朋友们的故事 | `http://p.s3.tuwa.starot.com/book/v1/10201_1700642522224.hd` | 9.5 MB |
| 听书馆 | 儿童文学 | 小鹿斑比——一只小鹿成为鹿王的成长之路 | `http://p.s3.tuwa.starot.com/book/v1/10202_1700642385711.hd` | 11.5 MB |
| 听书馆 | 儿童文学 | 小水的除夕 | `http://p.s3.tuwa.starot.com/book/v1/10203_1700642208522.hd` | 7.0 MB |
| 听书馆 | 儿童文学 | 小巫婆求仙记 | `http://p.s3.tuwa.starot.com/book/v1/10204_1700642179965.hd` | 13.3 MB |
| 听书馆 | 儿童文学 | 雪地天使——一个关于爱和永恒的故事 | `http://p.s3.tuwa.starot.com/book/v1/10205_1700640936676.hd` | 13.5 MB |
| 听书馆 | 儿童文学 | 鼹鼠的月亮河——鼹鼠发明家小米加和朋友的故事 | `http://p.s3.tuwa.starot.com/book/v1/10206_1700640785234.hd` | 11.8 MB |
| 听书馆 | 儿童文学 | 洋葱头历险记——洋葱头打败柠檬王的故事 | `http://p.s3.tuwa.starot.com/book/v1/10207_1700640604090.hd` | 13.0 MB |
| 听书馆 | 儿童文学 | 云朵工厂 | `http://p.s3.tuwa.starot.com/book/v1/10208_1700640217034.hd` | 8.6 MB |
| 听书馆 | 儿童文学 | 爱德华的奇妙之旅 | `http://p.s3.tuwa.starot.com/book/v1/10213_1700640187672.hd` | 11.9 MB |
| 听书馆 | 儿童文学 | 富兰克林传 | `http://p.s3.tuwa.starot.com/book/v1/10214_1700639948730.hd` | 20.0 MB |
| 听书馆 | 儿童文学 | 怪老头儿 | `http://p.s3.tuwa.starot.com/book/v1/10215_1700639925776.hd` | 9.9 MB |
| 听书馆 | 儿童文学 | 胡桃木小姐 | `http://p.s3.tuwa.starot.com/book/v1/10216_1700639888282.hd` | 11.5 MB |
| 听书馆 | 儿童文学 | 金篮子旅店 | `http://p.s3.tuwa.starot.com/book/v1/10217_1700639861185.hd` | 9.0 MB |
| 听书馆 | 儿童文学 | 菌儿自传 | `http://p.s3.tuwa.starot.com/book/v1/10218_1700639834264.hd` | 10.7 MB |
| 听书馆 | 儿童文学 | 蓝色的海豚岛 | `http://p.s3.tuwa.starot.com/book/v1/10219_1700639807155.hd` | 7.5 MB |
| 听书馆 | 儿童文学 | 李时珍的故事 | `http://p.s3.tuwa.starot.com/book/v1/10220_1700639781429.hd` | 10.1 MB |
| 听书馆 | 儿童文学 | 明天会有好运气 | `http://p.s3.tuwa.starot.com/book/v1/10221_1700639758751.hd` | 9.2 MB |
| 听书馆 | 儿童文学 | 奇迹男孩 | `http://p.s3.tuwa.starot.com/book/v1/10222_1700639729200.hd` | 7.1 MB |
| 听书馆 | 儿童文学 | 神秘岛 | `http://p.s3.tuwa.starot.com/book/v1/10223_1700639650163.hd` | 16.0 MB |
| 听书馆 | 儿童文学 | 太阳村的孩子 | `http://p.s3.tuwa.starot.com/book/v1/10224_1700632501969.hd` | 13.8 MB |
| 听书馆 | 儿童文学 | 汤姆的午夜花园 | `http://p.s3.tuwa.starot.com/book/v1/10225_1700632528651.hd` | 14.4 MB |
| 听书馆 | 儿童文学 | 兔子坡 | `http://p.s3.tuwa.starot.com/book/v1/10226_1691134849416.hd` | 12.0 MB |
| 听书馆 | 儿童文学 | 外公是棵樱桃树 | `http://p.s3.tuwa.starot.com/book/v1/10227_1691134868267.hd` | 13.4 MB |
| 听书馆 | 儿童文学 | 我和外公的战争 | `http://p.s3.tuwa.starot.com/book/v1/10228_1691134708188.hd` | 8.0 MB |
| 听书馆 | 儿童文学 | 写给孩子的论语课 | `http://p.s3.tuwa.starot.com/book/v1/10229_1691134886067.hd` | 10.2 MB |
| 听书馆 | 儿童文学 | 银河铁道之夜 | `http://p.s3.tuwa.starot.com/book/v1/10230_1691134908377.hd` | 13.9 MB |
| 听书馆 | 儿童文学 | 植物知道生命的答案 | `http://p.s3.tuwa.starot.com/book/v1/10231_1691134937228.hd` | 11.0 MB |
| 听书馆 | 个人成长 | 不爱说话的十一岁 | `http://p.s3.tuwa.starot.com/book/v1/10019_1700645218942.hd` | 7.7 MB |
| 听书馆 | 个人成长 | 弹性 | `http://p.s3.tuwa.starot.com/book/v1/10025_1700645241543.hd` | 7.2 MB |
| 听书馆 | 个人成长 | 番茄工作法 | `http://p.s3.tuwa.starot.com/book/v1/10029_1700645270411.hd` | 4.8 MB |
| 听书馆 | 个人成长 | 反脆弱 | `http://p.s3.tuwa.starot.com/book/v1/10030_1700645291688.hd` | 4.1 MB |
| 听书馆 | 个人成长 | 富爸爸，穷爸爸 | `http://p.s3.tuwa.starot.com/book/v1/10034_1700645311461.hd` | 6.5 MB |
| 听书馆 | 个人成长 | 高效能人士的七个习惯 | `http://p.s3.tuwa.starot.com/book/v1/10036_1700708909982.hd` | 6.1 MB |
| 听书馆 | 个人成长 | 激活右脑 | `http://p.s3.tuwa.starot.com/book/v1/10044_1700708574351.hd` | 5.5 MB |
| 听书馆 | 个人成长 | 开口就能说重点 | `http://p.s3.tuwa.starot.com/book/v1/10049_1700708549405.hd` | 6.7 MB |
| 听书馆 | 个人成长 | 考试脑科学 | `http://p.s3.tuwa.starot.com/book/v1/10050_1700708528322.hd` | 7.5 MB |
| 听书馆 | 个人成长 | 刻意练习：如何从新手到大师 | `http://p.s3.tuwa.starot.com/book/v1/10051_1700708500686.hd` | 6.3 MB |
| 听书馆 | 个人成长 | 跨越不可能 | `http://p.s3.tuwa.starot.com/book/v1/10053_1700708950460.hd` | 7.9 MB |
| 听书馆 | 个人成长 | 领导力 | `http://p.s3.tuwa.starot.com/book/v1/10058_1700708983887.hd` | 5.9 MB |
| 听书馆 | 个人成长 | 你的时间80%都用错了 | `http://p.s3.tuwa.starot.com/book/v1/10065_1700709008032.hd` | 6.2 MB |
| 听书馆 | 个人成长 | 逆商：我们该如何应对坏事件 | `http://p.s3.tuwa.starot.com/book/v1/10066_1700709030917.hd` | 6.6 MB |
| 听书馆 | 个人成长 | 认知天性 | `http://p.s3.tuwa.starot.com/book/v1/10069_1700709055085.hd` | 6.8 MB |
| 听书馆 | 个人成长 | 淘气包埃米尔 | `http://p.s3.tuwa.starot.com/book/v1/10075_1700709091320.hd` | 10.6 MB |
| 听书馆 | 个人成长 | 特别的女生萨哈拉 | `http://p.s3.tuwa.starot.com/book/v1/10076_1700709113626.hd` | 8.7 MB |
| 听书馆 | 个人成长 | "小狗钱钱 ——关于金钱的启蒙书" | `http://p.s3.tuwa.starot.com/book/v1/10087_1700709332714.hd` | 8.9 MB |
| 听书馆 | 个人成长 | 演讲的力量 | `http://p.s3.tuwa.starot.com/book/v1/10089_1700709394269.hd` | 5.5 MB |
| 听书馆 | 个人成长 | 意志力 | `http://p.s3.tuwa.starot.com/book/v1/10090_1700709472455.hd` | 4.5 MB |
| 听书馆 | 个人成长 | 再也不见，拖延症 | `http://p.s3.tuwa.starot.com/book/v1/10093_1700709826575.hd` | 6.4 MB |
| 听书馆 | 个人成长 | 掌控习惯 | `http://p.s3.tuwa.starot.com/book/v1/10094_1700709873650.hd` | 6.8 MB |
| 听书馆 | 个人成长 | 作文六要 | `http://p.s3.tuwa.starot.com/book/v1/10096_1700709897048.hd` | 7.4 MB |
| 听书馆 | 人文社科 | 被讨厌的勇气 | `http://p.s3.tuwa.starot.com/book/v1/10015_1700709984801.hd` | 10.9 MB |
| 听书馆 | 人文社科 | 感谢自己的不完美 | `http://p.s3.tuwa.starot.com/book/v1/10035_1700710055879.hd` | 7.0 MB |
| 听书馆 | 人文社科 | 蛤蟆先生去看心理医生 | `http://p.s3.tuwa.starot.com/book/v1/10040_1700710411047.hd` | 5.2 MB |
| 听书馆 | 人文社科 | 好无聊啊好无聊 | `http://p.s3.tuwa.starot.com/book/v1/10042_1700710515153.hd` | 3.4 MB |
| 听书馆 | 人文社科 | 乾隆十二时辰 | `http://p.s3.tuwa.starot.com/book/v1/10068_1700710594181.hd` | 4.5 MB |
| 听书馆 | 人文社科 | 与哲学家谈快乐 | `http://p.s3.tuwa.starot.com/book/v1/10092_1700710689247.hd` | 9.9 MB |
| 听书馆 | 人文社科 | 盘中餐 | `http://p.s3.tuwa.starot.com/book/v1/10117_1700711484364.hd` | 5.2 MB |
| 听书馆 | 人文社科 | "写给中学生的心理学 ——带你走进心理学的世界" | `http://p.s3.tuwa.starot.com/book/v1/10128_1700718676104.hd` | 12.9 MB |
| 听书馆 | 人文社科 | 哲学鸟飞罗系列 | `http://p.s3.tuwa.starot.com/book/v1/10130_1700718704059.hd` | 9.8 MB |
| 听书馆 | 人文社科 | 中国抗日战争史简明读本 | `http://p.s3.tuwa.starot.com/book/v1/10131_1700718725353.hd` | 11.1 MB |
| 听书馆 | 人文社科 | 从鸦片战争到五四运动——中华民族翻天覆地的近代史 | `http://p.s3.tuwa.starot.com/book/v1/10134_1700718852037.hd` | 15.7 MB |
| 听书馆 | 人文社科 | 寂寞圣哲 | `http://p.s3.tuwa.starot.com/book/v1/10138_1700718877131.hd` | 10.4 MB |
| 听书馆 | 人文社科 | 甲骨文的故事 | `http://p.s3.tuwa.starot.com/book/v1/10139_1700718900516.hd` | 11.1 MB |
| 听书馆 | 人文社科 | 思考世界的孩子 | `http://p.s3.tuwa.starot.com/book/v1/10151_1700718924380.hd` | 16.6 MB |
| 听书馆 | 人文社科 | 我心归处是敦煌——敦煌的女儿樊锦诗的故事 | `http://p.s3.tuwa.starot.com/book/v1/10155_1700719026053.hd` | 12.9 MB |
| 听书馆 | 人文社科 | 颜氏家训译注 | `http://p.s3.tuwa.starot.com/book/v1/10157_1700719052092.hd` | 14.5 MB |
| 听书馆 | 人文社科 | 中国古代衣食住行——带你走进古人的日常生活 | `http://p.s3.tuwa.starot.com/book/v1/10159_1700719167896.hd` | 14.4 MB |
| 听书馆 | 人文社科 | 中国历史上的科学发明 | `http://p.s3.tuwa.starot.com/book/v1/10160_1700719206158.hd` | 17.4 MB |
| 听书馆 | 人文社科 | 中国文化的根本精神——博大精深的中国传统文化 | `http://p.s3.tuwa.starot.com/book/v1/10163_1700720730512.hd` | 13.2 MB |
| 听书馆 | 人文社科 | 神奇的校车 | `http://p.s3.tuwa.starot.com/book/v1/10176_1700720761082.hd` | 9.5 MB |
| 听书馆 | 人文社科 | 希腊神话和传说——走进古希腊的神话世界 | `http://p.s3.tuwa.starot.com/book/v1/10180_1700720930377.hd` | 14.6 MB |
| 听书馆 | 人文社科 | 高考宝典 | `http://p.s3.tuwa.starot.com/book/v1/15433_1723184754200.hd` | 10.9 MB |
| 听书馆 | 人物传记 | 巴菲特传 | `http://p.s3.tuwa.starot.com/book/v1/10013_1700720995933.hd` | 9.9 MB |
| 听书馆 | 人物传记 | 稻盛和夫自传 | `http://p.s3.tuwa.starot.com/book/v1/10027_1700721015005.hd` | 16.6 MB |
| 听书馆 | 人物传记 | 梵高传 | `http://p.s3.tuwa.starot.com/book/v1/10032_1700721097748.hd` | 15.9 MB |
| 听书馆 | 人物传记 | "居里夫人自传 ——关于崇高的科学精神的故事" | `http://p.s3.tuwa.starot.com/book/v1/10048_1700721297470.hd` | 12.6 MB |
| 听书馆 | 人物传记 | "梦圆大地：袁隆平传 ——“水稻之父”的故事" | `http://p.s3.tuwa.starot.com/book/v1/10061_1700721398675.hd` | 13.0 MB |
| 听书馆 | 人物传记 | 苏东坡传 | `http://p.s3.tuwa.starot.com/book/v1/10072_1700721485143.hd` | 8.0 MB |
| 听书馆 | 人物传记 | "杜甫传 ——唐朝伟大诗人颠沛流离的一生" | `http://p.s3.tuwa.starot.com/book/v1/10100_1700721574951.hd` | 13.5 MB |
| 听书馆 | 人物传记 | 假如给我三天光明 | `http://p.s3.tuwa.starot.com/book/v1/10107_1700721599158.hd` | 7.9 MB |
| 听书馆 | 人物传记 | 孔子的故事 | `http://p.s3.tuwa.starot.com/book/v1/10109_1700721618212.hd` | 9.3 MB |
| 听书馆 | 人物传记 | 人民音乐家——冼星海 | `http://p.s3.tuwa.starot.com/book/v1/10149_1700721809035.hd` | 12.7 MB |
| 听书馆 | 人物传记 | 霍去病 | `http://p.s3.tuwa.starot.com/book/v1/10169_1700721835397.hd` | 9.1 MB |
| 听书馆 | 艺术 | 爸爸的画：沙坪小屋 | `http://p.s3.tuwa.starot.com/book/v1/10132_1700724846094.hd` | 7.0 MB |
| 听书馆 | 艺术 | 建筑艺术的语言——带你走进精妙绝伦的建筑世界 | `http://p.s3.tuwa.starot.com/book/v1/10141_1700724955040.hd` | 14.9 MB |
| 听书馆 | 艺术 | 京剧脸谱 | `http://p.s3.tuwa.starot.com/book/v1/10142_1700724978199.hd` | 7.8 MB |
| 听书馆 | 艺术 | 启功给你讲书法——教你如何学好书法 | `http://p.s3.tuwa.starot.com/book/v1/10146_1700725067443.hd` | 10.9 MB |
| 听书馆 | 艺术 | 谈美——生活中的美学之道 | `http://p.s3.tuwa.starot.com/book/v1/10152_1700725154060.hd` | 10.2 MB |
| 听书馆 | 艺术 | 中国民歌欣赏——带你走进民歌的世界 | `http://p.s3.tuwa.starot.com/book/v1/10162_1700725248043.hd` | 11.5 MB |
| 听书馆 | 自然科学 | 海陆的起源 | `http://p.s3.tuwa.starot.com/book/v1/10101_1700725287138.hd` | 8.6 MB |
| 听书馆 | 自然科学 | 蜡烛的故事 | `http://p.s3.tuwa.starot.com/book/v1/10110_1700725307237.hd` | 7.1 MB |
| 听书馆 | 自然科学 | 寂静的春天 | `http://p.s3.tuwa.starot.com/book/v1/10137_1700725326668.hd` | 14.0 MB |
| 听书馆 | 自然科学 | 空间简史——人类对空间的探索之路 | `http://p.s3.tuwa.starot.com/book/v1/10143_1700725427123.hd` | 17.6 MB |
| 听书馆 | 自然科学 | 《昆虫记》导读 | `http://p.s3.tuwa.starot.com/book/v1/10144_1700725519083.hd` | 7.4 MB |
| 听书馆 | 自然科学 | 趣味物理学——生活中的那些有趣的物理现象 | `http://p.s3.tuwa.starot.com/book/v1/10148_1700725609559.hd` | 12.6 MB |
| 听书馆 | 自然科学 | 伪科学与超自然现象——走近世界上的那些“神秘”事件 | `http://p.s3.tuwa.starot.com/book/v1/10153_1700725727269.hd` | 12.1 MB |
| 听书馆 | 自然科学 | 最初三分钟——带你了解宇宙起源的现代观点 | `http://p.s3.tuwa.starot.com/book/v1/10164_1700725805949.hd` | 11.3 MB |
| 听书馆 | 自然科学 | 天工开物 | `http://p.s3.tuwa.starot.com/book/v1/10178_1700725825905.hd` | 16.6 MB |
| 听书馆 | 自然科学 | 物理定律的本性——跟随费曼走进物理学的世界 | `http://p.s3.tuwa.starot.com/book/v1/10179_1700725904613.hd` | 11.9 MB |
| 听书馆 | 自然科学 | 海错图笔记 | `http://p.s3.tuwa.starot.com/book/v1/10188_1700725931048.hd` | 18.9 MB |
| 听书馆 | 励志故事 | 厄尔的故事 | `http://p.s3.tuwa.starot.com/book/v1/10017_1700725971018.hd` | 2.6 MB |
| 听书馆 | 励志故事 | 乞丐囝仔赖东进 | `http://p.s3.tuwa.starot.com/book/v1/10024_1700725990354.hd` | 7.4 MB |
| 听书馆 | 励志故事 | 我真的想尽办法了吗？ | `http://p.s3.tuwa.starot.com/book/v1/10031_1700726010043.hd` | 2.7 MB |
| 听书馆 | 励志故事 | 这里没有胆小鬼 | `http://p.s3.tuwa.starot.com/book/v1/10038_1700726033876.hd` | 2.2 MB |
| 听书馆 | 励志故事 | 珍妮的表演 | `http://p.s3.tuwa.starot.com/book/v1/10045_1700726053358.hd` | 2.8 MB |
| 听书馆 | 励志故事 | 47岁清洁工自学英语成为学霸 | `http://p.s3.tuwa.starot.com/book/v1/10052_1700726071669.hd` | 3.8 MB |
| 听书馆 | 励志故事 | 把精致做到极致 | `http://p.s3.tuwa.starot.com/book/v1/10059_1700726095225.hd` | 3.8 MB |
| 听书馆 | 励志故事 | 赤脚往前冲 | `http://p.s3.tuwa.starot.com/book/v1/10063_1700726114871.hd` | 4.1 MB |
| 听书馆 | 励志故事 | 从寒门学子到千万富翁 | `http://p.s3.tuwa.starot.com/book/v1/10070_1700726134839.hd` | 4.4 MB |
| 听书馆 | 励志故事 | 苦难中也有芬芳 | `http://p.s3.tuwa.starot.com/book/v1/10077_1700726154090.hd` | 2.9 MB |
| 听书馆 | 励志故事 | 梦想在坚持中起舞 | `http://p.s3.tuwa.starot.com/book/v1/10084_1700726178366.hd` | 4.3 MB |
| 听书馆 | 励志故事 | 失败的成功者 | `http://p.s3.tuwa.starot.com/book/v1/10091_1700726196986.hd` | 3.1 MB |
| 听书馆 | 励志故事 | 用脚改写人生的“无臂蛙王” | `http://p.s3.tuwa.starot.com/book/v1/10098_1700726215799.hd` | 3.7 MB |
| 听书馆 | 励志故事 | 大海里的船 | `http://p.s3.tuwa.starot.com/book/v1/10105_1700726236042.hd` | 1.9 MB |
| 听书馆 | 励志故事 | 断箭 | `http://p.s3.tuwa.starot.com/book/v1/10112_1700726255575.hd` | 2.2 MB |
| 听书馆 | 励志故事 | 化逆境为风景 | `http://p.s3.tuwa.starot.com/book/v1/10119_1700726273664.hd` | 3.4 MB |
| 听书馆 | 励志故事 | 坚持梦想永不放弃 | `http://p.s3.tuwa.starot.com/book/v1/10126_1700726305220.hd` | 2.4 MB |
| 听书馆 | 励志故事 | 梦想照亮人生路 | `http://p.s3.tuwa.starot.com/book/v1/10133_1700726327176.hd` | 2.7 MB |
| 听书馆 | 励志故事 | 生命的价值 | `http://p.s3.tuwa.starot.com/book/v1/10140_1700726347959.hd` | 1.7 MB |
| 听书馆 | 励志故事 | 心中的顽石 | `http://p.s3.tuwa.starot.com/book/v1/10147_1700726371065.hd` | 2.0 MB |
| 听书馆 | 励志故事 | 学英语需要骆驼精神 | `http://p.s3.tuwa.starot.com/book/v1/10154_1700726718023.hd` | 3.2 MB |
| 听书馆 | 励志故事 | 用汗水浇灌出奇迹 | `http://p.s3.tuwa.starot.com/book/v1/10161_1700727230835.hd` | 3.7 MB |
| 听书馆 | 励志故事 | 除了努力，没有捷径 | `http://p.s3.tuwa.starot.com/book/v1/10168_1700727249563.hd` | 2.7 MB |
| 听书馆 | 励志故事 | 从差生到诺贝尔奖获得者 | `http://p.s3.tuwa.starot.com/book/v1/10175_1700727271458.hd` | 3.1 MB |
| 听书馆 | 励志故事 | 换个方向，人生依旧精彩 | `http://p.s3.tuwa.starot.com/book/v1/10182_1700727309757.hd` | 3.0 MB |
| 听书馆 | 励志故事 | 收获香甜的果实 | `http://p.s3.tuwa.starot.com/book/v1/10183_1700727330078.hd` | 2.1 MB |
| 听书馆 | 励志故事 | 做自己人生的主人 | `http://p.s3.tuwa.starot.com/book/v1/10184_1700727351344.hd` | 3.1 MB |
| 听书馆 | 励志故事 | 1850次求职 | `http://p.s3.tuwa.starot.com/book/v1/10209_1700727373265.hd` | 2.1 MB |
| 听书馆 | 励志故事 | 凌晨四点的洛杉矶 | `http://p.s3.tuwa.starot.com/book/v1/10210_1700727392941.hd` | 3.2 MB |
| 听书馆 | 励志故事 | 梦想不是做梦 | `http://p.s3.tuwa.starot.com/book/v1/10211_1700727412218.hd` | 2.3 MB |
| 听书馆 | 励志故事 | 梦想之门永远不会关闭 | `http://p.s3.tuwa.starot.com/book/v1/10212_1700727432166.hd` | 2.4 MB |
| 听书馆 | 高考宝典 | 2025英语高考宝典(核心素养) | `http://p.s3.tuwa.starot.com/book/v1/15458_1723509007589.hd` | 66 KB |
| 听书馆 | 高考宝典 | 2025英语高考宝典(2020真题) | `http://p.s3.tuwa.starot.com/book/v1/15485_1723514881198.hd` | 10.9 MB |
| 听书馆 | 高考宝典 | 2025英语高考宝典(2021真题) | `http://p.s3.tuwa.starot.com/book/v1/15486_1723514929249.hd` | 7.5 MB |
| 听书馆 | 高考宝典 | 2025英语高考宝典(2022真题) | `http://p.s3.tuwa.starot.com/book/v1/15487_1723514973917.hd` | 7.3 MB |
| 听书馆 | 高考宝典 | 2025英语高考宝典(2023真题) | `http://p.s3.tuwa.starot.com/book/v1/15488_1723514994280.hd` | 8.7 MB |
| 听书馆 | 高考宝典 | 2025英语高考宝典(2024真题) | `http://p.s3.tuwa.starot.com/book/v1/15489_1723515038269.hd` | 6.5 MB |
| 听书馆 | 高考宝典 | 2025英语高考宝典(词汇A-E) | `http://p.s3.tuwa.starot.com/book/v1/15490_1723515063910.hd` | 45.7 MB |
| 听书馆 | 高考宝典 | 2025英语高考宝典(词汇F-Q) | `http://p.s3.tuwa.starot.com/book/v1/15491_1723515112843.hd` | 48.4 MB |
| 听书馆 | 高考宝典 | 2025英语高考宝典(词汇R-Z) | `http://p.s3.tuwa.starot.com/book/v1/15492_1723515143652.hd` | 42.9 MB |
| 听书馆 | 高考宝典 | 2025英语高考宝典(固定搭配+高频词) | `http://p.s3.tuwa.starot.com/book/v1/15493_1726216327206.hd` | 37 KB |
| 听书馆 | 高考宝典 | 2025语文高考宝典(核心素养) | `http://p.s3.tuwa.starot.com/book/v1/15494_1726216404579.hd` | 33 KB |
| 听书馆 | 高考宝典 | 2025语文高考宝典(知识梳理1) | `http://p.s3.tuwa.starot.com/book/v1/15495_1726216437364.hd` | 47 KB |
| 听书馆 | 高考宝典 | 2025语文高考宝典(知识梳理2) | `http://p.s3.tuwa.starot.com/book/v1/15496_1726216450920.hd` | 102 KB |
| 听书馆 | 高考宝典 | 2025语文高考宝典(知识梳理3) | `http://p.s3.tuwa.starot.com/book/v1/15497_1726216464244.hd` | 102 KB |
| 听书馆 | 高考宝典 | 2025语文高考宝典(知识梳理4) | `http://p.s3.tuwa.starot.com/book/v1/15499_1726216479805.hd` | 73 KB |
| 听书馆 | 高考宝典 | 2025语文高考宝典(知识梳理5) | `http://p.s3.tuwa.starot.com/book/v1/15500_1726216491672.hd` | 99 KB |
| 听书馆 | 高考宝典 | 2025语文高考宝典(知识梳理6) | `http://p.s3.tuwa.starot.com/book/v1/15501_1726216502849.hd` | 26 KB |
| 听书馆 | 高考宝典 | 2025语文高考宝典(知识梳理7) | `http://p.s3.tuwa.starot.com/book/v1/15502_1726216515305.hd` | 29 KB |
| 听书馆 | 高考宝典 | 2025语文高考宝典(知识梳理8) | `http://p.s3.tuwa.starot.com/book/v1/15503_1726216526917.hd` | 102 KB |
| 听书馆 | 高考宝典 | 2025语文高考宝典(知识梳理9) | `http://p.s3.tuwa.starot.com/book/v1/15504_1726216538646.hd` | 52 KB |
| 听书馆 | 高考宝典 | 2025语文高考宝典(真题题库) | `http://p.s3.tuwa.starot.com/book/v1/15505_1726216551261.hd` | 99 KB |
| 听书馆 | 高考宝典 | 2025化学高考宝典 | `http://p.s3.tuwa.starot.com/book/v1/15506_1726216562710.hd` | 87 KB |

## 7. 未决项
1. **24 字节结构块**（offset 608）语义未完全确认。
2. **设备端读取端**：`.hd` 读取端在设备主系统分区/设备应用（疑 Unity-il2cpp），固件 `xr_system_gen2.img`（2MB，全志 AWIH 引导镜像）不含读取端，未获取。
3. **生成器真机读取验证**：需把生成的 `.hd` 上传到学习机验证可读（依赖设备端）。
（~~hash4 @36~~ 已破解：= 标准 CRC-32/ISO-HDLC(zlib) 作用于全局头前 36 字节，见 §3.8，不再列入未决项。）

