// Small streaming voice cleaner. No per-block arrays, network, or main-thread DSP.
class HNVoiceCleaner extends AudioWorkletProcessor {
  static get parameterDescriptors() {
    return [
      {name:'noise', defaultValue:0, minValue:0, maxValue:30, automationRate:'k-rate'},
      {name:'deesser', defaultValue:0, minValue:0, maxValue:1, automationRate:'k-rate'},
      {name:'gate', defaultValue:0, minValue:0, maxValue:1, automationRate:'k-rate'},
      {name:'threshold', defaultValue:-48, minValue:-70, maxValue:-20, automationRate:'k-rate'},
      {name:'release', defaultValue:180, minValue:50, maxValue:800, automationRate:'k-rate'},
    ];
  }
  constructor() {
    super(); this.low = new Float32Array(2); this.env = 0; this.highEnv = 0; this.gain = 1;
    this.split = 1 - Math.exp(-2 * Math.PI * 5000 / sampleRate);
    this.open = 1 - Math.exp(-1 / (.003 * sampleRate));
    this.port.onmessage = (event) => {
      if (event.data.type === 'reset') { this.low.fill(0); this.env = this.highEnv = 0; this.gain = 1; }
    };
  }
  process(inputs, outputs, params) {
    const input = inputs[0], output = outputs[0];
    if (!input.length) return true;
    const release = 1 - Math.exp(-1 / (params.release[0] * .001 * sampleRate));
    const threshold = 10 ** (params.threshold[0] / 20);
    const floor = 10 ** (-params.noise[0] / 20);
    for (let i = 0; i < output[0].length; i++) {
      const left = input[0][i] || 0, right = (input[1] || input[0])[i] || 0;
      const level = Math.max(Math.abs(left), Math.abs(right));
      this.env += (level - this.env) * (level > this.env ? this.open : release);
      // Gentle expansion for immediate noise audition; FFT denoising runs on export.
      const clean = floor + (1 - floor) * Math.min(1, this.env / .012);
      const gate = params.gate[0] > .5 ? Math.max(.01, Math.min(1, (this.env / threshold) ** 3)) : 1;
      const target = clean * gate;
      this.gain += (target - this.gain) * (target > this.gain ? this.open : release);
      this.low[0] += this.split * (left - this.low[0]);
      this.low[1] += this.split * (right - this.low[1]);
      const high = Math.max(Math.abs(left - this.low[0]), Math.abs(right - this.low[1]));
      this.highEnv += (high - this.highEnv) * (high > this.highEnv ? this.open : release);
      const ess = 1 / (1 + params.deesser[0] * this.highEnv * 35);
      output[0][i] = (this.low[0] + (left - this.low[0]) * ess) * this.gain;
      if (output[1]) output[1][i] = (this.low[1] + (right - this.low[1]) * ess) * this.gain;
    }
    return true;
  }
}
registerProcessor('hn-voice-cleaner', HNVoiceCleaner);
