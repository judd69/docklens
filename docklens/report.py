"""Output renderers: coloured text, JSON, self-contained HTML report."""

from __future__ import annotations

import html
import json

COLORS = {
    "critical": "\033[91m", "warning": "\033[93m", "info": "\033[94m",
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "green": "\033[92m",
}


def render_text(analysis, path, use_color=True):
    def c(name):
        return COLORS[name] if use_color else ""

    lines = []
    lines.append(f"{c('bold')}DockLens report: {path}{c('reset')}")
    lines.append(
        f"Score {analysis.score}/100 (grade {analysis.grade})  "
        f"Verdict: {analysis.verdict}  "
        f"critical {analysis.counts['critical']}  "
        f"warning {analysis.counts['warning']}  "
        f"info {analysis.counts['info']}")
    if not analysis.findings:
        lines.append(f"{c('green')}No findings — this Dockerfile is clean."
                     f"{c('reset')}")
        return "\n".join(lines) + "\n"
    for f in analysis.findings:
        lines.append(
            f"{c(f.severity)}[{f.severity.upper()}] {f.rule} "
            f"(line {f.line}){c('reset')} {f.title}")
        lines.append(f"    {f.detail}")
        lines.append(f"    {c('dim')}Fix: {f.fix}{c('reset')}")
    return "\n".join(lines) + "\n"


def render_json(analysis, path):
    data = {"file": path}
    data.update(analysis.as_dict())
    return json.dumps(data, indent=2) + "\n"


_SEV_COLOR = {"critical": "#FF4D4D", "warning": "#F5A623", "info": "#2496ED"}


def render_html(analysis, path, source_lines):
    """Single self-contained HTML page: inline CSS + JS, no external assets."""
    esc = html.escape
    rows = []
    for f in analysis.findings:
        rows.append(
            f'<tr data-sev="{f.severity}" data-line="{f.line}">'
            f"<td><span class=\"pill {f.severity}\">{f.severity}</span></td>"
            f"<td>{esc(f.rule)}</td><td>{f.line}</td>"
            f"<td><strong>{esc(f.title)}</strong><br>"
            f"<span class=\"muted\">{esc(f.detail)}</span><br>"
            f"<span class=\"fix\">Fix: {esc(f.fix)}</span></td></tr>")
    findings_html = "\n".join(rows) if rows else (
        "<tr><td colspan=\"4\">No findings — this Dockerfile is clean.</td></tr>")
    src_html = []
    finding_lines = {f.line for f in analysis.findings}
    for n, line in enumerate(source_lines, 1):
        cls = " class=\"flagged\"" if n in finding_lines else ""
        src_html.append(
            f"<div{cls} id=\"line-{n}\"><span class=\"ln\">{n}</span>"
            f"{esc(line) if line else '&nbsp;'}</div>")
    score = analysis.score
    circ = 2 * 3.14159 * 52
    offset = circ * (1 - score / 100.0)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DockLens report — {esc(path)}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#0A0A0B;color:#F5F5F4;
font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1060px;margin:0 auto;padding:40px 24px 80px}}
h1{{font-size:40px;letter-spacing:-.02em;margin:8px 0 4px}}p.muted,.muted{{color:#A1A1AA}}
.badge{{display:inline-block;background:#1A1A1E;border:1px solid #2A2A30;
border-radius:999px;padding:6px 14px;font-size:13px;color:#A1A1AA}}
.cards{{display:flex;gap:16px;flex-wrap:wrap;margin:28px 0}}
.card{{background:#1A1A1E;border:1px solid #2A2A30;border-radius:20px;padding:20px 24px;flex:1;min-width:180px}}
.card b{{font-size:32px;display:block}}.card span{{color:#A1A1AA;font-size:13px}}
.gauge{{display:flex;align-items:center;gap:24px;background:#1A1A1E;
border:1px solid #2A2A30;border-radius:24px;padding:24px 28px;margin:28px 0}}
.gauge svg{{flex:none}} .gauge .num{{font-size:56px;font-weight:800;line-height:1}}
.gauge .verdict{{font-size:20px;font-weight:700}}
.chips button{{background:#1A1A1E;color:#F5F5F4;border:1px solid #2A2A30;
border-radius:999px;padding:8px 16px;margin:0 8px 8px 0;cursor:pointer;font-size:14px}}
.chips button.active{{background:#2496ED;border-color:#2496ED}}
table{{width:100%;border-collapse:collapse;margin-top:16px}}
th{{text-align:left;color:#A1A1AA;font-size:13px;text-transform:uppercase;
letter-spacing:.06em;padding:10px 12px;border-bottom:1px solid #2A2A30}}
td{{padding:12px;border-bottom:1px solid #232329;vertical-align:top;font-size:15px}}
.pill{{display:inline-block;border-radius:999px;padding:2px 10px;font-size:12px;
font-weight:700;text-transform:uppercase}}
.pill.critical{{background:#3D1214;color:#FF8A8A}}
.pill.warning{{background:#3A2A08;color:#F5C86B}}
.pill.info{{background:#0E2A42;color:#7CC0F5}}
.fix{{color:#7CC0F5}}
pre.src{{background:#111113;border:1px solid #2A2A30;border-radius:20px;
padding:20px;overflow:auto;font-size:13.5px;line-height:1.7;white-space:pre}}
pre.src div{{padding:0 8px;border-radius:6px;white-space:pre-wrap;word-break:break-all}}
pre.src div.flagged{{background:#3D1214}}
pre.src .ln{{color:#71717A;display:inline-block;width:32px;text-align:right;margin-right:12px}}
a{{color:#7CC0F5}}
</style></head><body><div class="wrap">
<span class="badge">DockLens report</span>
<h1>{esc(path)}</h1>
<p class="muted">Score, verdict and every finding, generated by DockLens.</p>
<div class="gauge">
<svg width="140" height="140" viewBox="0 0 140 140">
<circle cx="70" cy="70" r="52" fill="none" stroke="#232329" stroke-width="12"/>
<circle cx="70" cy="70" r="52" fill="none" stroke="#2496ED" stroke-width="12"
stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{offset:.1f}"
transform="rotate(-90 70 70)"/></svg>
<div><div class="num">{score}<span style="font-size:24px;color:#A1A1AA">/100</span></div>
<div class="verdict">Grade {analysis.grade} — {analysis.verdict}</div>
<p class="muted" style="margin:6px 0 0">Stages: {", ".join(s.base_image or "?" for s in analysis.stages)}</p></div>
</div>
<div class="cards">
<div class="card"><b>{analysis.counts['critical']}</b><span>critical</span></div>
<div class="card"><b>{analysis.counts['warning']}</b><span>warnings</span></div>
<div class="card"><b>{analysis.counts['info']}</b><span>info</span></div>
<div class="card"><b>{len(analysis.findings)}</b><span>total findings</span></div>
</div>
<div class="chips">
<button class="active" data-filter="all">All</button>
<button data-filter="critical">Critical</button>
<button data-filter="warning">Warnings</button>
<button data-filter="info">Info</button>
</div>
<table><thead><tr><th>Severity</th><th>Rule</th><th>Line</th><th>Finding</th></tr></thead>
<tbody id="rows">{findings_html}</tbody></table>
<h2 style="margin-top:48px">Source, with flagged lines</h2>
<pre class="src" id="src">{"".join(src_html)}</pre>
<p class="muted">Generated by DockLens — a zero-dependency Dockerfile auditor. Repo: github.com/judd69/docklens</p>
</div>
<script>
document.querySelectorAll(".chips button").forEach(function(btn){{
  btn.addEventListener("click", function(){{
    document.querySelectorAll(".chips button").forEach(function(b){{b.classList.remove("active")}});
    btn.classList.add("active");
    var f = btn.getAttribute("data-filter");
    document.querySelectorAll("#rows tr").forEach(function(tr){{
      tr.style.display = (f === "all" || tr.getAttribute("data-sev") === f) ? "" : "none";
    }});
  }});
}});
document.querySelectorAll("#rows tr").forEach(function(tr){{
  tr.style.cursor = "pointer";
  tr.addEventListener("click", function(){{
    var el = document.getElementById("line-" + tr.getAttribute("data-line"));
    if (el) el.scrollIntoView({{behavior: "smooth", block: "center"}});
  }});
}});
</script></body></html>
"""
