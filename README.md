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
                "url": "/book/custom_v1/16312/ebook_54644.hd",
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

http://p.s3.tuwa.starot.com/book/custom_v1/16312/ebook_54644.hd
书籍格式为txt，仅仅扩展名改为了".hd"。下载不需要token验证（亦即说不定可以下载别人上传的书）
