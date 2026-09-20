const $=id=>document.getElementById(id);
const clock=v=>`${Math.floor(v/60)}:${String(Math.floor(v%60)).padStart(2,'0')}`;
const STORE='hn-plus-presets-v1';
export class PlusStudio {
  constructor({getSettings,apply,onError,studio}){
    Object.assign(this,{getSettings,apply,onError,studio});this.presets=[];this.offset=0;this.selected=1;
    try{const raw=JSON.parse(localStorage.getItem(STORE)||'[]');if(Array.isArray(raw))this.presets=raw.slice(0,40);}catch{}
    $('save-preset').onclick=()=>{$('preset-name').value='';$('preset-dialog').showModal();};
    $('cancel-preset').onclick=()=>$('preset-dialog').close();
    $('preset-form').onsubmit=e=>{e.preventDefault();try{
      const name=$('preset-name').value.trim();if(!name)return;
      if(this.presets.length>=40)throw Error('Tối đa 40 công thức. Xóa một công thức trước.');
      this.presets.push({name,settings:this.getSettings()});this.save();$('preset-dialog').close();
    }catch(error){this.onError(error);}};
    $('load-preset').onclick=()=>{const p=this.presets[Number($('saved-presets').value)];if(p&&$('saved-presets').value!=='')this.apply(this.valid(p.settings));};
    $('delete-preset').onclick=()=>{if($('saved-presets').value==='')return;this.presets.splice(Number($('saved-presets').value),1);this.save();};
    $('export-preset').onclick=()=>{
      const data={format:'hn-voice-preset',version:1,name:'HN PLUS',settings:this.getSettings()};
      const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
      const a=document.createElement('a');a.href=url;a.download='HN-PLUS-cong-thuc-giong.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
    $('import-preset').onclick=()=>$('preset-file').click();
    $('preset-file').onchange=async e=>{try{
      const file=e.target.files[0];if(!file)return;if(file.size>20000)throw Error('File công thức quá lớn.');
      const data=JSON.parse(await file.text());if(data.format!=='hn-voice-preset')throw Error('Không phải công thức HN Voice.');
      this.apply(this.valid(data.settings));
    }catch(error){this.onError(error);}finally{e.target.value='';}};
    $('apply-analysis').onclick=()=>{if(this.analysis)this.apply({...this.getSettings(),...this.analysis.suggested});};
    $('close-hq').onclick=()=>$('hq-dialog').close();
    $('hq-dialog').addEventListener('close',()=>this.stopHQ(true));
    $('hq-play').onclick=()=>{if(this.playing)this.stopHQ();else this.playHQ().catch(this.onError);};
    $('hq-a').onclick=()=>this.selectHQ(0);$('hq-b').onclick=()=>this.selectHQ(1);
    $('hq-seek').oninput=()=>{const was=this.playing;this.stopHQ();this.offset=Number($('hq-seek').value);if(was)this.playHQ().catch(this.onError);else $('hq-time').textContent=clock(this.offset);};
  }
  configure(config){this.config=config;this.presets=this.presets.filter(p=>p&&typeof p.name==='string'&&p.settings);this.drawPresets();}
  valid(value){
    if(!value||typeof value!=='object'||Array.isArray(value))throw Error('Thông số công thức không hợp lệ.');
    const result={...this.config.defaults};
    for(const [key,def] of Object.entries(result)){
      if(!(key in value))continue;const v=value[key];
      if(typeof def==='boolean'){if(typeof v!=='boolean')throw Error('Công thức có giá trị không hợp lệ.');result[key]=v;}
      else{const c=this.config.controls.find(c=>c.id===key);if(typeof v!=='number'||!Number.isFinite(v)||v<c.min||v>c.max)throw Error('Thông số nằm ngoài giới hạn: '+key);result[key]=v;}
    }return result;
  }
  save(){try{localStorage.setItem(STORE,JSON.stringify(this.presets));this.drawPresets();}catch{this.onError(Error('Trình duyệt chưa cho lưu công thức. Dùng Xuất JSON để giữ lại.'));}}
  drawPresets(){const select=$('saved-presets');select.replaceChildren(new Option('Chọn công thức đã lưu',''));this.presets.forEach((p,i)=>select.add(new Option(p.name.slice(0,60),String(i))));}
  showAnalysis(a){
    this.analysis=a;$('analysis-result').hidden=!a;if(!a)return;
    $('analysis-metrics').textContent=`Mức trung bình ${a.rms_db} dB · Đỉnh ${a.peak_db} dB · Chênh ${a.dynamic_db} dB`;
    $('analysis-advice').textContent=(a.clip_percent>.05?'Có dấu hiệu vỡ ở bản gốc. Giảm âm lượng không phục hồi được chi tiết đã mất. ':a.dynamic_db>8?'Giọng chênh âm lượng; Auto Level có thể giúp câu nói đều hơn. ':'Mức giọng khá đều; dùng xử lý nhẹ để giữ sắc thái. ')+'Đây là gợi ý từ số đo âm thanh; bạn vẫn nên nghe A/B.';
  }
  async openHQ(project,settings){
    if(!project?.audition)return;
    this.studio.video.pause();this.stopHQ(true);this.buffers=null;this.offset=0;
    const version=(this.hqVersion||0)+1;this.hqVersion=version;
    const a=project.audition, dirty=Object.keys(settings).some(k=>settings[k]!==a.settings[k]);
    $('hq-details').textContent=`${clock(a.start)} → ${clock(a.start+a.duration)} · ${dirty?'Bạn đã chỉnh tiếp. Đây là HQ của thông số trước.':'Cùng bộ xử lý với MP4 xuất.'}`;
    $('hq-message').textContent='Đang nạp đoạn âm thanh…';$('hq-play').disabled=true;$('hq-dialog').showModal();
    this.hqContext ||= new AudioContext({latencyHint:'interactive'});
    try{
      const buffers=await Promise.all(['hq-a','hq-b'].map(async kind=>{
        const response=await fetch(`/api/projects/${project.id}/media/${kind}?v=${a.job_id}`,{credentials:'same-origin'});
        if(!response.ok)throw Error('Đoạn nghe HQ đã hết hạn. Bấm tạo lại HQ.');
        return this.hqContext.decodeAudioData(await response.arrayBuffer());
      }));
      if(version!==this.hqVersion||!$('hq-dialog').open)return;
      this.buffers=buffers;this.length=Math.min(...buffers.map(b=>b.duration));$('hq-seek').max=this.length;
      $('hq-seek').value=0;$('hq-time').textContent='0:00';$('hq-play').disabled=false;
      $('hq-message').textContent='Bấm phát rồi chuyển A / B để nghe khác biệt.';this.selectHQ(1);
    }catch(error){$('hq-message').textContent=error.message;}
  }
  async playHQ(){
    if(!this.buffers)return;await this.hqContext.resume();if(!$('hq-dialog').open)return;
    if(this.offset>=this.length-.01)this.offset=0;
    this.started=this.hqContext.currentTime+.025;this.base=this.offset;this.playing=true;
    this.sources=this.buffers.map((buffer,i)=>{
      const node=this.hqContext.createBufferSource(), gain=this.hqContext.createGain();node.buffer=buffer;gain.gain.value=i===this.selected?1:0;
      node.connect(gain).connect(this.hqContext.destination);node.start(this.started,this.offset);return {node,gain};
    });
    $('hq-play').textContent='Ⅱ';
    const draw=()=>{if(!this.playing)return;this.offset=Math.max(0,this.base+this.hqContext.currentTime-this.started);$('hq-seek').value=this.offset;$('hq-time').textContent=clock(this.offset);if(this.offset>=this.length){this.stopHQ(true);return;}this.hqFrame=requestAnimationFrame(draw);};draw();
  }
  selectHQ(index){this.selected=index;$('hq-a').classList.toggle('selected',index===0);$('hq-b').classList.toggle('selected',index===1);this.sources?.forEach(({gain},i)=>gain.gain.setTargetAtTime(i===index?1:0,this.hqContext.currentTime,.01));}
  stopHQ(reset=false){this.playing=false;cancelAnimationFrame(this.hqFrame);this.sources?.forEach(({node,gain})=>{try{node.stop();}catch{}node.disconnect();gain.disconnect();});this.sources=null;if(reset)this.offset=0;$('hq-play').textContent='▶';}
}
