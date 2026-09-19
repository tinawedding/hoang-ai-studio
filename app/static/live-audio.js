
const db = (value) => 10 ** (value / 20);
const taps = [13, 19, 29, 37, 53, 71, 89, 113];
const weights = [.22, .19, .16, .13, .10, .08, .07, .05];

export class LiveAudio {
  constructor(video, onState) {
    this.video = video; this.onState = onState; this.enabled = true;
    this.ctx = null; this.pending = null; this.ready = false; this.settings = null;
    this.last = new Map(); this.scope = new Float32Array(1024);
    this.spectrum = new Uint8Array(512); this.monitor = .8; this.muted = false;
    video.addEventListener('play', () => this.start().catch(() => {}));
    video.addEventListener('seeking', () => this.reset());
    video.addEventListener('emptied', () => this.reset());
  }
  async start() {
    if (!this.ctx) {
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) { this.onState('unavailable'); return false; }
      this.ctx = new Context({latencyHint:'interactive'});
      // Keep a working dry route while modules load, or if worklet setup fails.
      this.source = this.ctx.createMediaElementSource(this.video);
      this.dry = this.ctx.createGain(); this.wet = this.ctx.createGain();
      this.wet.gain.value = 0;
      this.analyser = this.ctx.createAnalyser(); this.analyser.fftSize = 1024;
      this.volume = this.ctx.createGain(); this.volume.gain.value = this.monitor;
      this.source.connect(this.dry).connect(this.analyser);
      this.wet.connect(this.analyser); this.analyser.connect(this.volume).connect(this.ctx.destination);
      this.video.volume = 1;
      this.pending = this.build().catch((error) => {
        console.warn('Live audio unavailable:', error.message);
        this.onState('unavailable'); return false;
      });
    }
    await this.ctx.resume();
    const result = await this.pending;
    if (this.ready) this.onState(this.enabled ? 'live' : 'original');
    return result;
  }
  async build() {
    this.onState('loading');
    const { SoundTouchNode } = await import('./vendor/soundtouch/SoundTouchNode.js');
    await Promise.all([
      SoundTouchNode.register(this.ctx, '/static/vendor/soundtouch/soundtouch-processor.js'),
      this.ctx.audioWorklet.addModule('/static/clean-processor.js'),
    ]);
    const ctx = this.ctx;
    this.hp = ctx.createBiquadFilter(); this.hp.type = 'highpass'; this.hp.Q.value = .707;
    this.lp = ctx.createBiquadFilter(); this.lp.type = 'lowpass'; this.lp.Q.value = .707;
    this.cleaner = new AudioWorkletNode(ctx, 'hn-voice-cleaner', {outputChannelCount:[2]});
    this.pitch = new SoundTouchNode({context:ctx});
    this.pitch.setStretchParameters({sequenceMs:40, seekWindowMs:10, overlapMs:8, quickSeek:true});
    this.eq = [140, 400, 1200, 3000, 6500].map((frequency) => {
      const node = ctx.createBiquadFilter(); node.type = 'peaking'; node.frequency.value = frequency; node.Q.value = .8; return node;
    });
    this.compressor = ctx.createDynamicsCompressor(); this.compressor.knee.value = 6;
    this.makeup = ctx.createGain(); this.roomSum = ctx.createGain(); this.output = ctx.createGain();
    this.limiter = ctx.createDynamicsCompressor();
    this.limiter.knee.value = 0; this.limiter.ratio.value = 20;
    this.limiter.attack.value = .003; this.limiter.release.value = .08;
    this.fade = ctx.createGain();
    const chain = [this.source, this.hp, this.lp, this.cleaner, this.pitch, ...this.eq,
      this.compressor, this.makeup, this.roomSum, this.output, this.limiter, this.fade, this.wet];
    for (let i = 1; i < chain.length; i++) chain[i-1].connect(chain[i]);
    this.echoes = taps.map((time, i) => {
      const delay = ctx.createDelay(.5), gain = ctx.createGain();
      delay.delayTime.value = time / 1000; gain.gain.value = 0;
      this.makeup.connect(delay).connect(gain).connect(this.roomSum);
      return {delay, gain, weight:weights[i], time};
    });
    for (const node of [this.pitch, this.cleaner]) node.onprocessorerror = () => {
      this.ready = false; this.ramp(this.wet.gain, 0); this.ramp(this.dry.gain, 1);
      this.onState('unavailable');
    };
    this.ready = true; this.update(this.settings); this.setEnabled(this.enabled);
    return true;
  }
  ramp(param, value, time = .02) {
    if (!param || this.last.get(param) === value) return;
    this.last.set(param, value);
    param.cancelScheduledValues(this.ctx.currentTime);
    param.setTargetAtTime(value, this.ctx.currentTime, time);
  }
  update(s) {
    if (s) this.settings = {...s};
    if (!this.ready || !this.settings) return;
    s = this.settings;
    this.ramp(this.hp.frequency, s.highpass ? s.highpass_hz : 5);
    this.ramp(this.lp.frequency, Math.min(s.lowpass_hz, this.ctx.sampleRate / 2 - 100));
    this.ramp(this.pitch.pitchSemitones, s.pitch, .025);
    this.ramp(this.cleaner.parameters.get('noise'), s.noise);
    this.ramp(this.cleaner.parameters.get('deesser'), s.deesser / 100);
    this.ramp(this.cleaner.parameters.get('gate'), s.gate ? 1 : 0);
    this.ramp(this.cleaner.parameters.get('threshold'), s.gate_threshold);
    this.ramp(this.cleaner.parameters.get('release'), s.gate_release);
    ['bass','lowmid','mid','presence','treble'].forEach((key, i) => this.ramp(this.eq[i].gain, s[key]));
    this.ramp(this.eq[2].frequency, s.mid_hz); this.ramp(this.eq[2].Q, s.mid_q);
    this.ramp(this.compressor.threshold, s.compress ? s.threshold : 0);
    this.ramp(this.compressor.ratio, s.compress ? s.ratio : 1);
    this.ramp(this.compressor.attack, s.attack / 1000);
    this.ramp(this.compressor.release, s.release / 1000);
    this.ramp(this.makeup.gain, s.compress ? db(s.makeup) : 1);
    this.echoes.forEach(({delay, gain, weight, time}) => {
      this.ramp(delay.delayTime, time * s.room / 1000, .04);
      this.ramp(gain.gain, s.reverb / 100 * weight);
    });
    this.ramp(this.output.gain, db(s.gain)); this.ramp(this.limiter.threshold, s.ceiling);
    this.tick();
  }
  setEnabled(enabled) {
    this.enabled = enabled;
    if (!this.ready) { this.onState('idle'); return; }
    this.ramp(this.wet.gain, enabled ? 1 : 0, .012);
    this.ramp(this.dry.gain, enabled ? 0 : 1, .012);
    this.onState(enabled ? 'live' : 'original');
  }
  setVolume(value, muted = false) {
    this.monitor = value; this.muted = muted;
    if (this.volume) this.ramp(this.volume.gain, muted ? 0 : value);
  }
  reset() {
    if (!this.ready) return;
    this.pitch.port.postMessage({type:'reset'}); this.cleaner.port.postMessage({type:'reset'});
    this.tick();
  }
  tick() {
    if (!this.ready) return;
    const fade = Math.min(this.settings?.fade || 0, this.video.duration / 2);
    const value = fade > 0 ? Math.max(0, Math.min(1, this.video.currentTime / fade,
      (this.video.duration - this.video.currentTime) / fade)) : 1;
    this.ramp(this.fade.gain, value, .01);
  }
  measure() {
    if (!this.analyser) return {peak:-60, reduction:0};
    this.analyser.getFloatTimeDomainData(this.scope);
    this.analyser.getByteFrequencyData(this.spectrum);
    let peak = 0;
    for (let i = 0; i < this.scope.length; i++) peak = Math.max(peak, Math.abs(this.scope[i]));
    return {peak:Math.max(-60, 20 * Math.log10(peak || 1e-6)),
      reduction:this.enabled && this.compressor ? this.compressor.reduction : 0};
  }
}
