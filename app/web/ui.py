"""The single-page web UI."""

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>URL Bypass</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin:0; min-height:100vh; display:flex; align-items:center;
    justify-content:center; padding:24px;
    background: radial-gradient(1100px 560px at 50% -10%, #1e293b, #020617 62%);
    font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    color:#e2e8f0;
  }
  .card {
    width:100%; max-width:660px; background:#0b1220; border:1px solid #1e293b;
    border-radius:18px; padding:28px; box-shadow:0 30px 80px rgba(0,0,0,.55);
  }
  h1 { margin:0 0 6px; font-size:22px; }
  p.sub { margin:0 0 20px; color:#94a3b8; font-size:14px; line-height:1.55; }
  form { display:flex; gap:10px; flex-wrap:wrap; }
  input[type=url] {
    flex:1 1 320px; padding:13px 14px; border-radius:11px;
    border:1px solid #26344b; background:#060b16; color:#e2e8f0; font-size:15px;
    outline:none;
  }
  input:focus { border-color:#38bdf8; }
  button {
    padding:13px 20px; border-radius:11px; border:0; cursor:pointer;
    background:#38bdf8; color:#052030; font-weight:650; font-size:15px;
  }
  button:disabled { opacity:.55; cursor:progress; }
  .row { display:flex; gap:14px; margin-top:12px; color:#94a3b8; font-size:13px; }
  #out { margin-top:20px; display:none; }
  .box {
    background:#060b16; border:1px solid #1e293b; border-radius:12px;
    padding:14px; margin-bottom:10px; word-break:break-all; font-size:14px;
  }
  .box a { color:#7dd3fc; text-decoration:none; }
  .box a:hover { text-decoration:underline; }
  .label { display:block; font-size:11px; letter-spacing:.09em; text-transform:uppercase;
           color:#64748b; margin-bottom:6px; }
  .err { border-color:#7f1d1d; color:#fca5a5; }
  .ok { border-color:#14532d; }
  .copy { margin-left:8px; font-size:12px; padding:5px 10px; border-radius:7px;
          background:#1e293b; color:#cbd5e1; border:0; cursor:pointer; }
  .meta { color:#64748b; font-size:12px; margin-top:6px; }
  .steps { margin-top:8px; font-size:12px; color:#64748b; line-height:1.7;
           max-height:220px; overflow:auto; }
  .examples { margin-top:18px; font-size:12.5px; color:#64748b; line-height:2; }
  .examples code { background:#0f172a; padding:3px 7px; border-radius:6px;
                   cursor:pointer; color:#94a3b8; }
  footer { margin-top:20px; font-size:12px; color:#475569; }
  .bar { height:3px; background:#1e293b; border-radius:3px; overflow:hidden;
         margin-top:14px; display:none; }
  .bar i { display:block; height:100%; width:35%; background:#38bdf8;
           animation: slide 1.1s infinite ease-in-out; }
  @keyframes slide { 0%{transform:translateX(-100%)} 100%{transform:translateX(320%)} }
</style>
</head>
<body>
<div class="card">
  <h1>🔗 URL Bypass</h1>
  <p class="sub">
    Paste a shortened or ad-locked link — gplinks, liteshort, adrinolinks,
    adf.ly, linkvertise, bit.ly and friends. A real browser walks the ad steps
    and Cloudflare checks for you, so the link you get is the actual file.
  </p>

  <form id="f">
    <input id="u" type="url" placeholder="https://gplinks.co/ZkVCbbry" required>
    <button id="go" type="submit">Bypass</button>
  </form>
  <label class="row"><input type="checkbox" id="v"> show what it did</label>
  <div class="bar" id="bar"><i></i></div>

  <div id="out"></div>

  <div class="examples">
    Try:
    <code class="ex">https://liteshort.com/al1t</code>
    <code class="ex">https://gplinks.co/ZkVCbbry</code>
  </div>

  <footer>API: <code>GET /api/bypass?url=…&amp;verbose=true</code> · Telegram bot available</footer>
</div>

<script>
const out = document.getElementById('out');
const inp = document.getElementById('u');
const bar = document.getElementById('bar');

document.querySelectorAll('.ex').forEach(e => e.onclick = () => inp.value = e.textContent);

const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function box(label, html, cls) {
  return '<div class="box ' + (cls||'') + '"><span class="label">' + label + '</span>' + html + '</div>';
}

document.getElementById('f').onsubmit = async ev => {
  ev.preventDefault();
  const btn = document.getElementById('go');
  const verbose = document.getElementById('v').checked;
  btn.disabled = true; bar.style.display = 'block';
  out.style.display = 'block';
  out.innerHTML = box('working', 'Walking the link… ad-step countdowns can take 30–60 seconds.');
  try {
    const r = await fetch('/api/bypass', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ url: inp.value, verbose })
    });
    const d = await r.json();
    if (!d.ok) {
      out.innerHTML = box('could not resolve', esc(d.error || d.detail || 'unknown error'), 'err');
    } else {
      const pct = Math.round((d.confidence||0) * 100);
      out.innerHTML = box('destination',
        '<a href="' + esc(d.url) + '" target="_blank" rel="noopener">' + esc(d.url) + '</a>' +
        '<button class="copy" onclick="navigator.clipboard.writeText(' +
          JSON.stringify(d.url) + ')">copy</button>' +
        '<div class="meta">confidence ' + pct + '% · engine ' + esc(d.engine) +
        ' · ' + d.elapsed + 's</div>', 'ok');
    }
    if (verbose && d.steps && d.steps.length) {
      out.innerHTML += box('what it did',
        '<div class="steps">' + d.steps.map(s =>
          '• <b>' + esc(s.kind) + '</b> ' + esc(s.detail) +
          (s.url ? '<br>&nbsp;&nbsp;<span style="color:#475569">' + esc(s.url) + '</span>' : '')
        ).join('<br>') + '</div>');
    }
  } catch (e) {
    out.innerHTML = box('error', esc(e), 'err');
  } finally {
    btn.disabled = false; bar.style.display = 'none';
  }
};
</script>
</body>
</html>
"""
