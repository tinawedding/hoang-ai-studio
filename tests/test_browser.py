import json
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright, expect

from tests.fixtures import make_video, inspect_video

ARTIFACTS = Path("test-artifacts")
ARTIFACTS.mkdir(exist_ok=True)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    folder = tmp_path_factory.mktemp("browser-server")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = dict(os.environ, DATA_DIR=str(folder / "data"), APP_ACCESS_KEY="",
               REQUIRE_ACCESS_KEY="false", SESSION_SECRET="browser-test-secret",
               PUBLIC_ORIGIN=f"http://127.0.0.1:{port}")
    with (ARTIFACTS / "browser-server.log").open("w") as log:
        process = subprocess.Popen(["python", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                                   env=env, stdout=log, stderr=log)
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail("Web server exited")
                try:
                    if httpx.get(url + "/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                pytest.fail("Web server did not become healthy")
            yield url, folder
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_browser_upload_render_download_and_layout(server):
    url, folder = server
    video = make_video(folder / "browser-video.mkv", seconds=4)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1100}, accept_downloads=True)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        expect(page.locator("#server-state")).to_have_text("Máy chủ sẵn sàng")
        expect(page.locator("#choose-file")).to_be_enabled()
        page.screenshot(path=str(ARTIFACTS / "studio-desktop.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(ARTIFACTS / "studio-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile overflow"
        page.set_viewport_size({"width": 1440, "height": 1100})
        page.locator("#file-input").set_input_files(str(video))
        expect(page.locator("#render-button")).to_be_enabled(timeout=90000)
        expect(page.locator("#file-name")).to_have_text("browser-video.mkv")
        assert page.locator("#video").evaluate("(v) => Number.isFinite(v.duration) && v.duration > 3.5")
        page.locator('[data-preset="deep"]').click()
        expect(page.locator("#pitch-value")).to_have_text("-3 st")
        page.locator("#render-button").click()
        expect(page.locator("#download-button")).to_have_attribute("aria-disabled", "false", timeout=90000)
        expect(page.locator("#compare-output")).to_be_enabled()
        expect(page.locator("#notice")).to_contain_text("MP4")
        page.screenshot(path=str(ARTIFACTS / "studio-result.png"), full_page=True)
        page.locator("#compare-original").click()
        page.locator("#compare-output").click()
        expect(page.locator("#output-dialog")).to_be_visible()
        expect(page.locator("#output-settings-note")).to_contain_text("Bản hoàn thiện")
        page.locator("#close-output").click()
        with page.expect_download(timeout=30000) as download:
            page.locator("#download-button").click()
        path = ARTIFACTS / "browser-download.mp4"
        download.value.save_as(path)
        info = inspect_video(path)
        assert info["video"] == "h264" and info["audio"] == "aac"
        assert abs(info["duration"] - 4) < .15
        page.locator("#pitch").fill("1")
        page.locator("#pitch").dispatch_event("input")
        expect(page.locator("#download-button")).to_have_attribute("aria-disabled", "true")
        assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
        assert not errors, errors
        browser.close()


def test_live_pitch_continuous_controls_and_mobile_preview(server):
    url, folder = server
    video = make_video(folder / "live-preview.mkv", seconds=40)
    errors, render_requests = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        # Test-only instrumentation: capture actual worklet output and metrics.
        context.add_init_script("""(() => {
          window.__worklets = []; window.__contexts = [];
          const AC = window.AudioContext, AWN = window.AudioWorkletNode;
          window.AudioContext = class extends AC {
            constructor(...args) { super(...args); window.__contexts.push(this); }
          };
          window.AudioWorkletNode = class extends AWN {
            constructor(...args) { super(...args); window.__worklets.push({node:this,name:args[1]}); }
          };
        })();""")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda req: render_requests.append(req.url)
                if req.method == "POST" and req.url.endswith("/render") else None)
        page.goto(url)
        expect(page.locator("#choose-file")).to_be_enabled()
        page.locator("#file-input").set_input_files(str(video))
        expect(page.locator("#render-button")).to_be_enabled(timeout=90000)
        page.locator("#play-button").click()
        expect(page.locator("#live-state")).to_have_attribute("data-state", "live", timeout=15000)
        page.wait_for_function("document.querySelector('#video').currentTime > 1")
        # Measure audible pitch from the real worklet graph, not a displayed value.
        page.locator("#pitch").fill("12")
        page.locator("#pitch").dispatch_event("input")
        page.locator("#pitch").dispatch_event("change")
        page.evaluate("""() => {
          const ctx = window.__contexts[0];
          const item = window.__worklets.find(x => x.name.includes('soundtouch'));
          if (!item) throw Error('Pitch worklet missing');
          window.__pitchNode = item.node;
          window.__testAnalyser = ctx.createAnalyser(); window.__testAnalyser.fftSize = 8192;
          item.node.connect(window.__testAnalyser);
          window.__pitchFrequency = () => {
            const data = new Float32Array(window.__testAnalyser.frequencyBinCount);
            window.__testAnalyser.getFloatFrequencyData(data);
            let peak = 0; for (let i=1; i<data.length; i++) if (data[i] > data[peak]) peak=i;
            return peak * ctx.sampleRate / 8192;
          };
        }""")
        page.wait_for_function("Math.abs(window.__pitchFrequency() - 880) < 20", timeout=10000)
        pitch_hz = page.evaluate("window.__pitchFrequency()")
        page.locator("#pitch").fill("0")
        page.locator("#pitch").dispatch_event("input")
        page.locator("#pitch").dispatch_event("change")
        page.wait_for_function("Math.abs(window.__pitchFrequency() - 440) < 15", timeout=10000)
        page.locator('[data-group="eq"]').click()
        measurement = page.evaluate("""async () => {
          const video = document.querySelector('#video'), events = {loadstart:0,emptied:0,waiting:0};
          for (const key of Object.keys(events)) video.addEventListener(key, () => events[key]++);
          const source = video.currentSrc, startTime = video.currentTime;
          const gaps = [], costs = []; let previous = 0;
          const before = window.__pitchNode.metrics;
          for (let i=0; i<180; i++) {
            const now = await new Promise(requestAnimationFrame);
            if (previous) gaps.push(now-previous); previous=now;
            const start = performance.now();
            for (const [id, value] of [['pitch', Math.sin(i/18)*6], ['mid',Math.sin(i/12)*9], ['reverb',i%40]]) {
              const input = document.getElementById(id); input.value=value; input.dispatchEvent(new Event('input'));
            }
            costs.push(performance.now()-start);
          }
          for (const id of ['pitch','mid','reverb']) document.getElementById(id).dispatchEvent(new Event('change'));
          gaps.sort((a,b)=>a-b); costs.sort((a,b)=>a-b);
          return {events, sameSource:source===video.currentSrc, advanced:video.currentTime-startTime,
            paused:video.paused, frames:gaps.length, frameP95:gaps[Math.floor(gaps.length*.95)],
            frameMax:gaps.at(-1), eventP95:costs[Math.floor(costs.length*.95)],
            beforeMetrics:before, afterMetrics:window.__pitchNode.metrics,
            peak:document.querySelector('#peak-value').textContent};
        }""")
        assert measurement["events"]["loadstart"] == 0 and measurement["events"]["emptied"] == 0
        assert measurement["sameSource"] and not measurement["paused"]
        assert measurement["advanced"] > 2, measurement
        assert measurement["frameP95"] < 55, measurement
        assert measurement["eventP95"] < 12, measurement
        assert "∞" not in measurement["peak"], measurement
        assert not render_requests, "Live slider changes must never render on the server"
        source = page.locator("#video").get_attribute("src")
        page.locator("#compare-original").click()
        expect(page.locator("#live-state")).to_have_attribute("data-state", "original")
        page.locator("#compare-live").click()
        expect(page.locator("#live-state")).to_have_attribute("data-state", "live")
        assert page.locator("#video").get_attribute("src") == source
        assert not page.locator("#video").evaluate("v => v.paused")
        page.screenshot(path=str(ARTIFACTS / "studio-live-eq.png"), full_page=True)
        page.locator('[data-group="dynamics"]').click()
        page.screenshot(path=str(ARTIFACTS / "studio-live-dynamics.png"), full_page=True)
        page.locator('[data-group="space"]').click()
        page.locator("#reverb-number").fill("25")
        page.locator("#reverb-number").press("Tab")
        expect(page.locator("#reverb")).to_have_value("25")
        page.locator("#undo-settings").click()
        assert page.locator("#reverb").input_value() != "25"
        page.locator("#redo-settings").click()
        expect(page.locator("#reverb")).to_have_value("25")
        page.locator("#reverb").dblclick()
        expect(page.locator("#reverb")).to_have_value("0")
        page.locator("#play-button").click()
        page.locator("#seek").fill("7.25")
        page.locator("#seek").dispatch_event("input")
        page.locator("#seek").dispatch_event("change")
        page.wait_for_function("Math.abs(document.querySelector('#video').currentTime - 7.25) < .05")
        page.locator("#mark-in").click()
        page.locator("#seek").fill("9.25")
        page.locator("#seek").dispatch_event("input")
        page.locator("#seek").dispatch_event("change")
        page.locator("#mark-out").click()
        page.locator("#loop-button").click()
        expect(page.locator("#loop-button")).to_have_attribute("aria-pressed", "true")
        page.locator("#play-button").click()
        page.wait_for_function("document.querySelector('#video').currentTime < 9")
        page.locator("#loop-button").click()
        # Mobile preview stays visible while the settings panel is scrolled.
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator('[data-group="voice"]').click()
        page.locator("#noise").scroll_into_view_if_needed()
        bounds = page.locator("#video").bounding_box()
        assert bounds and bounds["y"] >= -1 and bounds["y"] + bounds["height"] < 550, bounds
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile overflow"
        page.screenshot(path=str(ARTIFACTS / "studio-live-mobile.png"), full_page=True)
        page.screenshot(path=str(ARTIFACTS / "studio-live-mobile-viewport.png"))
        box = page.locator("#noise").bounding_box()
        page.mouse.move(box["x"] + 10, box["y"] + box["height"]/2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] - 5, box["y"] + box["height"]/2, steps=25)
        page.mouse.up()
        expect(page.locator("#noise-value")).not_to_have_text("8 dB")
        assert page.locator("#video").get_attribute("src") == source
        assert not errors, errors
        (ARTIFACTS / "live-performance.json").write_text(json.dumps({
            "browser": "Chromium headless / GitHub Actions; not a guarantee for every device",
            "pitch_plus_12_hz": pitch_hz, "slider_stress": measurement,
            "render_requests_while_editing": len(render_requests), "javascript_errors": errors,
        }, indent=2), encoding="utf-8")
        browser.close()
