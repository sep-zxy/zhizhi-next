// Synthetic subprocess only. Never runs Python, Codex, a shell command or a network request.
import { createInterface } from 'node:readline';
const mode = process.argv[2];
let active;
const send = value => process.stdout.write(JSON.stringify(value) + '\n');
const response = (id, status, extra = {}) => ({version:1,id,type:'response',status,...extra});
const reader = createInterface({input:process.stdin});
reader.on('line', line => {
  const request = JSON.parse(line);
  if (request.type === 'cancel') {
    if (mode === 'ignore') return;
    send(response(request.id, 'cancel_requested'));
    send(response(request.request_id, mode === 'late-success' ? 'succeeded' : 'cancelled', {output:null,error_code:null}));
    return;
  }
  active = request.id;
  if (mode === 'ignore' || mode === 'cancel' || mode === 'late-success') return;
  if (mode === 'slow-hello') { setTimeout(() => send(response(request.id,'ready')), 11000); return; }
  if (mode === 'duplicate') process.stdout.write(`{"version":1,"id":"${request.id}","type":"response","status":"ready","status":"succeeded"}\n`);
  else if (mode === 'oversize') process.stdout.write('x'.repeat(1048577));
  else if (mode === 'utf8') process.stdout.write(Buffer.from([0xff,10]));
  else if (mode === 'unsolicited') send(response('00000000-0000-0000-0000-000000000000','ready'));
  else if (mode === 'truncated') { process.stdout.write('{"version":'); process.exit(0); }
  else if (mode === 'stderr') { process.stderr.write('PRIVATE_EXCEPTION_SENTINEL'); send(response(request.id,'ready')); }
  else if (mode === 'stderr-oversize') process.stderr.write('x'.repeat(65537));
  else { const text = JSON.stringify(response(request.id,'ready')) + '\r\n'; process.stdout.write(text.slice(0,12)); setTimeout(()=>process.stdout.write(text.slice(12)),5); }
});
reader.on('close', () => process.exit(0));
if (mode === 'ignore') { process.on('SIGTERM',()=>{}); setInterval(()=>{},10000); }
