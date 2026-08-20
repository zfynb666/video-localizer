"""Small Windows GUI for the subtitle-only video localizer."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from dotenv import load_dotenv

from localize_video import transcribe, translate, write_srt


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Video Localizer - 字幕翻译")
        self.geometry("680x430")
        self.minsize(600, 360)
        self.video = tk.StringVar()
        self.output = tk.StringVar(value="")
        self.status = tk.StringVar(value="请选择视频文件")
        self._build_ui()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Video Localizer", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="仅将视频语音识别并翻译为带时间轴的 SRT 字幕").pack(anchor="w", pady=(2, 18))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text="视频文件", width=12).pack(side="left")
        ttk.Entry(row, textvariable=self.video).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="选择", command=self.choose_video).pack(side="left", padx=(8, 0))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text="字幕输出", width=12).pack(side="left")
        ttk.Entry(row, textvariable=self.output).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="选择", command=self.choose_output).pack(side="left", padx=(8, 0))
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(16, 8))
        self.start_button = ttk.Button(controls, text="开始翻译", command=self.start)
        self.start_button.pack(side="left")
        ttk.Label(controls, textvariable=self.status).pack(side="left", padx=14)
        self.log = tk.Text(frame, height=12, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(8, 0))

    def choose_video(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Video", "*.mp4 *.mkv *.avi *.mov *.webm"), ("All files", "*.*")])
        if path:
            self.video.set(path)
            if not self.output.get():
                self.output.set(str(Path(path).with_suffix(".zh.srt")))

    def choose_output(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".srt", filetypes=[("SRT subtitles", "*.srt")])
        if path:
            self.output.set(path)

    def write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self) -> None:
        video = Path(self.video.get())
        output = Path(self.output.get())
        if not video.is_file() or output.suffix.lower() != ".srt":
            messagebox.showerror("输入有误", "请选择有效的视频文件，并设置 .srt 输出文件。")
            return
        self.start_button.configure(state="disabled")
        self.status.set("处理中...")
        threading.Thread(target=self.run_job, args=(video, output), daemon=True).start()

    def run_job(self, video: Path, output: Path) -> None:
        try:
            load_dotenv()
            self.after(0, self.write_log, "正在进行语音识别...")
            cues = transcribe(video, "small", "cpu", "int8")
            self.after(0, self.write_log, f"识别到 {len(cues)} 条字幕，正在翻译...")
            cues = translate(cues)
            write_srt(cues, output)
            self.after(0, self.write_log, f"已输出：{output}")
            self.after(0, self.status.set, "完成")
            self.after(0, lambda: messagebox.showinfo("完成", f"字幕已保存到：\n{output}"))
        except Exception as exc:
            self.after(0, self.write_log, f"错误：{exc}")
            self.after(0, self.status.set, "失败")
            self.after(0, lambda: messagebox.showerror("处理失败", str(exc)))
        finally:
            self.after(0, self.start_button.configure, {"state": "normal"})


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    App().mainloop()
