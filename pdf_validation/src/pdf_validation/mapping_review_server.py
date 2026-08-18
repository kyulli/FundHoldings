"""Localhost browser UI for mapping proposal review and one-click draft compare.

Binds only to 127.0.0.1. Does not write formal registry until explicit promote.
"""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pdf_validation.mapping_onboarding import (
    acknowledge_extraction_gate,
    preview_promote_diff,
    promote_draft_mapping,
    review_payload,
    run_draft_compare,
    upsert_decision,
)
from pdf_validation.discrepancy_review import upsert_discrepancy_decision
from pdf_validation.reviewed_output import write_reviewed_extraction_excel
from pdf_validation.review_preview import render_preview_page_png, resolve_pdf_path


def _html_page(title: str, body: str) -> bytes:
    doc = f"""<!DOCTYPE html>
<html lang="en" data-lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{title}</title>
  <style>
    :root {{
      --bg: #0f1419; --card: #1a2332; --text: #e7ecf3; --muted: #9aa8bc;
      --accent: #3b82f6; --ok: #22c55e; --warn: #f59e0b; --bad: #ef4444; --border: #2a3648;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; background: var(--bg); color: var(--text); line-height: 1.45; }}
    header {{ position: sticky; top: 0; z-index: 10; background: rgba(15,20,25,.92); backdrop-filter: blur(8px); border-bottom: 1px solid var(--border); padding: 14px 20px; }}
    .header-row {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; }}
    h1 {{ margin: 0 0 6px; font-size: 18px; }}
    h2 {{ margin: 0 0 8px; font-size: 15px; }}
    .meta {{ color: var(--muted); font-size: 13px; display: flex; flex-wrap: wrap; gap: 12px; }}
    .counts span {{ display: inline-block; padding: 2px 8px; border-radius: 999px; background: #243044; margin-right: 6px; }}
    .ok {{ color: var(--ok); }} .warn {{ color: var(--warn); }} .bad {{ color: var(--bad); }}
    main {{ max-width: 1400px; margin: 0 auto; padding: 16px 20px 140px; }}
    .layout-split {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 0.95fr); gap: 16px; align-items: start; }}
    @media (max-width: 1100px) {{ .layout-split {{ grid-template-columns: 1fr; }} }}
    .preview-pane {{ position: sticky; top: 78px; }}
    .preview-frame {{ position: relative; background: #0b1016; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }}
    .preview-frame img {{ display: block; width: 100%; height: auto; }}
    #hlLayer {{ position: absolute; inset: 0; }}
    .hl {{ position: absolute; border: 2px solid #f59e0b; background: rgba(245,158,11,.28); border-radius: 2px; pointer-events: none; }}
    .hl.active {{ border-color: #3b82f6; background: rgba(59,130,246,.32); }}
    .disc-card {{ border-left: 3px solid var(--warn); }}
    .disc-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }}
    .disc-side {{ background: #101722; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }}
    .disc-side button {{ margin-top: 6px; }}
    .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; margin-bottom: 12px; }}
    .card.fund-self {{ border-color: #92400e; }}
    .hero-todo {{ border-color: #3b82f6; }}
    .extraction-gate {{ border-color: #f59e0b; background: #1f1808; }}
    .step-nav li.blocked {{ border-color: #92400e; color: #fcd34d; }}
    .step-nav li.blocked .n {{ background: #92400e; }}
    .todo-zone {{ margin: 18px 0; }}
    .auto-zone {{ margin: 10px 0 24px; padding: 10px 12px; border: 1px dashed var(--border); border-radius: 10px; color: var(--muted); }}
    .auto-summary {{ font-size: 13px; margin-bottom: 6px; }}
    .promote-zone {{ border-style: solid; border-color: var(--accent); margin-top: 28px; opacity: 1; }}
    .success-zone {{ border-style: solid; border-color: #166534; background: #102218; margin-top: 28px; }}
    .success-zone h2 {{ color: #86efac; }}
    input#reviewerName {{ display:block; width:min(420px,100%); margin:8px 0 12px; background:#101722; color:var(--text); border:1px solid var(--border); border-radius:8px; padding:10px 12px; font-size:14px; }}
    .step-nav {{ display:flex; gap:8px; flex-wrap:wrap; margin: 10px 0 0; padding: 0; list-style:none; }}
    .step-nav li {{ flex:1 1 140px; min-width:120px; background:#121a24; border:1px solid var(--border); border-radius:10px; padding:8px 10px; color:var(--muted); font-size:12px; }}
    .step-nav li .n {{ display:inline-block; width:18px; height:18px; line-height:18px; text-align:center; border-radius:999px; background:#243044; color:var(--text); margin-right:6px; font-size:11px; }}
    .step-nav li.done {{ border-color:#166534; color:#86efac; }}
    .step-nav li.done .n {{ background:#166534; }}
    .step-nav li.current {{ border-color:var(--accent); color:var(--text); box-shadow:0 0 0 1px var(--accent) inset; }}
    .step-nav li.current .n {{ background:var(--accent); }}
    .next-action {{ margin-top:12px; padding:12px 14px; border-radius:10px; background:#132033; border:1px solid var(--accent); }}
    .next-action strong {{ display:block; margin-bottom:4px; }}
    .callout {{ margin-top: 8px; padding: 10px; border-radius: 8px; background: #2a1f12; border: 1px solid #92400e; }}
    .row {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-start; }}
    .grow {{ flex: 1 1 280px; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }}
    button, .btn {{ appearance: none; border: 1px solid var(--border); background: #243044; color: var(--text); border-radius: 8px; padding: 8px 12px; cursor: pointer; font-size: 13px; text-decoration: none; }}
    button.primary {{ background: var(--accent); border-color: var(--accent); color: white; }}
    button.danger {{ background: #3a1d1d; border-color: #7f1d1d; }}
    button:disabled {{ opacity: .45; cursor: not-allowed; }}
    .action-hint {{ color: var(--muted); font-size: 13px; margin: 0 0 10px; }}
    .action-panel {{ position: relative; z-index: 6; background: #121a24; border: 1px solid var(--border); border-radius: 10px; padding: 12px; }}
    .action-panel button {{ position: relative; z-index: 7; pointer-events: auto; cursor: pointer; }}
    #toast {{ display:none; position:fixed; top:16px; left:50%; transform:translateX(-50%); z-index:100; background:#166534; color:#fff; padding:10px 14px; border-radius:8px; font-size:13px; max-width:90%; }}
    #toast.err {{ background:#7f1d1d; }}
    .lang-switch button {{ padding: 4px 10px; }}
    .lang-switch button.active {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
    html[data-lang="en"] .lang-zh {{ display: none !important; }}
    html[data-lang="zh"] .lang-en {{ display: none !important; }}
    select {{ width: 100%; background: #101722; color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 8px; }}
    .pill {{ font-size: 12px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); }}
    .pill.auto {{ border-color: #166534; color: #86efac; }}
    .pill.review {{ border-color: #92400e; color: #fcd34d; }}
    .pill.unmapped {{ border-color: #7f1d1d; color: #fca5a5; }}
    .pair {{ display: grid; grid-template-columns: 1fr auto 1fr; gap: 8px; align-items: center; background: #101722; border: 1px solid var(--border); border-radius: 10px; padding: 10px; margin-top: 8px; }}
    .pair .arrow {{ color: var(--muted); font-size: 18px; }}
    .pair .side label {{ display:block; color: var(--muted); font-size: 11px; margin-bottom: 2px; }}
    .kv {{ display:flex; flex-wrap:wrap; gap:10px 16px; margin-top:8px; font-size:13px; color: var(--muted); }}
    .kv b {{ color: var(--text); font-weight: 600; }}
    details.help {{ margin-top: 8px; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; background: #121a24; }}
    details.muted-details {{ border-style: dashed; opacity: .9; }}
    details.help summary {{ cursor: pointer; color: #93c5fd; font-size: 13px; }}
    details.help .body {{ margin-top: 8px; color: var(--muted); font-size: 13px; }}
    table.amt {{ width:100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }}
    table.amt th, table.amt td {{ border-bottom: 1px solid var(--border); padding: 6px 4px; text-align: left; vertical-align: top; }}
    table.amt th {{ color: var(--muted); font-weight: 500; }}
    ul.plain {{ margin: 8px 0 0 18px; padding: 0; }}
    ul.plain.compact li {{ margin: 2px 0; font-size: 12px; }}
    ol.plain {{ margin: 8px 0 0 18px; }}
    .footer-note {{ margin-top: 24px; }}
    .sticky-bar {{ position: fixed; bottom: 0; left: 0; right: 0; z-index: 80; background: #121a24; border-top: 1px solid var(--border); padding: 12px 20px; display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; pointer-events: auto; }}
    .sticky-bar button, .sticky-bar .btn {{ position: relative; z-index: 81; pointer-events: auto; }}
    pre {{ white-space: pre-wrap; font-size: 12px; color: var(--muted); }}
    a {{ color: #93c5fd; }}
  </style>
</head>
<body>
<div id="toast"></div>
{body}
<script>
function setLang(lang) {{
  document.documentElement.setAttribute('data-lang', lang);
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  localStorage.setItem('mapping_review_lang', lang);
  const en = document.getElementById('langEn');
  const zh = document.getElementById('langZh');
  if (en) en.classList.toggle('active', lang === 'en');
  if (zh) zh.classList.toggle('active', lang === 'zh');
}}
(function() {{
  const saved = localStorage.getItem('mapping_review_lang') || 'en';
  setLang(saved);
}})();
function toast(msg, isErr) {{
  const el = document.getElementById('toast');
  if (!el) {{ alert(msg); return; }}
  el.textContent = msg;
  el.className = isErr ? 'err' : '';
  el.style.display = 'block';
  setTimeout(() => {{ el.style.display = 'none'; }}, 2500);
}}
async function postJSON(url, body) {{
  const res = await fetch(url, {{ method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(body || {{}}) }});
  const data = await res.json().catch(() => ({{}}));
  if (!res.ok) throw new Error(data.error || res.statusText || ('HTTP ' + res.status));
  return data;
}}
async function decide(pdf, action, vendor) {{
  toast((document.documentElement.getAttribute('data-lang') === 'zh') ? '提交中…' : 'Saving…');
  await postJSON('/api/decide', {{ pdf_company_name: pdf, action: action, vendor_source_asset: vendor || null }});
  location.reload();
}}
async function acceptRecommended(pdf, vendor) {{ await decide(pdf, 'accept', vendor); }}
async function bulkAccept() {{ await postJSON('/api/bulk-accept-defaults', {{}}); location.reload(); }}
async function runCompare() {{
  const btn = document.getElementById('compareBtn');
  const zh = document.documentElement.getAttribute('data-lang') === 'zh';
  toast(zh ? '正在核对 Cost / Fair Value…（约几秒）' : 'Checking Cost / Fair Value… (a few seconds)');
  if (btn) {{ btn.disabled = true; btn.textContent = zh ? '核对中…' : 'Checking…'; }}
  try {{
    const data = await postJSON('/api/compare', {{}});
    const ok = data.comparability_status === 'comparable';
    toast(zh
      ? (ok ? '金额可以对上了，请保存名称对应。' : ('还不能保存：' + (data.comparability_status || '未知')))
      : (ok ? 'Amounts can be compared — please save name matches.' : ('Not ready yet: ' + (data.comparability_status || 'unknown'))));
    location.reload();
  }} catch (e) {{
    toast((zh ? '核对失败: ' : 'Check failed: ') + e.message, true);
    if (btn) {{
      btn.disabled = false;
      btn.textContent = zh ? '再核对一次 Cost / Fair Value' : 'Re-check Cost / Fair Value';
    }}
  }}
}}
async function promote() {{
  const zh = document.documentElement.getAttribute('data-lang') === 'zh';
  const input = document.getElementById('reviewerName');
  let reviewer = (input && input.value ? input.value : '').trim();
  if (!reviewer) {{
    reviewer = (window.prompt(zh ? '请输入你的名字后再保存' : 'Enter your name to save') || '').trim();
  }}
  if (!reviewer) {{
    toast(zh ? '请先填写名字' : 'Please enter your name first', true);
    if (input) input.focus();
    return;
  }}
  toast(zh ? '正在保存…' : 'Saving…');
  try {{
    await postJSON('/api/promote', {{ reviewer: reviewer, confirm: true }});
    toast(zh ? '保存成功' : 'Saved');
    location.reload();
  }} catch (e) {{
    toast((zh ? '保存失败: ' : 'Save failed: ') + e.message, true);
  }}
}}
async function ackExtract(action) {{
  const zh = document.documentElement.getAttribute('data-lang') === 'zh';
  toast(zh ? '保存中…' : 'Saving…');
  await postJSON('/api/ack-extraction', {{ action: action }});
  toast(zh ? '已记录。可以关掉这个网页。' : 'Saved. You can close this tab.');
  location.reload();
}}
async function decideDiscrepancy(itemId, action) {{
  const zh = document.documentElement.getAttribute('data-lang') === 'zh';
  toast(zh ? '保存金额决定…' : 'Saving amount decision…');
  await postJSON('/api/decide-discrepancy', {{ item_id: itemId, action: action }});
  toast(zh ? '已写入已审核 Excel（未改原始 Excel）' : 'Saved to reviewed Excel (vendor file untouched)');
  location.reload();
}}
function showPreview(page, itemId) {{
  const img = document.getElementById('previewImg');
  const layer = document.getElementById('hlLayer');
  if (!img || !page) return;
  img.src = '/api/preview?page=' + encodeURIComponent(page) + '&t=' + Date.now();
  fetch('/api/preview-meta?page=' + encodeURIComponent(page) + (itemId ? ('&item_id=' + encodeURIComponent(itemId)) : ''))
    .then(r => r.json())
    .then(data => {{
      if (!layer) return;
      layer.innerHTML = '';
      (data.highlights || []).forEach(h => {{
        const n = h.norm || [];
        if (n.length < 4) return;
        const el = document.createElement('div');
        el.className = 'hl' + ((itemId && h.item_id === itemId) ? ' active' : '');
        el.style.left = (n[0] * 100) + '%';
        el.style.top = (n[1] * 100) + '%';
        el.style.width = ((n[2] - n[0]) * 100) + '%';
        el.style.height = ((n[3] - n[1]) * 100) + '%';
        el.title = (h.label || '') + ' ' + (h.text || '');
        layer.appendChild(el);
      }});
    }})
    .catch(() => {{}});
}}
document.addEventListener('click', async (ev) => {{
  const btn = ev.target && ev.target.closest ? ev.target.closest('[data-action]') : null;
  if (!btn) return;
  ev.preventDefault();
  const action = btn.getAttribute('data-action');
  try {{
    if (action === 'decide') {{
      await decide(btn.getAttribute('data-pdf'), btn.getAttribute('data-decide'), null);
    }} else if (action === 'accept') {{
      const sel = document.getElementById(btn.getAttribute('data-sel') || '');
      const vendor = (sel && sel.value) || btn.getAttribute('data-fallback') || '';
      await acceptRecommended(btn.getAttribute('data-pdf'), vendor);
    }} else if (action === 'bulk') {{
      await bulkAccept();
    }} else if (action === 'compare') {{
      await runCompare();
    }} else if (action === 'promote') {{
      await promote();
    }} else if (action === 'ack-extract') {{
      await ackExtract(btn.getAttribute('data-ack') || 'needs_engineer');
    }} else if (action === 'decide-disc') {{
      await decideDiscrepancy(btn.getAttribute('data-item'), btn.getAttribute('data-decide'));
    }} else if (action === 'preview') {{
      showPreview(btn.getAttribute('data-page'), btn.getAttribute('data-item'));
    }}
  }} catch (e) {{
    toast(String(e.message || e), true);
  }}
}});
(function() {{
  const boot = document.getElementById('previewBoot');
  if (boot) {{
    const page = boot.getAttribute('data-page');
    const item = boot.getAttribute('data-item') || null;
    if (page) showPreview(page, item);
  }}
}})();
</script>
</body>
</html>
"""
    return doc.encode("utf-8")


def _esc(value: Any) -> str:
    from pdf_validation.review_ui_render import esc
    return esc(value)


def _render_index(payload: dict[str, Any], promote_preview: dict[str, Any] | None) -> bytes:
    from pdf_validation.review_ui_render import render_index_body

    return _html_page("Holdings name review", render_index_body(payload, promote_preview))


class _ReviewHandler(BaseHTTPRequestHandler):
    extraction_dir: Path
    vendor_csv: Path
    fund_id: str
    as_of_date: str | None
    repo_root: Path
    pkg_root: Path

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            payload = review_payload(
                extraction_dir=self.extraction_dir,
                vendor_csv=self.vendor_csv,
                fund_id=self.fund_id,
                as_of_date=self.as_of_date,
            )
            preview = None
            if payload.get("promote_ready"):
                try:
                    preview = preview_promote_diff(
                        extraction_dir=self.extraction_dir,
                        fund_id=self.fund_id,
                        pkg_root=self.pkg_root,
                    )
                except Exception as exc:  # noqa: BLE001
                    preview = {"error": str(exc)}
            raw = _render_index(payload, preview)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if parsed.path == "/api/payload":
            payload = review_payload(
                extraction_dir=self.extraction_dir,
                vendor_csv=self.vendor_csv,
                fund_id=self.fund_id,
                as_of_date=self.as_of_date,
            )
            self._json(200, payload)
            return
        if parsed.path == "/api/review-items":
            from pdf_validation.discrepancy_review import build_review_items_payload

            self._json(200, build_review_items_payload(extraction_dir=self.extraction_dir, fund_id=self.fund_id))
            return
        if parsed.path in {"/api/preview", "/api/preview-meta"}:
            qs = parse_qs(parsed.query or "")
            try:
                page = int((qs.get("page") or ["1"])[0])
            except ValueError:
                self._json(400, {"error": "invalid page"})
                return
            route_path = self.extraction_dir / "route.json"
            route = json.loads(route_path.read_text(encoding="utf-8")) if route_path.exists() else {}
            pdf_path = resolve_pdf_path(self.extraction_dir, route)
            if pdf_path is None or not pdf_path.exists():
                self._json(404, {"error": "pdf_not_found"})
                return
            # Security: only serve the PDF bound to this extraction_dir.
            if parsed.path == "/api/preview":
                cache = self.extraction_dir / "mapping_review" / "preview_cache"
                cache.mkdir(parents=True, exist_ok=True)
                out = cache / f"page-{page:04d}.png"
                try:
                    render_preview_page_png(pdf_path=pdf_path, page_number=page, out_path=out, dpi=144)
                except Exception as exc:  # noqa: BLE001
                    self._json(400, {"error": str(exc)})
                    return
                raw = out.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return
            # preview-meta: only highlight the active discrepancy (never the whole page)
            payload = review_payload(
                extraction_dir=self.extraction_dir,
                vendor_csv=self.vendor_csv,
                fund_id=self.fund_id,
                as_of_date=self.as_of_date,
            )
            item_id = (qs.get("item_id") or [None])[0]
            items = list(payload.get("discrepancy_items") or [])
            pending = [i for i in items if i.get("needs_action")]
            if not item_id and pending:
                item_id = pending[0].get("item_id")
            highlights = []
            for item in items:
                if item_id and item.get("item_id") != item_id:
                    continue
                if not item_id:
                    # No focus item → no highlights (avoid painting the whole schedule)
                    break
                ev = item.get("evidence") or {}
                # Only draw uniquely located boxes for this one field.
                if ev.get("status") != "located":
                    continue
                for h in ev.get("highlights") or []:
                    if int(h.get("page") or 0) == page:
                        hh = dict(h)
                        hh["item_id"] = item.get("item_id")
                        highlights.append(hh)
                break
            self._json(
                200,
                {
                    "page": page,
                    "item_id": item_id,
                    "highlights": highlights[:3],
                    "pdf_name": pdf_path.name,
                },
            )
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            body = self._read_json()
            if parsed.path == "/api/decide":
                state = upsert_decision(
                    self.extraction_dir,
                    pdf_company_name=str(body.get("pdf_company_name") or ""),
                    action=str(body.get("action") or ""),
                    vendor_source_asset=body.get("vendor_source_asset"),
                    notes=body.get("notes"),
                )
                self._json(200, {"ok": True, "review_state": state})
                return
            if parsed.path == "/api/ack-extraction":
                state = acknowledge_extraction_gate(
                    self.extraction_dir,
                    action=str(body.get("action") or "needs_engineer"),
                    notes=body.get("notes"),
                    reviewer=body.get("reviewer"),
                )
                self._json(200, {"ok": True, "review_state": state})
                return
            if parsed.path == "/api/decide-discrepancy":
                item_id = str(body.get("item_id") or "")
                action = str(body.get("action") or "")
                state = upsert_discrepancy_decision(
                    self.extraction_dir,
                    item_id=item_id,
                    action=action,
                    final_value=body.get("final_value"),
                    notes=body.get("notes"),
                    reviewer=body.get("reviewer") or "reviewer",
                )
                excel_meta = write_reviewed_extraction_excel(
                    extraction_dir=self.extraction_dir,
                    fund_id=self.fund_id,
                )
                self._json(200, {"ok": True, "review_state": state, "reviewed_excel": excel_meta})
                return
            if parsed.path == "/api/bulk-accept-defaults":
                payload = review_payload(
                    extraction_dir=self.extraction_dir,
                    vendor_csv=self.vendor_csv,
                    fund_id=self.fund_id,
                    as_of_date=self.as_of_date,
                )
                for ent in payload.get("entities") or []:
                    if ent.get("status") == "auto_accept" and not ent.get("user_decision"):
                        upsert_decision(
                            self.extraction_dir,
                            pdf_company_name=ent["pdf_company_name"],
                            action="accept",
                            vendor_source_asset=ent.get("recommended_vendor_source_asset"),
                        )
                self._json(200, {"ok": True})
                return
            if parsed.path == "/api/compare":
                summary = run_draft_compare(
                    extraction_dir=self.extraction_dir,
                    vendor_csv=self.vendor_csv,
                    fund_id=self.fund_id,
                    as_of_date=self.as_of_date,
                    repo_root=self.repo_root,
                    pkg_root=self.pkg_root,
                )
                self._json(200, summary)
                return
            if parsed.path == "/api/promote":
                if not body.get("confirm"):
                    self._json(400, {"error": "confirm required"})
                    return
                result = promote_draft_mapping(
                    extraction_dir=self.extraction_dir,
                    fund_id=self.fund_id,
                    reviewer=str(body.get("reviewer") or "anonymous"),
                    pkg_root=self.pkg_root,
                    confirm=True,
                )
                self._json(200, result)
                return
            self._json(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": str(exc)})


def serve_mapping_review(
    *,
    extraction_dir: Path,
    vendor_csv: Path,
    fund_id: str,
    as_of_date: str | None = None,
    repo_root: Path | None = None,
    pkg_root: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> ThreadingHTTPServer:
    """Start review server on localhost and optionally open the browser."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("mapping review server must bind to localhost")

    # Rebuild proposal and amount comparison from the current extraction before serving.
    # The review UI reads mapping_review/compare/amount_comparisons.jsonl; reusing an
    # older file after a PDF re-extract would otherwise display stale PDF amounts.
    run_draft_compare(
        extraction_dir=extraction_dir,
        vendor_csv=vendor_csv,
        fund_id=fund_id,
        as_of_date=as_of_date,
        repo_root=repo_root,
        pkg_root=pkg_root,
        rebuild_proposal=True,
    )
    review_payload(
        extraction_dir=extraction_dir,
        vendor_csv=vendor_csv,
        fund_id=fund_id,
        as_of_date=as_of_date,
    )

    handler = type(
        "BoundReviewHandler",
        (_ReviewHandler,),
        {
            "extraction_dir": Path(extraction_dir),
            "vendor_csv": Path(vendor_csv),
            "fund_id": fund_id,
            "as_of_date": as_of_date,
            "repo_root": Path(repo_root) if repo_root else Path(__file__).resolve().parents[3],
            "pkg_root": Path(pkg_root) if pkg_root else Path(__file__).resolve().parents[2],
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    bound_host, bound_port = server.server_address
    url = f"http://{bound_host}:{bound_port}/"
    # Write pid/url so callers can confirm the process is alive.
    try:
        pid_path = Path(extraction_dir) / "mapping_review" / "review_server.json"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(
            json.dumps({"pid": os.getpid(), "url": url, "host": bound_host, "port": bound_port}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    if open_browser:
        # Delay slightly so the listen socket is fully ready before Safari connects.
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"Mapping review UI: {url}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    return server
