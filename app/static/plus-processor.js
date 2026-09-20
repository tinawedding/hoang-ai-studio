// Streaming audition DSP. Fixed buffers and k-rate controls keep the UI thread free.
class HNPlus extends AudioWorkletProcessor {
  static get parameterDescriptors() {
    return [['auto_level',0,0,100],['level_target',-22,-30,-12],['harshness',0,0,100],
      ['harsh_hz',3200,1500,7000],['harsh_threshold',-26,-48,-6],['warmth',0,0,100],['plosive',0,0,100]]
      .map(([name,defaultValue,minValue,maxValue])=>({name,defaultValue,minValue,maxValue,automationRate:'k-rate'}));
  }
  constructor() {
    super(); this.state=new Float64Array(8); this.rms=0; this.level=1; this.bandEnv=0; this.low=new Float64Array(2); this.plosiveEnv=0;
    this.port.onmessage=()=>{this.state.fill(0);this.low.fill(0);this.rms=0;this.level=1;this.bandEnv=0;this.plosiveEnv=0;};
  }
  process(inputs, outputs, p) {
    const src=inputs[0],out=outputs[0]; if(!src.length) return true;
    const w=2*Math.PI*p.harsh_hz[0]/sampleRate, a=Math.sin(w)/2, a0=1+a;
    const b0=a/a0,b2=-a/a0,a1=-2*Math.cos(w)/a0,a2=(1-a)/a0;
    const threshold=10**(p.harsh_threshold[0]/20), limit=10**(p.auto_level[0]*.09/20), target=10**(p.level_target[0]/20);
    const smooth=1-Math.exp(-1/(.05*sampleRate)),lowA=1-Math.exp(-2*Math.PI*110/sampleRate);
    const drive=1+p.warmth[0]*.035;
    for(let i=0;i<out[0].length;i++){
      const l=src[0][i]||0,r=(src[1]||src[0])[i]||0;
      this.rms+=smooth*((l*l+r*r)/2-this.rms);
      const rms=Math.sqrt(this.rms), desired=rms>.006?Math.max(1/limit,Math.min(limit,target/rms)):1;
      this.level+=(desired-this.level)*(1-Math.exp(-1/((desired<this.level?.35:.8)*sampleRate)));
      let bandPeak=0;
      for(let ch=0;ch<2;ch++){
        const k=ch*4,x=ch?r:l;
        const y=b0*x+b2*this.state[k+1]-a1*this.state[k+2]-a2*this.state[k+3];
        this.state[k+1]=this.state[k];this.state[k]=x;this.state[k+3]=this.state[k+2];this.state[k+2]=y;
        bandPeak=Math.max(bandPeak,Math.abs(y)); this.low[ch]+=lowA*(x-this.low[ch]);
      }
      this.bandEnv+=(bandPeak-this.bandEnv)*(1-Math.exp(-1/((bandPeak>this.bandEnv?.008:.14)*sampleRate)));
      const attenuation=Math.min(.8,p.harshness[0]/100*Math.max(0,1-threshold/Math.max(threshold,this.bandEnv)));
      const lp=Math.max(Math.abs(this.low[0]),Math.abs(this.low[1]));
      this.plosiveEnv+=(lp-this.plosiveEnv)*(1-Math.exp(-1/((lp>this.plosiveEnv?.002:.12)*sampleRate)));
      const cut=p.plosive[0]/100*Math.max(0,1-.08/Math.max(.08,this.plosiveEnv));
      for(let ch=0;ch<out.length;ch++){
        let x=((ch?r:l)-this.state[ch*4+2]*attenuation-this.low[ch]*cut)*this.level;
        if(p.warmth[0]>.01)x=Math.tanh(x*drive)/drive;
        out[ch][i]=x;
      }
    }
    return true;
  }
}
class HNDuck extends AudioWorkletProcessor {
  static get parameterDescriptors(){return [{name:'amount',defaultValue:.5,minValue:0,maxValue:1,automationRate:'k-rate'}];}
  constructor(){super();this.env=0;}
  process(inputs,outputs,p){
    const wet=inputs[0],dry=inputs[1],out=outputs[0];if(!wet.length)return true;
    for(let i=0;i<out[0].length;i++){
      const v=Math.abs(dry[0]?.[i]||0);
      this.env+=(v-this.env)*(1-Math.exp(-1/((v>this.env?.01:.24)*sampleRate)));
      const gain=1/(1+this.env*p.amount[0]*20);
      for(let ch=0;ch<out.length;ch++)out[ch][i]=(wet[ch]||wet[0])[i]*gain;
    }return true;
  }
}
registerProcessor('hn-plus',HNPlus);
registerProcessor('hn-duck',HNDuck);
