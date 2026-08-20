"""Transcribe an English video and translate its subtitles into Chinese."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path


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
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{srt_time(cue.start)} --> {srt_time(cue.end)}\n{cue.text.strip()}\n"
        )
    output.write_text("\n".join(blocks), encoding="utf-8")


def transcribe(video: Path, model_name: str, device: str, compute_type: str) -> list[Cue]:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(str(video), language="en", vad_filter=True)
    return [Cue(segment.start, segment.end, segment.text) for segment in segments]


def translate(cues: list[Cue]) -> list[Cue]:
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY. Put it in the process environment or .env.")

    client = OpenAI(
        api_key=api_key,
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
    )
    model = os.environ.get("OPENAI_MODEL")
    if not model:
        raise RuntimeError("Missing OPENAI_MODEL.")

    translated: list[Cue] = []
    for cue in cues:
        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": "Translate English subtitle text into natural concise Simplified Chinese. Return only the translation.",
                },
                {"role": "user", "content": cue.text.strip()},
            ],
        )
        text = response.choices[0].message.content or ""
        translated.append(Cue(cue.start, cue.end, text))
    return translated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("output/zh.srt"))
    parser.add_argument("--model", default="small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--no-translate", action="store_true")
    args = parser.parse_args()

    if not args.video.is_file():
        raise SystemExit(f"Video not found: {args.video}")

    cues = transcribe(args.video, args.model, args.device, args.compute_type)
    if not args.no_translate:
        cues = translate(cues)
    write_srt(cues, args.output)
    print(f"Wrote {len(cues)} subtitle cues to {args.output}")


if __name__ == "__main__":
    main()
