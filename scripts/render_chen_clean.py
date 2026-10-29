#!/usr/bin/env python3
"""Render a clean Chen-notation E-R set for the PaperPilot data model.

The drawings in docs/data-model/diagrams/ are kept unchanged. This script writes
an alternative, hand-laid-out set to docs/data-model/diagrams-chen-clean/.

Design rules used by every view:
* rectangle = entity, diamond = relationship, ellipse = attribute;
* relationship attributes are attached to the diamond (e.g. project role);
* association tables such as project_members / paper_authors / task_papers are
  folded into N:M relationships in the conceptual views;
* no relationship line crosses another relationship line, and attribute
  connectors stay inside the free space around their entity.  The only
  exception is a relationship marked ``hop=True``: the total view is not
  planar (USER/PROJECT/CHUNK x PAPER/TASK/REPORT_VERSION contains a K3,3), so
  one crossing is unavoidable and is drawn as a line jump;
* a solid underline marks a primary key, a dashed underline marks a partial
  key that must be combined with the owner entity's key (weak-entity style
  without double borders);
* foreign keys are drawn as relationships, never as attributes.

Run with ``--check`` to fail on crossings, shape overlaps, or text that does not
fit its ellipse. PNG previews are produced separately with a browser.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data-model" / "diagrams-chen-clean"


# ---------------------------------------------------------------------------
# geometry helpers


def rect_anchor(entity: "Entity", side: str | tuple[float, float], frac: float = 0.5) -> tuple[float, float]:
    if isinstance(side, tuple):
        return side
    x, y, w, h = entity.x, entity.y, entity.w, entity.h
    if side == "N":
        return (x + (frac - 0.5) * w, y - h / 2)
    if side == "S":
        return (x + (frac - 0.5) * w, y + h / 2)
    if side == "W":
        return (x - w / 2, y + (frac - 0.5) * h)
    if side == "E":
        return (x + w / 2, y + (frac - 0.5) * h)
    corners = {
        "NW": (x - w / 2, y - h / 2),
        "NE": (x + w / 2, y - h / 2),
        "SW": (x - w / 2, y + h / 2),
        "SE": (x + w / 2, y + h / 2),
    }
    if side in corners:
        return corners[side]
    raise ValueError(side)


def ellipse_near_point(attr: "Attr", point: tuple[float, float]) -> tuple[float, float]:
    dx = point[0] - attr.x
    dy = point[1] - attr.y
    scale = 1.0 / math.sqrt((dx / attr.rx) ** 2 + (dy / attr.ry) ** 2)
    return (attr.x + dx * scale, attr.y + dy * scale)


def diamond_near_point(diamond: "Rel", point: tuple[float, float]) -> tuple[float, float]:
    dx = point[0] - diamond.x
    dy = point[1] - diamond.y
    scale = 1.0 / (abs(dx) / diamond.hw + abs(dy) / diamond.hh)
    return (diamond.x + dx * scale, diamond.y + dy * scale)


def seg_intersection(a, b, c, d):
    """Return intersection point of segments ab and cd, or None."""
    ax, ay = a
    bx, by = b
    cx, cy = c
    dx, dy = d
    r_x, r_y = bx - ax, by - ay
    s_x, s_y = dx - cx, dy - cy
    denom = r_x * s_y - r_y * s_x
    if abs(denom) < 1e-9:
        return None
    t = ((cx - ax) * s_y - (cy - ay) * s_x) / denom
    u = ((cx - ax) * r_y - (cy - ay) * r_x) / denom
    if -1e-6 <= t <= 1 + 1e-6 and -1e-6 <= u <= 1 + 1e-6:
        return (ax + t * r_x, ay + t * r_y)
    return None


def close(a, b, tol=1.2):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def box_overlap(a, b, gap=0.0):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw / 2 + gap <= bx - bw / 2
        or bx + bw / 2 + gap <= ax - aw / 2
        or ay + ah / 2 + gap <= by - bh / 2
        or by + bh / 2 + gap <= ay - ah / 2
    )


def text_width(text: str, size: float) -> float:
    width = 0.0
    for ch in text:
        if ord(ch) > 0x2E80:
            width += size
        elif ch in " .,:;'|":
            width += size * 0.30
        else:
            width += size * 0.56
    return width


# ---------------------------------------------------------------------------
# model

LEGEND = "矩形：实体　椭圆：属性　菱形：联系　实下划线：主键　虚下划线：部分键（与所属实体主键组合）　1 / N / M：最大基数"


@dataclass
class Attr:
    label: str
    name: str
    x: float
    y: float
    rx: float = 86
    ry: float = 38
    key: bool = False
    anchor: str | tuple[float, float] = "N"
    anchor_frac: float = 0.5
    parent: str = ""
    partial: bool = False


@dataclass
class Entity:
    ident: str
    label: str
    x: float
    y: float
    w: float = 200
    h: float = 90
    attrs: list[Attr] = field(default_factory=list)


@dataclass
class Rel:
    label: str
    x: float
    y: float
    endpoints: list[tuple[str, str | tuple[float, float], float, str]]
    hw: float = 86
    hh: float = 48
    attrs: list[Attr] = field(default_factory=list)
    hop: bool = False


@dataclass
class Diagram:
    name: str
    title: str
    width: int
    height: int
    entities: list[Entity]
    rels: list[Rel]
    note: list[str] = field(default_factory=list)
    legend: str = LEGEND
    show_legend: bool = True

    def entity(self, ident: str) -> Entity:
        for e in self.entities:
            if e.ident == ident:
                return e
        raise KeyError(ident)


# ---------------------------------------------------------------------------
# SVG


def render(d: Diagram) -> tuple[str, list[dict]]:
    shapes: list[str] = []
    attr_lines: list[str] = []
    rel_lines: list[str] = []
    texts: list[str] = []
    segments: list[dict] = []

    # relationship connectors and cardinalities
    rel_segs: list[tuple[tuple[float, float], tuple[float, float], bool]] = []
    for ri, rel in enumerate(d.rels):
        rid = f"rel:{ri}"
        for ent_id, anchor, frac, card in rel.endpoints:
            ent = d.entity(ent_id)
            start = rect_anchor(ent, anchor, frac)
            end = diamond_near_point(rel, start)
            rel_segs.append((start, end, rel.hop))
            segments.append(
                {
                    "a": start,
                    "b": end,
                    "kind": "rel",
                    "label": rel.label,
                    "ignore": {ent_id, rid},
                    "hop": rel.hop,
                }
            )
            if card:
                # 22% along the line, offset perpendicular to it
                t = 0.22
                px = start[0] + (end[0] - start[0]) * t
                py = start[1] + (end[1] - start[1]) * t
                vx, vy = end[0] - start[0], end[1] - start[1]
                ln = math.hypot(vx, vy) or 1.0
                nx, ny = -vy / ln, vx / ln
                # keep the label on the upper/right side for horizontal/vertical lines
                if abs(vy) < 1e-6 and ny > 0:
                    nx, ny = -nx, -ny
                if abs(vx) < 1e-6 and nx < 0:
                    nx, ny = -nx, -ny
                tx, ty = px + nx * 18, py + ny * 18
                texts.append(
                    f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle" class="card">{escape(card)}</text>'
                )

    for start, end, hop in rel_segs:
        crossings = []
        if hop:
            for other_start, other_end, other_hop in rel_segs:
                if other_hop:
                    continue
                p = seg_intersection(start, end, other_start, other_end)
                if p and not (
                    close(p, start) or close(p, end) or close(p, other_start) or close(p, other_end)
                ):
                    crossings.append(p)
        if not crossings:
            rel_lines.append(
                f'<line x1="{start[0]:.1f}" y1="{start[1]:.1f}" x2="{end[0]:.1f}" y2="{end[1]:.1f}"/>'
            )
            continue
        # draw a small semicircular jump over every crossing
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        ux, uy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
        crossings.sort(key=lambda p: (p[0] - start[0]) * ux + (p[1] - start[1]) * uy)
        radius = 9.0
        path = [f"M {start[0]:.1f} {start[1]:.1f}"]
        for p in crossings:
            a = (p[0] - ux * radius, p[1] - uy * radius)
            b = (p[0] + ux * radius, p[1] + uy * radius)
            path.append(f"L {a[0]:.1f} {a[1]:.1f} A {radius:.0f} {radius:.0f} 0 0 1 {b[0]:.1f} {b[1]:.1f}")
        path.append(f"L {end[0]:.1f} {end[1]:.1f}")
        rel_lines.append(f'<path d="{" ".join(path)}" class="hop"/>')

    # relationship attribute connectors
    for ri, rel in enumerate(d.rels):
        rid = f"rel:{ri}"
        for a in rel.attrs:
            start = (rel.x, rel.y)
            end = ellipse_near_point(a, start)
            attr_lines.append(
                f'<line x1="{start[0]:.1f}" y1="{start[1]:.1f}" x2="{end[0]:.1f}" y2="{end[1]:.1f}"/>'
            )
            segments.append(
                {
                    "a": start,
                    "b": end,
                    "kind": "relattr",
                    "label": a.label,
                    "ignore": {rid, f"relattr:{ri}:{a.label}"},
                }
            )

    # entity attribute connectors
    for e in d.entities:
        for a in e.attrs:
            start = rect_anchor(e, a.anchor, a.anchor_frac)
            end = ellipse_near_point(a, start)
            attr_lines.append(
                f'<line x1="{start[0]:.1f}" y1="{start[1]:.1f}" x2="{end[0]:.1f}" y2="{end[1]:.1f}"/>'
            )
            segments.append(
                {
                    "a": start,
                    "b": end,
                    "kind": "attr",
                    "label": a.label,
                    "ignore": {e.ident, f"attr:{e.ident}:{a.label}"},
                }
            )

    # shapes
    for e in d.entities:
        shapes.append(
            f'<rect x="{e.x - e.w / 2:.1f}" y="{e.y - e.h / 2:.1f}" width="{e.w:.1f}" height="{e.h:.1f}" rx="2" class="entity"/>'
        )
    for rel in d.rels:
        shapes.append(
            f'<polygon points="{rel.x:.1f},{rel.y - rel.hh:.1f} {rel.x + rel.hw:.1f},{rel.y:.1f} '
            f'{rel.x:.1f},{rel.y + rel.hh:.1f} {rel.x - rel.hw:.1f},{rel.y:.1f}" class="rel"/>'
        )
    for e in d.entities:
        for a in e.attrs:
            shapes.append(
                f'<ellipse cx="{a.x:.1f}" cy="{a.y:.1f}" rx="{a.rx:.1f}" ry="{a.ry:.1f}" class="attr"/>'
            )
    for rel in d.rels:
        for a in rel.attrs:
            shapes.append(
                f'<ellipse cx="{a.x:.1f}" cy="{a.y:.1f}" rx="{a.rx:.1f}" ry="{a.ry:.1f}" class="attr"/>'
            )

    # entity labels
    for e in d.entities:
        size = 18 if text_width(e.label, 18) <= e.w - 20 else 16
        texts.append(
            f'<text x="{e.x:.1f}" y="{e.y - 5:.1f}" text-anchor="middle" class="entity-label" font-size="{size}">{escape(e.label)}</text>'
        )
        texts.append(
            f'<text x="{e.x:.1f}" y="{e.y + 19:.1f}" text-anchor="middle" class="ident">{escape(e.ident)}</text>'
        )

    # relationship labels
    for rel in d.rels:
        texts.append(
            f'<text x="{rel.x:.1f}" y="{rel.y + 5:.1f}" text-anchor="middle" class="rel-label">{escape(rel.label)}</text>'
        )

    # attribute labels
    def attr_text(a: Attr, cx: float, cy: float):
        deco = ' text-decoration="underline"' if a.key and not a.partial else ""
        texts.append(
            f'<text x="{cx:.1f}" y="{cy - 3:.1f}" text-anchor="middle" class="attr-label"{deco}>{escape(a.label)}</text>'
        )
        if a.partial:
            half = text_width(a.label, 15) / 2
            texts.append(
                f'<line x1="{cx - half:.1f}" y1="{cy + 0.5:.1f}" x2="{cx + half:.1f}" y2="{cy + 0.5:.1f}" class="pk-partial"/>'
            )
        texts.append(
            f'<text x="{cx:.1f}" y="{cy + 16:.1f}" text-anchor="middle" class="field">{escape(a.name)}</text>'
        )

    for e in d.entities:
        for a in e.attrs:
            attr_text(a, a.x, a.y)
    for rel in d.rels:
        for a in rel.attrs:
            attr_text(a, a.x, a.y)

    # title, note, legend
    texts.append(
        f'<text x="{d.width / 2:.0f}" y="52" text-anchor="middle" class="title">{escape(d.title)}</text>'
    )
    note_svg = ""
    if d.note:
        note_h = 24 + 22 * len(d.note)
        note_y = d.height - 128 - note_h
        note_svg = (
            f'<rect x="70" y="{note_y}" width="{d.width - 140}" height="{note_h}" rx="8" class="note-box"/>'
            + "".join(
                f'<text x="92" y="{note_y + 30 + i * 22}" class="note">{escape(line)}</text>'
                for i, line in enumerate(d.note)
            )
        )
    legend_svg = ""
    if d.show_legend:
        legend_svg = (
            f'<line x1="70" y1="{d.height - 74}" x2="{d.width - 70}" y2="{d.height - 74}" class="rule"/>'
            f'<text x="{d.width / 2:.0f}" y="{d.height - 44}" text-anchor="middle" class="legend">{escape(d.legend)}</text>'
        )

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{d.width}" height="{d.height}" '
        f'viewBox="0 0 {d.width} {d.height}" role="img" aria-label="{escape(d.title)}">',
        "<defs>",
        '<pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse">',
        '<path d="M 24 0 L 0 0 0 24" fill="none" stroke="#eeeeee" stroke-width="0.7"/>',
        "</pattern>",
        "<style>",
        "  text { font-family: 'Microsoft YaHei', 'PingFang SC', 'Noto Sans CJK SC', sans-serif; fill: #1b1b1b; }",
        "  .entity { fill: #ffffff; stroke: #1b1b1b; stroke-width: 1.8; }",
        "  .rel { fill: #ffffff; stroke: #1b1b1b; stroke-width: 1.8; }",
        "  .attr { fill: #ffffff; stroke: #1b1b1b; stroke-width: 1.5; }",
        "  .note-box { fill: #fbfbfb; stroke: #cfcfcf; stroke-width: 1.2; stroke-dasharray: 5 4; }",
        "  line { stroke: #333333; stroke-width: 1.35; }",
        "  .title { font-size: 27px; font-weight: 700; }",
        "  .entity-label { font-weight: 600; }",
        "  .ident { font-size: 11px; fill: #666666; }",
        "  .rel-label { font-size: 16px; }",
        "  .attr-label { font-size: 15px; }",
        "  .field { font-size: 10.5px; fill: #6a6a6a; }",
        "  .card { font-size: 17px; }",
        "  .note { font-size: 13px; fill: #444444; }",
        "  .legend { font-size: 13px; fill: #555555; }",
        "  .rule { stroke: #dddddd; stroke-width: 1; }",
        "  .hop { fill: none; stroke: #333333; stroke-width: 1.35; }",
        "  .pk-partial { stroke: #1b1b1b; stroke-width: 1.2; stroke-dasharray: 4 3; }",
        "</style>",
        "</defs>",
        f'<rect width="{d.width}" height="{d.height}" fill="url(#grid)"/>',
        '<g class="lines">',
        *attr_lines,
        *rel_lines,
        "</g>",
        '<g class="shapes">',
        *shapes,
        "</g>",
        '<g class="labels">',
        *texts,
        "</g>",
        note_svg,
        legend_svg,
        "</svg>",
        "",
    ]
    return "\n".join(svg), segments


# ---------------------------------------------------------------------------
# validation


def validate(d: Diagram, segments) -> list[str]:
    problems: list[str] = []
    # crossing relationship / attribute lines
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            s, t = segments[i], segments[j]
            p = seg_intersection(s["a"], s["b"], t["a"], t["b"])
            if p is None:
                continue
            if close(p, s["a"]) or close(p, s["b"]) or close(p, t["a"]) or close(p, t["b"]):
                continue
            if s["kind"] == t["kind"] == "rel" and (s.get("hop") != t.get("hop")):
                # documented, unavoidable crossing drawn as a line jump
                continue
            problems.append(
                f"crossing: {s['kind']} {s['label']} x {t['kind']} {t['label']} at ({p[0]:.0f},{p[1]:.0f})"
            )

    # overlap checks: entity, diamond, ellipse bounding boxes
    boxes = []
    for e in d.entities:
        boxes.append((f"entity {e.ident}", (e.x, e.y, e.w, e.h)))
    for rel in d.rels:
        boxes.append((f"relationship {rel.label}", (rel.x, rel.y, rel.hw * 2, rel.hh * 2)))
    for e in d.entities:
        for a in e.attrs:
            boxes.append((f"attribute {a.label}", (a.x, a.y, a.rx * 2, a.ry * 2)))
    for rel in d.rels:
        for a in rel.attrs:
            boxes.append((f"relationship attribute {a.label}", (a.x, a.y, a.rx * 2, a.ry * 2)))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if box_overlap(boxes[i][1], boxes[j][1], gap=6):
                # ellipses attached to the same shape are allowed to be near but not overlap
                problems.append(f"overlap: {boxes[i][0]} / {boxes[j][0]}")

    # lines must not pass through a third shape (a line hidden by an entity,
    # diamond, or another attribute ellipse is a visual defect even when the
    # centre lines do not intersect).
    def inside_rect(p, e):
        return abs(p[0] - e.x) < e.w / 2 - 2 and abs(p[1] - e.y) < e.h / 2 - 2

    def inside_diamond(p, rel):
        return abs(p[0] - rel.x) / rel.hw + abs(p[1] - rel.y) / rel.hh < 0.96

    def inside_ellipse(p, a):
        return ((p[0] - a.x) / (a.rx - 2)) ** 2 + ((p[1] - a.y) / (a.ry - 2)) ** 2 < 1.0

    penetrations = set()
    for s in segments:
        for step in range(3, 98, 2):
            t = step / 100.0
            p = (s["a"][0] + (s["b"][0] - s["a"][0]) * t, s["a"][1] + (s["b"][1] - s["a"][1]) * t)
            for e in d.entities:
                if e.ident not in s["ignore"] and inside_rect(p, e):
                    penetrations.add(f"line passes through entity {e.ident}: {s['kind']} {s['label']}")
            for ri, rel in enumerate(d.rels):
                if f"rel:{ri}" not in s["ignore"] and inside_diamond(p, rel):
                    penetrations.add(
                        f"line passes through relationship {rel.label}: {s['kind']} {s['label']}"
                    )
            for e in d.entities:
                for a in e.attrs:
                    if f"attr:{e.ident}:{a.label}" not in s["ignore"] and inside_ellipse(p, a):
                        penetrations.add(f"line passes through attribute {a.label}: {s['kind']} {s['label']}")
            for ri, rel in enumerate(d.rels):
                for a in rel.attrs:
                    if f"relattr:{ri}:{a.label}" not in s["ignore"] and inside_ellipse(p, a):
                        penetrations.add(
                            f"line passes through relationship attribute {a.label}: {s['kind']} {s['label']}"
                        )
    problems.extend(sorted(penetrations))

    # text fit
    for e in d.entities:
        if text_width(e.label, 18) > e.w - 18:
            problems.append(f"entity text may overflow: {e.label}")
        for a in e.attrs:
            widest = max(text_width(a.label, 15), text_width(a.name, 10.5))
            if widest > a.rx * 2 - 16:
                problems.append(
                    f"attribute text may overflow: {a.label} ({widest:.0f} > {a.rx * 2 - 16:.0f})"
                )
    for rel in d.rels:
        if text_width(rel.label, 16) > rel.hw * 2 - 24:
            problems.append(f"relationship text may overflow: {rel.label}")
        for a in rel.attrs:
            widest = max(text_width(a.label, 15), text_width(a.name, 10.5))
            if widest > a.rx * 2 - 16:
                problems.append(f"relationship attribute text may overflow: {a.label}")

    return problems


# ---------------------------------------------------------------------------
# diagrams


def v1() -> Diagram:
    user = Entity(
        "USER",
        "用户",
        360,
        470,
        attrs=[
            Attr("用户编号", "user_id", 105, 190, 85, 38, True, "W", 0.2),
            Attr("用户名", "username", 105, 310, 72, 38, False, "W", 0.5),
            Attr("显示名", "display_name", 105, 430, 88, 38, False, "W", 0.8),
            Attr("创建时间", "created_at", 190, 690, 85, 38, False, "SW", 0.5),
        ],
    )
    project = Entity(
        "PROJECT",
        "研究项目",
        1240,
        470,
        attrs=[
            Attr("项目编号", "project_id", 1060, 250, 85, 38, True, "NW", 0.5),
            Attr("项目名称", "name", 1240, 220, 78, 38, False, "N", 0.5),
            Attr("项目描述", "description", 1430, 250, 95, 38, False, "NE", 0.5),
            Attr("创建时间", "created_at", 1430, 690, 85, 38, False, "SE", 0.5),
        ],
    )
    participate = Rel(
        "参与",
        800,
        470,
        [("USER", "E", 0.5, "N"), ("PROJECT", "W", 0.5, "M")],
        hw=90,
        hh=50,
        attrs=[
            Attr("项目角色", "role", 800, 270, 82, 38, False, (800, 470)),
            Attr("加入时间", "joined_at", 800, 680, 85, 38, False, (800, 470)),
        ],
    )
    return Diagram(
        "v1-identity-project",
        "分 E-R 图一 · 身份与项目",
        1600,
        1000,
        [user, project],
        [participate],
        note=[
            "模型修正：取消“项目成员”实体，也取消重复的“负责”联系。用户与项目直接用“参与”联系表达多对多；",
            "项目角色、加入时间是联系属性；每个项目恰好一名 role = owner 的成员，负责人不可移除或转让（K02、K03）。",
            "projects.owner_id / owner_role 是复合外键实现，不再作为重复联系或重复属性绘制。",
        ],
    )


def v2() -> Diagram:
    user = Entity(
        "USER",
        "用户",
        230,
        250,
        attrs=[
            Attr("用户编号", "user_id", 230, 115, 80, 38, True, "N", 0.5),
        ],
    )
    project = Entity(
        "PROJECT",
        "研究项目",
        250,
        620,
        attrs=[
            Attr("项目编号", "project_id", 120, 470, 85, 38, True, "NW", 0.5),
            Attr("项目名称", "name", 300, 390, 78, 38, False, "N", 0.6),
            Attr("项目描述", "description", 270, 860, 92, 38, False, "S", 0.5),
        ],
    )
    paper = Entity(
        "PAPER",
        "论文",
        860,
        620,
        w=220,
        h=100,
        attrs=[
            Attr("论文编号", "paper_id", 540, 440, 85, 38, True, "NW", 0.5),
            Attr("标题", "title", 1060, 510, 60, 38, False, "NE", 0.5),
            Attr("年份", "year", 600, 530, 55, 38, False, "W", 0.2),
            Attr("文件摘要", "digest", 500, 760, 82, 38, False, "W", 0.8),
            Attr("处理状态", "status", 640, 880, 72, 38, False, "SW", 0.5),
        ],
    )
    author = Entity(
        "AUTHOR",
        "作者",
        860,
        220,
        attrs=[
            Attr("作者编号", "author_id", 650, 130, 78, 38, True, "W", 0.5),
            Attr("姓名", "name", 1070, 160, 55, 38, False, "E", 0.5),
        ],
    )
    topic = Entity(
        "TOPIC",
        "研究主题",
        860,
        1020,
        attrs=[
            Attr("主题编号", "topic_id", 650, 1120, 78, 38, True, "W", 0.5),
            Attr("主题名称", "name", 1070, 1120, 80, 38, False, "E", 0.5),
        ],
    )
    chunk = Entity(
        "CHUNK",
        "原文片段",
        1480,
        620,
        attrs=[
            Attr("片段编号", "chunk_id", 1480, 390, 82, 38, True, "N", 0.5),
            Attr("页码", "page_number", 1290, 470, 70, 38, False, "NW", 0.5),
            Attr("来源摘录", "excerpt", 1480, 850, 82, 38, False, "S", 0.5),
            Attr("是否合成", "synthetic", 1650, 750, 72, 38, False, "SE", 0.5),
        ],
    )
    model = Entity(
        "EMBEDDING_MODEL",
        "向量模型",
        2160,
        620,
        w=220,
        attrs=[
            Attr("模型编号", "model_id", 2160, 390, 82, 38, True, "N", 0.5),
            Attr("模型供应方", "provider", 2350, 470, 88, 38, False, "NE", 0.5),
            Attr("模型名称", "model_name", 1980, 480, 84, 38, False, "NW", 0.5),
            Attr("模型版本", "revision", 2350, 770, 78, 38, False, "SE", 0.5),
            Attr("向量维度", "dimensions", 2160, 850, 78, 38, False, "S", 0.5),
            Attr("距离度量", "distance_metric", 1970, 770, 92, 38, False, "SW", 0.5),
        ],
    )
    upload_rel = Rel("上传", 520, 250, [("USER", "E", 0.5, "1"), ("PAPER", "N", 0.08, "N")], hw=78, hh=45)
    import_rel = Rel("导入", 555, 620, [("PROJECT", "E", 0.5, "1"), ("PAPER", "W", 0.5, "N")], hw=78, hh=45)
    split_rel = Rel("切分", 1170, 620, [("PAPER", "E", 0.5, "1"), ("CHUNK", "W", 0.5, "N")], hw=78, hh=45)
    write_rel = Rel(
        "撰写",
        860,
        420,
        [("PAPER", "N", 0.5, "N"), ("AUTHOR", "S", 0.5, "M")],
        hw=78,
        hh=45,
        attrs=[Attr("作者顺序", "author_order", 1060, 400, 82, 38, False, (860, 420))],
    )
    topic_rel = Rel("归类", 860, 820, [("PAPER", "S", 0.5, "N"), ("TOPIC", "N", 0.5, "M")], hw=78, hh=45)
    vector_rel = Rel(
        "向量化",
        1820,
        620,
        [("CHUNK", "E", 0.5, "N"), ("EMBEDDING_MODEL", "W", 0.5, "M")],
        hw=88,
        hh=50,
        attrs=[
            Attr("向量值", "embedding", 1800, 400, 78, 38, False, (1820, 620)),
            Attr("创建时间", "created_at", 1780, 860, 80, 38, False, (1820, 620)),
        ],
    )
    return Diagram(
        "v2-literature-source",
        "分 E-R 图二 · 文献与来源",
        2500,
        1420,
        [user, project, paper, author, topic, chunk, model],
        [upload_rel, import_rel, split_rel, write_rel, topic_rel, vector_rel],
        note=[
            "概念合并：PAPER 与 PROJECT_PAPER 合并为“论文”，一次导入对应一份论文元数据，公开 paper_id 仍指项目导入。",
            "外键画成联系：project_papers.imported_by 改为“用户 —上传— 论文”1:N，不再作为论文属性；EMBEDDING 并入“向量化”联系（K15）。",
            "作者、主题、向量模型在数据字典中均带非空 project_id（按项目隔离），本图不重复画这些辅助外键；完整字段与索引状态见数据字典。",
        ],
    )


def v3() -> Diagram:
    project = Entity(
        "PROJECT",
        "研究项目",
        300,
        300,
        attrs=[
            Attr("项目编号", "project_id", 100, 170, 82, 38, True, "NW", 0.5),
            Attr("项目名称", "name", 300, 150, 78, 38, False, "N", 0.5),
        ],
    )
    user = Entity(
        "USER",
        "用户",
        640,
        575,
        attrs=[
            Attr("用户编号", "user_id", 640, 440, 78, 38, True, "N", 0.5),
        ],
    )
    task = Entity(
        "TASK",
        "处理任务",
        300,
        850,
        attrs=[
            Attr("任务编号", "task_id", 100, 690, 78, 38, True, "NW", 0.5),
            Attr("任务类型", "kind", 100, 800, 65, 38, False, "W", 0.3),
            Attr("任务状态", "status", 100, 910, 65, 38, False, "W", 0.6),
            Attr("领取次数", "attempts", 100, 1020, 70, 38, False, "SW", 0.5),
        ],
    )
    paper = Entity(
        "PAPER",
        "论文",
        300,
        1350,
        attrs=[
            Attr("论文编号", "paper_id", 100, 1300, 80, 38, True, "W", 0.3),
            Attr("标题", "title", 100, 1410, 55, 38, False, "W", 0.7),
        ],
    )
    result = Entity(
        "TASK_RESULT",
        "研究结果",
        900,
        850,
        w=220,
        attrs=[
            Attr("任务编号", "task_id", 720, 1000, 78, 38, True, "SW", 0.5),
            Attr("结果类型", "kind", 1010, 700, 62, 38, False, "N", 0.75),
            Attr("结果载荷", "payload", 1150, 700, 62, 38, False, "NE", 0.5),
        ],
    )
    evidence = Entity(
        "EVIDENCE_FIELD",
        "证据字段",
        900,
        1350,
        w=220,
        attrs=[
            Attr("证据字段名", "field_name", 900, 1510, 88, 38, True, "S", 0.5, partial=True),
            Attr("证据状态", "status", 700, 1400, 62, 38, False, "SW", 0.5),
            Attr("证据文本", "text", 1110, 1400, 62, 38, False, "SE", 0.5),
        ],
    )
    source = Entity(
        "RESULT_SOURCE",
        "结果来源",
        1500,
        850,
        w=220,
        attrs=[
            Attr("引用编号", "citation_id", 1440, 700, 72, 38, True, "N", 0.4, partial=True),
            Attr("引用作用域", "scope", 1610, 700, 78, 38, True, "N", 0.75, partial=True),
            Attr("页码", "page_number", 1290, 600, 60, 38, False, "NW", 0.5),
            Attr("来源摘录", "excerpt", 1800, 610, 70, 38, False, "NE", 0.5),
        ],
    )
    chunk = Entity(
        "CHUNK",
        "原文片段",
        2150,
        850,
        attrs=[
            Attr("片段编号", "chunk_id", 2150, 1000, 78, 38, True, "S", 0.5),
            Attr("页码", "page_number", 2330, 850, 55, 38, False, "E", 0.5),
        ],
    )
    report = Entity(
        "REPORT",
        "研究报告",
        900,
        300,
        attrs=[
            Attr("报告编号", "report_id", 800, 150, 75, 38, True, "NW", 0.5),
            Attr("创建时间", "created_at", 1010, 150, 72, 38, False, "NE", 0.5),
        ],
    )
    version = Entity(
        "REPORT_VERSION",
        "报告版本",
        1500,
        300,
        w=220,
        attrs=[
            Attr("版本编号", "version_id", 1320, 150, 75, 38, True, "NW", 0.5),
            Attr("版本序号", "number", 1470, 120, 62, 38, False, "N", 0.4),
            Attr("标题", "title", 1600, 140, 55, 38, False, "N", 0.8),
            Attr("正文", "body", 1730, 175, 55, 38, False, "NE", 0.5),
            Attr("研究问题", "research_question", 1450, 470, 88, 38, False, "S", 0.3),
        ],
    )
    version_source = Entity(
        "VERSION_SOURCE",
        "版本引用快照",
        2150,
        300,
        w=220,
        attrs=[
            Attr("引用编号", "citation_id", 2100, 150, 72, 38, True, "N", 0.3, partial=True),
            Attr("页码", "page_number", 2380, 300, 55, 38, False, "E", 0.3),
            Attr("来源摘录", "excerpt", 2380, 430, 62, 38, False, "E", 0.8),
        ],
    )
    rels = [
        Rel("管理报告", 600, 300, [("PROJECT", "E", 0.5, "1"), ("REPORT", "W", 0.5, "N")], hw=85, hh=45),
        Rel(
            "保存版本",
            1200,
            300,
            [("REPORT", "E", 0.5, "1"), ("REPORT_VERSION", "W", 0.5, "N")],
            hw=85,
            hh=45,
        ),
        Rel(
            "保留快照",
            1830,
            300,
            [("REPORT_VERSION", "E", 0.5, "1"), ("VERSION_SOURCE", "W", 0.5, "N")],
            hw=85,
            hh=45,
        ),
        Rel(
            "基于",
            1730,
            500,
            [("REPORT_VERSION", "S", 0.65, "1"), ("REPORT_VERSION", "E", 0.9, "1")],
            hw=70,
            hh=42,
        ),
        Rel("保存", 1150, 500, [("USER", "E", 0.3, "1"), ("REPORT_VERSION", "SW", 0.5, "N")], hw=70, hh=42),
        Rel("发起", 450, 712, [("USER", "SW", 0.5, "1"), ("TASK", "NE", 0.5, "N")], hw=70, hh=42),
        Rel("调度", 300, 575, [("PROJECT", "S", 0.5, "1"), ("TASK", "N", 0.5, "N")], hw=70, hh=42),
        Rel("选定范围", 300, 1100, [("TASK", "S", 0.5, "N"), ("PAPER", "N", 0.5, "M")], hw=85, hh=45),
        Rel("产生", 600, 850, [("TASK", "E", 0.5, "1"), ("TASK_RESULT", "W", 0.5, "1")], hw=78, hh=45),
        Rel(
            "提取",
            900,
            1100,
            [("TASK_RESULT", "S", 0.5, "1"), ("EVIDENCE_FIELD", "N", 0.5, "N")],
            hw=78,
            hh=45,
        ),
        Rel(
            "引用",
            1200,
            850,
            [("TASK_RESULT", "E", 0.5, "1"), ("RESULT_SOURCE", "W", 0.5, "N")],
            hw=78,
            hh=45,
        ),
        Rel(
            "支撑",
            1200,
            1100,
            [("EVIDENCE_FIELD", "NE", 0.5, "1"), ("RESULT_SOURCE", "SW", 0.5, "N")],
            hw=85,
            hh=45,
        ),
        Rel("定位", 1800, 850, [("RESULT_SOURCE", "E", 0.5, "N"), ("CHUNK", "W", 0.5, "1")], hw=78, hh=45),
        Rel(
            "快照定位",
            2150,
            575,
            [("VERSION_SOURCE", "S", 0.5, "N"), ("CHUNK", "N", 0.5, "1")],
            hw=85,
            hh=45,
        ),
    ]
    return Diagram(
        "v3-research-report",
        "分 E-R 图三 · 研究与报告",
        2600,
        1800,
        [project, user, task, paper, result, evidence, source, chunk, report, version, version_source],
        rels,
        note=[
            "概念修正：结果来源 RESULT_SOURCE 与版本引用快照 VERSION_SOURCE 分开表达（C06）；外键均画成联系，不再作为属性：",
            "tasks.actor_id →“用户 —发起— 处理任务”，report_versions.created_by →“保存”，base_version_id →“基于”自联系（每版至多一个前版/后继，K11），",
            "片段引用只通过“定位/快照定位”表达。研究结果与处理任务 1:1，共享主键 task_id；已提交尝试 committed_attempt 见 V4/总图的“采纳”联系。",
            "虚下划线为部分键：证据字段=task_id+field_name，结果来源=task_id+scope+citation_id，版本引用快照=version_id+citation_id。current_version_id 仍为实现指针。",
        ],
    )


def v4() -> Diagram:
    user = Entity(
        "USER",
        "用户",
        300,
        300,
        attrs=[
            Attr("用户编号", "user_id", 100, 190, 78, 38, True, "W", 0.2),
            Attr("用户名", "username", 100, 310, 68, 38, False, "W", 0.5),
            Attr("显示名", "display_name", 100, 430, 82, 38, False, "W", 0.8),
        ],
    )
    idem = Entity(
        "IDEMPOTENCY_RECORD",
        "幂等记录",
        900,
        300,
        w=240,
        attrs=[
            Attr("业务操作", "operation", 790, 140, 70, 38, True, "N", 0.25, partial=True),
            Attr("幂等键", "key", 950, 120, 58, 38, True, "N", 0.55, partial=True),
            Attr("请求指纹", "request_fingerprint", 1130, 160, 105, 38, False, "N", 0.85),
            Attr("原响应快照", "response_snapshot", 1250, 300, 92, 38, False, "E", 0.3),
            Attr("创建时间", "created_at", 1250, 430, 75, 38, False, "E", 0.8),
        ],
    )
    project = Entity(
        "PROJECT",
        "研究项目",
        900,
        900,
        attrs=[
            Attr("项目编号", "project_id", 1120, 1000, 78, 38, True, "SE", 0.5),
            Attr("项目名称", "name", 980, 1090, 68, 38, False, "S", 0.3),
            Attr("项目描述", "description", 1320, 990, 88, 38, False, "E", 0.2),
        ],
    )
    audit = Entity(
        "AUDIT_EVENT",
        "审计事件",
        300,
        900,
        w=220,
        attrs=[
            Attr("事件编号", "event_id", 100, 760, 72, 38, True, "NW", 0.5),
            Attr("发生时间", "timestamp", 90, 870, 72, 38, False, "W", 0.3),
            Attr("请求编号", "request_id", 90, 980, 72, 38, False, "W", 0.8),
            Attr("操作动作", "action", 100, 1080, 60, 38, False, "SW", 0.5),
            Attr("安全决策", "decision", 250, 1120, 62, 38, False, "S", 0.4),
            Attr("执行状态", "execution_status", 420, 1135, 88, 38, False, "SE", 0.5),
        ],
    )
    outbox = Entity(
        "OUTBOX_EVENT",
        "待投递领域事件",
        300,
        1400,
        w=220,
        attrs=[
            Attr("事件编号", "event_id", 100, 1290, 70, 38, True, "NW", 0.5),
            Attr("投递次数", "delivery_attempts", 90, 1430, 80, 38, False, "W", 0.5),
            Attr("确认投递时间", "published_at", 300, 1550, 88, 38, False, "S", 0.5),
        ],
    )
    task = Entity(
        "TASK",
        "处理任务",
        900,
        1450,
        attrs=[
            Attr("任务编号", "task_id", 680, 1400, 76, 38, True, "NW", 0.5),
            Attr("任务类型", "kind", 680, 1520, 62, 38, False, "W", 0.3),
            Attr("任务状态", "status", 680, 1640, 62, 38, False, "SW", 0.5),
            Attr("领取次数", "attempts", 900, 1620, 65, 38, False, "S", 0.5),
        ],
    )
    attempt = Entity(
        "TASK_ATTEMPT",
        "执行尝试",
        1500,
        1450,
        w=220,
        attrs=[
            Attr("尝试序号", "attempt_no", 1650, 1300, 74, 38, True, "NE", 0.5, partial=True),
            Attr("尝试状态", "status", 1700, 1420, 55, 38, False, "E", 0.3),
            Attr("租约到期", "lease_expires_at", 1720, 1560, 90, 38, False, "E", 0.9),
            Attr("结束时间", "finished_at", 1300, 1620, 72, 38, False, "SW", 0.5),
        ],
    )
    result = Entity(
        "TASK_RESULT",
        "研究结果",
        1500,
        1900,
        w=220,
        attrs=[
            Attr("任务编号", "task_id", 1250, 1900, 78, 38, True, "W", 0.5),
            Attr("结果类型", "kind", 1720, 1900, 62, 38, False, "E", 0.5),
        ],
    )
    rels = [
        Rel(
            "提交请求",
            600,
            300,
            [("USER", "E", 0.5, "1"), ("IDEMPOTENCY_RECORD", "W", 0.5, "N")],
            hw=85,
            hh=45,
        ),
        Rel(
            "幂等归属",
            900,
            600,
            [("IDEMPOTENCY_RECORD", "S", 0.5, "N"), ("PROJECT", "N", 0.5, "1")],
            hw=85,
            hh=45,
        ),
        Rel("审计归属", 600, 900, [("PROJECT", "W", 0.5, "1"), ("AUDIT_EVENT", "E", 0.5, "N")], hw=85, hh=45),
        Rel("执行动作", 300, 600, [("USER", "S", 0.5, "1"), ("AUDIT_EVENT", "N", 0.5, "N")], hw=85, hh=45),
        Rel(
            "事件归属",
            600,
            1150,
            [("PROJECT", "SW", 0.5, "1"), ("OUTBOX_EVENT", "NE", 0.5, "N")],
            hw=85,
            hh=45,
        ),
        Rel("领取", 1200, 1450, [("TASK", "E", 0.5, "1"), ("TASK_ATTEMPT", "W", 0.5, "N")], hw=78, hh=45),
        Rel(
            "采纳",
            1500,
            1680,
            [("TASK_ATTEMPT", "S", 0.5, "1"), ("TASK_RESULT", "N", 0.5, "1")],
            hw=70,
            hh=42,
        ),
    ]
    return Diagram(
        "v4-control-audit",
        "分 E-R 图四 · 运行控制与审计",
        1900,
        2250,
        [user, idem, project, audit, outbox, task, attempt, result],
        rels,
        note=[
            "去冗余：outbox 与 audit 只按 event_id 幂等关联，不是强制外键，因此不画“投递为”菱形；审计也可独立追加。",
            "部分键（虚下划线）：幂等记录 = user_id + project_id + operation + key（所属用户、项目经“提交请求/幂等归属”确定）；执行尝试 = task_id + attempt_no。",
            "task_results.committed_attempt 画为“执行尝试 —采纳— 研究结果”1:1（每个结果恰好采纳一次尝试）；幂等记录的任务/版本重放外键不重复画成联系。",
            "基数只标最大值；审计/outbox 的项目、操作者可空（登录事件等），最小参与见数据字典。outbox.actor_id 是可选审计维度，不另画联系。",
        ],
    )


def total() -> Diagram:
    # Layout idea: USER, PROJECT, PAPER and TASK form a K4 (participate, upload,
    # schedule/start, import, scope).  PROJECT-TASK ("调度") is routed round the
    # left so that the identity/control cluster (idempotency, audit) sits in the
    # left face, while reports, literature and research results surround the
    # K4 on the right.  "保存" (USER-REPORT_VERSION) is the single unavoidable
    # crossing (K3,3: USER/PROJECT/CHUNK x PAPER/TASK/REPORT_VERSION) and is
    # drawn with a line jump.
    project = Entity(
        "PROJECT",
        "研究项目",
        1500,
        500,
        attrs=[
            Attr("项目编号", "project_id", 1440, 330, 78, 38, True, "N", 0.2),
            Attr("项目名称", "name", 1680, 320, 68, 38, False, "N", 0.85),
        ],
    )
    outbox = Entity(
        "OUTBOX_EVENT",
        "待投递领域事件",
        650,
        330,
        w=220,
        attrs=[
            Attr("事件编号", "event_id", 420, 190, 70, 38, True, "NW", 0.5),
            Attr("投递次数", "delivery_attempts", 380, 450, 80, 38, False, "SW", 0.5),
            Attr("确认投递时间", "published_at", 720, 500, 88, 38, False, "S", 0.5),
        ],
    )
    user = Entity(
        "USER",
        "用户",
        1500,
        1450,
        attrs=[
            Attr("用户编号", "user_id", 1600, 1650, 78, 38, True, "S", 0.75),
            Attr("用户名", "username", 1840, 1580, 68, 38, False, "SE", 0.5),
            Attr("显示名", "display_name", 1780, 1760, 82, 38, False, "SE", 0.5),
        ],
    )
    idem = Entity(
        "IDEMPOTENCY_RECORD",
        "幂等记录",
        1150,
        1050,
        w=220,
        attrs=[
            Attr("幂等键", "key", 1340, 960, 58, 38, True, "NE", 0.5, partial=True),
            Attr("响应快照", "response_snapshot", 1380, 1110, 85, 38, False, "E", 0.5),
            Attr("业务操作", "operation", 940, 1190, 70, 38, True, "SW", 0.5, partial=True),
            Attr("请求指纹", "request_fingerprint", 1100, 1320, 105, 38, False, "S", 0.3),
        ],
    )
    audit = Entity(
        "AUDIT_EVENT",
        "审计事件",
        700,
        1500,
        w=220,
        attrs=[
            Attr("事件编号", "event_id", 420, 1400, 72, 38, True, "NW", 0.5),
            Attr("发生时间", "timestamp", 400, 1530, 72, 38, False, "W", 0.5),
            Attr("请求编号", "request_id", 560, 1680, 72, 38, False, "S", 0.2),
            Attr("操作动作", "action", 720, 1730, 60, 38, False, "S", 0.6),
            Attr("安全决策", "decision", 900, 1700, 62, 38, False, "SE", 0.5),
            Attr("执行状态", "execution_status", 1060, 1600, 88, 38, False, "E", 0.9),
        ],
    )
    paper = Entity(
        "PAPER",
        "论文",
        2300,
        1450,
        attrs=[
            Attr("论文编号", "paper_id", 2240, 1290, 80, 38, True, "N", 0.3),
            Attr("标题", "title", 2390, 1300, 55, 38, False, "N", 0.8),
            Attr("年份", "year", 2280, 1640, 52, 38, False, "S", 0.4),
            Attr("文件摘要", "digest", 2420, 1720, 72, 38, False, "S", 0.85),
        ],
    )
    author = Entity(
        "AUTHOR",
        "作者",
        2720,
        1050,
        attrs=[
            Attr("作者编号", "author_id", 2660, 880, 75, 38, True, "N", 0.2),
            Attr("姓名", "name", 2930, 1050, 52, 38, False, "E", 0.5),
        ],
    )
    topic = Entity(
        "TOPIC",
        "研究主题",
        2650,
        1950,
        attrs=[
            Attr("主题编号", "topic_id", 2560, 2120, 72, 38, True, "SW", 0.5),
            Attr("主题名称", "name", 2760, 2130, 68, 38, False, "SE", 0.5),
        ],
    )
    chunk = Entity(
        "CHUNK",
        "原文片段",
        2900,
        1450,
        attrs=[
            Attr("片段编号", "chunk_id", 2880, 1250, 78, 38, True, "N", 0.4),
            Attr("页码", "page_number", 3080, 1600, 55, 38, False, "SE", 0.5),
            Attr("来源摘录", "excerpt", 2990, 1760, 70, 38, False, "S", 0.95),
        ],
    )
    model = Entity(
        "EMBEDDING_MODEL",
        "向量模型",
        3500,
        1450,
        w=220,
        attrs=[
            Attr("模型编号", "model_id", 3500, 1270, 78, 38, True, "N", 0.5),
            Attr("模型名称", "model_name", 3660, 1630, 80, 38, False, "SE", 0.5),
            Attr("向量维度", "dimensions", 3440, 1660, 72, 38, False, "S", 0.3),
        ],
    )
    task = Entity(
        "TASK",
        "处理任务",
        1500,
        2450,
        attrs=[
            Attr("任务编号", "task_id", 1260, 2620, 76, 38, True, "SW", 0.5),
            Attr("任务类型", "kind", 1380, 2740, 62, 38, False, "S", 0.2),
            Attr("任务状态", "status", 1540, 2800, 62, 38, False, "S", 0.5),
        ],
    )
    attempt = Entity(
        "TASK_ATTEMPT",
        "执行尝试",
        1900,
        2900,
        w=220,
        attrs=[
            Attr("尝试序号", "attempt_no", 1650, 2950, 74, 38, True, "W", 0.5, partial=True),
            Attr("尝试状态", "status", 1820, 3070, 55, 38, False, "S", 0.2),
            Attr("租约到期", "lease_expires_at", 2020, 3070, 90, 38, False, "S", 0.8),
            Attr("结束时间", "finished_at", 2200, 2960, 72, 38, False, "E", 0.5),
        ],
    )
    result = Entity(
        "TASK_RESULT",
        "研究结果",
        2300,
        2450,
        w=220,
        attrs=[
            Attr("任务编号", "task_id", 2170, 2290, 78, 38, True, "NW", 0.5),
            Attr("结果类型", "kind", 2330, 2250, 62, 38, False, "N", 0.6),
            Attr("结果载荷", "payload", 2480, 2300, 62, 38, False, "NE", 0.5),
        ],
    )
    evidence = Entity(
        "EVIDENCE_FIELD",
        "证据字段",
        2600,
        2900,
        w=220,
        attrs=[
            Attr("证据字段名", "field_name", 2600, 3070, 88, 38, True, "S", 0.5, partial=True),
            Attr("证据状态", "status", 2400, 3020, 62, 38, False, "SW", 0.5),
            Attr("证据文本", "text", 2810, 3020, 55, 38, False, "SE", 0.5),
        ],
    )
    source = Entity(
        "RESULT_SOURCE",
        "结果来源",
        2900,
        2450,
        w=220,
        attrs=[
            Attr("引用编号", "citation_id", 3150, 2320, 72, 38, True, "NE", 0.5, partial=True),
            Attr("引用作用域", "scope", 3230, 2450, 78, 38, True, "E", 0.5, partial=True),
            Attr("页码", "page_number", 3200, 2590, 58, 38, False, "E", 0.9),
            Attr("来源摘录", "excerpt", 3050, 2680, 62, 38, False, "SE", 0.5),
        ],
    )
    report = Entity(
        "REPORT",
        "研究报告",
        2100,
        500,
        attrs=[
            Attr("报告编号", "report_id", 2000, 330, 75, 38, True, "NW", 0.5),
            Attr("创建时间", "created_at", 2200, 330, 72, 38, False, "NE", 0.5),
        ],
    )
    version = Entity(
        "REPORT_VERSION",
        "报告版本",
        2700,
        500,
        w=220,
        attrs=[
            Attr("版本编号", "version_id", 2560, 330, 75, 38, True, "NW", 0.5),
            Attr("版本序号", "number", 2715, 290, 62, 38, False, "N", 0.55),
            Attr("标题", "title", 2860, 330, 55, 38, False, "NE", 0.5),
            Attr("正文", "body", 2430, 700, 55, 38, False, "SW", 0.5),
            Attr("研究问题", "research_question", 2700, 720, 88, 38, False, "S", 0.5),
        ],
    )
    version_source = Entity(
        "VERSION_SOURCE",
        "版本引用快照",
        3350,
        500,
        w=220,
        attrs=[
            Attr("引用编号", "citation_id", 3290, 330, 72, 38, True, "N", 0.3, partial=True),
            Attr("页码", "page_number", 3600, 440, 55, 38, False, "E", 0.3),
            Attr("来源摘录", "excerpt", 3600, 580, 62, 38, False, "E", 0.8),
        ],
    )

    rels = [
        # identity / control (left face)
        Rel(
            "参与",
            1500,
            975,
            [("USER", "N", 0.5, "N"), ("PROJECT", "S", 0.5, "M")],
            hw=88,
            hh=48,
            attrs=[
                Attr("项目角色", "role", 1710, 900, 80, 38, False, (1500, 975)),
                Attr("加入时间", "joined_at", 1710, 1040, 80, 38, False, (1500, 975)),
            ],
        ),
        Rel(
            "幂等归属",
            1300,
            800,
            [("PROJECT", "SW", 0.5, "1"), ("IDEMPOTENCY_RECORD", "N", 0.85, "N")],
            hw=78,
            hh=45,
        ),
        Rel(
            "提交请求",
            1300,
            1270,
            [("IDEMPOTENCY_RECORD", "SE", 0.5, "N"), ("USER", "NW", 0.5, "1")],
            hw=70,
            hh=42,
        ),
        Rel(
            "审计归属", 900, 1050, [("PROJECT", "W", 0.8, "1"), ("AUDIT_EVENT", "N", 0.5, "N")], hw=78, hh=45
        ),
        Rel("执行动作", 1130, 1480, [("AUDIT_EVENT", "E", 0.5, "N"), ("USER", "W", 0.8, "1")], hw=78, hh=45),
        Rel(
            "事件归属",
            1060,
            400,
            [("OUTBOX_EVENT", "E", 0.5, "N"), ("PROJECT", "NW", 0.5, "1")],
            hw=85,
            hh=45,
        ),
        Rel("调度", 150, 1475, [("PROJECT", "W", 0.3, "1"), ("TASK", "W", 0.5, "N")], hw=78, hh=45),
        # who did it
        Rel("上传", 1900, 1450, [("USER", "E", 0.5, "1"), ("PAPER", "W", 0.5, "N")], hw=78, hh=45),
        Rel("发起", 1500, 1950, [("USER", "S", 0.5, "1"), ("TASK", "N", 0.5, "N")], hw=78, hh=45),
        Rel(
            "保存",
            2250,
            1000,
            [("USER", "NE", 0.5, "1"), ("REPORT_VERSION", "S", 0.3, "N")],
            hw=78,
            hh=45,
            hop=True,
        ),
        # literature
        Rel("导入", 1900, 1000, [("PROJECT", "SE", 0.5, "1"), ("PAPER", "NW", 0.5, "N")], hw=78, hh=45),
        Rel(
            "撰写",
            2560,
            1210,
            [("PAPER", "NE", 0.5, "N"), ("AUTHOR", "SW", 0.5, "M")],
            hw=78,
            hh=45,
            attrs=[Attr("作者顺序", "author_order", 2470, 1080, 78, 38, False, (2560, 1210))],
        ),
        Rel("归类", 2600, 1700, [("PAPER", "SE", 0.5, "N"), ("TOPIC", "N", 0.5, "M")], hw=78, hh=45),
        Rel("切分", 2600, 1450, [("PAPER", "E", 0.5, "1"), ("CHUNK", "W", 0.5, "N")], hw=78, hh=45),
        Rel(
            "向量化",
            3200,
            1450,
            [("CHUNK", "E", 0.5, "N"), ("EMBEDDING_MODEL", "W", 0.5, "M")],
            hw=85,
            hh=45,
            attrs=[
                Attr("向量值", "embedding", 3200, 1260, 75, 38, False, (3200, 1450)),
                Attr("创建时间", "created_at", 3230, 1660, 75, 38, False, (3200, 1450)),
            ],
        ),
        # reports
        Rel("管理报告", 1800, 500, [("PROJECT", "E", 0.5, "1"), ("REPORT", "W", 0.5, "N")], hw=85, hh=45),
        Rel(
            "保存版本",
            2400,
            500,
            [("REPORT", "E", 0.5, "1"), ("REPORT_VERSION", "W", 0.5, "N")],
            hw=85,
            hh=45,
        ),
        Rel(
            "基于",
            2960,
            730,
            [("REPORT_VERSION", "S", 0.65, "1"), ("REPORT_VERSION", "E", 0.9, "1")],
            hw=70,
            hh=42,
        ),
        Rel(
            "保留快照",
            3030,
            500,
            [("REPORT_VERSION", "E", 0.5, "1"), ("VERSION_SOURCE", "W", 0.5, "N")],
            hw=85,
            hh=45,
        ),
        Rel(
            "快照定位",
            3150,
            950,
            [("VERSION_SOURCE", "S", 0.5, "N"), ("CHUNK", "NE", 0.5, "1")],
            hw=85,
            hh=45,
        ),
        # research tasks
        Rel("选定范围", 1900, 1950, [("TASK", "NE", 0.5, "N"), ("PAPER", "SW", 0.5, "M")], hw=85, hh=45),
        Rel("领取", 1700, 2680, [("TASK", "SE", 0.5, "1"), ("TASK_ATTEMPT", "NW", 0.5, "N")], hw=70, hh=42),
        Rel("产生", 1900, 2450, [("TASK", "E", 0.5, "1"), ("TASK_RESULT", "W", 0.5, "1")], hw=78, hh=45),
        Rel(
            "采纳",
            2120,
            2680,
            [("TASK_ATTEMPT", "NE", 0.5, "1"), ("TASK_RESULT", "SW", 0.5, "1")],
            hw=70,
            hh=42,
        ),
        Rel(
            "提取",
            2480,
            2680,
            [("TASK_RESULT", "SE", 0.5, "1"), ("EVIDENCE_FIELD", "NW", 0.5, "N")],
            hw=70,
            hh=42,
        ),
        Rel(
            "引用",
            2650,
            2450,
            [("TASK_RESULT", "E", 0.5, "1"), ("RESULT_SOURCE", "W", 0.5, "N")],
            hw=70,
            hh=42,
        ),
        Rel(
            "支撑",
            2800,
            2680,
            [("EVIDENCE_FIELD", "NE", 0.5, "1"), ("RESULT_SOURCE", "SW", 0.5, "N")],
            hw=70,
            hh=42,
        ),
        Rel("定位", 2900, 1975, [("RESULT_SOURCE", "N", 0.5, "N"), ("CHUNK", "S", 0.5, "1")], hw=78, hh=45),
    ]
    return Diagram(
        "total-er",
        "PaperPilot 概念总 E-R 图",
        3750,
        3400,
        [
            project,
            outbox,
            user,
            idem,
            audit,
            paper,
            author,
            topic,
            chunk,
            model,
            task,
            attempt,
            result,
            evidence,
            source,
            report,
            version,
            version_source,
        ],
        rels,
        note=[
            "概念整合：项目成员、论文作者、论文主题、任务论文范围、片段向量等关联表折叠为菱形联系；外键一律画成联系：上传(imported_by)、发起(tasks.actor_id)、保存(report_versions.created_by)、采纳(committed_attempt)、基于(base_version_id)。",
            "部分键（虚下划线）须与所属实体主键组合：幂等记录=用户+项目+operation+key，执行尝试=task_id+attempt_no，证据字段=task_id+field_name，结果来源=task_id+scope+citation_id，版本引用快照=version_id+citation_id；研究结果与处理任务 1:1 共享 task_id。",
            "“保存”与“导入”的一处交叉不可避免（用户/项目/片段 × 论文/任务/报告版本 构成 K3,3），以跨线弧表示不相交。基数只标最大值，最小参与与可空性见数据字典 K01—K15。",
            "去冗余：不画 projects.owner_id、reports.current_version_id、reports.created_by（=首版保存者）、outbox→audit 等指针/非强制外键；24 张逻辑表与 176 个字段以 resolved-er.svg 和数据字典为准。",
        ],
        legend=LEGEND + "　跨线弧：线条不相交",
    )


DIAGRAMS = [v1, v2, v3, v4, total]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail on crossings, overlaps or text overflow")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    failed = False
    for builder in DIAGRAMS:
        d = builder()
        svg, segments = render(d)
        problems = validate(d, segments)
        path = OUT / (d.name + ".svg")
        path.write_text(svg, encoding="utf-8")
        status = "ok" if not problems else f"{len(problems)} issue(s)"
        print(f"{d.name}: {len(d.entities)} entities, {len(d.rels)} relationships, {status}")
        for p in problems:
            print(f"  - {p}")
        if problems:
            failed = True
    if args.check and failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
