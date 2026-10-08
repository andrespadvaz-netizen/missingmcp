const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

const scriptId = '1g9BKx5-TShUuUi2w0fZLByFydG8_l1WB74vn-MDOYWc4nd3OGiolDp9o';
const functionName = process.argv[2];
if (!functionName || !/^[A-Za-z0-9_]+$/.test(functionName)) {
  throw new Error('Usage: node run-engine-function.cjs <functionName>');
}

async function main() {
  const c = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.clasprc.json'), 'utf8')).tokens.default;
  const refresh = await fetch('https://oauth2.googleapis.com/token', {method: 'POST', body: new URLSearchParams({
    client_id: c.client_id, client_secret: c.client_secret,
    refresh_token: c.refresh_token, grant_type: 'refresh_token'
  })});
  const token = await refresh.json();
  if (!refresh.ok) throw new Error('OAuth refresh failed: ' + refresh.status);
  const response = await fetch('https://script.googleapis.com/v1/scripts/' + scriptId + ':run', {
    method: 'POST',
    headers: {Authorization: 'Bearer ' + token.access_token, 'Content-Type': 'application/json'},
    body: JSON.stringify({function: functionName, devMode: true})
  });
  const data = await response.json();
  if (!response.ok || data.error) {
    console.error(JSON.stringify(data, null, 2));
    process.exitCode = 1;
    return;
  }
  console.log(JSON.stringify(data.response && data.response.result, null, 2));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
