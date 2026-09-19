"""Server-side media processing. No shell interpolation or remote media URLs."""
import asyncio
import math
import os
import re
import time
from pathlib import Path

import av
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
INPUT_FLAGS = [
    "-protocol_whitelist", "file,pipe",
    "-format_whitelist", "mov,matroska,webm,avi",
    "-threads", "2",
]
MAX_SECONDS = int(os.getenv("MAX_SECONDS", "900"))
TIMEOUT = int(os.getenv("PROCESS_TIMEOUT", "1800"))


class MediaError(Exception):
    pass


class Cancelled(Exception):
    pass


def probe(path: Path) -> dict:
    # The demuxer whitelist excludes HLS, concat and network-backed playlists.
    with av.open(str(path), options={
        "protocol_whitelist": "file,pipe",
        "format_whitelist": "mov,matroska,webm,avi",
        "probesize": "10000000",
        "analyzeduration": "10000000",
    }) as container:
        video = next(iter(container.streams.video), None)
        if not video:
            raise MediaError("File này không có hình ảnh video.")
        width, height = video.codec_context.width, video.codec_context.height
        duration = float(video.duration * video.time_base) if video.duration and video.time_base else 0
        if not duration and container.duration:
            duration = float(container.duration / av.time_base)
        audio = next(iter(container.streams.audio), None)
        video_start = float((video.start_time or 0) * video.time_base) if video.time_base else 0
        audio_start = float((audio.start_time or 0) * audio.time_base) if audio and audio.time_base else video_start
        fps = float(video.average_rate or 0)
        if not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS:
            raise MediaError(f"Video cần có thời lượng rõ ràng, tối đa {MAX_SECONDS // 60} phút.")
        if width < 2 or height < 2 or width * height > 3840 * 2160 or max(width, height) > 4096:
            raise MediaError("Bản hiện tại hỗ trợ video từ 2 pixel đến 4K.")
        if fps > 60.1:
            raise MediaError("Bản hiện tại hỗ trợ video tối đa 60 hình/giây.")
        return {
            "duration": round(duration, 3), "width": width, "height": height,
            "audio_offset": round(audio_start - video_start, 6),
            "fps": round(fps, 3), "video_codec": video.codec_context.name,
            "has_audio": bool(container.streams.audio),
            "audio_codec": container.streams.audio[0].codec_context.name if container.streams.audio else None,
        }


def validate_signature(path: Path):
    with path.open("rb") as handle:
        header = handle.read(32)
    mp4_atoms = {b"ftyp", b"wide", b"mdat", b"moov", b"free", b"skip"}
    valid = (len(header) >= 12 and (
        header[4:8] in mp4_atoms
        or header[:4] == b"\x1a\x45\xdf\xa3"
        or (header[:4] == b"RIFF" and header[8:12] == b"AVI ")
    ))
    if not valid:
        raise MediaError("Nội dung file không phải MP4, MOV, MKV, WEBM hoặc AVI hợp lệ.")


def filter_audio(settings: dict, duration: float, audio_offset: float = 0) -> str:
    chain = ["aresample=48000", "asetpts=PTS-STARTPTS"]
    if audio_offset < -0.001:
        chain.extend([f"atrim=start={-audio_offset:.6f}", "asetpts=PTS-STARTPTS"])
    if settings["highpass"]:
        chain.append(f'highpass=f={settings["highpass_hz"]:.1f}')
    if settings["lowpass_hz"] < 20000:
        chain.append(f'lowpass=f={settings["lowpass_hz"]:.1f}')
    if settings["noise"] > 0:
        chain.append(f'afftdn=nr={settings["noise"]:.2f}:nf=-35:tn=1')
    semitones = settings["pitch"]
    if abs(semitones) > 0.001:
        ratio = 2 ** (semitones / 12)
        # Resample changes pitch; inverse tempo keeps video/audio duration aligned.
        chain.extend([f"asetrate={round(48000 * ratio)}", "aresample=48000", f"atempo={1 / ratio:.8f}"])
    if settings["gate"]:
        chain.append(f'agate=threshold={10 ** (settings["gate_threshold"] / 20):.8f}:ratio=4:range=0.01:attack=3:release={settings["gate_release"]:.1f}')
    if settings["deesser"] > 0:
        chain.append(f'deesser=i={settings["deesser"] / 100:.3f}:m=0.6:f=0.5')
    for key, frequency in [("bass", 140), ("lowmid", 400), ("mid", settings["mid_hz"]), ("presence", 3000), ("treble", 6500)]:
        gain = settings[key]
        if abs(gain) > 0.001:
            q = settings["mid_q"] if key == "mid" else 0.8
            chain.append(f"equalizer=f={frequency}:t=q:w={q}:g={gain:.2f}")
    if settings["compress"]:
        chain.append(f'acompressor=threshold={10 ** (settings["threshold"] / 20):.8f}:ratio={settings["ratio"]:.2f}:attack={settings["attack"]:.1f}:release={settings["release"]:.1f}:makeup={10 ** (settings["makeup"] / 20):.6f}')
    if settings["reverb"] > 0:
        delays = "|".join(str(round(t * settings["room"])) for t in (13, 19, 29, 37, 53, 71, 89, 113))
        decays = "|".join(f'{settings["reverb"] / 100 * w:.6f}' for w in (.22, .19, .16, .13, .10, .08, .07, .05))
        chain.append(f'aecho=in_gain=1:out_gain=1:delays={delays}:decays={decays}')
    if settings["normalize"]:
        chain.append(f'loudnorm=I={settings["target_lufs"]:.1f}:TP={settings["ceiling"]:.1f}:LRA=11')
    if abs(settings["gain"]) > 0.001:
        chain.append(f'volume={settings["gain"]:.2f}dB')
    if audio_offset > 0.001:
        chain.append(f"adelay={round(audio_offset * 1000)}:all=1")
    if settings["fade"] > 0:
        fade = min(settings["fade"], duration / 2)
        chain.extend([f"afade=t=in:d={fade:.4f}", f"afade=t=out:st={duration-fade:.4f}:d={fade:.4f}"])
    # Pad processing tails only; never end the video early due to short audio.
    chain.extend([f'alimiter=limit={10 ** (settings["ceiling"] / 20):.6f}:level=false:latency=true', "aresample=48000",
                  "apad", f"atrim=duration={duration:.6f}", "asetpts=PTS-STARTPTS"])
    return ",".join(chain)


async def run_ffmpeg(args: list[str], job: dict, duration: float, logfile: Path, base=0, span=100):
    start = time.monotonic()
    with logfile.open("wb") as err:
        process = await asyncio.create_subprocess_exec(
            FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-progress", "pipe:1", "-nostats", *args,
            stdout=asyncio.subprocess.PIPE, stderr=err,
        )
        try:
            async def read_progress():
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    key, _, value = line.decode(errors="replace").strip().partition("=")
                    if key == "out_time_us":
                        try:
                            seconds = int(value) / 1_000_000
                            job["progress"] = max(job["progress"], min(base + span - 1, base + int(span * seconds / duration)))
                        except (ValueError, ZeroDivisionError):
                            pass
            reader = asyncio.create_task(read_progress())
            while process.returncode is None:
                if job.get("cancel"):
                    raise Cancelled()
                if time.monotonic() - start > TIMEOUT:
                    raise MediaError("Xử lý vượt thời gian cho phép. Hãy thử video ngắn hơn.")
                try:
                    await asyncio.wait_for(process.wait(), timeout=0.4)
                except asyncio.TimeoutError:
                    pass
            await reader
            if process.returncode:
                # Technical diagnostics stay in server logs, not the public API.
                detail = logfile.read_bytes()[-1500:].decode(errors="replace")
                print(f"ffmpeg_error job={job['id']} {detail}", flush=True)
                raise MediaError("Không giải mã được video này. File có thể hỏng hoặc dùng codec chưa hỗ trợ.")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if "reader" in locals() and not reader.done():
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)


async def prepare(project: dict, job: dict):
    folder = Path(project["folder"])
    source = folder / "source"
    validate_signature(source)
    try:
        meta = await asyncio.wait_for(asyncio.to_thread(probe, source), timeout=30)
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError("Không đọc được video. Hãy kiểm tra file gốc có phát được không.") from exc
    project["meta"] = meta
    duration = meta["duration"]
    job["stage"] = "Đang tạo video xem trước"
    preview = folder / "preview.mp4"
    timing = ["aresample=48000", "asetpts=PTS-STARTPTS"]
    offset = meta.get("audio_offset", 0)
    if offset < -0.001:
        timing.extend([f"atrim=start={-offset:.6f}", "asetpts=PTS-STARTPTS"])
    elif offset > 0.001:
        timing.append(f"adelay={round(offset * 1000)}:all=1")
    timing.extend(["apad", f"atrim=duration={duration:.6f}", "asetpts=PTS-STARTPTS"])
    # Prepare on the server even when the browser cannot decode the original codec.
    await run_ffmpeg([
        *INPUT_FLAGS, "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,setpts=PTS-STARTPTS",
        "-af", ",".join(timing),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p",
        "-g", "24", "-keyint_min", "24", "-sc_threshold", "0",
        "-threads", "2", "-c:a", "aac", "-b:a", "128k", "-t", str(duration),
        "-map_metadata", "-1", "-movflags", "+faststart", str(preview),
    ], job, duration, folder / "prepare.log", span=94)
    if meta["has_audio"]:
        job["stage"] = "Đang tạo dạng sóng âm thanh"
        raw = folder / "waveform.u8"
        await run_ffmpeg([
            *INPUT_FLAGS, "-i", str(preview), "-vn", "-ac", "1", "-ar", "2000",
            "-f", "u8", "-t", str(duration), str(raw),
        ], job, duration, folder / "waveform.log", base=94, span=6)
        def peaks():
            data = raw.read_bytes()  # bounded to 1.8 MB for a 15-minute video
            stride = max(1, math.ceil(len(data) / 1200))
            return [round(max(abs(v - 128) for v in data[i:i+stride]) / 128, 3)
                    for i in range(0, len(data), stride)]
        project["waveform"] = await asyncio.to_thread(peaks)
        raw.unlink(missing_ok=True)
    project["state"] = "ready"


async def render(project: dict, job: dict, settings: dict):
    folder = Path(project["folder"])
    duration = project["meta"]["duration"]
    if not project["meta"]["has_audio"]:
        raise MediaError("Video này không có âm thanh để đổi giọng.")
    pending = folder / f"{job['id']}.part.mp4"
    destination = folder / "output.mp4"
    job["stage"] = "Đang xử lý giọng và ghép MP4"
    await run_ffmpeg([
        *INPUT_FLAGS, "-i", str(folder / "source"), "-map", "0:v:0", "-map", "0:a:0",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1,setpts=PTS-STARTPTS",
        "-af", filter_audio(settings, duration, project["meta"].get("audio_offset", 0)),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-threads", "2", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", str(duration), "-map_metadata", "-1", "-movflags", "+faststart", str(pending),
    ], job, duration, folder / "render.log", span=94)
    job["stage"] = "Đang kiểm tra file xuất"
    try:
        result = await asyncio.to_thread(probe, pending)
        if (result["video_codec"] != "h264" or result["audio_codec"] != "aac"
                or abs(result["duration"] - duration) > max(0.25, 2 / (project["meta"]["fps"] or 25))):
            raise MediaError("File xuất không đạt kiểm tra hình, tiếng hoặc thời lượng.")
        if job.get("cancel"):
            raise Cancelled()
        pending.replace(destination)
        project["output"] = {"size": destination.stat().st_size, "settings": settings,
                             "duration": result["duration"], "job_id": job["id"]}
        project["state"] = "done"
    finally:
        pending.unlink(missing_ok=True)
