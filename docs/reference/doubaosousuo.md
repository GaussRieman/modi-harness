本文档介绍 **豆包搜索Global版 ** 接口的输入输出参数。调用该接口可获取搜索词相关的搜索结果，您可根据这些数据适配项目使用。


* Global版 仅支持**按量后付费**调用，不支持订阅套餐调用；

* 查看[搜索版本差异说明](https://www.volcengine.com/docs/87772/2272949?lang=zh)；


&nbsp;

接口限流：账号维度，默认 5 QPS，接入方可根据实际需要提工单扩容。（Global版和Custom版的并发限流相互独立）

免费额度：每个火山账号每月可免费调用500次联网搜索（额度与Custom版共用，不区分SearchType、付费类型），无论是否开通付费都将被优先消耗；


---



<span id="ZFgEZsKI"></span>
# 认证方式

提供 API Key 一种接入方式（不支持TOP网关接入）。


1. 获取API Key

   1. **按量计费（后付费）** ：登陆并进入 [联网搜索控制台 - API Key管理 - 按量后付费](https://console.volcengine.com/search-infinity/api-key?tab=post_paid)；

   2. 在弹出的名称文本框中填写 API Key 名称，单击创建。


> 说明：请妥善保存好API Key，强烈建议您不要将其直接写入到调用模型的代码中。



2. 签名构造


API Key 签名鉴权方式要求在 HTTP 请求 header 中按如下方式添加 Authorization:

```Bash
Authorization: Bearer <API_KEY>
```


我们提供postman、python的demo文件供您使用，填写APIKEY信息后可直接发起调用

<Attachment link="https://portal.volccdn.com/obj/volcfe/cloud-universal-doc/upload_4617e20ee11708e57cd0d5ffc35fc36c.zip" name="apikey方式访问global_search.zip">apikey方式访问global_search.zip</Attachment>


&nbsp;

<span id="l5LgRrII"></span>
# 接口详情

<span id="13igmMp1"></span>
## URL


|URL |https://open.feedcoopapi.com/search_api/global_search |
|---|---|
|Method |POST |
|Content\-Type |application/json |
|Authorization |Bearer ${APIKey} |


&nbsp;


---



<span id="90tI2VuY"></span>
## 请求

Request


|**字段名** |**类型** |**必须** |**默认值** |**说明** |
|---|---|---|---|---|
|Query |String |是 |\- |用户搜索query，**1~100**个字符(过长会截断)，不支持多词搜索 |
|DocCount |Number |否 |10 |返回结果条数，**最多20条，默认10条**<br><br>> 数量增加会增加搜索耗时<br><br>> 不等同于响应中的 TotalDocCount |
|MaxSnippetLength |Number |否 |500 |单个摘要片段的最大tokens，传入值 \> 0 时生效，**最大3000**，推荐1000以内 |
|MaxImageCountPerDoc |Number |否 |3 |单条搜索结果最多返回的图片数量，传入值 \> 0 时生效，**最多10个，默认3个** |



---



<span id="bwLBu8yN"></span>
## 响应

<span id="8Xmj7NLc"></span>
### Response


|**字段名** |**类型** |**必须** |**说明** |
|---|---|---|---|
|ResponseMetadata |Object |是 |统一响应元信息 |
|Result | |否 |请求成功时的搜索结果；失败时为 null |


<span id="iEoeHqpc"></span>
#### ResponseMetadata


|**字段名** |**类型** |**必须** |**说明** |
|---|---|---|---|
|RequestId |String |是 |请求 ID |
|Action |String |是 |Global版此字段为空 |
|Version |String |是 |Global版此字段为空 |
|Service |String |是 |Global版此字段为空 |
|Region |String |是 |Global版此字段为空 |
|Error |Object |否 |接口层错误信息 |


<span id="sUUvhWAQ"></span>
#### GlobalSearchResult


|**字段名** |**类型** |**必须** |**说明** |
|---|---|---|---|
|TotalDocCount |Number |是 |本次搜索可返回的总结果数，不等同于 Documents 实际返回条数。 |
|Documents |Array[GlobalSearchDocument] |否 |本次返回的结果列表，数量受 DocCount 控制。 |
|ErrorCode |Number |是 |错误码 |
|ErrorMsg |String |是 |错误信息 |


<span id="9k65daBm"></span>
#### GlobalSearchDocument


|**字段名** |**类型** |**必须** |**说明** |
|---|---|---|---|
|Rank |Number |是 |排序位置（由0开始） |
|Url |String |否 |搜索结果落地页URL |
|Title |String |否 |搜索结果标题 |
|Snippet |Array[GlobalSearchSnippet] |否 |相关摘要：搜索结果正文中和query相关的片段，长度由Request中的 [MaxSnippetLength] 决定 |
|DocumentInfo |GlobalSearchDocumentInfo |否 |结果统计与类型信息 |
|HostInfo |GlobalSearchHostInfo |否 |站点信息 |


<span id="Vzv7Gm4u"></span>
#### GlobalSearchSnippet


|**字段名** |**类型** |**必须** |**说明** |
|---|---|---|---|
|Type |String |否 |摘要类型，包括：text、image |
|Text |String |否 |文本摘要，仅 Type = text 时返回 |
|Image |GlobalSearchSnippetImage |否 |图片摘要，仅 Type = image 时返回，数量上限由 Request 中的 [MaxImageCountPerDoc] 决定 |


<span id="C41Apzj1"></span>
#### GlobalSearchSnippetImage


|**字段名** |**类型** |**必须** |**说明** |
|---|---|---|---|
|Width |Number |否 |图片宽度 |
|Height |Number |否 |图片高度 |
|ImageUrl |String |否 |图片地址 |


<span id="09ygl8Ew"></span>
#### GlobalSearchDocumentInfo


|**字段名** |**类型** |**必须** |**说明** |
|---|---|---|---|
|ContentCharCount |Number |否 |正文字符数 |
|ContentTokenCount |Number |否 |正文 token 数 |
|Filetype |String |否 |搜索结果类型，包括：webpage、pdf |
|PublishTime |String |否 |网页发布时间 |


<span id="6Bjo2Sgw"></span>
#### GlobalSearchHostInfo


|**字段名** |**类型** |**必须** |**说明** |
|---|---|---|---|
|Hostname |String |否 |站点名 |
|IconUrl |String |否 |站点 Icon URL |



---



<span id="1UKUQjcc"></span>
## 请求示例

```JSON
{
    "Query": "北京周边游玩景点推荐",
    "DocCount": 2,
    "MaxSnippetLength": 500,
    "MaxImageCountPerDoc": 3
}
```


<span id="S5Pmj3xV"></span>
## 响应示例

<span id="fmOnRFXW"></span>
### 正常请求示例

```JSON
{
    "ResponseMetadata": {
        "RequestId": "20260622120000A1B2C3D4E5F6",
        "Action": "",
        "Version": "",
        "Service": "",
        "Region": ""
    },
    "Result": {
        "TotalDocCount": 20,
        "Documents": [
            {
                "Rank": 0,
                "Url": "https://m.baike.com/wiki/%E5%A4%A9%E5%AE%89%E9%97%A8/19473746",
                "Title": "天安门[天安门城楼中国国家象征]_百科",
                "Snippet": [
                    {
                        "Type": "text",
                        "Text": "天安门[天安门城楼中国国家象征]_百科\n",
                    },
                    {
                        "Type": "image",
                        "Image": {
                            "Width": 1136,
                            "Height": 565,
                            "ImageUrl": "https://p3-sdbk2-media.byteimg.com/tos-cn-i-xv4ileqgde/831930a0dc184ca597585d696112278b~tplv-xv4ileqgde-cpq:q30.image",
                            "Alt": "",
                        }
                    },
                    {
                        "Type": "text",
                        "Text": "天安门是紫禁城的重要建筑，也是中国明清两代皇城的正门，位于中华人民共和国首都北京市的中心、故宫的南端。天安门是长安街的交汇点，是第一批全国重点文物保护单位。\n天安门，原名“承天门”，始建于明永乐十五年(1417年)，取“承天启运，受命于天”之意。起初是一座三层楼式的木坊。明英宗(天顺)时被烧毁。宪宗(成化)时工部尚书白圭主持修复，建成城楼。但在明朝末年，再次遭到损毁。清顺治八年(1651年)改建为“天安门”，取“受命于天，安邦治民”之意。在明清两代的500年间，天安门是皇城的正门，是新帝登基、皇后册封、颁诏天下、皇帝大婚的地方，同时也是皇帝殿试公布“三甲”(金殿传胪)、招贤取士，以及举行将领出征祭旗、御驾亲征祭路、刑部秋天提审要犯等重大仪式的场所。新中国成立后，政府曾多次主持修缮天安门，并重建了城楼上的木建筑、加厚城墙。1966年3月，河北邢台地区发生7.2级大地震，天安门城楼也受到了波及，与此同时，由于\"文革\"的爆发，天安门城楼的使用次数也显著增加。为确保安全，1969年底国务院决定:彻底拆除天安门城楼，在原址、按原规格和原建筑形式重新修建一座天安门城楼，并将建筑材料全部更新。重建时，将黄琉璃滴水和瓦当上的“龙纹”改为“葵纹”，将原大点金旋子彩画改为金龙和玺彩画。重建后天安门城楼基本保持了1651年改建的形制，只是比原来的33.87米高出了0.83米，通高达34.7米。1984年，为迎接国庆35周年，对天安门城楼重新油漆彩绘，将城楼大厅内的彩画改为“龙草和玺”和“团龙天花”彩画，外檐彩画改为“金龙和玺”彩画。1988年1月1日，天安门城楼为迎接龙年旅游年，正式对外开放。",
                    }
                ],
                "DocumentInfo": {
                    "ContentCharCount": 10349,
                    "ContentTokenCount": 7173,
                    "Filetype": "webpage",
                    "PublishTime": ""
                },
                "HostInfo": {
                    "Hostname": "抖音百科",
                    "IconUrl": "https://p26-volcsearch-d-sign.byteimg.com/isp-i18n-media/img/42d75365f84dd9854e4482bae39dfabf~tplv-be4g95zd3a-224x224.jpeg?lk3s=feb11e32&x-expires=1787629937&x-signature=mRRnm2Fw%2BsNSJY7OpI7wGGxGBhk%3D"
                }
            }
        ],
        "ErrorCode": 0,
        "ErrorMsg": ""
    }
}
```


<span id="QY7DdHsr"></span>
### 错误请求返回示例

```JSON
{
    "ResponseMetadata": {
        "RequestId": "20260622120000A1B2C3D4E5F6",
        "Action": "",
        "Version": "",
        "Service": "",
        "Region": "",
        "Error": {
            "CodeN": 10400,
            "Code": "10400",
            "Message": "query is empty"
        }
    },
    "Result": null
}
```



---



<span id="DnkByx6R"></span>
# 错误处理


|**错误码** |**说明** |**建议检查/处理方式** |
|---|---|---|
|10400 |通用参数错误 |检查 Query 是否为空、字段类型是否正确。 |
|10403 |账号或权限错误 |检查 APIKey、账号信息和服务开通状态。 |
|10408 |功能不可用 |检查账号是否具备对应功能权限。 |
|10409 |套餐模式不支持 |豆包搜索当前仅支持后付费；检查套餐模式。 |
|10410 |无可用搜索套餐 |检查账号是否已开通豆包搜索套餐。 |
|10412 |搜索套餐额度不足 |检查套餐额度或联系运营处理。 |
|10500 |默认内部错误 |可重试；持续失败时携带 RequestId 排查。 |
|10501 |免费额度链路依赖失败 |可重试；持续失败时携带 RequestId 排查。 |
|700429 |请求频率超过限制 |降低请求频率后重试。 |
|700901 |APIKey 无效 |检查 Authorization Header 是否为 Bearer APIKey。 |


