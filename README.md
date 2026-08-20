# Video Localizer

一个用于学习和作品集展示的视频汉化工具。

当前目标：自动识别视频中的语音，并转换为指定语言的字幕文件。

本项目只负责视频字幕的识别与翻译，不生成封面、标题、简介、缩略图或其他发布素材。

## MVP 功能

- 从视频中提取音频
- 使用 Whisper 自动识别视频中的语音语言
- 使用 AI API 翻译为指定目标语言
- 输出带时间轴的 `.srt` 字幕

## 技术栈

- Python
- Whisper / faster-whisper
- FFmpeg
- OpenAI-compatible API

## 项目结构

```text
video-localizer/
├─ src/              # 源代码
├─ examples/         # 示例输入和输出说明
├─ screenshots/      # 运行截图
├─ README.md
├─ requirements.txt
└─ .gitignore
```

## 使用流程

```text
输入视频
    ↓
提取音频
    ↓
语音识别
    ↓
翻译为指定语言
    ↓
输出 SRT 字幕
```

## 本地运行

也可以使用图形界面版本。安装依赖后运行：

```powershell
python src/app.py
```

打包 Windows EXE：

```powershell
.\build_exe.ps1
```

生成的文件位于 `dist/VideoLocalizer.exe`。首次运行仍需要本地配置翻译 API，模型文件也会在首次识别时下载。

建议使用 Python 3.12：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

设置本地环境变量后运行：

```powershell
$env:OPENAI_API_KEY = "你的 API Key"
$env:OPENAI_BASE_URL = "你的 OpenAI-compatible Base URL"
$env:OPENAI_MODEL = "你的翻译模型"
python src/localize_video.py input/example.mp4 -o output/example.zh.srt
```

只做英文识别、不调用翻译 API：

```powershell
python src/localize_video.py input/example.mp4 -o output/example.en.srt --no-translate
```

## 当前状态

项目处于 MVP 开发阶段。当前仓库先用于整理代码、记录实验过程和展示项目结构。

## 功能边界

- 输入：视频文件
- 输出：带时间轴的字幕文件（`.srt`）
- 不包含：视频封装、配音、封面、标题、简介和其他发布素材生成

## 安全说明

- API Key 只能放在本地环境变量或 `.env` 文件中。
- `.env`、视频文件、音频文件和生成的字幕默认不提交到 Git。
- 仅处理自己拥有版权或已获授权的视频。
