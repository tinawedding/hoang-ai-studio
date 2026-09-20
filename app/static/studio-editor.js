import { LiveAudio } from './live-audio.js';
const $ = (id) => document.getElementById(id);
const clock = (value) => `${Math.floor((value || 0) / 60)}:${String(Math.floor((value || 0) % 60)).padStart(2, '0')}`;

export function buildControls(config) {
  const groups = {voice:'Giọng & độ sạch', eq:'Equalizer · 5 dải', dynamics:'Compressor & Gate', space:'Không gian & đầu ra', master:'Hoàn thiện khi xuất'};
  const toggleGroups = {voice:[['highpass','Bật lọc tiếng ù'],['preserve_formants','Giữ màu giọng khi đổi cao độ ◇ HQ']], dynamics:[['compress','Bật Compressor'],['gate','Bật Noise Gate']], master:[['normalize','Cân âm LUFS khi xuất']]};
  const root = $('pro-controls'); root.replaceChildren();
  for (const [group, title] of Object.entries(groups)) {
    const section = document.createElement('section'); section.dataset.panel = group;
    section.className = 'pro-group'; section.hidden = group !== 'voice' && group !== 'master';
    const heading = document.createElement('h3'); heading.textContent = title; section.append(heading);
    if (group === 'eq') {
      const canvas = document.createElement('canvas'); canvas.id = 'eq-curve'; canvas.setAttribute('aria-label', 'Đường cong EQ'); section.append(canvas);
    }
    for (const [id, title] of toggleGroups[group] || []) {
      const label = document.createElement('label'); label.className = 'toggle-row';
      const span = document.createElement('span'); span.textContent = title;
      const input = document.createElement('input'); input.type = 'checkbox'; input.id = id; input.checked = config.defaults[id];
      label.append(span, input); section.append(label);
    }
    for (const item of config.controls.filter((item) => item.group === group)) {
      const row = document.createElement('div'); row.className = 'pro-control';
      const label = document.createElement('label'); label.className = 'slider-label'; label.htmlFor = item.id;
      const name = document.createElement('span'); name.textContent = item.label;
      const output = document.createElement('output'); output.id = item.id + '-value'; label.append(name, output);
      const line = document.createElement('div'); line.className = 'slider-line';
      const range = document.createElement('input'); range.type = 'range'; range.id = item.id;
      const number = document.createElement('input'); number.type = 'number'; number.id = item.id + '-number'; number.setAttribute('aria-label', item.label + ' · nhập số');
      for (const input of [range, number]) {
        input.min = item.min; input.max = item.max; input.step = item.step; input.value = item.default;
      }
      range.title = 'Nhấp đúp để đặt lại';
      range.addEventListener('dblclick', () => {
        range.value = item.default; range.dispatchEvent(new Event('input')); range.dispatchEvent(new Event('change'));
      });
      number.addEventListener('change', () => {
        let value = Number(number.value);
        if (!Number.isFinite(value) || number.value === '') value = Number(range.value);
        range.value = Math.min(item.max, Math.max(item.min, value));
        range.dispatchEvent(new Event('input')); range.dispatchEvent(new Event('change'));
        number.value = range.value;
      });
      line.append(range, number); row.append(label, line); section.append(row);
    }
    root.append(section);
  }
  document.querySelectorAll('[data-group]').forEach((button) => button.addEventListener('click', () => {
    document.querySelectorAll('[data-group]').forEach((b) => b.classList.toggle('selected', b === button));
    document.querySelectorAll('[data-panel]').forEach((panel) => { panel.hidden = panel.dataset.panel !== button.dataset.group && panel.dataset.panel !== 'master'; });
    window.dispatchEvent(new Event('resize'));
  }));
}

export class StudioEditor {
  constructor(onError) {
    this.video = $('video'); this.onError = onError; this.wave = []; this.frame = 0;
    this.project = null; this.loop = false; this.loopIn = 0; this.loopOut = 0;
    this.dragging = false; this.seekPending = null; this.lastSeek = 0; this.settings = null;
    this.audio = new LiveAudio(this.video, (state) => this.showState(state));
    $('play-button').addEventListener('click', () => this.togglePlay());
    this.video.addEventListener('click', () => this.togglePlay());
    this.video.addEventListener('play', () => { $('play-button').textContent = 'Ⅱ'; $('play-button').setAttribute('aria-label','Tạm dừng video'); this.animate(); });
    this.video.addEventListener('pause', () => { $('play-button').textContent = '▶'; $('play-button').setAttribute('aria-label','Phát video'); this.drawTime(); });
    this.video.addEventListener('loadedmetadata', () => {
      $('seek').max = this.video.duration || 1; this.loopOut = this.video.duration || 0; this.drawTime();
    });
    this.video.addEventListener('timeupdate', () => { if (this.video.paused) this.drawTime(); });
    $('skip-back').addEventListener('click', () => this.seek(this.video.currentTime - 5));
    $('skip-forward').addEventListener('click', () => this.seek(this.video.currentTime + 5));
    $('seek').addEventListener('pointerdown', () => {
      this.dragging = true; this.resumeAfterSeek = !this.video.paused; this.video.pause();
    });
    $('seek').addEventListener('input', () => {
      this.seekPending = Number($('seek').value); this.drawTime(this.seekPending);
      if (!this.seekTimer) this.seekTimer = setTimeout(() => this.flushSeek(false), 70);
    });
    const release = () => {
      if (!this.dragging) return;
      this.seekPending = Number($('seek').value);
      this.dragging = false; this.flushSeek(true);
      if (this.resumeAfterSeek) this.video.play().catch(this.onError);
    };
    window.addEventListener('pointerup', release); window.addEventListener('pointercancel', release);
    $('seek').addEventListener('change', () => {
      if (!this.dragging) { this.seekPending = Number($('seek').value); this.flushSeek(true); }
    });
    $('compare-original').addEventListener('click', () => this.audio.setEnabled(false));
    $('compare-live').addEventListener('click', () => {
      this.audio.setEnabled(true); this.audio.start().catch(this.onError);
    });
    $('monitor-volume').addEventListener('input', () => this.audio.setVolume(Number($('monitor-volume').value), this.audio.muted));
    $('mute-button').addEventListener('click', () => {
      this.audio.setVolume(this.audio.monitor, !this.audio.muted);
      $('mute-button').textContent = this.audio.muted ? 'Đã tắt' : 'Âm nghe';
    });
    $('fullscreen-button').addEventListener('click', () => {
      if (document.fullscreenElement) document.exitFullscreen().catch(this.onError);
      else if ($('video-wrap').requestFullscreen) $('video-wrap').requestFullscreen().catch(this.onError);
      else if (this.video.webkitEnterFullscreen) this.video.webkitEnterFullscreen();
    });
    document.addEventListener('fullscreenchange', () => { this.video.controls = !!document.fullscreenElement; });
    $('mark-in').addEventListener('click', () => { this.loopIn = this.video.currentTime; if (this.loopOut < this.loopIn + .2) this.loopOut = this.video.duration; this.drawLoop(); });
    $('mark-out').addEventListener('click', () => { this.loopOut = this.video.currentTime; if (this.loopOut < this.loopIn + .2) this.loopIn = 0; this.drawLoop(); });
    $('loop-button').addEventListener('click', () => {
      if (this.loopOut - this.loopIn < .2) return;
      this.loop = !this.loop; this.drawLoop();
    });
    this.video.addEventListener('ended', () => { if (this.loop) { this.seek(this.loopIn); this.video.play().catch(this.onError); } });
    $('close-output').addEventListener('click', () => $('output-dialog').close());
    $('output-dialog').addEventListener('close', () => { $('output-video').pause(); });
    document.addEventListener('keydown', (event) => {
      if (event.code !== 'Space' || /INPUT|TEXTAREA|SELECT|BUTTON/.test(event.target.tagName) || document.querySelector('dialog[open]') || !this.project) return;
      event.preventDefault(); this.togglePlay();
    });
    this.resize = new ResizeObserver(() => { this.drawWave(); this.drawEQ(); });
    this.resize.observe($('timeline')); this.resize.observe($('controls-panel') || document.querySelector('.controls-panel'));
    this.previewResize = new ResizeObserver(() => {
      const height = this.project ? document.querySelector('.editor-panel').getBoundingClientRect().height : 0;
      document.documentElement.style.setProperty('--preview-height', `${Math.ceil(height)}px`);
    });
    this.previewResize.observe(document.querySelector('.editor-panel'));
    window.addEventListener('resize', () => { this.drawWave(); this.drawEQ(); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden && !this.video.paused) this.animate(); });
  }
  showState(state) {
    const labels = {idle:'BẤM PHÁT ĐỂ NGHE', loading:'ĐANG MỞ ÂM THANH', live:'● LIVE', original:'A · ÂM GỐC', unavailable:'NGHE BẢN GỐC'};
    $('live-state').textContent = labels[state] || labels.idle;
    $('live-state').dataset.state = state;
    $('compare-live').classList.toggle('selected', this.audio.enabled);
    $('compare-original').classList.toggle('selected', !this.audio.enabled);
    $('playback-note').textContent = state === 'original' ? 'Âm thanh gốc' : state === 'unavailable'
      ? 'Thiết bị chưa mở được Live. Bạn vẫn có thể xuất MP4 để nghe.' : 'Nghe thử trực tiếp';
  }
  togglePlay() {
    if (!this.project || !this.video.getAttribute('src')) return;
    if (this.video.paused) {
      this.audio.start().catch(this.onError); this.video.play().catch(this.onError);
    } else this.video.pause();
  }
  seek(time) {
    if (!Number.isFinite(this.video.duration)) return;
    this.video.currentTime = Math.max(0, Math.min(this.video.duration, time)); this.drawTime();
  }
  flushSeek(exact) {
    clearTimeout(this.seekTimer); this.seekTimer = null;
    if (this.seekPending === null) return;
    const time = this.seekPending; this.seekPending = null;
    if (!exact && typeof this.video.fastSeek === 'function') this.video.fastSeek(time);
    else this.seek(time);
  }
  update(settings) { this.settings = {...settings}; this.audio.update(settings); this.drawEQ(); }
  async setProject(project) {
    const id = project?.id || null;
    if (this.project?.id === id) return;
    this.project = project; this.wave = []; this.loop = false; this.loopIn = 0; this.loopOut = project?.meta?.duration || 0;
    clearTimeout(this.seekTimer); this.seekTimer = null; this.seekPending = null; this.dragging = false;
    $('live-console').hidden = !project; document.body.classList.toggle('has-project', !!project);
    this.audio.reset(); this.drawLoop(); this.drawWave();
    if (!project) return;
    try {
      const response = await fetch(`/api/projects/${id}/waveform`, {credentials:'same-origin'});
      if (!response.ok) return;
      const data = await response.json();
      if (this.project?.id !== id) return;
      this.wave = data.peaks; this.drawWave();
    } catch { /* Waveform is optional; video and live DSP keep working. */ }
  }
  openOutput(project, dirty) {
    if (!project?.output) return;
    this.video.pause();
    const out = $('output-video'), src = `/api/projects/${project.id}/media/output?v=${project.output.job_id}`;
    if (out.getAttribute('src') !== src) out.src = src;
    $('output-settings-note').textContent = dirty ? 'Bạn đã chỉnh tiếp. Đây là MP4 của lần xuất trước.' : 'Bản hoàn thiện trên máy chủ, đúng thông số ở lần xuất này.';
    $('output-dialog').showModal();
  }
  canvas(id) {
    const canvas = $(id); if (!canvas || !canvas.clientWidth) return null;
    const dpr = Math.min(2, window.devicePixelRatio || 1), width = canvas.clientWidth, height = canvas.clientHeight;
    if (canvas.width !== Math.round(width*dpr) || canvas.height !== Math.round(height*dpr)) {
      canvas.width = Math.round(width*dpr); canvas.height = Math.round(height*dpr);
    }
    const ctx = canvas.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,width,height);
    return {ctx,width,height};
  }
  drawWave() {
    const c = this.canvas('waveform'); if (!c) return;
    const {ctx,width,height} = c; ctx.fillStyle = '#b39459';
    if (!this.wave.length) { ctx.fillRect(0,height/2,width,1); return; }
    for (let x=0; x<width; x+=3) {
      const a=Math.floor(x/width*this.wave.length), b=Math.max(a+1,Math.ceil((x+3)/width*this.wave.length));
      let peak=0; for(let i=a;i<Math.min(b,this.wave.length);i++) peak=Math.max(peak,this.wave[i]);
      const h=Math.max(1, Math.sqrt(peak)*height*.85); ctx.fillRect(x,(height-h)/2,2,h);
    }
  }
  drawTime(time = this.video.currentTime) {
    const total = this.video.duration || this.project?.meta?.duration || 0;
    if (!this.dragging) $('seek').value = time || 0;
    $('timecode').textContent = `${clock(time)} / ${clock(total)}`;
    $('playhead').style.transform = `translateX(${total ? Math.min(1,time/total)*$('timeline').clientWidth : 0}px)`;
    this.audio.tick();
  }
  drawLoop() {
    $('loop-button').setAttribute('aria-pressed', String(this.loop));
    $('mark-in').textContent = `In ${clock(this.loopIn)}`; $('mark-out').textContent = `Out ${clock(this.loopOut)}`;
    $('loop-region').hidden = !this.loop;
    const duration = this.project?.meta?.duration || 1;
    $('loop-region').style.left = `${100*this.loopIn/duration}%`;
    $('loop-region').style.width = `${100*(this.loopOut-this.loopIn)/duration}%`;
  }
  animate() {
    if (this.frame) return;
    const draw = (now) => {
      this.frame = 0;
      if (document.hidden) return;
      if (this.loop && this.video.currentTime >= this.loopOut && !this.dragging) this.seek(this.loopIn);
      if (!this.dragging) this.drawTime();
      if (!this.lastMeter || now - this.lastMeter > 50) {
        this.lastMeter = now;
        const meter = this.audio.measure();
        $('peak-value').textContent = meter.peak <= -60 ? '−∞ dBFS' : `${meter.peak.toFixed(1)} dBFS`;
        $('peak-meter').style.transform = `scaleX(${Math.min(1, Math.max(0, (meter.peak+60)/60))})`;
        $('peak-meter').classList.toggle('hot', meter.peak > -.5);
        $('reduction-value').textContent = `${meter.reduction.toFixed(1)} dB`;
        const c = this.canvas('spectrum');
        if (c && this.audio.analyser) {
          c.ctx.fillStyle='#d5b573';
          for(let x=0;x<c.width;x+=4) {
            const index=Math.min(511, Math.round(2 ** (x/c.width*9)));
            const h=this.audio.spectrum[index]/255*c.height;
            c.ctx.fillRect(x,c.height-h,2,h);
          }
        }
      }
      if (!this.video.paused) this.frame = requestAnimationFrame(draw);
    };
    this.frame = requestAnimationFrame(draw);
  }
  drawEQ() {
    const c = this.canvas('eq-curve'); if (!c || !this.settings) return;
    const {ctx,width,height} = c, s=this.settings;
    ctx.strokeStyle='#403a2a'; ctx.lineWidth=1;
    for (const y of [.25,.5,.75]) {ctx.beginPath();ctx.moveTo(0,height*y);ctx.lineTo(width,height*y);ctx.stroke();}
    const bands = [['bass',140,.8],['lowmid',400,.8],['mid',s.mid_hz,s.mid_q],['presence',3000,.8],['treble',6500,.8]];
    const coeffs = bands.map(([key,f,q]) => {
      const A=10**(s[key]/40), w=2*Math.PI*f/48000, a=Math.sin(w)/(2*q);
      return [1+a*A,-2*Math.cos(w),1-a*A,1+a/A,-2*Math.cos(w),1-a/A];
    });
    ctx.strokeStyle='#e4c582'; ctx.lineWidth=2;ctx.beginPath();
    for(let x=0;x<=width;x+=2){
      const w=2*Math.PI*(30*(20000/30)**(x/width))/48000;
      let gain=0;
      for(const [b0,b1,b2,a0,a1,a2] of coeffs){
        const n=(b0+b1*Math.cos(w)+b2*Math.cos(2*w))**2+(b1*Math.sin(w)+b2*Math.sin(2*w))**2;
        const d=(a0+a1*Math.cos(w)+a2*Math.cos(2*w))**2+(a1*Math.sin(w)+a2*Math.sin(2*w))**2;
        gain+=10*Math.log10(n/d);
      }
      const y=height/2-Math.max(-18,Math.min(18,gain))*height/40;
      if(x===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
    }
    ctx.stroke();ctx.fillStyle='#9f967e';ctx.font='9px sans-serif';ctx.fillText('30 Hz',4,height-3);ctx.fillText('20 kHz',width-40,height-3);
  }
}
