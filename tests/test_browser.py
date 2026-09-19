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
        expect(page.locator("#playback-note")).to_have_text("Âm thanh gốc")
        page.locator("#compare-output").click()
        expect(page.locator("#playback-note")).to_have_text("Âm thanh đã xử lý")
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
