"use strict";
import { buildControls, StudioEditor } from "./studio-editor.js";
import { PlusStudio } from "./plus-studio.js";
const $ = (id) => document.getElementById(id);
let numeric = [];
const toggles = ["highpass", "compress", "normalize", "gate", "preserve_formants"];
let config, current = null, currentJob = null, uploading = null, watchVersion = 0, projects = [];
let busy = false, online = false;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const numberFormat = new Intl.NumberFormat("vi-VN", {maximumFractionDigits: 1});
let units = {}, paintFrame = 0, settingsHistory = [], historyIndex = -1;
function duration(seconds) {
  const n = Math.max(0, Math.floor(seconds || 0));
  return Math.floor(n / 60) + ":" + String(n % 60).padStart(2, "0");
}
function size(bytes) { return numberFormat.format((bytes || 0) / 1024 / 1024) + " MB"; }
function notice(message = "", kind = "") {
  $("notice").textContent = message;
  $("notice").className = "notice " + kind;
  $("notice").hidden = !message;
}
function settings() {
  return Object.fromEntries([
    ...numeric.map((key) => [key, Number($(key).value)]),
    ...toggles.map((key) => [key, $(key).checked]),
  ]);
}
function sameSettings(a, b) {
  return [...numeric, ...toggles].every((key) => a?.[key] === b?.[key]);
}
function dirty() { return !!current?.output && !sameSettings(settings(), current.output.settings); }
function paintValues() {
  numeric.forEach((key) => {
    const value = Number($(key).value);
    const label = numberFormat.format(value) + " " + units[key];
    if ($(key + "-value").textContent !== label) $(key + "-value").textContent = label;
    if (document.activeElement !== $(key + "-number")) $(key + "-number").value = value;
  });
  studio.update(settings());
  paintActions();
}
function applySettings(value, remember = true) {
  value = {...config.defaults, ...value};
  numeric.forEach((key) => { $(key).value = value[key]; });
  toggles.forEach((key) => { $(key).checked = value[key]; });
  paintValues();
  if (remember) rememberSettings();
}
function rememberSettings() {
  const value = settings();
  if (sameSettings(value, settingsHistory[historyIndex])) return;
  settingsHistory = settingsHistory.slice(0, historyIndex + 1);
  settingsHistory.push(value);
  if (settingsHistory.length > 60) settingsHistory.shift();
  historyIndex = settingsHistory.length - 1;
  paintActions();
}
function undoSettings(direction) {
  const index = historyIndex + direction;
  if (index < 0 || index >= settingsHistory.length) return;
  historyIndex = index; clearPreset(); applySettings(settingsHistory[index], false);
}
function clearPreset() {
  document.querySelectorAll(".preset").forEach((button) => button.classList.remove("selected"));
}
function paintActions() {
  const ready = current && ["ready", "done"].includes(current.state) && current.meta?.has_audio;
  $("voice-controls").disabled = !config || !!uploading;
  $("undo-settings").disabled = historyIndex < 1;
  $("redo-settings").disabled = historyIndex >= settingsHistory.length - 1;
  $("render-button").disabled = !ready || busy || !online;
  $("hq-button").disabled = !ready || busy || !online;
  $("analyze-button").disabled = !ready || busy || !online;
  $("hq-open").disabled = !current?.audition || busy;
  $("new-project").disabled = !!uploading;
  $("choose-file").disabled = !config || !online || !!uploading;
  const canDownload = !!current?.output && !dirty() && !busy;
  const link = $("download-button");
  link.setAttribute("aria-disabled", String(!canDownload));
  if (canDownload) {
    link.href = "/api/projects/" + current.id + "/media/output?download=true&v=" + current.output.job_id;
    link.tabIndex = 0;
  } else {
    link.removeAttribute("href");
    link.tabIndex = -1;
  }
  $("compare-output").disabled = !current?.output;
  $("compare-original").disabled = busy && !current?.meta;
  $("export-hint").textContent = busy ? "Máy chủ đang xử lý video của bạn"
    : !current ? "Chọn video để bắt đầu"
    : current.meta && !current.meta.has_audio ? "Video này không có âm thanh"
    : dirty() ? "Đang nghe thông số mới. Xuất lại để cập nhật MP4."
    : current.output ? "MP4 đã sẵn sàng để xem và tải về"
    : "Nghe trực tiếp khi chỉnh · Bấm xuất khi hài lòng";
  $("step-1").className = "step " + (current?.meta ? "complete" : "current");
  $("step-2").className = "step " + (current?.meta && !current?.output ? "current" : current?.output ? "complete" : "");
  $("step-3").className = "step " + (current?.output ? "current" : "");
}
async function api(path, options = {}) {
  const response = await fetch(path, {credentials: "same-origin", cache: "no-store", ...options});
  let data;
  try { data = await response.json(); } catch { data = {detail: "Máy chủ trả về dữ liệu chưa hợp lệ."}; }
  if (!response.ok) {
    if (response.status === 401 && config?.password_required && !$("login-dialog").open) {
      $("login-dialog").showModal();
    }
    throw new Error(typeof data.detail === "string" ? data.detail : "Yêu cầu chưa thành công.");
  }
  return data;
}
function post(path, data = {}) {
  return api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
}
async function refreshProjects() {
  projects = await api("/api/projects");
  $("project-count").textContent = projects.length + " / 4";
  const container = $("project-list");
  container.replaceChildren();
  if (!projects.length) {
    const empty = document.createElement("p");
    empty.className = "empty-list";
    empty.textContent = "Các video bạn tải lên sẽ xuất hiện ở đây.";
    container.append(empty);
  }
  const status = {ready: "Sẵn sàng", done: "Có MP4", queued: "Đang xử lý", uploading: "Đang tải", error: "Cần kiểm tra"};
  projects.forEach((project) => {
    const button = document.createElement("button");
    button.className = "project-item" + (current?.id === project.id ? " selected" : "");
    const glyph = document.createElement("span");
    glyph.className = "project-glyph"; glyph.textContent = "▶";
    const copy = document.createElement("span"); copy.className = "project-copy";
    const name = document.createElement("strong"); name.textContent = project.name;
    const meta = document.createElement("small");
    const expires = new Date(project.expires_at * 1000);
    meta.textContent = size(project.size) + (project.meta ? " · " + duration(project.meta.duration) : "") + " · Lưu đến " + expires.toLocaleTimeString("vi-VN", {hour: "2-digit", minute: "2-digit"});
    const badge = document.createElement("span"); badge.className = "project-state";
    badge.textContent = status[project.state] || project.state;
    copy.append(name, meta); button.append(glyph, copy, badge);
    button.addEventListener("click", () => selectProject(project).catch(showError));
    container.append(button);
  });
}
function updateMediaDetails() {
  if (!current) return;
  $("media-details").hidden = false;
  $("file-name").textContent = current.name;
  $("file-meta").textContent = size(current.size) + (current.meta
    ? " · " + current.meta.width + " × " + current.meta.height + " · " + duration(current.meta.duration) + " · " + (current.meta.has_audio ? current.meta.audio_codec.toUpperCase() : "Không âm thanh")
    : " · Đang đọc thông tin trên máy chủ");
  $("video-label").textContent = current.meta ? current.meta.video_codec.toUpperCase() + " · VIDEO" : "ĐANG CHUẨN BỊ";
  plus?.showAnalysis(current.analysis);
}
function switchMedia(kind = "preview") {
  if (!current) return;
  if (kind === "output") { studio.openOutput(current, dirty()); return; }
  const player = $("video"), src = "/api/projects/" + current.id + "/media/preview";
  // One persistent source per project. Slider changes and A/B never reload it.
  if (player.getAttribute("src") !== src) {
    player.pause(); player.src = src; player.load();
  }
  studio.setProject(current);
  $("empty-preview").hidden = true;
  $("video-wrap").hidden = false;
  $("drop-zone").hidden = true;
  $("comparison").hidden = false;
}
async function selectProject(project) {
  if (uploading) return;
  watchVersion++;
  $("video").pause();
  current = await api("/api/projects/" + project.id);
  currentJob = null;
  busy = ["queued", "uploading", "running"].includes(current.state);
  $("job-panel").hidden = true;
  $("drop-zone").hidden = true;
  $("video-wrap").hidden = false;
  $("empty-preview").hidden = true;
  notice();
  updateMediaDetails();
  if (current.output) {
    clearPreset();
    applySettings(current.output.settings);
    switchMedia("preview");
  } else if (current.meta && current.state !== "error") {
    switchMedia("preview");
  } else {
    $("video").removeAttribute("src"); $("video").load();
    $("empty-preview").hidden = current.state === "error";
  }
  if (current.state === "error") notice("Video chưa được chuẩn bị thành công. Bạn có thể xóa và tải lại file.", "error");
  paintActions();
  await refreshProjects();
  if (busy && current.job_id) watchJob(current.job_id, watchVersion);
}
function showJob(stage, percent, note) {
  $("job-panel").hidden = false;
  $("job-stage").textContent = stage;
  $("job-percent").textContent = percent === null ? "…" : percent + "%";
  if (percent === null) $("job-progress").removeAttribute("value");
  else $("job-progress").value = percent;
  $("job-note").textContent = note || "Tiến độ được cập nhật từ máy chủ";
}
async function watchJob(jobId, version) {
  currentJob = jobId;
  busy = true;
  $("cancel-job").disabled = false;
  $("cancel-job").hidden = false;
  paintActions();
  let failures = 0;
  while (version === watchVersion) {
    try {
      const data = await api("/api/jobs/" + jobId);
      if (version !== watchVersion) return;
      failures = 0;
      current = data.project;
      const job = data.job;
      const queued = job.state === "queued";
      showJob(job.stage, queued ? null : job.progress, queued ? "Máy chủ xử lý lần lượt, bạn có thể chờ tại đây." : null);
      updateMediaDetails();
      if (["done", "error", "cancelled"].includes(job.state)) {
        busy = false; currentJob = null;
        $("cancel-job").hidden = true;
        $("empty-preview").hidden = true;
        if (job.state === "done") {
          $("job-panel").hidden = true;
          if (job.kind === "prepare") switchMedia("preview");
          if (job.kind === "audition") plus.openHQ(current, settings()).catch(showError);
          notice(job.kind === "render" ? "MP4 đã được kiểm tra và sẵn sàng. Bạn có thể nghe so sánh rồi tải về."
            : job.kind === "audition" ? "Đoạn nghe thử HQ đã sẵn sàng."
            : job.kind === "analyze" ? "Đã đo xong. Xem kết quả trong Trợ lý giọng nói."
            : current.meta.has_audio ? "Video đã sẵn sàng. Bấm phát rồi kéo thanh chỉnh để nghe ngay."
            : "Video phát được nhưng không có âm thanh để đổi giọng.", job.kind === "render" ? "success" : "");
        } else if (job.state === "error") {
          notice(job.error || "Xử lý chưa thành công. Hãy thử lại.", "error");
          if (current.output) switchMedia("preview");
          else if (current.meta && current.state === "ready") switchMedia("preview");
        } else {
          notice("Đã hủy lượt xử lý.");
          if (current.output) switchMedia("preview");
          else if (current.meta && current.state === "ready") switchMedia("preview");
        }
        paintActions(); await refreshProjects(); return;
      }
      paintActions();
    } catch (error) {
      if (version !== watchVersion) return;
      failures++;
      showJob("Đang kết nối lại với máy chủ", null, error.message);
      if (failures >= 8) {
        busy = false; currentJob = null; $("cancel-job").hidden = true;
        notice("Mất kết nối khi theo dõi. Lượt xử lý có thể vẫn chạy. Chọn lại video trong danh sách để kiểm tra.", "error");
        paintActions(); return;
      }
    }
    await sleep(1200);
  }
}
function resetEditor() {
  watchVersion++;
  current = null; currentJob = null; busy = false;
  studio.setProject(null);
  plus.showAnalysis(null);
  $("video").pause(); $("video").removeAttribute("src"); $("video").load();
  $("video-wrap").hidden = true; $("drop-zone").hidden = false;
  $("comparison").hidden = true; $("media-details").hidden = true;
  $("job-panel").hidden = true; $("video-label").textContent = "CHƯA CÓ VIDEO";
  notice(); paintActions();
}
async function uploadFile(file) {
  if (!file || uploading || !config) return;
  if (file.size > config.max_upload_mb * 1024 * 1024) {
    notice("Video vượt giới hạn " + config.max_upload_mb + " MB.", "error"); return;
  }
  if (!/\.(mp4|mov|mkv|webm|avi|m4v)$/i.test(file.name)) {
    notice("Hãy chọn file MP4, MOV, MKV, WEBM, M4V hoặc AVI.", "error"); return;
  }
  resetEditor();
  busy = true;
  $("drop-zone").hidden = true;
  $("video-wrap").hidden = false;
  $("empty-preview").hidden = false;
  $("media-details").hidden = false;
  $("file-name").textContent = file.name;
  $("file-meta").textContent = size(file.size) + " · Đang tải lên máy chủ";
  $("cancel-job").hidden = false;
  $("cancel-job").disabled = false;
  showJob("Đang tải video lên", 0, "Giữ tab mở đến khi tải xong.");
  const xhr = new XMLHttpRequest();
  uploading = xhr; paintActions();
  xhr.open("POST", "/api/projects");
  xhr.timeout = 610000;
  xhr.setRequestHeader("Content-Type", "application/octet-stream");
  xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      const percent = Math.round(100 * event.loaded / event.total);
      showJob(percent < 100 ? "Đang tải video lên" : "Máy chủ đang nhận file", percent,
        size(event.loaded) + " / " + size(event.total));
    }
  };
  xhr.onload = async () => {
    uploading = null;
    try {
      const data = JSON.parse(xhr.responseText);
      if (xhr.status < 200 || xhr.status >= 300) throw new Error(data.detail || "Tải video chưa thành công.");
      current = data.project; currentJob = data.job.id;
      await refreshProjects();
      await watchJob(data.job.id, watchVersion);
    } catch (error) {
      busy = false; $("empty-preview").hidden = true; $("cancel-job").hidden = true;
      $("drop-zone").hidden = false; $("video-wrap").hidden = true;
      showError(error); paintActions();
    }
  };
  const fail = (message) => {
    uploading = null; resetEditor(); notice(message, "error"); paintActions();
  };
  xhr.onerror = () => fail("Kết nối bị ngắt khi tải video. Hãy thử lại.");
  xhr.ontimeout = () => fail("Tải video quá thời gian cho phép. Hãy thử file nhỏ hơn.");
  xhr.onabort = () => fail("Đã hủy tải video.");
  xhr.send(file);
}
async function beginRender() {
  if (!current || busy) return;
  notice();
  busy = true; paintActions();
  try {
    const data = await post("/api/projects/" + current.id + "/render", settings());
    watchVersion++;
    watchJob(data.job.id, watchVersion);
  } catch (error) {
    busy = false; showError(error); paintActions();
  }
}
async function beginPlus(kind) {
  if (!current || busy) return;
  notice(); busy=true; paintActions();
  try {
    const payload=kind==='audition'?{start:Math.max(0,Math.min($("video").currentTime,current.meta.duration-.1)),settings:settings()}:{};
    const data=await post(`/api/projects/${current.id}/${kind}`,payload);
    watchVersion++;watchJob(data.job.id,watchVersion);
  } catch(error){busy=false;showError(error);paintActions();}
}
function showError(error) { notice(error.message || String(error), "error"); }

$("choose-file").addEventListener("click", () => $("file-input").click());
$("new-project").addEventListener("click", () => { resetEditor(); refreshProjects().catch(showError); });
$("studio-nav").addEventListener("click", () => window.scrollTo({top: 0, behavior: "smooth"}));
$("file-input").addEventListener("change", (event) => {
  const file = event.target.files?.[0]; event.target.value = ""; uploadFile(file);
});
["dragenter", "dragover"].forEach((type) => $("drop-zone").addEventListener(type, (event) => {
  event.preventDefault(); $("drop-zone").classList.add("dragover");
}));
["dragleave", "drop"].forEach((type) => $("drop-zone").addEventListener(type, (event) => {
  event.preventDefault(); $("drop-zone").classList.remove("dragover");
}));
$("drop-zone").addEventListener("drop", (event) => uploadFile(event.dataTransfer.files[0]));
// Prevent browser navigation when a file lands just outside the upload area.
window.addEventListener("dragover", (event) => { if (event.dataTransfer.types.includes("Files")) event.preventDefault(); });
window.addEventListener("drop", (event) => { if (event.dataTransfer.types.includes("Files")) event.preventDefault(); });
function bindEditControls() {
  const edit = () => {
    clearPreset();
    if (!paintFrame) paintFrame = requestAnimationFrame(() => { paintFrame = 0; paintValues(); });
  };
  numeric.forEach((key) => {
    $(key).addEventListener("input", edit);
    $(key).addEventListener("change", () => { paintValues(); rememberSettings(); });
  });
  toggles.forEach((key) => $(key).addEventListener("change", () => { edit(); rememberSettings(); }));
}
document.querySelectorAll("[data-preset]").forEach((button) => button.addEventListener("click", () => {
  const presets = {
    clean: {...config.defaults, mid: 1.5, treble: 1, auto_level:35, harshness:20, deesser:20},
    deep: {...config.defaults, pitch: -2, formant:-.5, bass: 2, mid: .5, treble: -1, auto_level:35, warmth:25},
    bright: {...config.defaults, pitch: 2, formant:.5, bass: -1.5, mid: 1, treble: 1.5, auto_level:30, harshness:20},
    original: {...config.defaults, pitch: 0, noise: 0, highpass: false, compress: false, normalize: false, gate: false},
  };
  clearPreset(); button.classList.add("selected"); applySettings(presets[button.dataset.preset]);
}));
$("reset-settings").addEventListener("click", () => { clearPreset(); applySettings(config.defaults); });
$("render-button").addEventListener("click", beginRender);
$("analyze-button").addEventListener("click",()=>beginPlus('analyze'));
$("hq-button").addEventListener("click",()=>beginPlus('audition'));
$("hq-open").addEventListener("click",()=>plus.openHQ(current,settings()).catch(showError));

$("compare-output").addEventListener("click", () => studio.openOutput(current, dirty()));
$("undo-settings").addEventListener("click", () => undoSettings(-1));
$("redo-settings").addEventListener("click", () => undoSettings(1));
document.addEventListener("keydown", (event) => {
  if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z" || document.querySelector("dialog[open]") || (event.target.tagName === "INPUT" && event.target.type !== "range")) return;
  event.preventDefault(); undoSettings(event.shiftKey ? 1 : -1);
});
$("cancel-job").addEventListener("click", async () => {
  if (uploading) { uploading.abort(); return; }
  if (!currentJob) return;
  $("cancel-job").disabled = true;
  try { await post("/api/jobs/" + currentJob + "/cancel"); } catch (error) { showError(error); }
});
$("delete-project").addEventListener("click", async () => {
  if (!current || !confirm("Xóa video và file xuất này khỏi máy chủ?")) return;
  try { await api("/api/projects/" + current.id, {method: "DELETE"}); resetEditor(); await refreshProjects(); }
  catch (error) { showError(error); }
});
$("video").addEventListener("error", () => {
  if (!$("video").getAttribute("src")) return;
  notice("Không phát được bản xem trước. File có thể đã hết hạn; hãy chọn lại video trong danh sách.", "error");
});
$("history-nav").addEventListener("click", () => $("projects-section").scrollIntoView({behavior: "smooth"}));
$("help-button").addEventListener("click", () => $("help-dialog").showModal());
$("close-help").addEventListener("click", () => $("help-dialog").close());
$("login-dialog").addEventListener("cancel", (event) => event.preventDefault());
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    await post("/api/session", {password: $("password").value});
    $("password").value = "";
    $("login-dialog").close();
    await refreshProjects();
  } catch (error) { $("login-error").textContent = error.message; }
});
window.addEventListener("beforeunload", (event) => { if (uploading) event.preventDefault(); });
async function init() {
  try {
    config = await api("/api/config");
    numeric = config.controls.map((item) => item.id);
    units = Object.fromEntries(config.controls.map((item) => [item.id, item.unit]));
    buildControls(config); bindEditControls();
    plus.configure(config);
    await api("/healthz");
    online = true;
    $("server-dot").className = "dot online";
    $("server-state").textContent = "Máy chủ sẵn sàng";
    $("upload-limit").textContent = "Tối đa " + config.max_upload_mb + " MB · " + config.max_minutes + " phút / video";
    const hours = numberFormat.format(config.retention_seconds / 3600);
    $("retention-note").textContent = "Lưu tạm " + hours + " giờ trên máy chủ · Không tự tải về thiết bị";
    $("help-storage").textContent = "File lưu tạm " + hours + " giờ trên máy chủ và có thể mất khi máy chủ khởi động lại. Hãy tải kết quả cần giữ trước khi file hết hạn.";
    clearPreset(); applySettings(config.defaults);
    if (config.password_required) {
      try { await refreshProjects(); } catch { $("login-dialog").showModal(); }
    } else {
      await post("/api/session"); await refreshProjects();
    }
  } catch (error) {
    online = false; $("server-dot").className = "dot offline";
    $("server-state").textContent = "Chưa kết nối";
    showError(new Error("Chưa kết nối được máy chủ. Tải lại trang để thử lại. " + error.message));
  }
  paintActions();
}
const studio = new StudioEditor(showError);
const plus = new PlusStudio({getSettings:settings, apply:value=>{clearPreset();applySettings(value);}, onError:showError, studio});
init();
