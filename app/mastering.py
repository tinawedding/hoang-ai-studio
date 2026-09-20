"""Bounded-memory mastering, cached AI cleanup, and exact-engine auditions."""
import asyncio
import hashlib
import json
import math
import wave
from pathlib import Path

import numpy as np

from . import media

MODEL = Path(__file__).parent / "models/std.rnnn"


def capabilities():
    return {"rnnoise": MODEL.is_file(), "formant": True, "hq_preview": True,
            "music_separation": False, "ai_dereverb": False}


def impulse(path, settings):
    """Original deterministic stereo room IR, shared algorithm with the web worker."""
    sr = 48000
    decay = min(3, settings["decay"] * math.sqrt(settings["room"]))
    pre = round(settings["predelay"] * sr / 1000)
    n = round(decay * sr) + pre
    result = np.zeros((n, 2), dtype=np.float64)
    alpha = 1 - math.exp(-2 * math.pi * settings["damping"] / sr)
    for ch in range(2):
        seed, low = 1234567 + ch * 76543, 0.0
        for i in range(pre, n):
            seed ^= (seed << 13) & 0xffffffff
            seed ^= seed >> 17
            seed ^= (seed << 5) & 0xffffffff
            noise = (seed & 0xffffffff) / 2147483648 - 1
            low += alpha * (noise - low)
            result[i, ch] = low * math.exp(-6.907755 * (i-pre) / (decay*sr))
        result[:, ch] /= max(.0001, math.sqrt(float(np.sum(result[:, ch] ** 2))))
    with wave.open(str(path), "wb") as out:
        out.setparams((2, 2, sr, 0, "NONE", "not compressed"))
        out.writeframes((result * 32767).astype("<i2").tobytes())


def analyze_pcm(path):
    levels, total, energy, peak, clipped = [], 0, 0.0, 0.0, 0
    with path.open("rb") as f:
        while chunk := f.read(16000):  # half-second mono PCM; never load full video
            a = np.frombuffer(chunk, dtype="<i2").astype(np.float64) / 32768
            energy += float(np.sum(a*a)); total += len(a)
            peak = max(peak, float(np.max(np.abs(a))))
            clipped += int(np.sum(np.abs(a) >= .999))
            levels.append(20 * math.log10(max(1e-6, math.sqrt(float(np.mean(a*a))))))
    active = [x for x in levels if x > -48]
    spread = float(np.percentile(active, 90)-np.percentile(active, 10)) if active else 0
    rms = 20 * math.log10(max(1e-6, math.sqrt(energy/max(1,total))))
    return {"rms_db": round(rms, 1), "peak_db": round(20*math.log10(max(1e-6,peak)), 1),
            "dynamic_db": round(spread, 1), "clip_percent": round(clipped/max(1,total)*100, 3),
            "duration": round(total/16000, 2),
            "suggested": {"auto_level": 55 if spread > 8 else 25, "warmth": 15,
                          "harshness": 20, "deesser": 15, "noise": 0,
                          "compress": True, "normalize": True, "pitch": 0, "formant": 0}}


async def source_audio(project, job):
    folder = Path(project["folder"])
    path = folder / "original.flac"
    if path.exists():
        return path
    duration = project["meta"]["duration"]
    offset = project["meta"].get("audio_offset", 0)
    timing = ["aresample=48000", "asetpts=PTS-STARTPTS"]
    if offset < -.001:
        timing.extend([f"atrim=start={-offset:.6f}", "asetpts=PTS-STARTPTS"])
    elif offset > .001:
        timing.append(f"adelay={round(offset*1000)}:all=1")
    timing.extend(["apad", f"atrim=duration={duration:.6f}"])
    tmp = folder / "original.part.flac"
    await media.run_ffmpeg([*media.INPUT_FLAGS, "-i", str(folder/"source"), "-vn", "-af", ",".join(timing),
        "-ac", "2", "-c:a", "flac", "-sample_fmt", "s32", str(tmp)], job, duration, folder/"audio.log", span=12)
    tmp.replace(path)
    return path


async def analyze(project, job):
    folder = Path(project["folder"])
    source = await source_audio(project, job)
    raw = folder / "analysis.s16"
    job["stage"] = "Đang đo mức giọng và độ chênh giữa các câu"
    await media.run_ffmpeg(["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", str(raw)],
                           job, project["meta"]["duration"], folder/"analysis.log", base=12, span=86)
    try:
        project["analysis"] = await asyncio.to_thread(analyze_pcm, raw)
    finally:
        raw.unlink(missing_ok=True)
    project["state"] = "done" if project.get("output") else "ready"


async def master(project, job, settings):
    folder = Path(project["folder"])
    duration = project["meta"]["duration"]
    signature = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
    result = folder / "master.flac"
    if project.get("master_signature") == signature and result.exists():
        job["progress"] = 80
        return result
    source = await source_audio(project, job)
    inputs = ["-i", str(source)]
    graph = []
    if settings["ai_noise"] > 0:
        if not MODEL.is_file():
            raise media.MediaError("Mô hình RNNoise chưa sẵn sàng trên máy chủ.")
        clean = folder / "rnnoise.flac"
        if not clean.exists():
            job["stage"] = "Đang khử nhiễu AI lần đầu · kết quả được giữ cho lần chỉnh sau"
            pending = folder / "rnnoise.part.flac"
            await media.run_ffmpeg(["-i", str(source), "-af", f"arnndn=m='{MODEL}':mix=1", "-c:a", "flac", str(pending)],
                                   job, duration, folder/"ai.log", base=12, span=22)
            pending.replace(clean)
        inputs += ["-i", str(clean)]
        mix = settings["ai_noise"]/100
        graph.append(f"[0:a][1:a]amix=inputs=2:weights='{1-mix:.6f} {mix:.6f}':normalize=0[voice]")
    else:
        graph.append("[0:a]anull[voice]")
    graph.append(f"[voice]{media.filter_audio(settings, duration, finishing=False)}[shaped]")
    if settings["reverb"] > 0:
        ir = folder / "room.wav"
        await asyncio.to_thread(impulse, ir, settings)
        idx = len(inputs)//2
        inputs += ["-i", str(ir)]
        graph.append("[shaped]asplit=3[dry][send][detector]")
        graph.append(f"[send][{idx}:a]afir=dry=1:wet=1:gtype=none:irgain=1:irnorm=-1:minp=256:maxp=2048:precision=float[room]")
        graph.append(f'[room][detector]sidechaincompress=threshold=0.025:ratio={1+settings["reverb_duck"]*.11:.3f}:attack=10:release=240[ducked]')
        graph.append(f'[dry][ducked]amix=inputs=2:weights=\'1 {settings["reverb"]/100:.6f}\':normalize=0[mixed]')
    else:
        graph.append("[shaped]anull[mixed]")
    tail = []
    if settings["normalize"]:
        tail.append(f'loudnorm=I={settings["target_lufs"]}:TP={settings["ceiling"]}:LRA=11')
    # Some loudnorm flush frames contain discontinuous timestamps despite a
    # continuous PCM stream. Reclock by samples before fades and duration trim.
    tail += ["aresample=48000", "asetpts=N/SR/TB"]
    tail.append(f'volume={settings["gain"]}dB')
    if settings["fade"]:
        fade = min(settings["fade"], duration/2)
        tail += [f"afade=t=in:d={fade}", f"afade=t=out:st={duration-fade}:d={fade}"]
    tail += [f'alimiter=limit={10**(settings["ceiling"]/20):.6f}:level=false:latency=true',
             "aresample=48000", "asetpts=N/SR/TB", "apad", f"atrim=end_sample={round(duration*48000)}", "asetpts=N/SR/TB"]
    graph.append("[mixed]" + ",".join(tail) + "[out]")
    pending = folder / "master.part.flac"
    job["stage"] = "Đang hoàn thiện giọng · Auto Level / Formant / EQ / Không gian"
    await media.run_ffmpeg([*inputs, "-filter_complex_threads", "1", "-filter_complex", ";".join(graph),
                           "-map", "[out]", "-c:a", "flac", "-sample_fmt", "s32", str(pending)],
                           job, duration, folder/"master.log", base=34, span=46)
    pending.replace(result)
    project["master_signature"] = signature
    return result


async def audition(project, job, payload):
    folder = Path(project["folder"])
    source = await master(project, job, payload["settings"])
    start = payload["start"]
    duration = min(10, project["meta"]["duration"]-start)
    job["stage"] = "Đang tạo đoạn HQ và cân mức A / B"
    paths = []
    for name, path in [("hq-a", folder/"original.flac"), ("hq-b", source)]:
        dest = folder/f"{name}.{job['id']}.wav"
        await media.run_ffmpeg(["-ss", str(start), "-i", str(path), "-t", str(duration), "-ac", "2", "-c:a", "pcm_s16le", str(dest)],
                               job, duration, folder/"hq.log", base=80, span=18)
        paths.append(dest)
    def match():
        arrays = []
        for path in paths:
            with wave.open(str(path), "rb") as f:
                arrays.append(np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype(np.float64))
        levels = [math.sqrt(float(np.mean(a*a))) for a in arrays]
        target = min(levels)
        for path, a, level in zip(paths, arrays, levels):
            gain = target/level if level > .01 else 1
            with wave.open(str(path), "wb") as f:
                f.setparams((2,2,48000,0,"NONE","not compressed"))
                f.writeframes((a*gain).astype("<i2").tobytes())
    await asyncio.to_thread(match)
    if job.get("cancel"):
        raise media.Cancelled()
    for old in folder.glob("hq-*.wav"):
        if old not in paths:
            old.unlink(missing_ok=True)
    project["audition"] = {"job_id":job["id"], "start":start, "duration":duration,
                           "settings":payload["settings"], "matched":"RMS"}
    project["state"] = "done" if project.get("output") else "ready"
