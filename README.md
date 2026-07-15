# 媒体解析台

本地 Instagram / TikTok 媒体解析工具，默认通过 `http://127.0.0.1:2080` 请求上游。

## 启动

```powershell
cd C:\Users\lol\Desktop\ins逆向
python -m pip install -r requirements.txt
python app.py
```

浏览器打开 `http://127.0.0.1:21359/`。

可通过环境变量修改代理：

```powershell
$env:MEDIA_PROXY = "http://127.0.0.1:2080"
python app.py
```

## 命令行

```powershell
python main.py "https://www.instagram.com/reels/.../"
python main.py "https://www.tiktok.com/@user/video/..." --json
```

## API

- `POST /api/parse`：明文 JSON `{"url":"..."}`，返回结构化媒体结果。
- `GET /api/media/{token}`：短时同源预览，支持视频 Range 请求。
- `GET /api/media/{token}?download=1`：下载媒体。
- `POST /start-task`：保留旧版自定义 Base64 与 URL 数组响应。

TikTok 视频使用无水印 H.264 `playAddr`。媒体令牌默认有效 15 分钟，过期后重新解析即可。

## 测试

```powershell
python -m unittest discover -s tests -v
python -m py_compile app.py main.py instagram_extractor.py tiktok_extractor.py media_resolver.py media_registry.py
node --check static\app.js
```
