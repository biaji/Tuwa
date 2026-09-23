# 途蛙记忆卡逆向

## 目标
防止厂家倒闭/跑路导致设备失联，逆向词书（`.hd`）生成与同步链路，实现本地替代服务器 + 独立生成器。

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

## 3. type=1 二进制词书 `.hd` 格式（生成端）

结论源于真实二进制样本逆向，并经独立生成器**逐字节复现**验证（除 hash4 外全部一致）。

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
36    hash4 (u32)     = 自定义完整性校验（算法未识别，见 §9）
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
- **hash4**：自定义完整性校验和，常见 CRC-32 变体均不匹配，算法未识别。


---

# 二、设备端（词书读取）

## 4. 协议

### 设备注册（prod.study）
```
POST /wms/token?sn=<设备序列号>&code=<未知>&secret=<未知>
→ {"code":200,"data":{"token":"...","expired":<秒>,"base":"http://p.s3.tuwa.starot.com"}}
```

### 资源推送（prod.study）
```
GET /wms/wait/download?cId=<1单词本|6电子书>&offset=&size=
Header: Authorization: 'Bearer '<token>
→ {"code":200,"data":{"total":N,"list":[{pushId,bookId,url,size,planId,study,review,updateTime,pressId,press,time,type,name,count,studyMode}]}}
```
- **type=2 电子书 `.hd` = 纯文本改扩展名**；**type=1 自定义词书 `.hd` = 二进制**（见 §3）。

## 5. MQTT 主题
APP↔设备实时通信/推书走 `tuwa.study.machine.*` 主题，如 `...custom.book.push.device`、`...custom.book.word.info`、`...book.plan.info`、`...wifi.password`、`...wifi.ssid`。


---

# 三、通用

## 6. 鉴权格式
- 厂商按 **`'Bearer '<token>`（带字面单引号）** 前缀解析。标准 `Bearer `（无引号）→ 401；`'Bearer '` → 200。
- token 为 **JWT(HS512)**：
  `{"alg":"HS512"}` + `{"created":<毫秒>,"id":<userId>,"sn":null,"rid":null,"type":"android|device","exp":<秒>}`
- token 获取（APP 侧）：读设备 `tuwa.db` 的 `stusermodel` 表。

## 7. 真机 .hd 获取路径

前置：设备已 root 并安装登录目标 App；设备自带网络工具（curl）且可直连（不经中转代理）。

### 7.1 取 token
读设备数据库 `databases/tuwa.db` 的 `stusermodel` 表（HS512 JWT）：
```
adb shell sqlite3 /data/data/<包名>/databases/tuwa.db 'SELECT token FROM stusermodel'
```

### 7.2 词书列表（prod.app）
```
GET /wms/custom/book/list
Header: Authorization: 'Bearer '<token>
→ data:[{id, name, downloadUrl, fileSize, ...}]
```

### 7.3 资源列表（prod.study，电子书等）
```
GET /wms/wait/download?cId=<1单词本|6电子书>&offset=0&size=N
Header: Authorization: 'Bearer '<token>
→ data:{total, list:[{bookId, url, size, ...}]}
```

### 7.4 下载 .hd（S3/CDN 公有读，无需 token）
```
curl -o out.hd 'http://p.s3.tuwa.starot.com<downloadUrl | url>'
```
- 自定义词书（type=1）：`/book/custom_v1/<userId>/<bookId>_<ts>.hd`（二进制）
- 电子书（type=2）：`/book/custom_v1/<userId>/ebook_<ts>.hd`（纯文本）

### 7.5 本地生成（触发「保存」）
在 App 内创建/保存自定义词书后，`.hd` 写入
`getExternalFilesDir(null)/<bookId>.hd` = `/storage/emulated/0/Android/data/<包名>/files/<bookId>.hd`，经 `adb pull` 取回。

## 8. 本地替代服务器
- **DNS**：把 4 个 tuwa 域名（prod.app / prod.study / p.s3 / p.s3.public）A 记录指向本机局域网 IP，其余转发上游。
- **HTTP**：S3 风格静态文件服务（按原 bucket 路径结构）+ 已按 `Api.java` 全量实现约 60 个 API 端点（/wms/*、/ums/*、/cms/*、/oms/*、/pms/*），返回 `{code,message,data}` 信封 + Bearer 校验（兼容 `'Bearer '` 与 `Bearer ` 前缀）。
- 已实测：DNS 劫持、token 签发、Bearer 鉴权（200/401）、静态 .hd、404、按 Host 路由。
- 限制：响应体仍为占位（未对拍真实结构）；MQTT 模拟未做。
- 运行：`sudo python3 tuwa_server.py --dns-port 53 --http-port 80 --any`

## 9. 未决项
1. **hash4 @36**：常见 CRC-32 变体均不匹配，判定为库内置自定义校验和（区域/多项式未知）。不影响内容解码；若要生成设备完全接受的 `.hd`，需反汇编 `libSTBookGeneratorLib.so` 确认。
2. **24 字节结构块**（offset 608）语义未完全确认。
3. **设备端读取端**：`.hd` 读取端在设备主系统分区/设备应用（疑 Unity-il2cpp），固件 `xr_system_gen2.img`（2MB，全志 AWIH 引导镜像）不含读取端，未获取。
4. **生成器真机读取验证**：需把生成的 `.hd` 上传到学习机验证可读（依赖设备端）。

