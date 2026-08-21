from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit, urlunsplit


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
CONFIG_PATH = APP_DIR / "config.json"
MODEL_DIR = DATA_DIR / "models"
MODEL_LABELS = {
    "tiny": "tiny - 最快，准确率较低",
    "base": "base - 快速，普通清晰英语",
    "small": "small - 均衡（推荐）",
    "medium": "medium - 较慢，准确率更高",
    "large-v3": "large-v3 - 最慢，准确率最高",
    "turbo": "turbo - 速度快，建议较强电脑",
}
AI_MODEL_OPTIONS = (
    "gpt-5.4-mini",
    "gpt-5.4",
    "gpt-5.5",
    "gpt-4o-mini",
)
LANGUAGE_LABELS = {
    "auto": "自动识别",
    "en": "English",
    "zh": "简体中文",
    "ja": "日本語",
    "ko": "한국어",
    "fr": "Français",
    "de": "Deutsch",
    "es": "Español",
    "ru": "Русский",
}
TARGET_LANGUAGE_LABELS = {code: label for code, label in LANGUAGE_LABELS.items() if code != "auto"}
DEFAULT_CONFIG = {
    "base_url": "",
    "api_key": "",
    "ai_model": "",
    "whisper_model": "small",
    "source_language": "en",
    "target_language": "zh",
}
FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def load_config() -> dict[str, str]:
    config = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        try:
            value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                config.update({key: str(value[key]) for key in config if key in value})
        except (OSError, ValueError):
            pass
    return config


def save_config(config: dict[str, str]) -> None:
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def get_ffmpeg() -> Path:
    bundled = APP_DIR / "ffmpeg.exe"
    if bundled.exists():
        return bundled
    import imageio_ffmpeg

    return Path(imageio_ffmpeg.get_ffmpeg_exe())


def model_is_cached(model_name: str) -> bool:
    patterns = (
        f"models--Systran--faster-whisper-{model_name}/snapshots/*/model.bin",
        f"models--mobiuslabsgmbh--faster-whisper-{model_name}/snapshots/*/model.bin",
    )
    return any(path.is_file() and path.stat().st_size > 0 for pattern in patterns for path in MODEL_DIR.glob(pattern))


def probe_duration(video: Path, ffmpeg: Path) -> float:
    result = subprocess.run(
        [str(ffmpeg), "-i", str(video), "-hide_banner"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    match = re.search(r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise RuntimeError("无法读取视频时长")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_srt(path: Path, entries: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for index, entry in enumerate(entries, 1):
            handle.write(f"{index}\n{entry['start']} --> {entry['end']}\n{entry['text'].strip()}\n\n")


def strip_json_fence(value: str) -> str:
    value = value.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_base_url(value: str) -> str:
    """Accept either a gateway root or an OpenAI-compatible /v1 base URL."""
    value = value.strip().rstrip("/")
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme and parts.netloc and parts.path in ("", "/"):
        return urlunsplit((parts.scheme, parts.netloc, "/v1", parts.query, parts.fragment))
    return value


class TranslationBatchError(RuntimeError):
    def __init__(self, message: str, partial: dict[int, str] | None = None):
        super().__init__(message)
        self.partial = partial or {}


class TranslationServiceError(RuntimeError):
    """A temporary provider/network failure; retry the same request."""

    def __init__(self, message: str, retry_after: int = 60, status_code: int | None = None):
        super().__init__(message)
        self.retry_after = max(1, retry_after)
        self.status_code = status_code


def translate_batch(
    entries: list[dict],
    base_url: str,
    api_key: str,
    model: str,
    source_name: str,
    target_name: str,
    artifact_dir: Path,
    batch_number: int,
    attempt: int,
) -> list[str]:
    source = [{"id": index, "text": item["text"]} for index, item in enumerate(entries)]
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"Translate every {source_name} subtitle into natural {target_name}. Use the full array as context. "
                    "Return only valid JSON in exactly this shape: {\"translations\":[{\"id\":0,\"text\":\"...\"}]}. "
                    "Keep every id exactly once and in the original order. Do not alter ids or omit entries."
                ),
            },
            {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
        ],
    }
    artifact_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"batch-{batch_number:04d}-attempt-{attempt:02d}"
    save_json(artifact_dir / f"{prefix}-input.json", body)
    request = urllib.request.Request(
        normalize_base_url(base_url) + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        (artifact_dir / f"{prefix}-error.txt").write_text(
            f"HTTP {exc.code}\n{detail}", encoding="utf-8"
        )
        if exc.code == 429 or 500 <= exc.code <= 599:
            retry_after = 60
            try:
                retry_after = int(exc.headers.get("Retry-After", retry_after))
            except (TypeError, ValueError):
                pass
            try:
                retry_after = int(json.loads(detail).get("retry_after", retry_after))
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                pass
            raise TranslationServiceError(
                f"AI 接口暂时不可用（HTTP {exc.code}）：{detail}", retry_after, exc.code
            ) from exc
        raise RuntimeError(f"AI 接口返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        (artifact_dir / f"{prefix}-error.txt").write_text(
            f"连接错误\n{exc.reason}", encoding="utf-8"
        )
        raise TranslationServiceError(f"无法连接 AI 接口：{exc.reason}", 30) from exc
    save_json(artifact_dir / f"{prefix}-response.json", payload)
    try:
        content = payload["choices"][0]["message"]["content"]
        (artifact_dir / f"{prefix}-response.txt").write_text(str(content), encoding="utf-8")
        parsed = json.loads(strip_json_fence(content))
        save_json(artifact_dir / f"{prefix}-parsed.json", parsed)
        translations = parsed["translations"]
        by_id = {int(item["id"]): str(item["text"]).strip() for item in translations}
        expected = set(range(len(entries)))
        if set(by_id) != expected:
            missing = sorted(expected - set(by_id))
            raise TranslationBatchError(
                f"返回的字幕编号不完整，缺少：{missing}", by_id
            )
        return [by_id[index] for index in range(len(entries))]
    except TranslationBatchError as exc:
        (artifact_dir / f"{prefix}-error.txt").write_text(
            f"返回格式错误\n{exc}\n原始内容：{content if 'content' in locals() else payload}", encoding="utf-8"
        )
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        (artifact_dir / f"{prefix}-error.txt").write_text(
            f"返回格式错误\n{exc}\n原始内容：{content if 'content' in locals() else payload}", encoding="utf-8"
        )
        raise RuntimeError(f"AI 返回格式不正确：{exc}") from exc


def translate_batch_with_retries(
    entries: list[dict],
    base_url: str,
    api_key: str,
    model: str,
    source_name: str,
    target_name: str,
    artifact_dir: Path,
    batch_number: int,
    max_attempts: int = 3,
    depth: int = 0,
    on_retry=None,
) -> list[str]:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return translate_batch(
                entries, base_url, api_key, model, source_name, target_name,
                artifact_dir, batch_number, attempt,
            )
        except TranslationServiceError as exc:
            last_error = exc
            if exc.status_code in (503, 524) and len(entries) > 1 and depth < 4:
                midpoint = max(1, len(entries) // 2)
                left = translate_batch_with_retries(
                    entries[:midpoint], base_url, api_key, model, source_name,
                    target_name, artifact_dir, batch_number * 100 + 1,
                    max_attempts, depth + 1, on_retry,
                )
                right = translate_batch_with_retries(
                    entries[midpoint:], base_url, api_key, model, source_name,
                    target_name, artifact_dir, batch_number * 100 + 2,
                    max_attempts, depth + 1, on_retry,
                )
                return left + right
            if attempt < max_attempts:
                delay = min(180, exc.retry_after * attempt)
                for remaining in range(delay, 0, -1):
                    if on_retry is not None:
                        on_retry(batch_number, attempt, max_attempts, remaining, str(exc))
                    time.sleep(1)
                continue
            raise RuntimeError(
                f"第 {batch_number} 批接口连续 {max_attempts} 次暂时不可用：{exc}。"
                f"没有拆分批次，现场文件已保存到：{artifact_dir}"
            ) from exc
        except TranslationBatchError as exc:
            last_error = exc
            if exc.partial:
                missing_indices = sorted(set(range(len(entries))) - set(exc.partial))
                missing_entries = [entries[index] for index in missing_indices]
                if len(missing_entries) < len(entries):
                    recovered = translate_batch_with_retries(
                        missing_entries, base_url, api_key, model, source_name,
                        target_name, artifact_dir, batch_number * 100 + attempt,
                        max_attempts, depth + 1, on_retry,
                    )
                    result: list[str | None] = [None] * len(entries)
                    for index, text in exc.partial.items():
                        result[index] = text
                    for index, text in zip(missing_indices, recovered):
                        result[index] = text
                    if all(text is not None for text in result):
                        return [text for text in result if text is not None]
            if len(entries) > 1:
                midpoint = len(entries) // 2
                left = translate_batch_with_retries(
                    entries[:midpoint], base_url, api_key, model, source_name,
                    target_name, artifact_dir, batch_number * 100 + 1,
                    max_attempts, depth + 1, on_retry,
                )
                right = translate_batch_with_retries(
                    entries[midpoint:], base_url, api_key, model, source_name,
                    target_name, artifact_dir, batch_number * 100 + 2,
                    max_attempts, depth + 1, on_retry,
                )
                return left + right
            if attempt < max_attempts:
                continue
            raise RuntimeError(
                f"第 {batch_number} 批连续 {max_attempts} 次返回校验失败：{exc}。"
                f"现场文件已保存到：{artifact_dir}"
            ) from exc
        except Exception as exc:
            last_error = exc
            if attempt < max_attempts:
                continue
            raise RuntimeError(
                f"第 {batch_number} 批连续 {max_attempts} 次请求或返回失败：{exc}。"
                f"现场文件已保存到：{artifact_dir}"
            ) from exc
    raise RuntimeError(str(last_error))


def subtitle_filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    value = value.replace(":", r"\:").replace("'", r"\'")
    return (
        f"subtitles='{value}':force_style="
        "'FontName=Microsoft YaHei,FontSize=24,Outline=2,Shadow=1,Alignment=2,MarginV=36'"
    )


def prepare_render_subtitle(source: Path) -> tuple[Path, str]:
    render_dir = DATA_DIR / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    safe_path = render_dir / "subtitle.srt"
    shutil.copyfile(source, safe_path)
    return safe_path, (
        "subtitles='data/render/subtitle.srt':force_style="
        "'FontName=Microsoft YaHei,FontSize=24,Outline=2,Shadow=1,Alignment=2,MarginV=36'"
    )


class SubtitleApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("AI 视频中文字幕工具")
        self.root.geometry("1040x720")
        self.root.minsize(920, 650)
        self.events: queue.Queue[tuple] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.process: subprocess.Popen[str] | None = None
        self.cancel_requested = False
        self.duration = 1.0
        config = load_config()

        self.video_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.base_url_var = tk.StringVar(value=config["base_url"])
        self.api_key_var = tk.StringVar(value=config["api_key"])
        self.ai_model_var = tk.StringVar(value=config["ai_model"])
        self.whisper_var = tk.StringVar(value=MODEL_LABELS.get(config["whisper_model"], MODEL_LABELS["small"]))
        self.source_var = tk.StringVar(value=LANGUAGE_LABELS.get(config["source_language"], LANGUAGE_LABELS["en"]))
        self.target_var = tk.StringVar(value=TARGET_LANGUAGE_LABELS.get(config["target_language"], TARGET_LANGUAGE_LABELS["zh"]))
        self.detail_var = tk.StringVar(value="请选择一个视频")
        self.progress_var = tk.DoubleVar(value=0)
        self.stage_var = tk.StringVar(value="准备")
        self._build_ui()
        self.root.after(100, self._poll_events)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="AI 视频中文字幕工具", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(outer, text="选择视频后，一键完成英文转写、AI 翻译和中文字幕烧录").pack(anchor="w", pady=(3, 12))

        paths = ttk.LabelFrame(outer, text="文件", padding=10)
        paths.pack(fill="x")
        ttk.Label(paths, text="输入视频", width=9).grid(row=0, column=0, sticky="w")
        ttk.Entry(paths, textvariable=self.video_var).grid(row=0, column=1, sticky="ew")
        self.browse_button = ttk.Button(paths, text="选择视频", command=self.choose_video)
        self.browse_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Label(paths, text="输出目录", width=9).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(paths, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", pady=(8, 0))
        self.output_button = ttk.Button(paths, text="选择目录", command=self.choose_output)
        self.output_button.grid(row=1, column=2, padx=(8, 0), pady=(8, 0))
        paths.columnconfigure(1, weight=1)

        settings = ttk.LabelFrame(outer, text="处理设置", padding=10)
        settings.pack(fill="x", pady=10)
        ttk.Label(settings, text="Whisper 模型").grid(row=0, column=0, sticky="w")
        self.model_box = ttk.Combobox(settings, textvariable=self.whisper_var, values=list(MODEL_LABELS.values()), state="readonly", width=31)
        self.model_box.grid(row=0, column=1, sticky="w", padx=(8, 20))
        ttk.Label(settings, text="源语言").grid(row=0, column=2, sticky="w")
        self.source_box = ttk.Combobox(settings, textvariable=self.source_var, values=list(LANGUAGE_LABELS.values()), state="readonly", width=12)
        self.source_box.grid(row=0, column=3, sticky="w", padx=(6, 12))
        ttk.Label(settings, text="目标语言").grid(row=0, column=4, sticky="w")
        self.target_box = ttk.Combobox(settings, textvariable=self.target_var, values=list(TARGET_LANGUAGE_LABELS.values()), state="readonly", width=12)
        self.target_box.grid(row=0, column=5, sticky="w", padx=(6, 0))

        api = ttk.LabelFrame(outer, text="AI 接口设置", padding=10)
        api.pack(fill="x")
        ttk.Label(api, text="Base URL", width=9).grid(row=0, column=0, sticky="w")
        ttk.Entry(api, textvariable=self.base_url_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(api, text="API Key", width=9).grid(row=1, column=0, sticky="w", pady=(7, 0))
        ttk.Entry(api, textvariable=self.api_key_var, show="*").grid(row=1, column=1, sticky="ew", pady=(7, 0))
        ttk.Label(api, text="模型", width=9).grid(row=2, column=0, sticky="w", pady=(7, 0))
        self.ai_model_box = ttk.Combobox(
            api,
            textvariable=self.ai_model_var,
            values=AI_MODEL_OPTIONS,
        )
        self.ai_model_box.grid(row=2, column=1, sticky="ew", pady=(7, 0))
        ttk.Button(api, text="保存设置", command=self.save_settings).grid(row=0, column=2, rowspan=3, padx=(10, 0))
        api.columnconfigure(1, weight=1)

        stage = ttk.LabelFrame(outer, text="处理进度", padding=10)
        stage.pack(fill="x", pady=10)
        ttk.Label(stage, textvariable=self.stage_var, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        ttk.Progressbar(stage, variable=self.progress_var, maximum=100).pack(fill="x", pady=(8, 4))
        ttk.Label(stage, textvariable=self.detail_var).pack(anchor="w")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(buttons, text="开始处理", command=self.start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(buttons, text="取消", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)

        log_frame = ttk.LabelFrame(outer, text="运行日志", padding=8)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=7, state="disabled", wrap="word", font=("Consolas", 9))
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set)

    def selected_model(self) -> str:
        label = self.whisper_var.get()
        return next((name for name, text in MODEL_LABELS.items() if text == label), "small")

    def selected_language(self, value: str, default: str) -> str:
        return next((code for code, text in LANGUAGE_LABELS.items() if text == value), default)

    def current_config(self) -> dict[str, str]:
        return {
            "base_url": self.base_url_var.get().strip(),
            "api_key": self.api_key_var.get().strip(),
            "ai_model": self.ai_model_var.get().strip(),
            "whisper_model": self.selected_model(),
            "source_language": self.selected_language(self.source_var.get(), "en"),
            "target_language": self.selected_language(self.target_var.get(), "zh"),
        }

    def save_settings(self, silent: bool = False) -> None:
        try:
            save_config(self.current_config())
            if not silent:
                messagebox.showinfo("设置已保存", f"配置已保存到：\n{CONFIG_PATH}")
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))

    def choose_video(self) -> None:
        path = filedialog.askopenfilename(title="选择视频", filetypes=[("视频文件", "*.mp4 *.mkv *.mov *.avi *.webm"), ("所有文件", "*.*")])
        if path:
            self.video_var.set(path)
            if not self.output_var.get().strip():
                self.output_var.set(str(Path(path).parent))

    def choose_output(self) -> None:
        initial = self.output_var.get().strip() or str(Path.home())
        path = filedialog.askdirectory(title="选择输出目录", initialdir=initial if Path(initial).is_dir() else None)
        if path:
            self.output_var.set(path)

    def append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_progress(self, stage: str, percent: float, detail: str) -> None:
        self.stage_var.set(stage)
        self.progress_var.set(max(0, min(100, percent)))
        self.detail_var.set(detail)

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        video = Path(self.video_var.get().strip())
        config = self.current_config()
        if not video.is_file():
            messagebox.showerror("无法开始", "请选择存在的视频文件。")
            return
        needs_translation = config["source_language"] != config["target_language"]
        if needs_translation and not all((config["base_url"], config["api_key"], config["ai_model"])):
            messagebox.showerror("无法开始", "请先填写完整的 AI 接口设置。")
            return
        output_dir = Path(self.output_var.get().strip()) if self.output_var.get().strip() else video.parent
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            save_config(config)
        except OSError as exc:
            messagebox.showerror("无法开始", f"输出目录或配置文件不可用：\n{exc}")
            return
        self.cancel_requested = False
        for widget in (self.start_button, self.browse_button, self.output_button, self.model_box, self.source_box, self.target_box):
            widget.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.set_progress("准备", 0, "正在检查运行环境...")
        self.worker = threading.Thread(target=self.run_pipeline, args=(video, output_dir, config), daemon=True)
        self.worker.start()

    def cancel(self) -> None:
        self.cancel_requested = True
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.events.put(("log", "正在取消当前步骤..."))

    def run_pipeline(self, video: Path, output_dir: Path, config: dict[str, str]) -> None:
        try:
            from faster_whisper import WhisperModel

            ffmpeg = get_ffmpeg()
            self.duration = probe_duration(video, ffmpeg)
            job_dir = DATA_DIR / "jobs" / video.stem
            job_dir.mkdir(parents=True, exist_ok=True)
            english_srt = job_dir / f"{video.stem}.en.srt"
            chinese_srt = job_dir / f"{video.stem}.zh.srt"
            self.events.put(("log", f"输入：{video}"))
            self.events.put(("log", f"视频时长：{self.duration:.1f} 秒"))
            model_status = "已检测到本地模型" if model_is_cached(config["whisper_model"]) else "本地没有该模型，首次使用需要联网下载"
            self.events.put(("progress", "正在加载 Whisper 模型", 1, f"模型：{config['whisper_model']}；{model_status}"))
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            model = WhisperModel(
                config["whisper_model"],
                device="cpu",
                compute_type="int8",
                download_root=str(MODEL_DIR),
            )
            if self.cancel_requested:
                raise RuntimeError("任务已取消")

            source_code = config["source_language"]
            target_code = config["target_language"]
            language_arg = None if source_code == "auto" else source_code
            source_name = LANGUAGE_LABELS.get(source_code, source_code)
            target_name = LANGUAGE_LABELS.get(target_code, target_code)
            self.events.put(("progress", "正在提取字幕", 3, f"Whisper 正在识别 {source_name} 音频..."))
            segments, info = model.transcribe(str(video), language=language_arg, vad_filter=True, beam_size=5)
            entries: list[dict] = []
            for segment in segments:
                if self.cancel_requested:
                    raise RuntimeError("任务已取消")
                entries.append({"start": srt_time(segment.start), "end": srt_time(segment.end), "text": segment.text.strip()})
                progress = 3 + min(segment.end / self.duration, 1) * 22
                self.events.put(("progress", "正在提取字幕", progress, f"已识别到 {segment.end:.1f} / {self.duration:.1f} 秒"))
            if not entries:
                raise RuntimeError("视频中没有识别到英文语音")
            write_srt(english_srt, entries)
            self.events.put(("log", f"英文字幕：{english_srt}"))

            if source_code == target_code:
                translated = [entry["text"] for entry in entries]
                self.events.put(("progress", "跳过翻译", 70, f"源语言和目标语言都是 {target_name}，直接使用转写文本"))
            else:
                translated = []
                batch_size = 25
                translation_dir = job_dir / "translation_batches"
                total_batches = (len(entries) + batch_size - 1) // batch_size
                self.events.put(("log", f"翻译批次：每批 {batch_size} 条，共 {total_batches} 批；每批最多重试 3 次"))

                def show_retry(batch_id, attempt, max_attempts, remaining, error):
                    if self.cancel_requested:
                        raise RuntimeError("任务已取消")
                    self.events.put((
                        "progress",
                        "AI 接口暂时不可用",
                        25 + start / len(entries) * 45,
                        f"批次 {batch_id} 第 {attempt}/{max_attempts} 次失败，{remaining} 秒后重试",
                    ))
                    if remaining == 1:
                        self.events.put(("log", f"接口暂时不可用，等待后重试同一批次：{error}"))

                for start in range(0, len(entries), batch_size):
                    if self.cancel_requested:
                        raise RuntimeError("任务已取消")
                    batch = entries[start:start + batch_size]
                    batch_number = start // batch_size + 1
                    self.events.put(("progress", "正在翻译字幕", 25 + start / len(entries) * 45, f"AI 正在翻译 {source_name} -> {target_name}：第 {start + 1}-{start + len(batch)} 条，共 {len(entries)} 条；批次 {batch_number}/{total_batches}"))
                    translated.extend(translate_batch_with_retries(
                        batch, config["base_url"], config["api_key"], config["ai_model"],
                        source_name, target_name, translation_dir, batch_number,
                        on_retry=show_retry,
                    ))
                    self.events.put(("log", f"第 {batch_number} 批翻译并校验通过，现场文件：{translation_dir}"))
            chinese_entries = [{**entry, "text": translated[index]} for index, entry in enumerate(entries)]
            write_srt(chinese_srt, chinese_entries)
            self.events.put(("progress", "正在翻译字幕", 70, f"已完成 {len(entries)} 条字幕翻译"))
            self.events.put(("log", f"中文字幕：{chinese_srt}"))

            output = output_dir / f"{video.stem} zh-subbed.mp4"
            render_subtitle, filter_arg = prepare_render_subtitle(chinese_srt)
            self.events.put(("log", f"FFmpeg 安全字幕副本：{render_subtitle}"))
            args = [
                str(ffmpeg), "-y", "-i", str(video), "-vf", filter_arg,
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "copy",
                "-movflags", "+faststart", str(output),
            ]
            self.events.put(("progress", "正在生成视频", 70, "FFmpeg 正在烧录中文字幕..."))
            self.process = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                cwd=str(APP_DIR),
            )
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.events.put(("log", line.rstrip()))
                match = FFMPEG_TIME_RE.search(line)
                if match:
                    hours, minutes, seconds = match.groups()
                    current = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
                    self.events.put(("progress", "正在生成视频", 70 + min(current / self.duration, 1) * 30, f"已编码 {current:.1f} / {self.duration:.1f} 秒"))
                if self.cancel_requested and self.process.poll() is None:
                    self.process.terminate()
            code = self.process.wait()
            self.process = None
            if code != 0:
                raise RuntimeError("任务已取消" if self.cancel_requested else f"视频生成失败，退出码 {code}")
            self.events.put(("done", output))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _unlock(self) -> None:
        self.start_button.configure(state="normal")
        self.browse_button.configure(state="normal")
        self.output_button.configure(state="normal")
        self.model_box.configure(state="readonly")
        self.source_box.configure(state="readonly")
        self.target_box.configure(state="readonly")
        self.cancel_button.configure(state="disabled")

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "log":
                    self.append_log(event[1])
                elif event[0] == "progress":
                    self.set_progress(event[1], event[2], event[3])
                elif event[0] == "done":
                    self.set_progress("处理完成", 100, f"输出：{event[1]}")
                    self.append_log(f"完成：{event[1]}")
                    self._unlock()
                    messagebox.showinfo("处理完成", f"中文字幕视频已生成：\n{event[1]}")
                elif event[0] == "error":
                    self.set_progress("处理失败", 0, event[1])
                    self.append_log("错误：" + event[1])
                    self._unlock()
                    messagebox.showerror("处理失败", event[1])
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)


if __name__ == "__main__":
    root = tk.Tk()
    SubtitleApp(root)
    root.mainloop()
