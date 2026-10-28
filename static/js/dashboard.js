const $ = (id) => document.getElementById(id);

let soilChart = null;
let flowChart = null;

function fmtTime(value) {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}

function showNotice(message, error = false) {
  const el = $('notice');
  if (!el) return;

  el.textContent = message;
  el.classList.remove('hidden');
  el.style.background = error ? '#fff0ef' : '#eaf6ee';
  el.style.color = error ? '#a23d37' : '#246c43';

  window.setTimeout(() => el.classList.add('hidden'), 5000);
}

function getCsrfToken() {
  // Supports the meta-tag approach and the original global variable.
  return document.querySelector('meta[name="csrf-token"]')?.content
    || window.DRIPFERTI_CSRF
    || '';
}

async function readJsonResponse(response) {
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    throw new Error(`Expected JSON response (HTTP ${response.status}). Check the Flask endpoint.`);
  }
  return response.json();
}

async function sendControl(payload) {
  const csrfToken = getCsrfToken();

  const response = await fetch('/api/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, csrf: csrfToken })
  });

  const data = await readJsonResponse(response);

  if (!response.ok || !data.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }

  await refreshDashboard();
  showNotice('Control request saved.');
}

function makeChart(canvasId, label, labels, values, color, fill = false) {
  const canvas = $(canvasId);
  if (!canvas || typeof Chart === 'undefined') return null;

  const ctx = canvas.getContext('2d');
  return new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label,
        data: values,
        borderColor: color,
        backgroundColor: fill ? 'rgba(55,142,88,.10)' : 'rgba(70,126,190,.08)',
        fill,
        tension: 0.28,
        pointRadius: 0,
        borderWidth: 2
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      interaction: { intersect: false, mode: 'index' },
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { maxTicksLimit: 6, color: '#8b978f', font: { size: 10 } },
          grid: { display: false }
        },
        y: {
          beginAtZero: true,
          ticks: { color: '#8b978f', font: { size: 10 } },
          grid: { color: '#edf1ed' }
        }
      }
    }
  });
}

function updateChart(existing, canvasId, label, rows, valueKey, color, fill = false) {
  const safeRows = Array.isArray(rows) ? rows : [];
  const labels = safeRows.map((row) => fmtTime(row.created_at));
  const values = safeRows.map((row) => Number(row[valueKey] ?? 0));

  if (!existing) {
    return makeChart(canvasId, label, labels, values, color, fill);
  }

  existing.data.labels = labels;
  existing.data.datasets[0].data = values;
  existing.update('none');
  return existing;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  })[char]);
}

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

async function refreshDashboard() {
  try {
    const response = await fetch('/api/data', { cache: 'no-store' });
    const data = await readJsonResponse(response);

    if (!response.ok || !data.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }

    const live = data.live || {};
    const status = data.status || {};
    const soil = live.soil || null;
    const flow = live.flow || {};
    const history = data.history || {};
    const soilHistory = Array.isArray(history.soil) ? history.soil : [];
    const flowHistory = Array.isArray(history.flow) ? history.flow : [];
    const events = Array.isArray(history.events) ? history.events : [];
    const threshold = Number(status.moisture_threshold ?? 30);
    const moisture = soil ? Number(soil.moisture_percent) : null;
    const valveStatus = status.valve_status || 'OFF';
    const controlMode = status.control_mode || 'manual';

    setText('moisture', moisture !== null && Number.isFinite(moisture) ? moisture.toFixed(1) : '--');
    setText(
      'soil-voltage',
      soil && Number.isFinite(Number(soil.raw_voltage_v))
        ? `${Number(soil.raw_voltage_v).toFixed(3)} V`
        : '-- V'
    );
    setText(
      'soil-condition',
      !soil
        ? 'Waiting for sensor'
        : moisture < threshold
          ? 'Below threshold'
          : 'Moisture sufficient'
    );

    const moistureBar = $('moisture-bar');
    if (moistureBar) {
      const safeMoisture = moisture !== null && Number.isFinite(moisture) ? moisture : 0;
      moistureBar.style.width = `${Math.max(0, Math.min(100, safeMoisture))}%`;
    }

    setText('flow', Number(flow.flow_lpm || 0).toFixed(3));
    setText('total-liters', Number(flow.total_liters || 0).toFixed(3));
    setText('cycle-liters', `Cycle: ${Number(live.cycle_liters || 0).toFixed(3)} L`);
    setText('flow-pulses', Number.isFinite(Number(flow.pulse_count)) ? `${flow.pulse_count} pulses` : 'Live sample');
    setText('valve-status', valveStatus);
    setText('valve-caption', valveStatus === 'ON' ? 'Valve open' : 'Valve closed');
    setText('mode-pill', controlMode.toUpperCase());

    const modeSelect = $('mode-select');
    if (modeSelect) modeSelect.value = controlMode;

    const thresholdInput = $('threshold');
    if (thresholdInput) thresholdInput.value = String(threshold);
    setText('threshold-value', String(threshold));

    const manualMode = controlMode === 'manual';
    const valveOnButton = $('valve-on');
    const valveOffButton = $('valve-off');
    if (valveOnButton) valveOnButton.disabled = !manualMode || valveStatus === 'ON';
    if (valveOffButton) valveOffButton.disabled = !manualMode || valveStatus !== 'ON';

    setText('sensor-health', soil ? 'Receiving data' : 'No reading');
    setText('database-health', data.database_ok === false ? 'Unavailable' : 'Connected');
    setText('db-status', data.database_ok === false ? 'DB unavailable' : 'DB connected');
    setText(
      'last-saved',
      live.last_saved
        ? (typeof live.last_saved === 'number'
            ? new Date(live.last_saved * 1000).toLocaleTimeString()
            : fmtTime(live.last_saved))
        : 'Waiting'
    );
    setText('hardware-mode', data.hardware_mode || 'Unknown');
    setText('control-reason', live.last_control_reason || 'No automatic action yet');

    const hasError = Boolean(live.last_error) || data.database_ok === false;
    const healthBadge = $('health-badge');
    if (healthBadge) {
      healthBadge.textContent = hasError ? 'Attention' : 'Operational';
      healthBadge.style.background = hasError ? '#fff0ef' : '#edf6ef';
      healthBadge.style.color = hasError ? '#a23d37' : '#26764e';
    }

    const errorBox = $('error-box');
    if (errorBox) {
      if (live.last_error) {
        errorBox.textContent = live.last_error;
        errorBox.classList.remove('hidden');
      } else {
        errorBox.classList.add('hidden');
      }
    }

    soilChart = updateChart(
      soilChart, 'soil-chart', 'Soil moisture (%)',
      soilHistory, 'moisture_percent', '#328957', true
    );
    flowChart = updateChart(
      flowChart, 'flow-chart', 'Flow (L/min)',
      flowHistory, 'flow_lpm', '#467ebe', false
    );

    const tbody = $('events-body');
    if (tbody) {
      if (!events.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty">No irrigation events recorded yet.</td></tr>';
      } else {
        tbody.innerHTML = events.map((event) => `
          <tr>
            <td>${escapeHtml(fmtTime(event.started_at))}</td>
            <td>${escapeHtml(fmtTime(event.ended_at))}</td>
            <td>${escapeHtml(event.source || '—')}</td>
            <td class="${event.action === 'ON' ? 'event-on' : 'event-off'}">${escapeHtml(event.action || '—')}</td>
            <td>${escapeHtml(event.notes || '—')}</td>
          </tr>
        `).join('');
      }
    }

    setText('last-updated', `Updated ${new Date().toLocaleTimeString()}`);
  } catch (error) {
    setText('db-status', 'Connection issue');
    setText('database-health', 'Unavailable');
    setText('health-badge', 'Attention');
    showNotice(error.message || 'Unable to load dashboard data.', true);
  }
}

function bindEvents() {
  $('valve-on')?.addEventListener('click', async () => {
    try {
      await sendControl({ action: 'on' });
    } catch (error) {
      showNotice(error.message, true);
    }
  });

  $('valve-off')?.addEventListener('click', async () => {
    try {
      await sendControl({ action: 'off' });
    } catch (error) {
      showNotice(error.message, true);
    }
  });

  $('mode-select')?.addEventListener('change', async (event) => {
    try {
      await sendControl({ action: 'mode', mode: event.target.value });
    } catch (error) {
      showNotice(error.message, true);
      await refreshDashboard();
    }
  });

  $('threshold')?.addEventListener('input', (event) => {
    setText('threshold-value', event.target.value);
  });

  $('save-threshold')?.addEventListener('click', async () => {
    try {
      await sendControl({ action: 'threshold', value: Number($('threshold').value) });
    } catch (error) {
      showNotice(error.message, true);
    }
  });

  $('refresh')?.addEventListener('click', refreshDashboard);
}

document.addEventListener('DOMContentLoaded', () => {
  bindEvents();
  refreshDashboard();
  window.setInterval(refreshDashboard, 5000);
});
