const els = {
  form: document.getElementById('upload-form'),
  selectFrom: document.getElementById('select-from'),
  selectTo: document.getElementById('select-to'),
  dropzone: document.getElementById('dropzone'),
  fileInput: document.getElementById('file-input'),
  fileChosen: document.getElementById('file-chosen'),
  formAlert: document.getElementById('form-alert'),
  btnSubmit: document.getElementById('btn-submit'),
  btnSimulated: document.getElementById('btn-simulated'),
};

let chosenFile = null;

async function populateStations(){
  const res = await fetch('/api/stations');
  const stations = await res.json();
  [els.selectFrom, els.selectTo].forEach(sel => {
    sel.innerHTML = stations.map(s => `<option value="${s.code}">${s.name}</option>`).join('');
  });
  // sensible default range: first -> last station
  els.selectFrom.selectedIndex = 0;
  els.selectTo.selectedIndex = stations.length - 1;
}

function showAlert(message, isSuccess){
  els.formAlert.hidden = false;
  els.formAlert.textContent = message;
  els.formAlert.classList.toggle('is-success', !!isSuccess);
}

function hideAlert(){
  els.formAlert.hidden = true;
}

function setFile(file){
  chosenFile = file;
  els.fileChosen.textContent = file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : 'No file selected';
}

els.dropzone.addEventListener('click', () => els.fileInput.click());
els.dropzone.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') els.fileInput.click();
});
els.fileInput.addEventListener('change', () => setFile(els.fileInput.files[0] || null));

['dragenter', 'dragover'].forEach(evt =>
  els.dropzone.addEventListener(evt, (e) => { e.preventDefault(); els.dropzone.classList.add('is-dragover'); })
);
['dragleave', 'drop'].forEach(evt =>
  els.dropzone.addEventListener(evt, (e) => { e.preventDefault(); els.dropzone.classList.remove('is-dragover'); })
);
els.dropzone.addEventListener('drop', (e) => {
  const file = e.dataTransfer.files[0];
  if (file) setFile(file);
});

els.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideAlert();

  if (!chosenFile){
    showAlert('Please choose a CSV file to upload.');
    return;
  }
  if (els.selectFrom.value === els.selectTo.value){
    showAlert('From and To stations must be different.');
    return;
  }

  const formData = new FormData();
  formData.append('file', chosenFile);
  formData.append('from_station', els.selectFrom.value);
  formData.append('to_station', els.selectTo.value);

  els.btnSubmit.disabled = true;
  els.btnSubmit.textContent = 'Analyzing…';

  try{
    const res = await fetch('/api/upload', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok){
      showAlert(data.error || 'Upload failed.');
      return;
    }
    let msg = `Analyzed ${data.sensor_count} sensor(s) from ${data.filename}.`;
    if (data.warnings && data.warnings.length){
      msg += ` ${data.warnings.length} warning(s) — see monitoring page for details.`;
    }
    showAlert(msg + ' Redirecting to monitoring…', true);
    setTimeout(() => { window.location.href = '/monitor'; }, 900);
  } catch (err){
    showAlert('Network error while uploading. Please try again.');
  } finally {
    els.btnSubmit.disabled = false;
    els.btnSubmit.textContent = 'Analyze Dataset';
  }
});

els.btnSimulated.addEventListener('click', async () => {
  els.btnSimulated.disabled = true;
  try{
    await fetch('/api/use-simulated', { method: 'POST' });
    window.location.href = '/monitor';
  } finally {
    els.btnSimulated.disabled = false;
  }
});

populateStations();
