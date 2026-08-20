"""Translate a video's speech into subtitles and burn them into a new video."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI


@dataclass
class Cue:
    start: float
    end: float
    text: str


def srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_srt(cues: list[Cue], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    blocks = [
        f"{index}\n{srt_time(cue.start)} --> {srt_time(cue.end)}\n{cue.text.strip()}\n"
        for index, cue in enumerate(cues, start=1)
    ]
    output.write_text("\n".join(blocks), encoding="utf-8-sig")


def transcribe(video: Path, model_name: str = "small", device: str = "cpu", compute_type: str = "int8") -> list[Cue]:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(str(video), language="en", vad_filter=True, beam_size=5)
    return [Cue(segment.start, segment.end, segment.text.strip()) for segment in segments]


def _parse_json_response(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content)
    result = json.loads(content)
    if not isinstance(result, dict) or not isinstance(result.get("translations"), list):
        raise ValueError("AI response must contain a translations array")
    return result


def translate_batch(client: OpenAI, cues: list[Cue], model: str, batch_number: int) -> list[str]:
    source = [{"id": index, "text": cue.text} for index, cue in enumerate(cues)]
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": (
                    "Translate every English subtitle into natural Simplified Chinese. "
                    "Use the whole array as context. Return only valid JSON in exactly this shape: "
                    "{\"translations\":[{\"id\":0,\"text\":\"...\"}]}. "
                    "Keep every id exactly once and do not omit or reorder entries."
                ),
            },
            {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
        ],
    )
    content = response.choices[0].message.content or ""
    parsed = _parse_json_response(content)
    by_id = {int(item["id"]): str(item["text"]).strip() for item in parsed["translations"]}
    expected = set(range(len(cues)))
    if set(by_id) != expected:
        missing = sorted(expected - set(by_id))
        raise ValueError(f"translation batch {batch_number} is missing ids: {missing}")
    return [by_id[index] for index in range(len(cues))]


def translate(cues: list[Cue], model: str, base_url: str | None, api_key: str, batch_size: int = 100) -> list[Cue]:
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY")
    client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=1800, max_retries=0)
    translated: list[str] = []
    for start in range(0, len(cues), batch_size):
        batch = cues[start:start + batch_size]
        for attempt in range(1, 4):
            try:
                translated.extend(translate_batch(client, batch, model, start // batch_size + 1))
                break
            except Exception as exc:
                if attempt == 3:
                    raise RuntimeError(f"translation batch {start // batch_size + 1} failed: {exc}") from exc
                time.sleep(20 * attempt)
    return [Cue(cue.start, cue.end, text) for cue, text in zip(cues, translated)]


def find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError("FFmpeg was not found. Install it or install imageio-ffmpeg.") from exc


def _ffmpeg_filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")


def burn_subtitles(video: Path, subtitle: Path, output: Path, ffmpeg: str | None = None) -> None:
    ffmpeg = ffmpeg or find_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)
    filter_arg = (
        f"subtitles='{_ffmpeg_filter_path(subtitle)}':force_style="
        "'FontName=Arial,FontSize=24,Outline=2,Shadow=1,Alignment=2,MarginV=36'"
    )
    subprocess.run(
        [ffmpeg, "-y", "-i", str(video), "-vf", filter_arg, "-c:v", "libx264",
         "-preset", "medium", "-crf", "18", "-c:a", "copy", "-movflags", "+faststart", str(output)],
        check=True,
    )


def localize_video(video: Path, output: Path, model: str = "small", device: str = "cpu", compute_type: str = "int8") -> None:
    cues = transcribe(video, model, device, compute_type)
    if not cues:
        raise RuntimeError("No speech was detected")
    translated = translate(
        cues,
        os.environ.get("OPENAI_TRANSLATION_MODEL", os.environ.get("OPENAI_MODEL", "gpt-4o-mini")),
        os.environ.get("OPENAI_BASE_URL"),
        os.environ.get("OPENAI_API_KEY", ""),
    )
    with tempfile.TemporaryDirectory(prefix="video-localizer-") as temp_dir:
        subtitle = Path(temp_dir) / "translated.zh.srt"
        write_srt(translated, subtitle)
        burn_subtitles(video, subtitle, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--model", default="small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    args = parser.parse_args()
    if not args.video.is_file():
        raise SystemExit(f"Video not found: {args.video}")
    output = args.output or args.video.with_name(f"{args.video.stem}_zh-subbed.mp4")
    localize_video(args.video, output, args.model, args.device, args.compute_type)
    print(f"Wrote translated video to {output}")


if __name__ == "__main__":
    main()
