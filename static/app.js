// WebSocket connection for real-time updates
let ws = null;

function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host;
  ws = new WebSocket(`${protocol}//${host}/ws/progress`);

  ws.onopen = () => {
    console.log('WebSocket connected');
  };

  ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === 'download_progress') {
      updateDownloadProgress(message.data);
    } else if (message.type === 'download_error') {
      showDownloadError(message.error);
    }
  };

  ws.onclose = () => {
    console.log('WebSocket disconnected');
    // Attempt to reconnect after 5 seconds
    setTimeout(connectWebSocket, 5000);
  };
}

// Initialize WebSocket connection when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
  connectWebSocket();

  // Create progress bar container if it doesn't exist
  if (!document.getElementById('download-progress-container')) {
    const container = document.createElement('div');
    container.id = 'download-progress-container';
    container.style.position = 'fixed';
    container.style.bottom = '20px';
    container.style.right = '20px';
    container.style.zIndex = '1000';
    container.style.maxWidth = '300px';
    container.style.backgroundColor = '#ffffff';
    container.style.borderRadius = '8px';
    container.style.boxShadow = '0 2px 10px rgba(0,0,0,0.2)';
    container.style.padding = '15px';
    document.body.appendChild(container);
  }
});

function updateDownloadProgress(data) {
  const container = document.getElementById('download-progress-container');
  let progressBar = document.getElementById(`progress-${data.hash}`);

  if (!progressBar) {
    // Create new progress bar for this download
    const wrapper = document.createElement('div');
    wrapper.id = `download-${data.hash}`;
    wrapper.style.marginBottom = '10px';

    const title = document.createElement('div');
    title.textContent = data.name;
    title.style.fontWeight = 'bold';
    title.style.marginBottom = '5px';

    progressBar = document.createElement('progress');
    progressBar.id = `progress-${data.hash}`;
    progressBar.max = 100;
    progressBar.value = data.progress * 100;
    progressBar.style.width = '100%';
    progressBar.style.height = '20px';

    const details = document.createElement('div');
    details.id = `details-${data.hash}`;
    details.style.fontSize = '0.8em';
    details.style.color = '#666';
    details.textContent = `${Math.round(data.progress * 100)}% | ${formatSpeed(data.download_speed)}`;

    wrapper.appendChild(title);
    wrapper.appendChild(progressBar);
    wrapper.appendChild(details);
    container.appendChild(wrapper);
  } else {
    // Update existing progress bar
    progressBar.value = data.progress * 100;
    const details = document.getElementById(`details-${data.hash}`);
    details.textContent = `${Math.round(data.progress * 100)}% | ${formatSpeed(data.download_speed)} | ${data.status}`;

    // Show completion notification and cleanup
    if (data.progress === 1.0) {
      showDownloadComplete(data.name);
      // Remove progress bar after 5 seconds
      setTimeout(() => {
        const wrapper = document.getElementById(`download-${data.hash}`);
        if (wrapper) wrapper.remove();
      }, 5000);
    }
  }
}

function showDownloadError(error) {
  const container = document.getElementById('download-progress-container');
  const errorDiv = document.createElement('div');
  errorDiv.style.color = '#d32f2f';
  errorDiv.style.padding = '10px';
  errorDiv.style.marginBottom = '10px';
  errorDiv.style.borderRadius = '4px';
  errorDiv.style.backgroundColor = '#ffebee';
  errorDiv.textContent = `Download error: ${error}`;
  container.appendChild(errorDiv);

  // Remove error message after 5 seconds
  setTimeout(() => {
    errorDiv.remove();
  }, 5000);
}

function formatSpeed(bytes) {
  if (bytes < 1024) return `${bytes} B/s`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB/s`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB/s`;
}

function showDownloadComplete(name) {
  const container = document.getElementById('download-progress-container');
  const notification = document.createElement('div');
  notification.className = 'download-notification';
  notification.style.backgroundColor = '#4caf50';
  notification.style.color = 'white';
  notification.style.padding = '10px';
  notification.style.borderRadius = '4px';
  notification.style.marginBottom = '10px';
  notification.style.animation = 'fadeIn 0.3s';
  notification.innerHTML = `
    <div style="display: flex; align-items: center;">
      <span style="margin-right: 8px;">✓</span>
      <span>${name} download complete!</span>
    </div>
  `;

  container.insertBefore(notification, container.firstChild);

  // Remove notification after 5 seconds
  setTimeout(() => {
    notification.style.animation = 'fadeOut 0.3s';
    setTimeout(() => notification.remove(), 300);
  }, 5000);
}
