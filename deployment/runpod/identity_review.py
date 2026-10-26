"""Deterministic local Markdown/HTML review of build_identity and relationship."""
import argparse
import json
import math
import os
from pathlib import Path
import sys
from urllib.parse import quote

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'src'))
from manga_pipeline.storage.io import atomic_bytes, atomic_json, fingerprint, read_json
from manga_pipeline.review.v6 import V6Review


def generate_identity_reviews(reviewer, output):
    """Read existing artifacts only; safe for a syncing replica and agent reruns."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    identity = reviewer.data.get('build_identity', {})
    graph = reviewer.data.get('relationship', {})
    binding = identity.get('name_binding', {})
    preview = False
    if not binding and 'build_identity' in reviewer.directories:
        file = reviewer.root / reviewer.directories['build_identity'] / 'name-binding/result.json'
        if file.is_file():
            binding = read_json(file)
            if binding.get('snapshot_hash') != fingerprint({k: v for k, v in binding.items() if k != 'snapshot_hash'}):
                raise ValueError('Incomplete or changed binding preview')
            preview = True

    def asset(crop):
        path = (reviewer.root / crop['file']).resolve()
        path.relative_to(reviewer.root)
        return {'url': quote(Path(os.path.relpath(path, output)).as_posix(), safe='/:'),
                'available': path.is_file(), 'ref': crop.get('crop_id'),
                'kind': crop.get('kind', 'bank'), 'magi': crop.get('magi_id'),
                'distance': crop.get('distance'), 'sha256': crop.get('retained_sha256')}

    crops = {c['crop_id']: asset(c) for c in [*identity.get('reference_crops', []),
        *binding.get('reference_crops', []), *graph.get('reference_crops', [])]}
    definitions = {**identity.get('eligible_identities', {}), **graph.get('identities', {})}
    nodes = []
    for pid, item in sorted(definitions.items()):
        profile = identity.get('profiles', {}).get(pid, {})
        mids = item.get('magi_ids', [])
        nodes.append({'id': pid, 'label': 'MAGI ' + '/'.join(map(str, mids)), 'magi': mids,
            'main_magi': item.get('main_magi_id'), 'appearances': item.get('chapter_appearances'),
            'profile': profile.get('attributes', {}), 'version': profile.get('version'),
            'images': [c for c in crops.values() if c['kind'] != 'panel' and c['magi'] in mids][:10]})
    claims = []
    for claim in binding.get('accepted', []):
        occurrence = claim['occurrence']
        claims.append({'id': claim['decision_id'], 'name': occurrence['surface'],
            'group_id': next((g['group_id'] for g in binding.get('groups', [])
                              if any(o['id'] == occurrence['id'] for o in g['occurrences'])), None),
            'magi': claim['owner_magi_id'], 'person': reviewer.person(claim['person_id']),
            'text': occurrence['text'], 'utterance_id': occurrence['utterance_id'],
            'kind': claim['evidence_kind'], 'reason': reviewer.display_identifiers(claim['reason']),
            'evidence_refs': claim['evidence_refs'],
            'supplied_evidence_refs': claim.get('supplied_evidence_refs', []),
            'model_evidence_refs': claim.get('model_evidence_refs', claim['evidence_refs']),
            'images': [crops[r] for r in claim.get('supplied_evidence_refs', claim['evidence_refs']) if r in crops]})
    rows = {r['utterance_id']: r for r in reviewer.data.get('analyze', {}).get('utterances', [])}
    if binding.get('policy') == 'analyze-authoritative-v3':
        for selection in binding.get('selections', []):
            if not selection['eligible']:
                continue
            winning = next(c for c in selection['candidates'] if c['group_id'] == selection['group_id'])
            first = winning['occurrences'][0]
            claims.append({'id': first['candidate_id'], 'name': selection['canonical_name'],
                'group_id': selection['group_id'], 'magi': selection['magi_id'],
                'person': reviewer.person(selection['identity_id']), 'text': reviewer.sentence(first['utterance_id']),
                'utterance_id': first['utterance_id'], 'kind': 'registered_from_analyze',
                'reason': f"Analyze support: {selection['support']}; eligible={selection['eligible']}; "
                          f"crop_count={selection['chapter_crop_count']}; {selection['tie_break']}",
                'evidence_refs': [o['utterance_id'] for o in winning['occurrences']],
                'supplied_evidence_refs': [], 'model_evidence_refs': [],
                'registration': selection,
                'images': [c for c in crops.values() if c['kind'] != 'panel' and c['magi'] == selection['magi_id']][:10]})
    edges = []
    for edge in graph.get('address_table', []):
        copied = dict(edge)
        copied.update(from_label=reviewer.person(edge['from_id']), to_label=reviewer.person(edge['to_id']),
            reason=reviewer.display_identifiers(edge['reason']),
            quotes=[{'id': ref, 'text': reviewer.sentence(ref),
                     'speaker': reviewer.person(rows.get(ref, {}).get('speaker_person_id'))}
                    for ref in edge.get('supplied_evidence_refs', edge.get('evidence_refs', [])) if ref in rows])
        edges.append(copied)
    payload = {'version': 'identity-relationship-local-review-v1',
        'story': reviewer.manifest['source']['story'], 'chapter': reviewer.manifest['source']['chapter'],
        'run_id': reviewer.root.name, 'binding_preview': preview,
        'stages': {s: reviewer.manifest.get('steps', {}).get(s, {}).get('status', 'pending')
                   for s in ('build_identity', 'relationship')},
        'summary': binding.get('summary', {}), 'claims': claims, 'nodes': nodes, 'edges': edges,
        'social_relations': graph.get('social_relations', []),
        'identity_reviews': binding.get('needs_review', identity.get('needs_review', [])),
        'name_registrations': binding.get('selections', []),
        'relationship_reviews': graph.get('needs_review', []),
        'inference_failures': [*binding.get('inference_failures', []), *graph.get('inference_failures', [])],
        'effective_updates': binding.get('effective_updates', {}),
        'lineage': {k: identity.get(k) for k in ('binding_snapshot_hash', 'profile_snapshot_hash')}
                   | {'relationship_snapshot_hash': graph.get('snapshot_hash')}}
    atomic_json(output / 'report.json', payload)
    markdown = ['# Review local — build_identity và relationship', '',
        'HTML được dựng bằng code từ artifact đã lưu; không gọi model và không sửa dữ liệu nguồn.', '',
        '[Identity HTML](build_identity.html) · [Relationship HTML](relationship.html) · [Toàn bộ response lỗi](errors.html) · [Chi tiết lỗi Markdown](errors.md) · [JSON cho agent](report.json)', '',
        f"Run: {payload['run_id']} · binding preview: {preview}", '']
    for step in ('build_identity', 'relationship'):
        if step in reviewer.steps:
            content = reviewer.render_step(step, output, output / 'review.md')
            atomic_bytes(output / (step+'.md'), content.encode('utf-8'))
            markdown.append(content)
    if preview:
        markdown.extend(['## Name-binding preview — build_identity chưa commit', '',
                         json.dumps(payload['summary'], ensure_ascii=False), ''])
    atomic_bytes(output / 'review.md', ('\n'.join(markdown)+'\n').encode('utf-8'))
    data = json.dumps(payload, ensure_ascii=False).replace('</', '<\\/').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    for page, view in (('index', 'binding'), ('build_identity', 'binding'), ('relationship', 'graph')):
        html = HTML.replace('__DATA__', data).replace('__VIEW__', json.dumps(view))
        atomic_bytes(output / (page+'.html'), html.encode('utf-8'))
    from identity_failure_review import generate_failure_review
    failures = generate_failure_review(reviewer.root, output,
        reviewer.root / reviewer.directories['build_identity'] if 'build_identity' in reviewer.directories else None)
    return {'review': str(output / 'review.md'), 'html': str(output / 'index.html'),
            'errors_html': str(output / 'errors.html'), 'rejected_requests': failures['request_count'],
            'identity_html': str(output / 'build_identity.html'), 'relationship_html': str(output / 'relationship.html'),
            'json': str(output / 'report.json'), 'claims': len(claims), 'identities': len(nodes), 'directed_edges': len(edges)}


HTML = '''<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>v7 · Identity & Relationship</title><style>
:root{color-scheme:dark;font:15px system-ui;background:#0c1422;color:#e8effb}*{box-sizing:border-box}body{margin:0}header{padding:24px 4vw;border-bottom:1px solid #304159;background:#111e31}h1{font-size:26px;margin:0 0 8px}h2{font-size:20px}h3{font-size:17px}p{line-height:1.65}small,.muted{color:#a9bbd3}a{color:#8bceff}button,input{font:inherit;border:1px solid #45607e;background:#182a43;color:inherit;border-radius:8px;padding:9px 12px}button{cursor:pointer}button.active{border-color:#5de0bb;background:#17463f}.toolbar{display:flex;gap:9px;flex-wrap:wrap;margin:16px 0}.stats{display:flex;gap:12px;flex-wrap:wrap}.stat{background:#182a40;border:1px solid #344c68;border-radius:10px;padding:12px;min-width:120px}.stat b{display:block;font-size:24px;color:#72dfbd}main{padding:20px 4vw}.layout{display:grid;grid-template-columns:minmax(420px,1fr) minmax(360px,1fr);gap:20px}.panel,.card{background:#122137;border:1px solid #304761;border-radius:12px;padding:17px;margin-bottom:14px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}.tag{display:inline-block;border:1px solid #426483;border-radius:20px;padding:3px 9px;font-size:12px;margin:3px}.confirmed{color:#63e2bc}.provisional{color:#ffd380}.unknown{color:#a9b8ce}.warn{padding:12px;background:#49371d;border-left:4px solid #e9b45d;border-radius:6px}.empty{padding:24px;background:#18283b;border:1px dashed #58718f;border-radius:10px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid #33465f;padding:9px;text-align:left;vertical-align:top}table button{padding:4px 7px}.scroll{overflow:auto}.images{display:flex;gap:8px;flex-wrap:wrap}.images img{height:140px;max-width:100%;object-fit:contain;background:#fff;border-radius:7px;cursor:zoom-in}.images figure{margin:6px 0}.images figcaption{font-size:11px;max-width:240px;color:#a9bbd3;overflow-wrap:anywhere}blockquote{margin:12px 0;padding:12px;border-left:3px solid #67b9df;background:#172d45;line-height:1.6}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:480px;overflow:auto}svg{width:100%;min-height:420px}svg text{fill:#e8effb;text-anchor:middle}svg .node{cursor:pointer}svg .node circle{fill:#203d58;stroke:#7ac8f2;stroke-width:2}svg .edge{fill:none;stroke-width:3;cursor:pointer}dialog{background:#101b2d;border:1px solid #6a89a9;color:white;max-width:95vw}dialog img{max-width:90vw;max-height:83vh;object-fit:contain}dialog::backdrop{background:#000b}.hidden{display:none!important}@media(max-width:1000px){.layout{grid-template-columns:1fr}main,header{padding:16px}.panel{padding:12px}}
</style><header><h1>v7 · Identity & Relationship</h1><div id="meta"></div><div class="toolbar"><button id="tb-binding" onclick="view('binding')">Build identity</button><button id="tb-graph" onclick="view('graph')">Relationship</button><button onclick="location.reload()">Tải lại dữ liệu</button><a href="errors.html">Tất cả response bị từ chối</a><a href="review.md">Markdown</a><a href="report.json">JSON / agent</a></div><div id="stats" class="stats"></div></header>
<main><div id="notice"></div><p class="muted">Ảnh và thoại hiển thị gồm nguồn đầu vào được cung cấp cho model. Điều này không có nghĩa model đã trích dẫn từng nguồn; report.json lưu riêng supplied_evidence_refs và model_evidence_refs.</p><div class="toolbar"><input id="search" placeholder="Lọc tên, MAGI, lý do hoặc câu thoại" oninput="render()"><label><input type="checkbox" id="uncertain" onchange="render()"> Chỉ chưa confirmed</label></div>
<section id="binding"><div class="layout"><div><h2>Tên → nhân vật</h2><div id="claims"></div></div><div><h2>Profile identity</h2><div id="profiles"></div></div></div><details class="panel"><summary>Lượt tên bỏ qua / chưa phân giải / xung đột</summary><pre id="identity-reviews"></pre></details><details class="panel"><summary>Effective speaker/addressee và provenance</summary><pre id="effective"></pre></details></section>
<section id="graph" class="hidden"><div class="layout"><div class="panel"><h2>Xưng hô có hướng</h2><div id="svg"></div><p class="muted">Mỗi mũi tên là một chiều tự xưng / gọi người kia. Bấm cạnh hoặc nút trong bảng để xem lý do. Màu xanh: confirmed; vàng: provisional; nét đứt: unknown.</p></div><div class="panel" id="detail"></div></div><div class="panel scroll"><h2>Từng chiều xưng hô</h2><table><thead><tr><th>Từ → đến</th><th>Tự xưng / gọi</th><th>Trạng thái</th><th>Căn cứ</th><th>Chi tiết</th></tr></thead><tbody id="edge-table"></tbody></table></div><details class="panel"><summary>Quan hệ xã hội — khác với xưng hô</summary><pre id="social"></pre></details><details class="panel"><summary>Review và lỗi inference</summary><pre id="relationship-reviews"></pre></details></section></main>
<dialog id="zoom"><button onclick="document.getElementById('zoom').close()">Đóng ảnh</button><br><img id="zoom-image" alt="Ảnh bằng chứng"></dialog>
<script>const D=__DATA__;let mode=__VIEW__;let selected=-1;const $=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const pretty=o=>JSON.stringify(o,null,2);const N=Object.fromEntries(D.nodes.map(n=>[n.id,n]));
function match(o){const q=$('search').value.trim().toLowerCase();return !q||pretty(o).toLowerCase().includes(q)}
function pics(images){return '<div class="images">'+images.map(c=>`<figure><img src="${esc(c.url)}" alt="${esc(c.kind==='panel'?'Panel evidence':'MAGI '+c.magi)}" onclick="zoom(this.src)" onerror="this.alt='Ảnh chưa đồng bộ';this.style.opacity=.4"><figcaption>${esc(c.kind==='panel'?'Panel có nhãn MAGI':'MAGI '+c.magi)} · ${esc(c.ref)}</figcaption></figure>`).join('')+'</div>'}
function zoom(src){$('zoom-image').src=src;$('zoom').showModal()}
function view(v){mode=v;$('binding').classList.toggle('hidden',v!=='binding');$('graph').classList.toggle('hidden',v!=='graph');$('tb-binding').classList.toggle('active',v==='binding');$('tb-graph').classList.toggle('active',v==='graph');render()}
function edgeDetail(i){selected=i;const e=D.edges[i];if(!e)return;$('detail').innerHTML=`<h2>${esc(e.from_label)} → ${esc(e.to_label)}</h2><p><b>Tự xưng:</b> ${esc(e.self)} · <b>Gọi:</b> ${esc(e.address)}</p><p><span class="tag ${esc(e.status)}">${esc(e.status)}</span><span class="tag">${esc(e.basis)}</span></p>${e.basis==='visual_age_estimate'?'<p class="warn">Suy đoán từ tuổi/ngoại hình trong profile; chưa phải quan hệ được lời thoại xác nhận.</p>':''}<h3>Lý do đã lưu</h3><p>${esc(e.reason)}</p><h3>Câu thoại / evidence</h3>${e.quotes.map(q=>`<blockquote>${esc(q.text)}<br><small>${esc(q.speaker)} · ${esc(q.id)}</small></blockquote>`).join('')||'<p class="muted">References có thể là profile/crop; xem JSON để đối chiếu đầy đủ.</p>'}<pre>${esc(pretty(e.evidence_refs))}</pre>${pics([...(N[e.from_id]?.images||[]).slice(0,3),...(N[e.to_id]?.images||[]).slice(0,3)])}`;drawGraph()}
function drawGraph(){if(!D.nodes.length){$('svg').innerHTML='<p class="empty">Chưa có identity đủ điều kiện. Không có cạnh không đồng nghĩa nhân vật không có quan hệ.</p>';return}const W=820,H=590,cx=W/2,cy=H/2,R=D.nodes.length===1?0:205;const pos=Object.fromEntries(D.nodes.map((n,i)=>[n.id,[cx+R*Math.cos(2*Math.PI*i/D.nodes.length-Math.PI/2),cy+R*Math.sin(2*Math.PI*i/D.nodes.length-Math.PI/2)]]));let html='<svg viewBox="0 0 820 590"><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L7,3 z" fill="#a4c6de"/></marker></defs>';D.edges.forEach((e,i)=>{if(!pos[e.from_id]||!pos[e.to_id]||!match(e))return;if($('uncertain').checked&&e.status==='confirmed')return;let a=pos[e.from_id],b=pos[e.to_id],dx=b[0]-a[0],dy=b[1]-a[1],l=Math.hypot(dx,dy)||1;let x1=a[0]+dx/l*43,y1=a[1]+dy/l*43,x2=b[0]-dx/l*49,y2=b[1]-dy/l*49;let mx=(x1+x2)/2-dy/l*42,my=(y1+y2)/2+dx/l*42;const color=e.status==='confirmed'?'#63e2bc':e.status==='provisional'?'#ffd380':'#91a6bd';html+=`<path class="edge" d="M${x1},${y1} Q${mx},${my} ${x2},${y2}" stroke="${color}" stroke-width="${selected===i?6:3}" ${e.status==='unknown'?'stroke-dasharray="7 5"':''} marker-end="url(#arrow)" onclick="edgeDetail(${i})"><title>${esc(e.from_label+' → '+e.to_label+': '+e.self+' / '+e.address)}</title></path>`});D.nodes.forEach(n=>{let a=pos[n.id];html+=`<g class="node"><circle cx="${a[0]}" cy="${a[1]}" r="41"/><text x="${a[0]}" y="${a[1]-4}" font-size="14">MAGI ${esc(n.magi.join('/'))}</text><text x="${a[0]}" y="${a[1]+15}" font-size="11">${esc(n.appearances??'?')} crop/chapter</text></g>`});$('svg').innerHTML=html+'</svg>'}
function render(){$('meta').textContent=D.story+' · '+D.chapter+' · '+D.run_id+' | build_identity: '+D.stages.build_identity+' | relationship: '+D.stages.relationship;const summary=[['Tên xác nhận',D.summary.confirmed_occurrences??D.claims.length],['Identity có profile',D.nodes.length],['Chiều xưng hô',D.edges.length],['Request binding',D.summary.requests??0]];$('stats').innerHTML=summary.map(([k,v])=>`<div class="stat"><b>${esc(v)}</b>${esc(k)}</div>`).join('');$('notice').innerHTML=D.binding_preview?'<p class="warn">Đây là name-binding preview; build_identity chưa commit. Profile và graph có thể chưa được tạo.</p>':D.stages.relationship!=='completed'?'<p class="warn">Relationship chưa commit. Trang này chỉ hiển thị artifact đã có, không tự suy luận cạnh còn thiếu.</p>':'';$('claims').innerHTML=D.claims.filter(match).map(c=>`<article class="card"><h3>${esc(c.name)} → MAGI ${esc(c.magi)}</h3><span class="tag confirmed">confirmed</span><span class="tag">${esc(c.kind)}</span><blockquote>${esc(c.text)}</blockquote><p>${esc(c.reason)}</p>${pics(c.images)}<details><summary>References / decision</summary><pre>${esc(pretty({decision:c.id,evidence:c.evidence_refs,utterance:c.utterance_id}))}</pre></details></article>`).join('')||'<p class="empty">Chưa có tên được xác nhận trong artifact hiện tại. Xem lý do bỏ qua/chưa phân giải bên dưới.</p>';$('profiles').innerHTML=D.nodes.filter(match).map(n=>`<article class="card"><h3>${esc(n.label)}</h3><p>Main MAGI ${esc(n.main_magi)} · ${esc(n.appearances)} detection/chapter</p>${pics(n.images)}<table>${Object.entries(n.profile).map(([k,a])=>`<tr><td>${esc(k)}</td><td>${esc(a.value)}<br><small>${esc(a.status)} · ${esc(a.basis)}</small></td></tr>`).join('')}</table></article>`).join('')||'<p class="empty">Chưa có profile đủ điều kiện hoặc stage chưa hoàn tất.</p>';$('identity-reviews').textContent=pretty(D.identity_reviews);$('effective').textContent=pretty(D.effective_updates);$('social').textContent=pretty(D.social_relations);$('relationship-reviews').textContent=pretty({review:D.relationship_reviews,inference_failures:D.inference_failures});$('edge-table').innerHTML=D.edges.map((e,i)=>({e,i})).filter(({e})=>match(e)&&(!$('uncertain').checked||e.status!=='confirmed')).map(({e,i})=>`<tr><td>${esc(e.from_label)} → ${esc(e.to_label)}</td><td>${esc(e.self)} / ${esc(e.address)}</td><td class="${esc(e.status)}">${esc(e.status)}</td><td>${esc(e.basis)}</td><td><button onclick="edgeDetail(${i})">Lý do / thoại / ảnh</button></td></tr>`).join('');if(!D.edges.length)$('detail').innerHTML='<h2>Chưa có cạnh xưng hô</h2><p>Kiểm tra số identity, profile eligibility, direct communication pairs và request failures. Không tự thêm quan hệ để lấp đồ thị.</p>';else if(selected<0)edgeDetail(0);drawGraph()}
view(mode);</script></html>'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-folder', type=Path)
    parser.add_argument('--bank-root', type=Path)
    args = parser.parse_args()
    print(json.dumps(generate_identity_reviews(V6Review(args.run_dir,
        source_folder=args.source_folder, bank_root=args.bank_root), args.output), ensure_ascii=False))
