# 途蛙记忆卡逆向

## 设备说明

本设备并非Android设备。 最新系统固件为： http://p.s3.tuwa.starot.com/firmware/study_v2_channel/01.02.02.61/xr_system_gen2.img
定制设备固件基本没改造可能。可以理解为换了墨水屏幕的MP4。

防止厂家倒闭、跑路、消失，故进行逆向。省的白花钱。

## 交互协议

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

## 单词本的生成

单词本最终由com.funky.STBookGenerator调用```STBookGeneratorLib.so```生成。最后如果实现不行，就自己写一个apk来进行生成。

### 流程

 1. 触发点: 保存操作由 STAICardCustomBookSaveWordAct.N() 方法发起。这个方法很可能是在用户点击“保存”按钮时被调用的。
 2. 核心组件: 保存的核心逻辑依赖于 com.funky.STBookGenerator 这个类。特别是它的一个 native 方法：generateHashRecord(JJIILjava/lang/String;)I。
 3. 数据流: generateHashRecord 方法本身并不直接接收单词列表。相反，它通过一个回调机制（Listener）来按需获取数据。
       * 在调用 generateHashRecord 之前，代码通过 new-instance v4, Lcom/starot/tuwa/ui/aicard/activity/h0; 创建了一个监听器。
       * 然后通过 invoke-virtual {v1, v4}, Lcom/funky/STBookGenerator;->setListener(Lcom/funky/STBookGenerator$Listener;)V 将监听器设置给了 STBookGenerator 实例。
       * 这个监听器 h0 实现了 com/funky/STBookGenerator$Listener 接口，该接口包含两个关键的回调方法：getWord(I)Ljava/lang/String; 和 getMeta()Ljava/lang/String;。
 4. `generateHashRecord` 的工作方式:
       * 它接收了单词列表 N 的大小作为参数 (iget-object v1, p0, ...->N; ... invoke-virtual {v1}, Ljava/util/ArrayList;->size()I)。
       * native C++ 代码会根据传入的 size，循环调用 Java 层的 getWord(int index) 回调方法，一次获取一个单词的数据。
       * 它还会调用 getMeta() 方法来获取这本书的元数据（比如书名、ID等）。
       * native 代码拿到这些字符串后，会将它们处理并写入到最终的文件中（路径由最后一个参数指定，即 K 字段）。方法名 generateHashRecord
         暗示了最终的文件格式可能是一个包含哈希索引的自定义二进制格式，以便于快速查找，而不仅仅是纯文本。

  单词列表 N 中的每个单词对象，在保存时会经过以下处理：

   1. STAICardCustomBookSaveWordAct 中的监听器（即 h0 实例）的回调方法 getWord(int index) 被 native 代码调用。
   2. 该方法会从列表 N 中取出对应索引的单词对象。
   3. 将该单词对象序列化成一个 JSON 格式的字符串。
   4. 返回这个 JSON 字符串给 native 代码。
   5. native 代码将收到的一个一个的 JSON 字符串，连同元数据（通过 getMeta 回调获取，同样是 JSON 格式），一起写入到一个自定义的、可能带有哈希索引的二进制文件（.hd 文件）中。

### 单词读取

STBookGenerator.Listener 的具体实现，也就是 com/starot/tuwa/ui/aicard/activity/h0 这个类。
getWord(I) 方法负责将 N 列表（STAICardCustomBookSaveWordAct中的N字段）中的单个单词对象 STAICardCustomBookWordModel 转换成一个JSON字符串。

  这个JSON字符串的格式如下：
```json
{
   "word": "单词本身",
   "symbols": [
     {
       "symbol": "音标字符串",
       "url": "音标发音文件URL"
     }
   ],
   "means": [
     {
       "mean": "词性. 释义"
     }
   ],
   "sentences": [
     {
       "en": "例句的英文内容",
       "zh": "例句的中文内容",
       "en_url": "例句的英文发音URL"
     }
   ]
 }
```

  字段说明:

   * word: (String) 单词本身。
   * symbols: (Array) 一个包含音标信息的数组。
       * symbol: (String) 音标，例如 /wɜːd/。
       * url: (String) 音标发音的音频文件地址。
   * means: (Array) 一个包含单词释义的数组。
       * mean: (String) 单词的释义，格式通常是“词性. 中文意思”，例如 "n. 单词；话语"。
   * sentences: (Array) 一个包含例句的数组。
       * en: (String) 英文例句。
       * zh: (String) 例句的中文翻译。
       * en_url: (String) 英文例句发音的音频文件地址。

  要点:

   * 代码逻辑显示，如果某个字段（如symbol）或某个数组（如sentences）没有内容，那么对应的键值对将不会出现在最终的JSON字符串中。
   * getMeta() 方法也生成一个JSON字符串，用于描述整个词书的元数据，但它与单个单词的格式是分开的。


