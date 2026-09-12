# 通用网页爬虫

这是一个面向公开网页、默认尊重 `robots.txt` 并带限速的命令行爬虫。请只抓取你有权访问和处理的内容，并遵守目标网站的服务条款。

## 安装

```bash
python -m pip install -r requirements.txt
```

## 使用

最快测试：

```powershell
py -3 crawler.py --demo books --max-pages 2
```

另一个可测试站点：

```powershell
py -3 crawler.py --demo quotes --max-pages 2
```

通用 CSS 模式：

```bash
python crawler.py "https://example.com/list" ^
  --item ".product" ^
  --field title=".product-title" ^
  --field price=".price" ^
  --field url="a::attr(href)" ^
  --max-pages 3 ^
  --next "a.next" ^
  --delay 2 ^
  --output data.json
```

字段选择器默认提取文字。提取属性时使用 `选择器::attr(属性名)`，例如 `a::attr(href)`。
`href` 和 `src` 会自动转换为绝对 URL，便于后续去重和处理。

CSV 示例：

```bash
python crawler.py "https://example.com/list" --item ".product" \
  --field title=".product-title" --format csv --output data.csv
```

## 个人信息字段

如需在你有授权的页面中提取姓名、性别等个人信息，显式添加 `--allow-personal-data`：

```powershell
py -3 crawler.py "https://example.com/members" ^
  --item ".member" ^
  --field 姓名=".name" ^
  --field 性别=".gender" ^
  --allow-personal-data ^
  --output members.json
```

程序会识别常见个人信息字段名，例如 `姓名`、`性别`、`phone`、`email`、`address` 等；如果没有 `--allow-personal-data`，会拒绝运行并提示确认授权。测试或演示时可以加 `--redact-personal-data` 输出脱敏结果：

```powershell
py -3 crawler.py "https://example.com/members" ^
  --item ".member" ^
  --field 姓名=".name" ^
  --field 手机=".mobile" ^
  --allow-personal-data ^
  --redact-personal-data
```

请只采集你有权处理的数据，避免抓取身份证号、住址、联系方式等非必要字段，并遵守目标网站规则和适用法律。

## 百度搜索模式

按关键词抓取百度普通搜索结果：

```powershell
py -3 crawler.py --baidu "Python 爬虫教程" --max-pages 2 --delay 3 --output baidu.json
```

输出字段包括 `title`、`url`、`summary` 和 `source`。百度可能根据访问频率返回安全验证；程序不会绕过验证码，请降低频率并遵守百度服务条款。

## 稳定性参数

爬虫默认会对临时网络错误、限流和 5xx 服务端错误进行有限重试，并在请求间保持限速：

```powershell
py -3 crawler.py --demo books --max-pages 2 --timeout 15 --retries 3 --backoff 0.8
```

可用参数：

- `--timeout`：单次请求超时时间，默认 `20` 秒。
- `--retries`：临时网络错误和限流的重试次数，默认 `2` 次。
- `--backoff`：重试退避系数，默认 `0.5`。
- `--ignore-robots`：跳过 `robots.txt` 检查，仅在你确认有抓取权限时使用。

输出文件的父目录会自动创建，例如 `--output output/books.json`。
