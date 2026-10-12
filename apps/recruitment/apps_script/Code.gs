// Starts the panel's import right after a form submission (REVIEW_PANEL_SPEC.md §1, R1).
// Install, verify and rotate the token: README.md next to this file.
// Cloud Scheduler calls the same endpoint every 10 minutes, so a failed run only delays a candidate.

const COUNT_KEYS = ['created', 'existing', 'skipped', 'skipped_rows', 'errors'];

function onFormSubmit(e) {
  ingest_();
}

function testIngest() {
  ingest_();
}

function ingest_() {
  const response = UrlFetchApp.fetch(property_('INGEST_URL'), {
    method: 'post',
    contentType: 'application/json',
    payload: '{}',
    headers: {'X-Ingest-Token': property_('INGEST_TOKEN')},
    muteHttpExceptions: true,
  });
  const status = response.getResponseCode();
  if (status < 200 || status >= 300) {
    throw new Error('Ingest failed: HTTP ' + status);
  }
  const body = JSON.parse(response.getContentText());
  const counts = COUNT_KEYS.map(key => {
    const value = body[key];
    return key + '=' + (Array.isArray(value) ? value.length : value);
  });
  console.log('Ingest HTTP ' + status + ': ' + counts.join(' '));
}

function property_(name) {
  const value = PropertiesService.getScriptProperties().getProperty(name);
  if (!value) {
    throw new Error('Script Property ' + name + ' is missing: Project Settings -> Script Properties (README.md, step 2)');
  }
  return value;
}
