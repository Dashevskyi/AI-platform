"""
LLM Direct Probe — диагностический инструмент.
Показывает raw request, raw SSE response, TTFT, total time, tokens/s.
Запрос идёт через /stream-proxy (избавляет от CORS к vLLM).

Запуск:
  cd /home/ai-platform/llm-probe
  ../backend/venv/bin/python main.py [--port 8765]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
import uvicorn

app = FastAPI()

DEFAULT_BASE_URL = "http://172.10.100.9:8000/v1"
DEFAULT_MODEL    = "qwen3-14b"


# ── Proxy endpoint ─────────────────────────────────────────────────────────────
@app.post("/stream-proxy")
async def stream_proxy(request: Request):
    """
    Receives the same JSON the browser would send to vLLM, forwards it,
    and streams back the raw SSE + injected timing events.
    """
    payload = await request.json()
    base_url  = payload.pop("__base_url__", DEFAULT_BASE_URL).rstrip("/")
    api_key   = payload.pop("__api_key__", "")

    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    target_url = f"{base_url}/chat/completions"

    async def generate() -> AsyncGenerator[bytes, None]:
        t0 = time.perf_counter()
        first_token = None
        token_count = 0
        think_chars = 0

        # Send probe:start meta event
        yield b'data: ' + json.dumps({
            "probe": "start",
            "url": target_url,
            "t0": t0,
        }).encode() + b'\n\n'

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST", target_url,
                    headers=headers,
                    content=json.dumps(payload),
                ) as resp:
                    # Forward status
                    yield b'data: ' + json.dumps({
                        "probe": "http_status",
                        "status": resp.status_code,
                    }).encode() + b'\n\n'

                    if resp.status_code != 200:
                        body = await resp.aread()
                        yield b'data: ' + json.dumps({
                            "probe": "error",
                            "detail": body.decode(errors="replace")[:500],
                        }).encode() + b'\n\n'
                        yield b'data: [DONE]\n\n'
                        return

                    # Stream raw lines + inject timing annotations
                    async for raw_line in resp.aiter_lines():
                        now = time.perf_counter()

                        # Forward the raw SSE line verbatim
                        if raw_line:
                            yield raw_line.encode() + b'\n\n'

                        if not raw_line.startswith("data:"):
                            continue

                        data_str = raw_line[5:].strip()
                        if data_str == "[DONE]":
                            break

                        try:
                            chunk = json.loads(data_str)
                        except Exception:
                            continue

                        delta = (chunk.get("choices") or [{}])[0].get("delta", {})

                        # Thinking content
                        think_delta = delta.get("reasoning_content") or ""
                        if think_delta:
                            think_chars += len(think_delta)

                        # Visible text
                        text_delta = delta.get("content") or ""
                        if text_delta:
                            token_count += 1
                            if first_token is None:
                                first_token = now
                                ttft_ms = (now - t0) * 1000
                                yield b'data: ' + json.dumps({
                                    "probe": "ttft",
                                    "ttft_ms": ttft_ms,
                                    "elapsed_ms": (now - t0) * 1000,
                                }).encode() + b'\n\n'

            total_ms = (time.perf_counter() - t0) * 1000
            yield b'data: ' + json.dumps({
                "probe": "done",
                "ttft_ms": (first_token - t0) * 1000 if first_token else None,
                "total_ms": total_ms,
                "token_count": token_count,
                "think_chars": think_chars,
                "tps": token_count / (total_ms / 1000) if total_ms > 0 else 0,
            }).encode() + b'\n\n'

        except Exception as exc:
            yield b'data: ' + json.dumps({
                "probe": "error",
                "detail": str(exc)[:300],
            }).encode() + b'\n\n'

        yield b'data: [DONE]\n\n'

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── HTML ──────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML.replace("__BASE_URL__", DEFAULT_BASE_URL).replace("__MODEL__", DEFAULT_MODEL)


HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>LLM Probe</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Courier New',monospace;background:#0d1117;color:#e6edf3;font-size:13px;height:100vh;display:flex;flex-direction:column;overflow:hidden}
h1{padding:8px 14px;background:#161b22;border-bottom:1px solid #30363d;font-size:14px;color:#58a6ff;flex-shrink:0}

.cfg{display:grid;grid-template-columns:2fr 1fr 1fr;gap:8px;padding:8px 14px;background:#161b22;border-bottom:1px solid #30363d;flex-shrink:0}
.cfg-wide{grid-column:1/-1}
label{display:block;color:#8b949e;font-size:10px;margin-bottom:2px;text-transform:uppercase;letter-spacing:.05em}
input,select,textarea{width:100%;background:#0d1117;border:1px solid #30363d;color:#e6edf3;border-radius:3px;padding:4px 7px;font-family:inherit;font-size:12px}
input:focus,select:focus,textarea:focus{outline:none;border-color:#58a6ff}
textarea{resize:vertical}

.controls{display:flex;gap:8px;align-items:flex-end;padding:7px 14px;background:#161b22;border-bottom:1px solid #30363d;flex-shrink:0;flex-wrap:wrap}
.controls>div{flex:1;min-width:60px}
button{padding:6px 16px;background:#238636;border:1px solid #2ea043;color:#fff;border-radius:3px;cursor:pointer;font-size:12px;font-weight:600;white-space:nowrap}
button:hover{background:#2ea043}
button:disabled{background:#21262d;border-color:#30363d;color:#8b949e;cursor:not-allowed}
.btn-stop{background:#b91c1c;border-color:#ef4444}
.btn-stop:hover{background:#dc2626}

.stats{display:flex;gap:20px;align-items:center;padding:7px 14px;background:#0d1117;border-bottom:1px solid #30363d;flex-shrink:0;flex-wrap:wrap}
.stat{display:flex;flex-direction:column}
.sl{font-size:10px;color:#8b949e;text-transform:uppercase}
.sv{font-size:17px;font-weight:700}
.sv.ttft{color:#3fb950}.sv.total{color:#58a6ff}.sv.tps{color:#d2a8ff}.sv.tok{color:#ffa657}
#status{margin-left:auto;font-size:11px;color:#8b949e}
#status.run{color:#f0f6ff}#status.ok{color:#3fb950}#status.err{color:#f85149}

.panels{display:grid;grid-template-columns:1fr 1fr;flex:1;min-height:0}
.panel{display:flex;flex-direction:column;overflow:hidden;border-right:1px solid #30363d}
.panel:last-child{border-right:none}
.ph{padding:5px 10px;background:#161b22;border-bottom:1px solid #30363d;font-size:10px;color:#8b949e;text-transform:uppercase;display:flex;justify-content:space-between;align-items:center;flex-shrink:0}
.ph button{padding:1px 7px;font-size:10px;background:#21262d;border:1px solid #30363d}
pre{flex:1;overflow:auto;padding:8px 10px;margin:0;white-space:pre-wrap;word-break:break-all;line-height:1.5;background:#0d1117;font-size:11.5px}

.c-probe{color:#8b949e}
.c-raw{color:#e6edf3}
.c-text{color:#3fb950;font-weight:600}
.c-think{color:#d2a8ff;font-style:italic}
.c-mark{color:#ffa657;font-weight:bold}
.c-err{color:#f85149}
.c-ttft{color:#3fb950;font-weight:bold}
</style>
</head>
<body>
<h1>⚡ LLM Direct Probe</h1>

<div class="cfg">
  <div><label>Base URL</label><input id="base_url" value="__BASE_URL__"></div>
  <div><label>Model</label><input id="model" value="__MODEL__"></div>
  <div><label>API Key (opt)</label><input id="api_key" type="password" placeholder="Bearer token"></div>
  <div class="cfg-wide">
    <label>System prompt &nbsp;<span id="sys-tok" style="color:#58a6ff;font-size:10px"></span></label>
    <textarea id="system_prompt" rows="2" placeholder="Пусто = без system prompt"></textarea>
  </div>
</div>

<div class="controls">
  <div style="flex:3"><label>Сообщение (Enter = отправить)</label>
    <input id="message" value="Привет!" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send()}">
  </div>
  <div style="max-width:70px">
    <label>Temp</label>
    <input id="temperature" value="0.6" type="number" step="0.1" min="0" max="2">
  </div>
  <div style="max-width:120px;display:flex;flex-direction:column;gap:4px;justify-content:flex-end">
    <label style="display:flex;align-items:center;gap:5px;cursor:pointer;color:#d2a8ff;margin:0">
      <input type="checkbox" id="thinking-enabled" style="width:14px;height:14px;cursor:pointer;accent-color:#d2a8ff">
      🧠 Thinking
    </label>
    <label style="display:flex;align-items:center;gap:5px;cursor:pointer;color:#8b949e;font-size:10px;margin:0" title="Если снято — параметр вообще не передаётся (модель решает сама)">
      <input type="checkbox" id="thinking-override" style="width:12px;height:12px;cursor:pointer;accent-color:#8b949e">
      передавать флаг
    </label>
  </div>
  <div style="max-width:80px"><label>Max tok</label><input id="max_tokens" value="512" type="number" step="64" min="64"></div>
  <div style="max-width:70px" title="Добавить N фиктивных пар user/assistant для теста влияния длины контекста">
    <label>Fake hist</label><input id="fake_hist" value="0" type="number" min="0" max="100"></div>
  <div style="max-width:75px"><label>Chars/turn</label><input id="fake_chars" value="300" type="number" step="100" min="50"></div>
  <div style="max-width:60px"><label>Runs</label><input id="runs" value="1" type="number" min="1" max="30"></div>
  <div style="flex:0"><button id="btn-run" onclick="send()">▶ Run</button></div>
  <div style="flex:0"><button id="btn-stop" class="btn-stop" onclick="stopAll()" style="display:none">■ Stop</button></div>
</div>

<div class="stats">
  <div class="stat"><div class="sl">TTFT</div><div class="sv ttft" id="s-ttft">—</div></div>
  <div class="stat"><div class="sl">Total</div><div class="sv total" id="s-total">—</div></div>
  <div class="stat"><div class="sl">Tokens</div><div class="sv tok" id="s-tok">—</div></div>
  <div class="stat"><div class="sl">Tok/s</div><div class="sv tps" id="s-tps">—</div></div>
  <div class="stat"><div class="sl">Think chars</div><div class="sv" id="s-think" style="color:#d2a8ff">—</div></div>
  <div class="stat"><div class="sl">Prompt est.</div><div class="sv" id="s-prompt" style="color:#8b949e;font-size:13px">—</div></div>
  <div id="status">готов</div>
</div>

<div class="panels">
  <div class="panel">
    <div class="ph">RAW REQUEST <button onclick="copyEl('req-pre')">copy</button></div>
    <pre id="req-pre"></pre>
  </div>
  <div class="panel">
    <div class="ph">RAW SSE RESPONSE <button onclick="copyEl('resp-pre')">copy</button></div>
    <pre id="resp-pre"></pre>
  </div>
</div>

<script>
let abortCtrl = null;

function copyEl(id){ navigator.clipboard.writeText(document.getElementById(id).textContent).catch(()=>{}); }

document.getElementById('system_prompt').addEventListener('input', function(){
  const n = Math.round(this.value.length / 3.5);
  document.getElementById('sys-tok').textContent = n > 0 ? `~${n} tok` : '';
});

function stopAll(){
  if(abortCtrl){ abortCtrl.abort(); abortCtrl=null; }
  document.getElementById('btn-run').disabled = false;
  document.getElementById('btn-stop').style.display = 'none';
  setStatus('остановлено','');
}

function setStatus(txt, cls){ const el=document.getElementById('status'); el.textContent=txt; el.className=cls||''; }

function appendTo(id, text, cls){
  const pre = document.getElementById(id);
  const span = document.createElement('span');
  if(cls) span.className = cls;
  span.textContent = text;
  pre.appendChild(span);
  pre.scrollTop = pre.scrollHeight;
}

function buildMessages(){
  const sys       = document.getElementById('system_prompt').value.trim();
  const message   = document.getElementById('message').value.trim();
  const fakeHist  = parseInt(document.getElementById('fake_hist').value)||0;
  const fakeChars = parseInt(document.getElementById('fake_chars').value)||300;

  const messages = [];
  if(sys) messages.push({role:'system', content:sys});
  for(let i=0;i<fakeHist;i++){
    messages.push({role:'user',      content:'[fake] '+'А'.repeat(fakeChars-7)});
    messages.push({role:'assistant', content:'[fake] '+'Б'.repeat(fakeChars-7)});
  }
  messages.push({role:'user', content:message});
  return messages;
}

async function send(){
  const runs     = parseInt(document.getElementById('runs').value)||1;
  const reqPre   = document.getElementById('req-pre');
  const respPre  = document.getElementById('resp-pre');
  reqPre.innerHTML  = '';
  respPre.innerHTML = '';

  document.getElementById('btn-run').disabled = true;
  document.getElementById('btn-stop').style.display = '';

  const allTtft=[], allTotal=[];

  for(let run=0; run<runs; run++){
    if(!document.getElementById('btn-run').disabled) break;
    setStatus(`run ${run+1}/${runs}…`, 'run');

    if(runs>1){
      appendTo('resp-pre',`\n━━━ Run ${run+1}/${runs} ━━━\n`,'c-mark');
    }

    try {
      const r = await runOnce(run===0, reqPre, respPre);
      if(r){ allTtft.push(r.ttft); allTotal.push(r.total); }
    } catch(e){
      if(e.name==='AbortError') break;
      appendTo('resp-pre',`\n[ERROR] ${e.message}\n`,'c-err');
      setStatus('ошибка','err'); break;
    }
  }

  if(allTtft.length>1){
    const avg=a=>a.reduce((x,y)=>x+y,0)/a.length;
    const fmtArr=a=>`avg=${avg(a).toFixed(0)} min=${Math.min(...a).toFixed(0)} max=${Math.max(...a).toFixed(0)}`;
    appendTo('resp-pre',
      `\n━━━ TTFT:  ${fmtArr(allTtft)} ms\n` +
      `━━━ Total: ${fmtArr(allTotal)} ms\n`,'c-mark');
  }

  document.getElementById('btn-run').disabled = false;
  document.getElementById('btn-stop').style.display = 'none';
  setStatus(allTtft.length>0 ? `готово (${allTtft.length}/${runs})` : 'готово', 'ok');
}

async function runOnce(showReq, reqPre, respPre){
  const base_url    = document.getElementById('base_url').value.trim();
  const model       = document.getElementById('model').value.trim();
  const api_key     = document.getElementById('api_key').value.trim();
  const thinkingOverride = document.getElementById('thinking-override').checked;
  const thinkingEnabled  = document.getElementById('thinking-enabled').checked;
  const temperature = parseFloat(document.getElementById('temperature').value);
  const max_tokens  = parseInt(document.getElementById('max_tokens').value);
  const messages    = buildMessages();

  // Estimate prompt tokens
  const promptChars = messages.reduce((s,m)=>s+m.content.length,0);
  document.getElementById('s-prompt').textContent = `~${Math.round(promptChars/3.5)} tok`;

  // Build vLLM payload
  const vllmBody = { model, messages, temperature, max_tokens, stream: true };
  // Only add chat_template_kwargs if "передавать флаг" checked
  if(thinkingOverride)
    vllmBody.chat_template_kwargs = { enable_thinking: thinkingEnabled };

  // Probe proxy payload (adds routing fields, stripped on server)
  const probePayload = { ...vllmBody, __base_url__: base_url, __api_key__: api_key };

  if(showReq){
    reqPre.textContent = JSON.stringify({
      url: `${base_url}/chat/completions`,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: api_key ? 'Bearer ***' : '(none)' },
      body: vllmBody,
    }, null, 2);
  }

  document.getElementById('s-ttft').textContent = '…';
  document.getElementById('s-total').textContent = '…';
  document.getElementById('s-tok').textContent = '—';
  document.getElementById('s-tps').textContent = '—';
  document.getElementById('s-think').textContent = '—';

  abortCtrl = new AbortController();
  // Use relative URL so it works both on :8765 directly and behind /probe/ nginx prefix
  const proxyUrl = new URL('stream-proxy', window.location.href).pathname;
  const resp = await fetch(proxyUrl, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(probePayload),
    signal: abortCtrl.signal,
  });

  if(!resp.ok) throw new Error(`proxy ${resp.status}`);

  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  let result = null;

  while(true){
    const {done,value} = await reader.read();
    if(done) break;
    buf += dec.decode(value,{stream:true});
    const lines = buf.split('\n');
    buf = lines.pop();

    for(const line of lines){
      if(!line.trim()) continue;

      if(!line.startsWith('data:')) continue;
      const ds = line.slice(5).trim();
      if(ds==='[DONE]') continue;

      let ev;
      try{ ev=JSON.parse(ds); } catch{
        appendTo('resp-pre', line+'\n','c-raw');
        continue;
      }

      // Probe meta events
      if(ev.probe){
        switch(ev.probe){
          case 'start':
            appendTo('resp-pre',`▶ Connecting to ${ev.url}\n`,'c-probe');
            break;
          case 'http_status':
            appendTo('resp-pre',`◀ HTTP ${ev.status}\n`,'c-probe');
            break;
          case 'ttft':
            appendTo('resp-pre',`\n◀◀ TTFT = ${ev.ttft_ms.toFixed(0)} ms\n`,'c-ttft');
            document.getElementById('s-ttft').textContent = `${ev.ttft_ms.toFixed(0)}ms`;
            break;
          case 'done':
            result = {ttft: ev.ttft_ms??ev.total_ms, total: ev.total_ms};
            document.getElementById('s-total').textContent = `${ev.total_ms.toFixed(0)}ms`;
            document.getElementById('s-tok').textContent = ev.token_count;
            document.getElementById('s-tps').textContent = ev.tps.toFixed(1);
            document.getElementById('s-think').textContent = ev.think_chars;
            appendTo('resp-pre',
              `\n◀◀ DONE: ttft=${ev.ttft_ms?.toFixed(0)??'N/A'}ms  total=${ev.total_ms.toFixed(0)}ms  tokens=${ev.token_count}  tok/s=${ev.tps.toFixed(1)}  think_chars=${ev.think_chars}\n`,
              'c-mark');
            break;
          case 'error':
            appendTo('resp-pre',`\n[ERROR] ${ev.detail}\n`,'c-err');
            break;
        }
        continue;
      }

      // Raw vLLM SSE line — show as-is, highlight delta parts
      const delta = (ev?.choices??[{}])[0]?.delta??{};
      const txt  = delta.content ?? '';
      const think = delta.reasoning_content ?? '';

      // Show raw JSON line
      appendTo('resp-pre', 'data: '+JSON.stringify(ev)+'\n', 'c-raw');
      // Annotate extracted parts below it
      if(think) appendTo('resp-pre', `  ↳ 🧠 think(${think.length}ch): ${think.slice(0,80)}${think.length>80?'…':''}\n`, 'c-think');
      if(txt)   appendTo('resp-pre', `  ↳ 📝 text: ${txt}\n`, 'c-text');

      // Live token counter
      if(txt){
        const curTok = parseInt(document.getElementById('s-tok').textContent)||0;
        document.getElementById('s-tok').textContent = curTok+1;
      }
    }
    respPre.scrollTop = respPre.scrollHeight;
  }
  return result;
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM Direct Probe")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    print(f"\n  LLM Probe → http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
