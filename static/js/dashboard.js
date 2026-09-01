/* RailPulse dashboard client
   Polls the Flask API and renders the sensor map, KPI strip, alert feed,
   and the time-domain / FFT / Fourier-series charts for the selected sensor. */

const REFRESH_MS = 5000;

const state = {
  selectedId: null,
  harmonics: 8,
  segments: [],
};

const els = {
  modeIndicator: document.getElementById('mode-indicator'),
  trackTitle: document.getElementById('track-title'),
  chainageStart: document.getElementById('chainage-start'),
  kpiTotal: document.getElementById('kpi-total'),
  kpiHealth: document.getElementById('kpi-health'),
  kpiWarning: document.getElementById('kpi-warning'),
  kpiCritical: document.getElementById('kpi-critical'),
  kpiAlerts: document.getElementById('kpi-alerts'),
  railTrack: document.getElementById('rail-track'),
  chainageEnd: document.getElementById('chainage-end'),
  detailTitle: document.getElementById('detail-title'),
  detailSub: document.getElementById('detail-sub'),
  detailStatus: document.getElementById('detail-status'),
  statScore: document.getElementById('stat-score'),
  statDefect: document.getElementById('stat-defect'),
  statFreq: document.getElementById('stat-freq'),
  statSpeed: document.getElementById('stat-speed'),
  statAction: document.getElementById('stat-action'),
  alertsList: document.getElementById('alerts-list'),
  alertsCount: document.getElementById('alerts-count'),
  harmonicsSlider: document.getElementById('harmonics-slider'),
  harmonicsValue: document.getElementById('harmonics-value'),
  fidelityReadout: document.getElementById('fidelity-readout'),
  selectFrom: document.getElementById('select-from'),
  selectTo: document.getElementById('select-to'),
  btnApplyRoute: document.getElementById('btn-apply-route'),
  routeSummary: document.getElementById('route-summary'),
  datasetBadge: document.getElementById('dataset-badge'),
  mapIframe: document.getElementById('map-iframe'),
  warningsCard: document.getElementById('warnings-card'),
  warningsList: document.getElementById('warnings-list'),
};

const CHART_COLORS = {
  grid: 'rgba(255,255,255,0.05)',
  text: '#8fa0b3',
  original: '#4c8fd1',
  recon: '#f2a93b',
  fft: '#34c77b',
};

Chart.defaults.font.family = "'IBM Plex Mono', monospace";
Chart.defaults.font.size = 10.5;
Chart.defaults.color = CHART_COLORS.text;

function baseLineOptions(yLabel){
  return {
    responsive: true,
    animation: { duration: 250 },
    interaction: { intersect: false, mode: 'index' },
    plugins: { legend: { display: false } },
    elements: { point: { radius: 0 }, line: { borderWidth: 1.6, tension: 0.15 } },
    scales: {
      x: { grid: { color: CHART_COLORS.grid }, ticks: { maxTicksLimit: 6 } },
      y: { grid: { color: CHART_COLORS.grid }, title: { display: !!yLabel, text: yLabel, color: CHART_COLORS.text } },
    },
  };
}

const timeChart = new Chart(document.getElementById('chart-timedomain'), {
  type: 'line',
  data: { labels: [], datasets: [{ data: [], borderColor: CHART_COLORS.original }] },
  options: baseLineOptions('g'),
});

const fftChart = new Chart(document.getElementById('chart-fft'), {
  type: 'line',
  data: { labels: [], datasets: [{ data: [], borderColor: CHART_COLORS.fft, fill: true, backgroundColor: 'rgba(52,199,123,0.08)' }] },
  options: baseLineOptions('amplitude'),
});

const fourierChart = new Chart(document.getElementById('chart-fourier'), {
  type: 'line',
  data: {
    labels: [],
    datasets: [
      { label: 'Original', data: [], borderColor: CHART_COLORS.original },
      { label: 'Fourier reconstruction', data: [], borderColor: CHART_COLORS.recon, borderDash: [4, 3] },
    ],
  },
  options: {
    ...baseLineOptions('g'),
    plugins: { legend: { display: true, position: 'top', labels: { boxWidth: 12, padding: 12 } } },
  },
});

function fmtTime(iso){
  const d = new Date(iso);
  return d.toLocaleTimeString('en-GB', { hour12: false });
}

async function fetchJSON(url){
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

function renderSummary(summary){
  els.kpiTotal.textContent = summary.total_segments;
  els.kpiHealth.textContent = `${summary.avg_health}%`;
  els.kpiWarning.textContent = summary.warning_count;
  els.kpiCritical.textContent = summary.critical_count;
  els.kpiAlerts.textContent = summary.active_alerts;
  els.chainageEnd.textContent = `${summary.total_km} km`;
}

function renderTrack(segments){
  state.segments = segments;
  els.railTrack.innerHTML = '';
  segments.forEach(seg => {
    const btn = document.createElement('button');
    btn.className = `sensor-pole status-${seg.status}` + (seg.id === state.selectedId ? ' active' : '');
    btn.dataset.id = seg.id;
    btn.setAttribute('role', 'listitem');
    btn.setAttribute('aria-label', `${seg.name}, chainage ${seg.chainage_km} km, status ${seg.status}, health ${seg.health_score}%`);
    btn.innerHTML = `
      <span class="sensor-pole__lamp"></span>
      <span class="sensor-pole__stem"></span>
      <span class="sensor-pole__id">${seg.id}</span>
    `;
    btn.addEventListener('click', () => selectSegment(seg.id));
    els.railTrack.appendChild(btn);
  });
}

function renderAlerts(alerts){
  els.alertsCount.textContent = alerts.length;
  if (!alerts.length){
    els.alertsList.innerHTML = `<p class="dim empty-state">No alerts yet. Monitoring nominal.</p>`;
    return;
  }
  els.alertsList.innerHTML = alerts.map(a => `
    <div class="alert-item status-${a.status}">
      <div class="alert-item__top">
        <span class="alert-item__seg">${a.segment_id} · ${a.chainage_km} km</span>
        <span class="alert-item__time">${fmtTime(a.timestamp)}</span>
      </div>
      <div class="alert-item__msg">${a.message}</div>
    </div>
  `).join('');
}

async function populateStationSelects(){
  const stations = await fetchJSON('/api/stations');
  state.stations = stations;
  [els.selectFrom, els.selectTo].forEach(sel => {
    sel.innerHTML = stations.map(s => `<option value="${s.code}">${s.name}</option>`).join('');
  });
}

function renderState(s){
  els.modeIndicator.textContent = s.mode === 'uploaded' ? 'Uploaded Dataset' : 'Simulated Demo';
  if (els.trackTitle){
    els.trackTitle.textContent = `Track Sensor Map — ${s.from_station.name} \u2192 ${s.to_station.name}`;
  }
  if (els.chainageStart) els.chainageStart.textContent = `${s.from_station.chainage_km} km`;

  if (els.selectFrom && els.selectFrom.value !== s.from_station.code) els.selectFrom.value = s.from_station.code;
  if (els.selectTo && els.selectTo.value !== s.to_station.code) els.selectTo.value = s.to_station.code;

  if (els.mapIframe && els.mapIframe.dataset.route !== s.maps_embed_url){
    els.mapIframe.src = s.maps_embed_url;
    els.mapIframe.dataset.route = s.maps_embed_url;
  }

  if (els.routeSummary){
    els.routeSummary.innerHTML = `
      <div><strong>${s.from_station.name}</strong> \u2192 <strong>${s.to_station.name}</strong></div>
      <div>${s.distance_km} km · ${s.sensor_count} sensors</div>
    `;
  }

  if (els.datasetBadge){
    if (s.mode === 'uploaded'){
      els.datasetBadge.textContent = `${s.filename} · uploaded ${fmtTime(s.uploaded_at)}`;
      els.datasetBadge.classList.add('is-uploaded');
    } else {
      els.datasetBadge.textContent = 'Simulated demo data';
      els.datasetBadge.classList.remove('is-uploaded');
    }
  }

  if (els.warningsCard){
    if (s.warnings && s.warnings.length){
      els.warningsCard.hidden = false;
      els.warningsList.innerHTML = s.warnings.map(w => `<li>${w}</li>`).join('');
    } else {
      els.warningsCard.hidden = true;
    }
  }
}

if (els.btnApplyRoute){
  els.btnApplyRoute.addEventListener('click', async () => {
    els.btnApplyRoute.disabled = true;
    try{
      await fetch('/api/select-stations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from_station: els.selectFrom.value, to_station: els.selectTo.value }),
      });
      state.selectedId = null;
      await pollLoop();
    } finally {
      els.btnApplyRoute.disabled = false;
    }
  });
}

async function selectSegment(id){
  state.selectedId = id;
  document.querySelectorAll('.sensor-pole').forEach(el => {
    el.classList.toggle('active', el.dataset.id === id);
  });

  const detail = await fetchJSON(`/api/segments/${id}`);
  renderDetail(detail);
  await refreshFourier();
}

function renderDetail(d){
  els.detailTitle.textContent = `${d.name} — Chainage ${d.chainage_km} km`;
  els.detailSub.textContent = `Sampling ${d.sampling_rate_hz} Hz · ${d.id}`;
  els.detailStatus.textContent = d.status;
  els.detailStatus.className = `status-pill status-${d.status}`;
  els.statScore.textContent = `${d.health_score}%`;
  els.statDefect.textContent = d.defect_label;
  els.statFreq.textContent = `${d.dominant_frequency_hz} Hz`;
  els.statSpeed.textContent = `${d.speed_limit_kmph} km/h`;
  els.statAction.textContent = d.recommended_action;

  timeChart.data.labels = d.time_series.t.map(t => t.toFixed(3));
  timeChart.data.datasets[0].data = d.time_series.signal;
  timeChart.update();

  // limit FFT display to a readable band (0-1000 Hz covers all defect bands)
  const freqs = d.spectrum.freqs;
  const amps = d.spectrum.amps;
  const cutoff = freqs.findIndex(f => f > 1000);
  const end = cutoff === -1 ? freqs.length : cutoff;
  fftChart.data.labels = freqs.slice(0, end).map(f => Math.round(f));
  fftChart.data.datasets[0].data = amps.slice(0, end);
  fftChart.update();
}

async function refreshFourier(){
  if (!state.selectedId) return;
  const data = await fetchJSON(`/api/segments/${state.selectedId}/fourier?harmonics=${state.harmonics}`);
  fourierChart.data.labels = data.t.map(t => t.toFixed(3));
  fourierChart.data.datasets[0].data = data.original;
  fourierChart.data.datasets[1].data = data.reconstructed;
  fourierChart.update();
  els.fidelityReadout.textContent = `fidelity ${data.fidelity_pct}%`;
}

els.harmonicsSlider.addEventListener('input', (e) => {
  state.harmonics = Number(e.target.value);
  els.harmonicsValue.textContent = state.harmonics;
});
els.harmonicsSlider.addEventListener('change', refreshFourier);

async function pollLoop(){
  try{
    const [summary, segments, alerts, appState] = await Promise.all([
      fetchJSON('/api/summary'),
      fetchJSON('/api/segments'),
      fetchJSON('/api/alerts'),
      fetchJSON('/api/state'),
    ]);
    renderSummary(summary);
    renderTrack(segments);
    renderAlerts(alerts);
    renderState(appState);

    const stillExists = state.selectedId && segments.some(s => s.id === state.selectedId);
    if (stillExists){
      const d = await fetchJSON(`/api/segments/${state.selectedId}`);
      renderDetail(d);
    } else if (segments.length){
      const worst = [...segments].sort((a, b) => a.health_score - b.health_score)[0];
      await selectSegment(worst.id);
    } else {
      state.selectedId = null;
    }
  } catch (err){
    console.error('RailPulse polling error:', err);
  }
}

async function init(){
  await populateStationSelects();
  await pollLoop();
  setInterval(pollLoop, REFRESH_MS);
}

init();
