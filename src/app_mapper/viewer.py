from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app_mapper.artifacts import write_json
from app_mapper.graph import graph_summary
from app_mapper.identity import visual_similarity
from app_mapper.models import NodeRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest(root: Path, filename: str) -> dict[str, Any] | None:
    paths = sorted(root.glob(f"*/{filename}")) if root.is_dir() else []
    for path in reversed(paths):
        try:
            return _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _image_data_uri(path_value: str) -> str | None:
    path = Path(path_value)
    if not path.is_file():
        return None
    mime = "image/png" if path.suffix.casefold() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _duplicate_candidates(nodes: list[NodeRecord]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, left in enumerate(nodes):
        for right in nodes[index + 1 :]:
            if left.state_type != right.state_type:
                continue
            structural = bool(
                set(left.fingerprints.structural_variants)
                & set(right.fingerprints.structural_variants)
            )
            semantic = bool(
                set(left.fingerprints.semantic_variants)
                & set(right.fingerprints.semantic_variants)
            )
            visual = max(
                visual_similarity(a, b)
                for a in left.fingerprints.visual_variants
                for b in right.fingerprints.visual_variants
            )
            score = 0.5 * float(structural) + 0.25 * float(semantic) + 0.25 * visual
            if structural or semantic or score >= 0.72:
                candidates.append(
                    {
                        "left": left.node_id,
                        "right": right.node_id,
                        "score": round(score, 3),
                        "reasons": [
                            reason
                            for matched, reason in (
                                (structural, "shared structural fingerprint"),
                                (semantic, "shared semantic fingerprint"),
                                (visual >= 0.9, f"visual similarity {visual:.0%}"),
                            )
                            if matched
                        ],
                    }
                )
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def build_viewer_data(
    graph_root: Path,
    registry_root: Path,
    discovery_root: Path,
    exploration_root: Path,
    validation_root: Path,
) -> dict[str, Any]:
    graph = graph_summary(graph_root, registry_root)
    records: list[NodeRecord] = []
    for summary in graph.nodes:
        path = registry_root / summary.node_id / "node.json"
        if path.is_file():
            records.append(NodeRecord.model_validate(_read_json(path)))

    nodes = []
    for node in records:
        payload = node.model_dump(mode="json")
        payload["screenshot_data_uri"] = _image_data_uri(node.representative_screenshot)
        nodes.append(payload)

    edges = []
    for edge in graph.edges:
        payload = edge.model_dump(mode="json")
        attempts = edge.replay.attempts
        payload["success_rate"] = (
            round(edge.replay.successes / attempts, 6) if attempts else None
        )
        payload["confidence"] = (
            "high"
            if attempts and edge.replay.successes == attempts
            else "low"
            if edge.replay.failures
            else "recorded"
        )
        edges.append(payload)

    discovery = _latest(discovery_root, "discovery.json")
    exploration = _latest(exploration_root, "state.json")
    validation = _latest(validation_root, "validation.json")
    rejected = []
    if discovery:
        rejected = [
            candidate
            for candidate in discovery.get("candidates", [])
            if candidate.get("policy_decision") == "rejected"
            or candidate.get("status") in {"failed", "skipped"}
        ]
    unexplored = []
    if exploration:
        unexplored = [
            task
            for task in exploration.get("queue", [])
            if task.get("status") in {"queued", "failed", "skipped"}
        ]
    failed_edges = [
        edge
        for edge in edges
        if edge["replay"]["failures"] or edge["replay"]["refused_wrong_source"]
    ]
    latest_results = validation.get("results", []) if validation else []
    failed_validations = [r for r in latest_results if r.get("status") != "success"]
    attempts = sum(edge["replay"]["attempts"] for edge in edges)
    successes = sum(edge["replay"]["successes"] for edge in edges)
    registry_count = len(list(registry_root.glob("node-*/node.json")))
    approved = (
        [c for c in discovery.get("candidates", []) if c.get("policy_decision") == "approved"]
        if discovery
        else []
    )
    explored_approved = [c for c in approved if c.get("status") == "succeeded"]
    return {
        "generated_at": _now(),
        "nodes": nodes,
        "edges": edges,
        "duplicate_candidates": _duplicate_candidates(records),
        "failed_edges": failed_edges,
        "failed_validations": failed_validations,
        "rejected_controls": rejected,
        "unexplored_controls": unexplored,
        "latest_validation": validation,
        "metrics": {
            "mapped_nodes": len(nodes),
            "registry_nodes": registry_count,
            "node_coverage": round(len(nodes) / registry_count, 6) if registry_count else 0,
            "directed_edges": len(edges),
            "recorded_edges": sum(bool(edge["evidence"]) for edge in edges),
            "replay_attempts": attempts,
            "replay_successes": successes,
            "replay_reliability": round(successes / attempts, 6) if attempts else None,
            "approved_controls": len(approved),
            "explored_approved_controls": len(explored_approved),
            "control_coverage": (
                round(len(explored_approved) / len(approved), 6) if approved else None
            ),
        },
    }


def generate_viewer(
    output_root: Path,
    graph_root: Path,
    registry_root: Path,
    discovery_root: Path,
    exploration_root: Path,
    validation_root: Path,
) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    data = build_viewer_data(
        graph_root, registry_root, discovery_root, exploration_root, validation_root
    )
    write_json(output_root / "viewer-data.json", data)
    encoded = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    document = _HTML.replace("__VIEWER_DATA__", encoded)
    destination = output_root / "index.html"
    destination.write_text(document, encoding="utf-8")
    return destination


_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenVSP Navigation Graph</title>
<style>
:root{color-scheme:dark;--bg:#101419;--panel:#192028;--line:#34414e;--text:#ecf2f8;--muted:#98a9b9;--blue:#61b5ff;--green:#5ddd9d;--amber:#ffc766;--red:#ff7383}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 ui-sans-serif,system-ui,sans-serif}header{padding:24px 28px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:24px;align-items:end}h1,h2,h3,p{margin:0}h1{font-size:22px}.muted{color:var(--muted)}main{padding:20px;display:grid;gap:18px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px}.metric,.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px}.metric{padding:14px}.metric strong{display:block;font-size:23px}.workspace{display:grid;grid-template-columns:minmax(500px,1.5fr) minmax(320px,1fr);gap:18px}.panel{padding:16px;overflow:hidden}.panel h2{font-size:16px;margin-bottom:12px}#graph{width:100%;height:570px;background:#121820;border-radius:8px}.edge{stroke:#718394;stroke-width:1.7;fill:none;opacity:.72}.edge.bad{stroke:var(--red)}.node{cursor:pointer}.node circle{fill:#263342;stroke:var(--blue);stroke-width:2}.node.workspace circle{fill:#173650;stroke:#8dcaff}.node.dialog circle{stroke:var(--amber)}.node text{fill:var(--text);font-size:11px;text-anchor:middle;pointer-events:none}.node:hover circle{stroke-width:4}.badge{display:inline-block;padding:2px 7px;border-radius:99px;background:#293643;color:#cbd8e4;font-size:12px}.good{color:var(--green)}.warn{color:var(--amber)}.bad-text{color:var(--red)}#detail{min-height:570px;overflow:auto}#detail img{width:100%;max-height:260px;object-fit:contain;background:#0c1014;border-radius:7px;margin:12px 0}dl{display:grid;grid-template-columns:115px 1fr;gap:7px 10px}dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}pre{white-space:pre-wrap;word-break:break-word;background:#10151b;padding:10px;border-radius:7px;font-size:12px}.lists{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:18px}.item{padding:10px 0;border-top:1px solid var(--line)}.item:first-of-type{border-top:0}.empty{color:var(--muted);font-style:italic}@media(max-width:900px){.workspace{grid-template-columns:1fr}#graph{height:480px}}
</style></head><body>
<header><div><h1>OpenVSP Navigation Graph</h1><p class="muted">Directed, evidence-backed UI states and safe navigation actions</p></div><span id="generated" class="muted"></span></header>
<main><section id="metrics" class="metrics"></section><section class="workspace"><div class="panel"><h2>Map <span class="muted">— select a node or edge</span></h2><svg id="graph" viewBox="0 0 900 570" role="img" aria-label="Directed OpenVSP navigation graph"></svg></div><aside id="detail" class="panel"><h2>Selection details</h2><p class="muted">Select a state in the map.</p></aside></section><section id="lists" class="lists"></section></main>
<script id="data" type="application/json">__VIEWER_DATA__</script><script>
const D=JSON.parse(document.getElementById('data').textContent),$=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
$('#generated').textContent='Generated '+new Date(D.generated_at).toLocaleString();
const fmt=v=>v==null?'—':typeof v==='number'&&v<=1?(v*100).toFixed(0)+'%':v;
const metricLabels={mapped_nodes:'Mapped states',directed_edges:'Directed edges',replay_reliability:'Replay reliability',replay_attempts:'Replay attempts',control_coverage:'Control coverage',recorded_edges:'Evidence-backed edges'};
$('#metrics').innerHTML=Object.entries(metricLabels).map(([k,l])=>`<div class="metric"><span class="muted">${l}</span><strong>${fmt(D.metrics[k])}</strong></div>`).join('');
const svg=$('#graph'),NS='http://www.w3.org/2000/svg';svg.innerHTML='<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#718394"/></marker></defs>';
const center=D.nodes.find(n=>n.state_type==='workspace')||D.nodes[0],others=D.nodes.filter(n=>n!==center),pos={};if(center)pos[center.node_id]=[450,285];others.forEach((n,i)=>{const a=(Math.PI*2*i/Math.max(others.length,1))-Math.PI/2;pos[n.node_id]=[450+315*Math.cos(a),285+215*Math.sin(a)]});
function el(name,attrs={}){const x=document.createElementNS(NS,name);Object.entries(attrs).forEach(([k,v])=>x.setAttribute(k,v));return x}
D.edges.forEach(e=>{const a=pos[e.source_node_id],b=pos[e.destination_node_id];if(!a||!b)return;const dx=b[0]-a[0],dy=b[1]-a[1],len=Math.hypot(dx,dy)||1,ox=dx/len*47,oy=dy/len*47;const p=el('path',{d:`M ${a[0]+ox} ${a[1]+oy} L ${b[0]-ox} ${b[1]-oy}`,'marker-end':'url(#arrow)',class:'edge '+(e.replay.failures?'bad':'')});p.style.cursor='pointer';p.onclick=()=>showEdge(e);svg.appendChild(p)});
D.nodes.forEach(n=>{const [x,y]=pos[n.node_id],g=el('g',{class:'node '+n.state_type,transform:`translate(${x} ${y})`});g.appendChild(el('circle',{r:43}));const label=el('text',{y:-2});label.textContent=n.semantic_name.length>19?n.semantic_name.slice(0,18)+'…':n.semantic_name;g.appendChild(label);const type=el('text',{y:15});type.textContent=n.state_type;type.setAttribute('fill','#98a9b9');g.appendChild(type);g.onclick=()=>showNode(n);svg.appendChild(g)});
function showNode(n){$('#detail').innerHTML=`<h2>${esc(n.semantic_name)}</h2><span class="badge">${esc(n.state_type)}</span>${n.screenshot_data_uri?`<img src="${n.screenshot_data_uri}" alt="Screenshot of ${esc(n.semantic_name)}">`:'<p class="empty">Screenshot unavailable</p>'}<p>${esc(n.semantic_description)}</p><dl><dt>Node ID</dt><dd>${esc(n.node_id)}</dd><dt>Semantic source</dt><dd>${esc(n.semantic_source)}</dd><dt>Observations</dt><dd>${n.observation_count}</dd><dt>First seen</dt><dd>${esc(n.first_seen)}</dd><dt>Last seen</dt><dd>${esc(n.last_seen)}</dd><dt>Controls</dt><dd>${esc(JSON.stringify(n.interactive_control_summary))}</dd></dl>`}
function showEdge(e){const rate=e.success_rate==null?'not tested':fmt(e.success_rate);$('#detail').innerHTML=`<h2>${esc(e.action.semantic_description)}</h2><span class="badge">${esc(e.action.risk)}</span> <span class="badge">confidence: ${esc(e.confidence)}</span><dl><dt>Edge ID</dt><dd>${esc(e.edge_id)}</dd><dt>Direction</dt><dd>${esc(e.source_node_id)} → ${esc(e.destination_node_id)}</dd><dt>Mechanism</dt><dd>${esc(e.action.mechanism)}</dd><dt>Replay</dt><dd class="${e.replay.failures?'bad-text':'good'}">${e.replay.successes}/${e.replay.attempts} (${rate})</dd><dt>Evidence runs</dt><dd>${e.evidence.length}</dd><dt>Reverse action</dt><dd>${esc(e.action.reverse_action_key)}</dd></dl><h3>Locator</h3><pre>${esc(JSON.stringify(e.action.accessibility_locator,null,2))}</pre><h3>Preconditions</h3><pre>${esc(e.action.preconditions.join('\\n'))}</pre><h3>Expected result</h3><pre>${esc(e.action.expected_postconditions.join('\\n'))}</pre>`}
const blocks=[['Duplicate-node candidates',D.duplicate_candidates,x=>`${esc(x.left)} ↔ ${esc(x.right)} · ${(x.score*100).toFixed(0)}%<br><span class="muted">${esc(x.reasons.join(', '))}</span>`],['Failed transitions / validation', [...D.failed_edges,...D.failed_validations],x=>`${esc(x.edge_id||x.action?.action_key)} <span class="bad-text">${esc(x.status||((x.replay?.failures||0)+' failures'))}</span><br><span class="muted">${esc(x.error||'Inspect replay statistics')}</span>`],['Rejected controls',D.rejected_controls,x=>`${esc(x.label)} <span class="badge">${esc(x.classification)}</span><br><span class="muted">${esc((x.policy_reasons||[]).join('; '))}</span>`],['Unexplored controls',D.unexplored_controls,x=>`${esc(x.target)} <span class="warn">${esc(x.status)}</span><br><span class="muted">${esc(x.error||'Not yet explored')}</span>`]];
$('#lists').innerHTML=blocks.map(([title,items,render])=>`<div class="panel"><h2>${title} <span class="muted">(${items.length})</span></h2>${items.length?items.map(x=>`<div class="item">${render(x)}</div>`).join(''):'<p class="empty">None</p>'}</div>`).join('');
if(center)showNode(center);
</script></body></html>"""
