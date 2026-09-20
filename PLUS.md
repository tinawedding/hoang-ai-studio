# HN AI VOICE STUDIO PRO PLUS · 0.3

This is a separate edition. The original `main` branch and
https://hn-ai-voice-studio-pro.onrender.com remain on v0.2.
Deploy PLUS from `release/studio-plus` to a separate free Render service.
Do not repoint or redeploy the original service as part of this release.

## Working features

- Real upload → original-source mastering → H.264/AAC MP4. Original video
  dimensions, audio offset, and duration are preserved.
- 39 numeric controls plus five switches, with undo/redo and local number input.
- Original RNNoise neural model, adjustable wet/dry blend; one cached clean
  audio per project, reused when downstream controls change.
- Rubber Band pitch with envelope preservation and independent formant colour.
  Envelope estimates are approximate and source-dependent; suitable starting
  shifts are small, especially for speech with background music.
- Auto Level before compression, dynamic harshness EQ, plosive reduction,
  de-click, gentle saturation, five-band EQ, de-esser, gate and limiting.
- Dense, generated stereo room impulse, predelay, decay, damping and ducking.
  These are original generated IRs, not copied commercial presets.
- Signal analysis across the whole recording: RMS, sample peak, clipping ratio
  and active half-second level spread. Suggestions are transparent heuristics,
  not an AI quality score or speaker diagnosis.
- HQ audition of up to 10 seconds from the playhead. The complete master is
  processed once, so loudness and look-ahead have the same context as export.
  MP4 then uses that exact cached master. A/B snippets are RMS matched by only
  attenuating the louder snippet; MP4 retains the chosen export level.
- Personal recipes: settings-only local storage, JSON export/import, deletion.
  Video remains temporary on the server; no video is saved to localStorage.

## Live and HQ

The persistent video source is never reloaded by a slider or A/B click.
AudioWorklets handle streaming preview. Parameter smoothing avoids abrupt
control changes; the room impulse is generated in a Web Worker and crossfaded.
Live Auto Level, dynamic EQ, gate, limiter and ducking are audition approximations.
Controls marked ◇ (RNNoise, formant, de-click and formant preservation) are heard
in HQ/export. The UI explains this distinction. Loudness normalization is also
finalized on the server.

This edition does not implement music/voice stem separation, AI room de-reverb,
voice identity conversion, automatic breath classification or note-level singing
correction. RNNoise can attenuate music and quiet syllables; start gently and
compare HQ. No claims of commercial-plugin equivalence or Vietnamese speech
quality benchmarking are made. The app never sends media to a third-party AI API.

## Runtime and verification

Python 3.11+, FFmpeg from imageio-ffmpeg 0.6.0, NumPy. Build:

```sh
pip install -r requirements.txt
python scripts/fetch_model.py
```

Use one Uvicorn worker. Processing concurrency is one, file ownership and
same-origin protection are enforced. AI and master files are disk streamed;
analysis holds half a second of samples, not full video in RAM. HQ is bounded to
10 seconds. Project caches expire together after two hours or a service restart.
Free Render services may cold-start and share workspace free instance hours.

Tests measure Auto Level's reduction in phrase level difference, dynamic EQ's
level-dependent response, formant envelope movement with unchanged harmonic
spacing, actual RNNoise suppression on synthetic noise, MP4 codec/timing,
HQ/master identity, cache reuse, ownership, range playback, browser controls,
recipe persistence, and performance during a multi-slider sweep. Synthetic tests
are not a subjective assessment of a person's voice.
