import array
import math
import subprocess
from pathlib import Path

import av
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def make_video(path: Path, seconds=4, audio=True, delay=0):
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=25:duration={seconds}"]
    if audio:
        if delay:
            args += ["-itsoffset", str(delay)]
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds-delay}"]
    args += ["-map", "0:v:0"]
    if audio:
        args += ["-map", "1:a:0"]
    # FFV1/PCM in MKV intentionally exercises an input browsers do not play natively.
    args += (["-c:v", "ffv1", "-c:a", "pcm_s16le"] if path.suffix == ".mkv"
             else ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"])
    args += ["-threads", "2", "-t", str(seconds), str(path)]
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
    return path


def inspect_video(path):
    with av.open(str(path)) as container:
        v = container.streams.video[0]
        a = container.streams.audio[0] if container.streams.audio else None
        return {"video": v.codec_context.name, "audio": a.codec_context.name if a else None,
                "width": v.codec_context.width, "height": v.codec_context.height,
                "duration": float(container.duration / av.time_base),
                "audio_start": float((a.start_time or 0) * a.time_base) if a else None,
                "video_start": float((v.start_time or 0) * v.time_base)}


def samples(path):
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-map", "0:a:0",
                          "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "48000", "-"],
                         check=True, capture_output=True, timeout=60).stdout
    result = array.array("h")
    result.frombytes(raw)
    return result


def frequency(values, start=1.0, end=3.0):
    chunk = values[int(start * 48000):int(end * 48000)]
    rising = sum(a <= 0 < b for a, b in zip(chunk, chunk[1:]))
    return rising / ((len(chunk) - 1) / 48000)


def rms(values, start, end):
    chunk = values[int(start * 48000):int(end * 48000)]
    return math.sqrt(sum((x / 32768) ** 2 for x in chunk) / max(1, len(chunk)))
