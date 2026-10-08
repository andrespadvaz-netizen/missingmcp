const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

const names = [
  'Metis', 'Andrea', 'Shokko', 'Venture Quest',
  'Arquitecto Interior', 'Personal', '.Final_Final'
];

async function main() {
  const tokens = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.clasprc.json'), 'utf8')).tokens.default;
  const refresh = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    body: new URLSearchParams({
      client_id: tokens.client_id,
      client_secret: tokens.client_secret,
      refresh_token: tokens.refresh_token,
      grant_type: 'refresh_token'
    })
  });
  const auth = await refresh.json();
  if (!refresh.ok) throw new Error('OAuth refresh failed: ' + refresh.status);
  for (const name of names) {
    const escaped = name.replace(/'/g, "\\'");
    const q = `mimeType='application/vnd.google-apps.folder' and trashed=false and name contains '${escaped}'`;
    const url = new URL('https://www.googleapis.com/drive/v3/files');
    url.searchParams.set('q', q);
    url.searchParams.set('fields', 'files(id,name,parents,driveId,webViewLink)');
    url.searchParams.set('pageSize', '100');
    url.searchParams.set('supportsAllDrives', 'true');
    url.searchParams.set('includeItemsFromAllDrives', 'true');
    const response = await fetch(url, {headers: {Authorization: 'Bearer ' + auth.access_token}});
    const data = await response.json();
    if (!response.ok) throw new Error('Drive ' + response.status + ' ' + (data.error?.message || 'failed'));
    console.log(JSON.stringify({context: name, folders: data.files || []}));
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
