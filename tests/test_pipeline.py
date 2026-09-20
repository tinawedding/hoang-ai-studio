"""Meaningful end-to-end tests against real FFmpeg output, not mocked processing."""
import json
import os
import tempfile
import time
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="hn-pipeline-")
os.environ["APP_ACCESS_KEY"] = "test-password-only"
os.environ["SESSION_SECRET"] = "test-secret-only-never-deploy"
os.environ["REQUIRE_ACCESS_KEY"] = "true"

import pytest
from fastapi.testclient import TestClient
from app.main import app
from tests.fixtures import make_video, inspect_video, samples, frequency, rms

ARTIFACTS = Path("test-artifacts")
ARTIFACTS.mkdir(exist_ok=True)


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("media") / "unsupported-browser.mkv")


def upload(client, path):
    response = client.post("/api/projects", content=path.read_bytes(),
                           headers={"Content-Type": "application/octet-stream", "X-File-Name": path.name})
    assert response.status_code == 202, response.text
    return response.json()


def wait(client, job_id):
    deadline = time.monotonic() + 90
    previous = 0
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        data = response.json()
        job = data["job"]
        assert 0 <= job["progress"] <= 100
        if job["state"] != "queued":
            assert job["progress"] >= previous
            previous = job["progress"]
        if job["state"] in {"done", "error", "cancelled"}:
            return data
        time.sleep(.1)
    pytest.fail("Real processing did not complete in 90 seconds")


def plain(pitch=0):
    return {"pitch": pitch, "noise": 0, "bass": 0, "mid": 0, "treble": 0,
            "gain": 0, "fade": 0, "highpass": False, "compress": False, "normalize": False}


def test_real_upload_pitch_export_download(client, video):
    uploaded = upload(client, video)
    data = wait(client, uploaded["job"]["id"])
    assert data["job"]["state"] == "done", data
    project_id = data["project"]["id"]
    assert data["project"]["meta"]["video_codec"] == "ffv1"
    preview = client.get(f"/api/projects/{project_id}/media/preview")
    assert preview.status_code == 200
    (ARTIFACTS / "preview.mp4").write_bytes(preview.content)
    preview_meta = inspect_video(ARTIFACTS / "preview.mp4")
    assert preview_meta["video"] == "h264" and preview_meta["audio"] == "aac"
    partial = client.get(f"/api/projects/{project_id}/media/preview", headers={"Range": "bytes=0-255"})
    assert partial.status_code == 206 and len(partial.content) == 256
    assert partial.headers["cache-control"] == "no-store"
    waveform = client.get(f"/api/projects/{project_id}/waveform").json()
    assert abs(waveform["duration"] - 4) < .1
    assert 500 <= len(waveform["peaks"]) <= 1200
    assert all(0 <= value <= 1 for value in waveform["peaks"])
    assert max(waveform["peaks"]) > .05
    rendered = client.post(f"/api/projects/{project_id}/render", json=plain(pitch=12))
    assert rendered.status_code == 202, rendered.text
    data = wait(client, rendered.json()["job"]["id"])
    assert data["job"]["state"] == "done", data
    download = client.get(f"/api/projects/{project_id}/media/output?download=true")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    result = ARTIFACTS / "pitch-plus-12.mp4"
    result.write_bytes(download.content)
    meta = inspect_video(result)
    assert meta["video"] == "h264" and meta["audio"] == "aac"
    assert (meta["width"], meta["height"]) == (320, 180)
    assert abs(meta["duration"] - 4) < .15
    assert abs(meta["audio_start"] - meta["video_start"]) < .05
    hz = frequency(samples(result))
    assert 865 < hz < 895, hz
    assert result.read_bytes().find(b"moov") < result.read_bytes().find(b"mdat"), "MP4 must support fast start"
    (ARTIFACTS / "verification.json").write_text(json.dumps({
        "input": "FFV1/PCM MKV, 440 Hz, 4 sec", "output": meta,
        "output_frequency_hz": hz, "expected_frequency_hz": 880,
        "range_response": partial.status_code, "result": "passed",
    }, indent=2), encoding="utf-8")
    assert client.delete(f"/api/projects/{project_id}").status_code == 200
    assert client.get(f"/api/projects/{project_id}/media/output").status_code == 404


def test_negative_pitch_and_full_effect_chain(client, video):
    project = wait(client, upload(client, video)["job"]["id"])["project"]
    pid = project["id"]
    effect = plain(-12)
    effect.update(noise=10, bass=2, mid=1, treble=-1, gain=-1, fade=.2,
                  highpass=True, compress=True, normalize=True)
    response = client.post(f"/api/projects/{pid}/render", json=effect)
    data = wait(client, response.json()["job"]["id"])
    assert data["job"]["state"] == "done", data
    result = ARTIFACTS / "pitch-minus-12-effects.mp4"
    result.write_bytes(client.get(f"/api/projects/{pid}/media/output").content)
    assert 210 < frequency(samples(result)) < 230
    assert abs(inspect_video(result)["duration"] - 4) < .15
    client.delete(f"/api/projects/{pid}")


def test_preserve_audio_delay(client, tmp_path):
    original = make_video(tmp_path / "delayed.mp4", seconds=5, delay=.5)
    original_meta = inspect_video(original)
    assert original_meta["audio_start"] - original_meta["video_start"] > .4
    project = wait(client, upload(client, original)["job"]["id"])["project"]
    pid = project["id"]
    response = client.post(f"/api/projects/{pid}/render", json=plain())
    data = wait(client, response.json()["job"]["id"])
    assert data["job"]["state"] == "done", data
    result = ARTIFACTS / "preserved-delay.mp4"
    result.write_bytes(client.get(f"/api/projects/{pid}/media/output").content)
    sound = samples(result)
    assert rms(sound, .05, .3) < .002
    assert rms(sound, 1, 2) > .02
    assert abs(inspect_video(result)["duration"] - 5) < .15
    client.delete(f"/api/projects/{pid}")


def test_corrupt_file_and_silent_video(client, tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not-a-real-video" * 50)
    data = wait(client, upload(client, broken)["job"]["id"])
    assert data["job"]["state"] == "error"
    client.delete(f"/api/projects/{data['project']['id']}")
    silent = make_video(tmp_path / "silent.mp4", audio=False)
    data = wait(client, upload(client, silent)["job"]["id"])
    assert data["job"]["state"] == "done", data
    pid = data["project"]["id"]
    assert data["project"]["meta"]["has_audio"] is False
    assert client.post(f"/api/projects/{pid}/render", json=plain()).status_code == 422
    assert client.get(f"/api/projects/{pid}/media/preview").status_code == 200
    client.delete(f"/api/projects/{pid}")


def test_access_validation_and_cancellation(client, video):
    data = upload(client, video)
    pid, job_id = data["project"]["id"], data["job"]["id"]
    cookie = client.cookies.get("hn_voice_session")
    client.cookies.clear()
    assert client.get(f"/api/projects/{pid}").status_code == 401
    assert client.post("/api/session", json={"password": "incorrect"}).status_code == 401
    assert client.post("/api/session", json={"password": "test-password-only"}).status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.get(f"/api/projects/{pid}/media/preview").status_code == 404
    assert client.get(f"/api/projects/{pid}/waveform").status_code == 404
    client.cookies.clear()
    client.cookies.set("hn_voice_session", cookie)
    data = wait(client, job_id)
    assert data["job"]["state"] == "done"
    assert client.post(f"/api/projects/{pid}/render", json={"pitch": 999}).status_code == 422
    assert client.post(f"/api/projects/{pid}/render", json={"pitch": "0; echo x"}).status_code == 422
    assert client.post(f"/api/projects/{pid}/render", json={"unknown": 1}).status_code == 422
    for invalid in [{"mid_q": 0}, {"gate_threshold": -99}, {"ceiling": 1}, {"room": 3}, {"deesser": True}, {"target_lufs": -5}]:
        assert client.post(f"/api/projects/{pid}/render", json=invalid).status_code == 422
    assert client.post(f"/api/projects/{pid}/render", json=plain(),
                       headers={"Origin": "https://elsewhere.invalid"}).status_code == 403
    assert client.post("/api/projects", content=b"x", headers={
        "Content-Length": str(251 * 1024 * 1024), "X-File-Name": "large.mp4",
    }).status_code == 413
    response = client.post(f"/api/projects/{pid}/render", json=plain())
    next_id = response.json()["job"]["id"]
    assert client.post(f"/api/jobs/{next_id}/cancel").status_code == 200
    done = wait(client, next_id)
    assert done["job"]["state"] == "cancelled"
    assert client.get(f"/api/projects/{pid}/media/output").status_code == 404
    client.delete(f"/api/projects/{pid}")


def test_advanced_voice_chain_and_gate_are_audible(client, video, tmp_path):
    """Decode the new complete chain; verify Gate suppresses sound, not just a UI value."""
    pid = wait(client, upload(client, video)["job"]["id"])["project"]["id"]
    config = client.get("/api/config").json()
    assert len(config["controls"]) == 39
    effect = {**config["defaults"], "pitch": 3, "deesser": 40, "lowpass_hz": 14000,
              "lowmid": -2, "mid": 2, "mid_hz": 1600, "mid_q": 1.2, "presence": 1,
              "threshold": -24, "ratio": 4, "attack": 8, "release": 250, "makeup": 2,
              "gate": True, "gate_threshold": -50, "gate_release": 120,
              "reverb": 30, "room": 1.8, "ceiling": -3, "target_lufs": -18, "fade": .2}
    rendered = client.post(f"/api/projects/{pid}/render", json=effect)
    data = wait(client, rendered.json()["job"]["id"])
    assert data["job"]["state"] == "done", data
    path = ARTIFACTS / "advanced-studio.mp4"
    path.write_bytes(client.get(f"/api/projects/{pid}/media/output").content)
    sound = samples(path)
    meta = inspect_video(path)
    assert meta["video"] == "h264" and meta["audio"] == "aac"
    assert abs(meta["duration"] - 4) < .15
    assert 510 < frequency(sound) < 535
    assert .015 < rms(sound, 1, 3) < .5
    assert max(abs(x) for x in sound) / 32768 < .78  # -3 dB ceiling with AAC tolerance
    client.delete(f"/api/projects/{pid}")
    quiet_input = make_video(tmp_path / "quiet.mkv", volume_db=-24)
    assert rms(samples(quiet_input), 1, 3) > .004
    pid = wait(client, upload(client, quiet_input)["job"]["id"])["project"]["id"]
    quiet = {**plain(), "gate": True, "gate_threshold": -20, "gate_release": 50}
    response = client.post(f"/api/projects/{pid}/render", json=quiet)
    assert wait(client, response.json()["job"]["id"])["job"]["state"] == "done"
    path = ARTIFACTS / "gate-suppression.mp4"
    path.write_bytes(client.get(f"/api/projects/{pid}/media/output").content)
    assert rms(samples(path), 1, 3) < .0004
    client.delete(f"/api/projects/{pid}")
