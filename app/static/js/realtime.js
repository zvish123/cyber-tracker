/**
 * Real-time updates via Socket.IO
 * Flask-SocketIO 5.3.x / Socket.IO protocol v4
 */

const socket = io({ transports: ['websocket', 'polling'] });

// Read role + student ID from <html> data attributes set in base.html
const htmlEl    = document.documentElement;
const userRole  = htmlEl.dataset.userRole  || '';
const studentId = htmlEl.dataset.studentId || '';

// ── Connection lifecycle ──────────────────────────────────────────────────────

socket.on('connect', function () {
  const wsStatus = document.getElementById('ws-status');
  if (wsStatus) {
    wsStatus.className = 'badge bg-success';
    wsStatus.innerHTML = '<i class="bi bi-circle-fill"></i> חי';
  }
  const liveIndicator = document.getElementById('live-indicator');
  if (liveIndicator) {
    liveIndicator.className = 'badge bg-success';
    liveIndicator.innerHTML = '<i class="bi bi-circle-fill"></i> חי';
  }

  // Join appropriate room
  if (userRole === 'teacher') {
    socket.emit('join', { room: 'teachers' });
  } else if (userRole === 'student' && studentId) {
    socket.emit('join', { room: 'student_' + studentId });
  }
});

socket.on('disconnect', function () {
  const wsStatus = document.getElementById('ws-status');
  if (wsStatus) {
    wsStatus.className = 'badge bg-secondary';
    wsStatus.innerHTML = '<i class="bi bi-circle"></i> מנותק';
  }
  const liveIndicator = document.getElementById('live-indicator');
  if (liveIndicator) {
    liveIndicator.className = 'badge bg-secondary';
    liveIndicator.innerHTML = '<i class="bi bi-circle"></i> מנותק';
  }
});

// ── Phase update handler ──────────────────────────────────────────────────────

socket.on('phase_updated', function (data) {

  // ── Teacher dashboard: update row ──
  const row = document.querySelector(`tr[data-student-id="${data.student_id}"]`);
  if (row) {
    const phaseCell   = row.querySelector('.current-phase');
    const pctCell     = row.querySelector('.overall-percentage');
    const progressBar = row.querySelector('.progress-bar');

    if (pctCell)     pctCell.textContent      = data.overall_percentage + '%';
    if (progressBar) {
      progressBar.style.width             = data.overall_percentage + '%';
      progressBar.setAttribute('aria-valuenow', data.overall_percentage);
    }

    // Flash animation
    row.classList.add('table-warning');
    setTimeout(() => row.classList.remove('table-warning'), 2000);
  }

  // ── Teacher project detail & student dashboard: update phase row ──
  const phaseRow = document.querySelector(`.phase-row[data-phase="${data.phase}"]`);
  if (phaseRow) {
    const pctSpan = phaseRow.querySelector('.phase-percentage');
    const bar     = phaseRow.querySelector('.progress-bar');
    if (pctSpan) pctSpan.textContent = data.percentage + '%';
    if (bar) {
      bar.style.width = data.percentage + '%';
      bar.setAttribute('aria-valuenow', data.percentage);
      if (data.percentage > 10) bar.textContent = data.percentage + '%';
    }
  }

  // ── Overall percentage (circular or plain) ──
  const overallEl = document.getElementById('overall-pct');
  if (overallEl) overallEl.textContent = data.overall_percentage + '%';
});

// ── Meeting event handler ─────────────────────────────────────────────────────

socket.on('meeting_event', function (data) {
  const container = document.getElementById('meeting-notifications');
  if (!container) return;

  const labels = {
    scheduled:   'פגישה חדשה נקבעה',
    cancelled:   'פגישה בוטלה',
    rescheduled: 'פגישה נדחתה',
  };

  const colorMap = {
    scheduled:   'success',
    cancelled:   'danger',
    rescheduled: 'warning',
  };

  const label = labels[data.type] || 'עדכון פגישה';
  const color = colorMap[data.type] || 'info';
  const m     = data.meeting;

  const alert = document.createElement('div');
  alert.className = `alert alert-${color} alert-dismissible fade show`;
  alert.innerHTML = `
    <strong>${label}:</strong> ${m.title}
    ${m.scheduled_at ? `<span class="ms-2 text-muted small">${m.scheduled_at}</span>` : ''}
    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
  `;
  container.prepend(alert);

  // Auto-dismiss after 8 seconds
  setTimeout(() => {
    const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
    bsAlert.close();
  }, 8000);
});
