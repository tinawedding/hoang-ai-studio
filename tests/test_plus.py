"""Measure real signals and prove HQ/export reuse the same finished audio."""
import json
import subprocess
import wave
from pathlib import Path

import numpy as np

from app import media
from app.main import PROJECTS
from app.settings import DEFAULTS
from tests.test_pipeline import video, upload, wait, plain, ARTIFACTS
from tests.fixtures import samples, rms, inspect_video, make_video


def process(tmp_path, signal, settings, name):
    original=tmp_path/f"{name}-in.wav"
    with wave.open(str(original),"wb") as w:
        w.setparams((1,2,48000,0,"NONE","not compressed"))
        w.writeframes((np.clip(signal,-.99,.99)*32767).astype('<i2').tobytes())
    result=tmp_path/f"{name}-out.wav"
    s={**DEFAULTS,**plain(),**settings}
    subprocess.run([media.FFMPEG,'-v','error','-y','-i',str(original),'-af',media.filter_audio(s,len(signal)/48000),str(result)],check=True,capture_output=True)
    return np.array(samples(result))/32768


def test_auto_level_reduces_phrase_difference(tmp_path):
    t=np.arange(12*48000)/48000
    x=np.sin(2*np.pi*220*t)*np.where(t<6,.025,.2)
    y=process(tmp_path,x,{"auto_level":100},'level')
    def difference(a):
        return 20*np.log10(np.sqrt(np.mean(a[7*48000:10*48000]**2))/np.sqrt(np.mean(a[2*48000:5*48000]**2)))
    before,after=difference(x),difference(y)
    assert after<before-4,(before,after)


def test_dynamic_eq_only_cuts_loud_problem_band(tmp_path):
    t=np.arange(5*48000)/48000
    def run(amplitude):
        x=amplitude*np.sin(2*np.pi*3200*t)
        y=process(tmp_path,x,{"harshness":100,"harsh_threshold":-26},str(amplitude))
        return 20*np.log10(np.sqrt(np.mean(y[48000:-48000]**2))/np.sqrt(np.mean(x[48000:-48000]**2)))
    loud,quiet=run(.3),run(.005)
    assert loud < -3,(loud,quiet)
    assert abs(quiet)<1,(loud,quiet)


def test_independent_formant_moves_envelope_without_moving_harmonics(tmp_path):
    t=np.arange(4*48000)/48000
    x=sum(np.sin(2*np.pi*120*k*t)*np.exp(-((120*k-900)/230)**2) for k in range(1,35))*.035
    a=process(tmp_path,x,{"formant":0},'vowel-a')
    b=process(tmp_path,x,{"formant":5},'vowel-b')
    def envelope(signal):
        segment=signal[48000:3*48000]
        spectrum=np.abs(np.fft.rfft(segment*np.hanning(len(segment))))**2
        freq=np.fft.rfftfreq(len(segment),1/48000)
        mask=(freq>400)&(freq<2000)
        centroid=float(np.sum(freq[mask]*spectrum[mask])/np.sum(spectrum[mask]))
        peaks=freq[np.argsort(spectrum)[-8:]]
        assert all(abs(hz/120-round(hz/120))<.02 for hz in peaks),peaks
        return centroid
    low,high=envelope(a),envelope(b)
    assert high-low>50,(low,high)


def test_plus_hq_analysis_cache_and_real_export(client, tmp_path):
    video=make_video(tmp_path/'plus-timing.mkv',seconds=6)
    p=wait(client,upload(client,video)['job']['id'])['project'];pid=p['id']
    analyzed=wait(client,client.post(f'/api/projects/{pid}/analyze').json()['job']['id'])
    assert analyzed['job']['state']=='done',analyzed
    assert -30<analyzed['project']['analysis']['rms_db']<-15
    effect={**DEFAULTS,"pitch":-2,"formant":-.5,"ai_noise":25,"noise":0,"declick":15,
            "plosive":25,"auto_level":35,"harshness":30,"warmth":25,
            "reverb":12,"decay":.8}
    response=client.post(f'/api/projects/{pid}/audition',json={"settings":effect,"start":1})
    assert response.status_code==202,response.text
    data=wait(client,response.json()['job']['id']);assert data['job']['state']=='done',data
    folder=Path(PROJECTS[pid]['folder']);cache=(folder/'master.flac').stat().st_mtime_ns
    ai_cache=(folder/'rnnoise.flac').stat().st_mtime_ns
    assert len(samples(folder/'master.flac'))==6*48000,'Master must retain every sample of the video duration'
    levels=[]
    for kind in ('hq-a','hq-b'):
        result=client.get(f'/api/projects/{pid}/media/{kind}');assert result.status_code==200
        path=ARTIFACTS/f'{kind}.wav';path.write_bytes(result.content)
        values=samples(path);levels.append(rms(values,0,5))
        assert len(values)==5*48000,'Non-zero HQ start must produce the exact requested interval'
        assert client.get(f'/api/projects/{pid}/media/{kind}',headers={'Range':'bytes=0-255'}).status_code==206
    assert abs(20*np.log10(levels[0]/levels[1]))<.15,levels
    data=wait(client,client.post(f'/api/projects/{pid}/render',json=effect).json()['job']['id'])
    assert data['job']['state']=='done',data
    assert (folder/'master.flac').stat().st_mtime_ns==cache,'HQ and export must reuse identical master'
    path=ARTIFACTS/'plus-complete-chain.mp4';path.write_bytes(client.get(f'/api/projects/{pid}/media/output').content)
    info=inspect_video(path);assert info['video']=='h264' and info['audio']=='aac'
    assert abs(info['duration']-6)<.1
    # A final AAC frame may add up to 1023 samples of padding; no missing tail
    # is allowed. The container duration must also describe the complete track.
    assert 6*48000 <= len(samples(path)) < 6*48000+1024
    import av
    with av.open(str(path)) as container:
        audio=container.streams.audio[0]
        assert abs(float(audio.duration*audio.time_base)-6)<.03
    assert abs(info['video_start']-info['audio_start'])<.05
    # AI weights are not rerun when a downstream control changes.
    effect['warmth']=40
    data=wait(client,client.post(f'/api/projects/{pid}/audition',json={'settings':effect,'start':1}).json()['job']['id'])
    assert data['job']['state']=='done',data
    assert (folder/'rnnoise.flac').stat().st_mtime_ns==ai_cache
    for payload in ({'settings':{},'start':-1},{'settings':{},'start':999},{'settings':[],'start':0}):
        assert client.post(f'/api/projects/{pid}/audition',json=payload).status_code==422
    original_cookie=client.cookies.get('hn_voice_session');client.cookies.clear()
    assert client.get(f'/api/projects/{pid}/media/hq-b').status_code==401
    client.post('/api/session',json={'password':'test-password-only'})
    assert client.get(f'/api/projects/{pid}/media/hq-b').status_code==404
    assert client.post(f'/api/projects/{pid}/analyze').status_code==404
    client.cookies.clear();client.cookies.set('hn_voice_session',original_cookie)
    client.delete(f'/api/projects/{pid}')


def test_rnnoise_model_removes_stationary_noise(tmp_path):
    from app.mastering import MODEL
    rng=np.random.default_rng(42);x=rng.normal(0,.025,48000*4)
    source=tmp_path/'noise.wav';dest=tmp_path/'clean.wav'
    with wave.open(str(source),'wb') as w:
        w.setparams((1,2,48000,0,'NONE','not compressed'));w.writeframes((x*32767).astype('<i2').tobytes())
    subprocess.run([media.FFMPEG,'-v','error','-y','-i',str(source),'-af',f'arnndn=m={MODEL}',str(dest)],check=True,capture_output=True)
    reduction=20*np.log10(rms(samples(dest),1,3)/rms(samples(source),1,3))
    assert reduction < -6,reduction
    (ARTIFACTS/'rnnoise-measurement.json').write_text(json.dumps({'stationary_noise_reduction_db':float(reduction),'note':'Synthetic noise measurement; not an intelligibility or Vietnamese speech quality score.'}))
