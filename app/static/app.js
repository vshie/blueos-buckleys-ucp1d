"use strict";

const MAX_WINDOW_MS = 60 * 60 * 1000;
const RED = "#fe0000";
const NAVY = "#071689";
const INK = "#404040";
const MUTED = "#595959";
const GRID = "#ececec";

const $ = (id) => document.getElementById(id);
const api = (path, opts = {}) =>
  fetch(path, { headers: { "Content-Type": "application/json" }, ...opts }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  });
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

let windowMs = 5 * 60 * 1000;
let lastStatus = null;
let settingsLoaded = false;
let redrawQueued = false;
const points = [];

// ---------- chart ----------

Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
Chart.defaults.color = MUTED;

const chart = new Chart($("chart"), {
  type: "line",
  data: {
    datasets: [{
      label: "Potential (mV)",
      data: points,
      borderColor: RED,
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 5,
      pointHoverBackgroundColor: RED,
      pointHoverBorderColor: "#fff",
      pointHoverBorderWidth: 2,
      tension: 0,
      spanGaps: false,
    }],
  },
  options: {
    animation: false,
    parsing: false,
    normalized: true,
    maintainAspectRatio: false,
    interaction: { mode: "nearest", axis: "x", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#fff",
        borderColor: "#d0d0d0",
        borderWidth: 1,
        titleColor: INK,
        bodyColor: INK,
        displayColors: false,
        callbacks: {
          title: (items) => fmtTime(items[0].parsed.x),
          label: (item) => `${item.parsed.y.toFixed(1)} mV`,
        },
      },
    },
    scales: {
      x: {
        type: "time",
        time: { tooltipFormat: "HH:mm:ss", displayFormats: { second: "HH:mm:ss", minute: "HH:mm", hour: "HH:mm" } },
        grid: { color: GRID },
        border: { color: "#cfcfcf" },
        ticks: { maxRotation: 0, autoSkipPadding: 24 },
      },
      y: {
        title: { display: true, text: "mV", color: MUTED },
        grid: { color: GRID },
        border: { display: false },
        grace: "5%",
      },
    },
  },
  plugins: [{
    // vertical crosshair on hover
    id: "crosshair",
    afterDatasetsDraw(c) {
      const active = c.tooltip && c.tooltip.getActiveElements();
      if (!active || !active.length) return;
      const x = active[0].element.x;
      const { top, bottom } = c.chartArea;
      const ctx = c.ctx;
      ctx.save();
      ctx.strokeStyle = "#bdbdbd";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
      ctx.restore();
    },
  }],
});

function queueRedraw() {
  if (redrawQueued) return;
  redrawQueued = true;
  requestAnimationFrame(() => {
    redrawQueued = false;
    const now = Date.now();
    const cutoff = now - MAX_WINDOW_MS;
    let drop = 0;
    while (drop < points.length && points[drop].x < cutoff) drop++;
    if (drop) points.splice(0, drop);
    chart.options.scales.x.min = now - windowMs;
    chart.options.scales.x.max = now;
    chart.update("none");
    updateStats(now - windowMs);
  });
}

function addSample(s) {
  points.push({ x: s.epoch * 1000, y: s.mv });
}

$("window").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  windowMs = Number(b.dataset.min) * 60 * 1000;
  for (const x of $("window").children) x.classList.toggle("on", x === b);
  queueRedraw();
});

setInterval(queueRedraw, 1000); // keep the time axis scrolling even without new data

// ---------- status ----------

function fmtTime(ms) {
  const d = new Date(ms);
  const utc = d.toISOString().slice(11, 19);
  return `${d.toLocaleTimeString([], { hour12: false })} (${utc} UTC)`;
}

function showReading(mv, ts) {
  $("mv").textContent = mv == null ? "–" : mv.toFixed(1);
  $("last-ts").textContent = ts ? `Last sample ${fmtTime(Date.parse(ts))}` : "No data yet";
}

function updateStats(from) {
  let n = 0, sum = 0, min = Infinity, max = -Infinity;
  for (let i = points.length - 1; i >= 0 && points[i].x >= from; i--) {
    const y = points[i].y;
    n++; sum += y;
    if (y < min) min = y;
    if (y > max) max = y;
  }
  const f = (v) => (n ? `${v.toFixed(1)} mV` : "–");
  $("s-min").textContent = f(min);
  $("s-max").textContent = f(max);
  $("s-mean").textContent = f(sum / n);
  $("s-n").textContent = n;
}

function renderStatus(s) {
  lastStatus = s;
  const dot = $("dot");
  dot.className = "dot " + (s.connected ? "ok" : "err");
  $("conn").textContent = s.connected ? (s.simulated ? "Connected (simulated)" : "Connected") : (s.error ? `Disconnected: ${s.error}` : "Disconnected");
  $("st-port").textContent = s.port || s.settings.port;
  $("st-baud").textContent = s.baud || s.settings.baud;
  $("st-interval").textContent = `${s.interval_ms || s.settings.interval_ms} ms`;
  $("st-fw").textContent = s.firmware || "–";
  $("st-log").textContent = s.logging ? s.log_file || "on" : "off";
  $("temp").textContent = s.temperature_c == null ? "–" : s.temperature_c.toFixed(1);
  $("version").textContent = `v${s.version}`;
  const lt = $("log-toggle");
  lt.textContent = s.logging ? "Stop logging" : "Start logging";
  lt.className = s.logging ? "" : "primary";
  if (s.last_mv != null) showReading(s.last_mv, s.last_ts);
  if (!settingsLoaded) {
    settingsLoaded = true;
    $("interval").value = s.settings.interval_ms;
    loadPorts();
  }
}

async function loadPorts() {
  const { ports, baud_rates } = await api("api/ports");
  const cur = lastStatus ? lastStatus.settings : {};
  const sel = $("port");
  sel.innerHTML = "";
  const opts = [["auto", "Auto-detect"], ...ports.map((p) => [p.device, `${p.device}${p.description ? " — " + p.description : ""}`])];
  if (cur.port && !opts.some(([v]) => v === cur.port)) opts.push([cur.port, `${cur.port} (not found)`]);
  for (const [v, t] of opts) sel.add(new Option(t, v, false, v === cur.port));

  const common = [9600, 115200];
  for (const id of ["baud", "probe-baud"]) {
    const b = $(id);
    b.innerHTML = "";
    const g1 = document.createElement("optgroup");
    g1.label = "Common";
    const g2 = document.createElement("optgroup");
    g2.label = "Other";
    for (const r of baud_rates) (common.includes(r) ? g1 : g2).appendChild(new Option(String(r), r, false, r === cur.baud));
    b.append(g1, g2);
  }
}

// ---------- files ----------

function fmtSize(n) {
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

async function loadFiles() {
  const { files } = await api("api/files");
  const tb = $("files");
  tb.innerHTML = "";
  if (!files.length) {
    tb.innerHTML = '<tr><td colspan="4" class="muted">No files yet</td></tr>';
    return;
  }
  const current = lastStatus && lastStatus.log_file;
  for (const f of files) {
    const tr = document.createElement("tr");
    const link = document.createElement("a");
    link.href = `api/files/${encodeURIComponent(f.name)}`;
    link.textContent = f.name;
    link.download = f.name;
    const name = document.createElement("td");
    name.appendChild(link);
    if (f.name === current) name.insertAdjacentHTML("beforeend", '<span class="current">● recording</span>');
    const del = document.createElement("button");
    del.textContent = "Delete";
    del.disabled = f.name === current;
    del.onclick = async () => {
      if (!confirm(`Delete ${f.name}?`)) return;
      await api(`api/files/${encodeURIComponent(f.name)}`, { method: "DELETE" }).catch((e) => alert(e.message));
      loadFiles();
    };
    const act = document.createElement("td");
    act.appendChild(del);
    tr.append(name, Object.assign(document.createElement("td"), { textContent: fmtSize(f.size) }),
      Object.assign(document.createElement("td"), { textContent: new Date(f.modified * 1000).toLocaleString() }), act);
    tb.appendChild(tr);
  }
}

setInterval(loadFiles, 5000);

// ---------- console ----------

function logConsole(entry) {
  const el = $("console");
  const line = document.createElement("div");
  line.className = entry.dir;
  line.textContent = `${entry.dir === "tx" ? ">" : "<"} ${entry.text}`;
  el.appendChild(line);
  while (el.childElementCount > 200) el.firstChild.remove();
  el.scrollTop = el.scrollHeight;
}

// ---------- controls ----------

$("apply").onclick = async () => {
  try {
    renderStatus(await post("api/settings", {
      port: $("port").value,
      baud: Number($("baud").value),
      interval_ms: Number($("interval").value),
    }));
  } catch (e) { alert(e.message); }
};
$("refresh-ports").onclick = loadPorts;
$("log-toggle").onclick = async () => {
  renderStatus(await post(lastStatus && lastStatus.logging ? "api/logging/stop" : "api/logging/start"));
  loadFiles();
};
$("read-temp").onclick = () => post("api/temperature");
$("send-cmd").onclick = () => {
  const c = $("cmd").value.trim();
  if (c) post("api/command", { command: c }).catch((e) => alert(e.message));
  $("cmd").value = "";
};
$("cmd").addEventListener("keydown", (e) => { if (e.key === "Enter") $("send-cmd").click(); });
$("set-probe-baud").onclick = () => {
  const b = Number($("probe-baud").value);
  if (!confirm(`Send BD${b} to the probe and reconnect at ${b} baud?\n\nIf the probe doesn't accept it, you'll need to select its actual baud rate to reconnect.`)) return;
  post("api/probe_baud", { baud: b }).catch((e) => alert(e.message));
};

// ---------- websocket ----------

function connect() {
  const url = new URL("ws", location.href);
  url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(url);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    switch (msg.type) {
      case "sample":
        addSample(msg.data);
        showReading(msg.data.mv, msg.data.ts);
        queueRedraw();
        break;
      case "history":
        points.length = 0;
        msg.data.forEach(addSample);
        queueRedraw();
        break;
      case "status":
        renderStatus(msg.data);
        break;
      case "console":
        logConsole(msg.data);
        break;
      case "console_history":
        $("console").innerHTML = "";
        msg.data.forEach(logConsole);
        break;
    }
  };
  ws.onclose = () => {
    $("dot").className = "dot err";
    $("conn").textContent = "Extension offline — reconnecting…";
    setTimeout(connect, 2000);
  };
}

connect();
loadFiles();
