"""评测仪表盘 — 从 .kun/reports/ + .kun/evaluations.jsonl 生成 HTML.

v0.8: ThinkBlock 评分趋势 + AdaRubric 维度 + 过度思考日志
零外部依赖, 单 HTML 文件.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


def build_dashboard(report_dir: str = ".kun/reports", output: str | None = None) -> Path:
    rd = Path(report_dir)
    sessions = _load_sessions(rd)
    evaluations = _load_evaluations(rd)
    html = _render(sessions, evaluations, len(sessions))
    if output is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = f".kun/dashboard-{stamp}.html"
    out = Path(output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def _load_sessions(report_dir: Path) -> list[dict]:
    sessions = []
    for f in report_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            events = data.get("events", [])
            sessions.append({
                "id": data.get("session_id", f.stem),
                "started": data.get("started_at", "")[:19],
                "elapsed": round(data.get("elapsed_seconds", 0), 1),
                "events": len(events),
                "tool_calls": sum(1 for e in events if e.get("type") == "tool_use"),
                "errors": sum(1 for e in events if e.get("type") in ("error", "permission_denied")),
                "turns": sum(1 for e in events if e.get("type") == "turn_start"),
                "thinking_events": sum(1 for e in events
                                       if e.get("type") == "content_block_delta"
                                       and e.get("data", {}).get("type") == "thinking"),
                "events_raw": events,
            })
        except Exception:
            continue
    sessions.sort(key=lambda s: s["started"], reverse=True)
    return sessions


def _load_evaluations(report_dir: Path) -> list[dict]:
    """加载评测数据."""
    evals = []
    path = report_dir.parent / "evaluations.jsonl"
    if not path.exists():
        return evals
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        try:
            evals.append(json.loads(line))
        except Exception:
            continue
    return list(reversed(evals))  # 最新的在前


def _elapsed_chart(sessions: list[dict]) -> str:
    """渲染耗时趋势柱状图."""
    recent = sessions[:30]
    if not recent:
        return ""
    max_elapsed = max(s["elapsed"] for s in recent) or 1
    bar_w = max(4, 800 // max(len(recent), 1))
    bars = []
    for s in recent:
        h = s["elapsed"] / max_elapsed * 100
        bars.append(
            f'<div class="vbar" style="height:{h}px;width:{bar_w}px;background:var(--blue)" '
            f'title="{s["id"][:8]}: {s["elapsed"]}s"></div>'
        )
    return '<div class="vchart">' + "".join(bars) + "</div>"


def _render(sessions: list[dict], evaluations: list[dict], total: int) -> str:
    # ── 聚合 ──
    total_events = sum(s["events"] for s in sessions)
    total_tools = sum(s["tool_calls"] for s in sessions)
    total_errors = sum(s["errors"] for s in sessions)
    total_elapsed = sum(s["elapsed"] for s in sessions)
    avg_turns = sum(s["turns"] for s in sessions) / max(len(sessions), 1)

    # ── 工具分布 ──
    tool_dist = {}
    for s in sessions[:200]:
        for e in s.get("events_raw", []):
            if e.get("type") == "tool_use":
                name = e.get("data", {}).get("name", "?")
                tool_dist[name] = tool_dist.get(name, 0) + 1
    top_tools = sorted(tool_dist.items(), key=lambda x: x[1], reverse=True)[:10]
    max_tool = top_tools[0][1] if top_tools else 1
    tool_bars = "".join(
        f"""<div class="bar-row"><span class="bar-label">{n}</span>
        <div class="bar-track"><div class="bar-fill" style="width:{c/max_tool*100}%"></div></div>
        <span class="bar-count">{c}</span></div>"""
        for n, c in top_tools
    )

    # ── ThinkBlock 评分趋势 ──
    thinking_scores = []
    for ev in evaluations[:50]:
        te = ev.get("thinking_eval", {})
        if te.get("overall", -1) >= 0:
            thinking_scores.append({
                "sid": ev.get("session_id", "")[:8],
                "paralysis": te.get("analysis_paralysis", 0),
                "rogue": te.get("rogue_actions", 0),
                "disengage": te.get("premature_disengagement", 0),
                "overall": te.get("overall", 0),
            })
    thinking_chart = ""
    if thinking_scores:
        max_h = 100
        bar_w = max(4, 800 // max(len(thinking_scores), 1))
        for s in thinking_scores:
            h = s["overall"] / 10 * max_h
            color = "#3fb950" if s["overall"] <= 3 else "#d2991d" if s["overall"] <= 6 else "#f85149"
            thinking_chart += (
                f'<div class="vbar" style="height:{h}px;width:{bar_w}px;background:{color}" '
                f'title="{s["sid"]}: paralysis={s["paralysis"]} rogue={s["rogue"]} disengage={s["disengage"]}">'
                f'</div>'
            )

    # ── AdaRubric 维度 ──
    rubric_items = ""
    rubric_count = 0
    dim_totals = {}
    dim_counts = {}
    for ev in evaluations[:50]:
        te = ev.get("task_eval", {})
        for d in te.get("dimensions", []):
            name = d.get("name", "?")
            score = d.get("score", 0)
            mx = d.get("max", 3)
            dim_totals[name] = dim_totals.get(name, 0) + score
            dim_counts[name] = dim_counts.get(name, 0) + 1
            rubric_count += 1
    if dim_totals:
        rubric_items = "".join(
            f"""<div class="bar-row"><span class="bar-label">{n[:20]}</span>
            <div class="bar-track"><div class="bar-fill" style="width:{dim_totals[n]/max(dim_counts[n],1)/3*100}%"></div></div>
            <span class="bar-count">{dim_totals[n]/max(dim_counts[n],1):.1f}/3</span></div>"""
            for n in sorted(dim_totals.keys(), key=lambda x: dim_totals[x]/max(dim_counts[x],1), reverse=True)[:15]
        )

    # ── 会话表格 ──
    rows = ""
    for s in sessions[:20]:
        es = "color:var(--red)" if s["errors"] > 0 else ""
        # 找对应的评测
        ev_match = next((e for e in evaluations if e.get("session_id","")[:8] == s["id"][:8]), {})
        think_score = ev_match.get("thinking_eval", {}).get("overall", -1)
        score_str = f"{think_score}/10" if think_score >= 0 else "-"
        rows += f"""<tr>
            <td class="id">{s['id'][:8]}</td><td>{s['started']}</td><td>{s['elapsed']}s</td>
            <td>{s['turns']}</td><td>{s['tool_calls']}</td><td style="{es}">{s['errors']}</td>
            <td>{s['thinking_events']}</td><td>{score_str}</td></tr>"""

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>Kunkun 评测仪表盘</title>
<style>
:root{{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#c9d1d9;--dim:#8b949e;--green:#3fb950;--yellow:#d2991d;--red:#f85149;--blue:#58a6ff}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);padding:24px}}
h1{{font-size:24px;margin-bottom:4px}}
.subtitle{{color:var(--dim);margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:24px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px}}
.card .value{{font-size:28px;font-weight:700}}
.card .label{{color:var(--dim);font-size:13px;margin-top:4px}}
.section{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px}}
.section h2{{font-size:16px;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse}}
th,td{{padding:6px 10px;text-align:left;border-bottom:1px solid var(--border);font-size:13px}}
th{{color:var(--dim);font-weight:600}}
.id{{font-family:monospace;color:var(--blue)}}
.bar-row{{display:flex;align-items:center;gap:8px;margin:4px 0}}
.bar-label{{width:100px;font-size:13px;text-align:right;color:var(--dim);flex-shrink:0}}
.bar-track{{flex:1;height:20px;background:var(--border);border-radius:4px;overflow:hidden}}
.bar-fill{{height:100%;background:var(--green);border-radius:4px;transition:width .3s}}
.bar-count{{width:50px;font-size:13px;text-align:right;flex-shrink:0}}
.vchart{{display:flex;align-items:flex-end;gap:2px;height:110px;padding:4px 0}}
.vbar{{border-radius:2px 2px 0 0;min-width:4px}}
.legend{{display:flex;gap:16px;font-size:12px;color:var(--dim);margin-top:8px}}
.legend span{{display:flex;align-items:center;gap:4px}}
.legend .dot{{width:10px;height:10px;border-radius:2px}}
.footer{{color:var(--dim);font-size:12px;margin-top:24px;text-align:center}}
</style>
</head>
<body>
<h1>📊 Kunkun 评测仪表盘</h1>
<p class="subtitle">{total} 个会话 · {len(evaluations)} 条评测 · {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

<div class="grid">
    <div class="card"><div class="value">{total}</div><div class="label">会话总数</div></div>
    <div class="card"><div class="value">{total_events:,}</div><div class="label">事件总数</div></div>
    <div class="card"><div class="value">{total_tools:,}</div><div class="label">工具调用</div></div>
    <div class="card"><div class="value">{total_errors:,}</div><div class="label">错误/警告</div></div>
    <div class="card"><div class="value">{total_elapsed:.0f}s</div><div class="label">总耗时</div></div>
    <div class="card"><div class="value">{avg_turns:.1f}</div><div class="label">平均轮次</div></div>
</div>

<div class="section">
    <h2>🧠 ThinkBlock 过度思考评分趋势 (综合)</h2>
    {"<div class='vchart'>" + thinking_chart + "</div>" if thinking_chart else "<p style='color:var(--dim)'>暂无评测数据，执行几次对话后自动生成</p>"}
    <div class="legend"><span><div class="dot" style="background:var(--green)"></div>0-3 健康</span><span><div class="dot" style="background:var(--yellow)"></div>4-6 注意</span><span><div class="dot" style="background:var(--red)"></div>7-10 严重</span></div>
</div>

<div class="section">
    <h2>⏱ 执行耗时趋势 (最近 30 会话)</h2>
    {_elapsed_chart(sessions)}
    <div class="legend"><span><div class="dot" style="background:var(--blue)"></div>单次耗时 (秒)</span></div>
</div>

<div class="section">
    <h2>📋 AdaRubric 任务维度评分</h2>
    {"<p style='color:var(--dim);margin-bottom:8px'>最近 {0} 条评测中出现的维度平均分</p>".format(len(evaluations[:50])) if rubric_items else ""}
    {rubric_items if rubric_items else "<p style='color:var(--dim)'>暂无评测数据</p>"}
</div>

<div class="section">
    <h2>🔧 工具调用分布 (Top 10)</h2>
    {tool_bars}
</div>

<div class="section">
    <h2>📋 最近会话</h2>
    <table>
        <thead><tr><th>Session</th><th>时间</th><th>耗时</th><th>轮次</th><th>工具</th><th>错误</th><th>Think</th><th>评分</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>
</div>

<p class="footer">Kunkun v0.8 · 评测仪表盘 · 数据来自 .kun/reports/ + .kun/evaluations.jsonl</p>
</body>
</html>"""
