self.onmessage=({data:{id,s,sr}})=>{
  const decay=Math.min(3,s.decay*Math.sqrt(s.room)), pre=Math.round(s.predelay*sr/1000), n=Math.round(decay*sr)+pre;
  const channels=[]; const alpha=1-Math.exp(-2*Math.PI*s.damping/sr);
  for(let ch=0;ch<2;ch++){
    const data=new Float32Array(n);let seed=1234567+ch*76543,low=0,power=0;
    for(let i=pre;i<n;i++){
      seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;
      low+=alpha*((seed>>>0)/2147483648-1-low);
      data[i]=low*Math.exp(-6.907755*(i-pre)/(decay*sr));power+=data[i]*data[i];
    }
    const norm=Math.sqrt(power)||1;for(let i=0;i<n;i++)data[i]/=norm;
    channels.push(data.buffer);
  }
  self.postMessage({id,channels},channels);
};
