"""Generate the README diagrams as SVG. Standard library only.

    python3 docs/assets/diagrams.py        # writes docs/assets/*.svg next to this file

Hand-laid-out so the pictures stay stable across renderers (GitHub, editors, browsers).
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
FONT = "'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"
INK = "#101820"
MUTED = "#46606e"
LINE = "#6f93a6"
BOX_FILL = "#f3f7fa"
BOX_STROKE = "#3e6577"
GATE_FILL = "#2b5a4a"
GATE_STROKE = "#5fd3a8"
STORE_FILL = "#e8f1f6"
WARN_FILL = "#fdecea"
WARN_STROKE = "#c0392b"
GOOD_FILL = "#e9f7f0"
GOOD_STROKE = "#2e8b57"


class Svg:
    def __init__(self, width: int, height: int, title: str, desc: str) -> None:
        self.w, self.h = width, height
        self.parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="t d" '
            f'font-family="{FONT}">',
            f'  <title id="t">{escape(title)}</title>',
            f'  <desc id="d">{escape(desc)}</desc>',
            "  <defs>",
            '    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
            'markerHeight="8" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker>',
            '    <marker id="arrow-dim" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
            'markerHeight="8" orient="auto-start-reverse">'
            '<path d="M0,0 L10,5 L0,10 z" fill="#a9bfcb"/></marker>',
            "  </defs>",
            f'  <rect width="{width}" height="{height}" rx="18" fill="#ffffff"/>',
        ]

    def add(self, s: str) -> None:
        self.parts.append("  " + s)

    def text(self, x: float, y: float, s: str, size: int = 15, fill: str = INK,
             weight: str = "normal", anchor: str = "middle", italic: bool = False) -> None:
        style = ' font-style="italic"' if italic else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
                 f'font-weight="{weight}" text-anchor="{anchor}"{style}>{escape(s)}</text>')

    def lines(self, x: float, y: float, rows: list[str], size: int = 12, fill: str = MUTED,
              anchor: str = "middle", gap: int = 15) -> None:
        for i, row in enumerate(rows):
            self.text(x, y + i * gap, row, size=size, fill=fill, anchor=anchor)

    def box(self, x: float, y: float, w: float, h: float, title: str, sub: list[str] | None = None,
            fill: str = BOX_FILL, stroke: str = BOX_STROKE, title_fill: str = INK,
            sub_fill: str = MUTED, title_size: int = 15) -> None:
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="10" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>')
        cx = x + w / 2
        sub = sub or []
        block = title_size + 4 + len(sub) * 15
        ty = y + (h - block) / 2 + title_size - 2
        self.text(cx, ty, title, size=title_size, fill=title_fill, weight="600")
        self.lines(cx, ty + 17, sub, fill=sub_fill)

    def gate(self, cx: float, cy: float, w: float, h: float, title: str,
             sub: list[str] | None = None) -> None:
        d = h / 2
        pts = [(cx - w / 2 + d, cy - d), (cx + w / 2 - d, cy - d), (cx + w / 2, cy),
               (cx + w / 2 - d, cy + d), (cx - w / 2 + d, cy + d), (cx - w / 2, cy)]
        path = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        self.add(f'<polygon points="{path}" fill="{GATE_FILL}" stroke="{GATE_STROKE}" '
                 'stroke-width="2"/>')
        sub = sub or []
        block = 15 + 4 + len(sub) * 15
        ty = cy - block / 2 + 13
        self.text(cx, ty, title, size=15, fill="#eef4f2", weight="600")
        self.lines(cx, ty + 17, sub, fill="#cfe0e6")

    def store(self, x: float, y: float, w: float, h: float, title: str,
              sub: list[str] | None = None) -> None:
        ry = 9
        self.add(f'<path d="M{x:.1f},{y + ry:.1f} a{w / 2:.1f},{ry} 0 0 1 {w:.1f},0 '
                 f'v{h - 2 * ry:.1f} a{w / 2:.1f},{ry} 0 0 1 -{w:.1f},0 z" '
                 f'fill="{STORE_FILL}" stroke="{BOX_STROKE}" stroke-width="1.6"/>')
        self.add(f'<ellipse cx="{x + w / 2:.1f}" cy="{y + ry:.1f}" rx="{w / 2:.1f}" ry="{ry}" '
                 f'fill="{STORE_FILL}" stroke="{BOX_STROKE}" stroke-width="1.6"/>')
        sub = sub or []
        block = 15 + 4 + len(sub) * 15
        ty = y + ry + (h - ry - block) / 2 + 13
        self.text(x + w / 2, ty, title, size=15, weight="600")
        self.lines(x + w / 2, ty + 17, sub)

    def actor(self, cx: float, cy: float, label: str) -> None:
        self.add(f'<circle cx="{cx:.1f}" cy="{cy - 22:.1f}" r="10" fill="{BOX_FILL}" '
                 f'stroke="{BOX_STROKE}" stroke-width="1.6"/>')
        self.add(f'<path d="M{cx:.1f},{cy - 12:.1f} v22 M{cx - 14:.1f},{cy - 4:.1f} h28 '
                 f'M{cx:.1f},{cy + 10:.1f} l-12,18 M{cx:.1f},{cy + 10:.1f} l12,18" '
                 f'stroke="{BOX_STROKE}" stroke-width="2" fill="none"/>')
        self.text(cx, cy + 46, label, size=14, weight="600")

    def arrow(self, x1: float, y1: float, x2: float, y2: float, label: str = "",
              dashed: bool = False, dim: bool = False, label_dy: float = -8) -> None:
        dash = ' stroke-dasharray="6,5"' if dashed else ""
        color = "#a9bfcb" if dim else LINE
        marker = "arrow-dim" if dim else "arrow"
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                 f'stroke="{color}" stroke-width="2"{dash} marker-end="url(#{marker})"/>')
        if label:
            self.text((x1 + x2) / 2, (y1 + y2) / 2 + label_dy, label, size=12, fill=MUTED)

    def path(self, d: str, label: str = "", lx: float = 0, ly: float = 0,
             dashed: bool = False, dim: bool = False) -> None:
        dash = ' stroke-dasharray="6,5"' if dashed else ""
        color = "#a9bfcb" if dim else LINE
        marker = "arrow-dim" if dim else "arrow"
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"{dash} '
                 f'marker-end="url(#{marker})"/>')
        if label:
            self.text(lx, ly, label, size=12, fill=MUTED)

    def group(self, x: float, y: float, w: float, h: float, label: str,
              fill: str = "#fbfcfd") -> None:
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="14" '
                 f'fill="{fill}" stroke="#c8d6de" stroke-width="1.4" stroke-dasharray="4,4"/>')
        self.text(x + 14, y + 22, label, size=13, fill=MUTED, weight="600", anchor="start")

    def caption(self, s: str) -> None:
        self.text(self.w / 2, self.h - 16, s, size=12, fill=MUTED, italic=True)

    def write(self, name: str) -> Path:
        self.parts.append("</svg>")
        out = HERE / name
        out.write_text("\n".join(self.parts) + "\n", encoding="utf-8", newline="\n")
        return out


# --- 1. the pipeline --------------------------------------------------------------------------

def pipeline() -> Path:
    s = Svg(1240, 350, "The skill-factory pipeline",
            "describe, capture, generate, validate, render, evaluate, regression, package, "
            "human approval gate, install, registry and rollback.")
    stages = [
        ("describe", ["your paragraph", "or spec file"]),
        ("capture", ["spec.json"]),
        ("generate", ["SKILL.md"]),
        ("validate", ["static checks", "nothing runs"]),
        ("render", ["Claude + Codex", "variants"]),
        ("evaluate", ["trigger + behaviour", "cases, graded"]),
        ("regression", ["vs last approved", "version"]),
        ("package", ["immutable,", "checksummed"]),
    ]
    x, y, w, h, gap = 24, 70, 128, 76, 14
    s.group(x + 1 * (w + gap) - 8, y - 38, 7 * (w + gap) - gap + 16, h + 54,
            "skillz run does all of this in one command, then stops")
    for i, (title, sub) in enumerate(stages):
        bx = x + i * (w + gap)
        s.box(bx, y, w, h, title, sub)
        if i < len(stages) - 1:
            s.arrow(bx + w, y + h / 2, bx + w + gap, y + h / 2)
    gy = 252
    s.gate(300, gy, 190, 64, "human approval gate", ["skillz approve"])
    s.box(470, gy - 32, 170, 64, "install", ["skillz install", "only approved versions"])
    s.store(700, gy - 34, 190, 70, "registry", ["skills/registry.json", "who, what, where, when"])
    s.box(960, gy - 32, 200, 64, "rollback", ["skillz rollback", "previous approved version"])
    last_cx = x + 7 * (w + gap) + w / 2
    s.path(f"M{last_cx:.1f},{y + h:.1f} v34 H405 v{gy - 32 - y - h - 34:.1f}",
           "awaiting approval", last_cx - 80, y + h + 24)
    s.arrow(395, gy, 470, gy)
    s.arrow(640, gy, 700, gy, "record")
    s.arrow(890, gy, 960, gy)
    s.path(f"M1060,{gy + 32} v26 H555 v-26", "reinstall from the registry", 800, gy + 50,
           dashed=True)
    s.text(610, gy - 44, "into .claude/skills/<name>/ or .agents/skills/<name>/", size=12,
           fill=MUTED)
    return s.write("pipeline.svg")


# --- 2. one source, two platforms --------------------------------------------------------------

def platforms() -> Path:
    s = Svg(1240, 400, "One canonical skill, two platform variants",
            "skills/name/ is the only source; skillz render produces a Claude variant and a "
            "Codex variant; skillz install copies them into the platform directories.")
    s.group(30, 40, 330, 320, "skills/<name>/  (canonical, the only thing you edit)")
    s.box(60, 90, 270, 96, "SKILL.md", ["frontmatter: name, description,",
                                        "optional license, allowed-tools, metadata",
                                        "short body, progressive disclosure"])
    s.box(60, 210, 270, 70, "scripts/  references/  assets/", ["only when the spec asks"])
    s.box(60, 296, 270, 50, ".factory/", ["reports, versions, candidates"], fill="#fafafa",
          stroke="#b8c7d0", title_fill=MUTED)
    s.path("M360,150 H470", "skillz render", 415, 138)
    s.path("M360,250 H470", "skillz render", 415, 238)
    s.box(470, 100, 300, 100, "Claude variant", [".factory/dist/claude/",
                                                 "SKILL.md + supporting files",
                                                 "nothing else needed"])
    s.box(470, 220, 300, 110, "Codex variant", [".factory/dist/codex/",
                                                "same files",
                                                "+ agents/openai.yaml (name, description, interface)"])
    s.path("M770,150 H890", "skillz install", 830, 138)
    s.path("M770,275 H890", "skillz install", 830, 263)
    s.store(890, 100, 320, 100, "Claude Code", ["project  .claude/skills/<name>/",
                                                "user       ~/.claude/skills/<name>/"])
    s.store(890, 225, 320, 100, "Codex", ["project  .agents/skills/<name>/",
                                          "user       ~/.agents/skills/<name>/"])
    s.caption("Canonical files are byte-identical before and after rendering; "
              "install verifies every file against the version manifest first.")
    return s.write("platforms.svg")


# --- 3. a release, step by step ------------------------------------------------------------------

def release() -> Path:
    s = Svg(1240, 640, "A release, step by step",
            "You run skillz run, read the reports, approve, install, and roll back; "
            "skillz writes reports and versions under .factory and copies the variant into "
            "the platform directory.")
    lanes = [("You", 110), ("skillz", 420), ("skills/<name>/.factory/", 760),
             (".claude/skills or .agents/skills", 1090)]
    top, bottom = 70, 580
    for name, cx in lanes:
        if name == "You":
            s.actor(cx, 50, name)
        else:
            s.box(cx - 120, 24, 240, 44, name)
        s.add(f'<line x1="{cx}" y1="{top + 30}" x2="{cx}" y2="{bottom}" stroke="#c8d6de" '
              'stroke-width="1.6" stroke-dasharray="5,5"/>')
    steps = [
        (0, 1, 'skillz run "paragraph"', False),
        (1, 2, "spec, validate, render, eval, regression reports", False),
        (1, 2, "versions/0.1.0/  manifest.json with sha256 of every file", False),
        (1, 0, "pipeline.md: awaiting approval", True),
        (0, 0, "read pipeline.md and eval.md", False),
        (0, 1, "skillz approve <name> 0.1.0", False),
        (1, 2, "versions/0.1.0/approval.json  (who, when)", False),
        (0, 1, "skillz install <name> --platform claude --scope project", False),
        (1, 2, "verify checksums against manifest.json", False),
        (1, 3, "copy the rendered variant  (atomic swap)", False),
        (1, 2, "registry.json: name, version, platform, scope, approval", False),
        (0, 1, "skillz rollback <name>", False),
        (1, 3, "reinstall the previous approved version", False),
    ]
    y = 128
    for n, (a, b, label, dashed) in enumerate(steps, 1):
        xa, xb = lanes[a][1], lanes[b][1]
        s.add(f'<circle cx="{xa}" cy="{y}" r="11" fill="{INK}"/>')
        s.text(xa, y + 4, str(n), size=11, fill="#ffffff", weight="600")
        if a == b:
            s.path(f"M{xa + 11},{y} h60 v24 h-50")
            s.text(xa + 80, y + 16, label, size=12, fill=MUTED, anchor="start")
            y += 50
        else:
            dx = 12 if xb > xa else -12
            s.arrow(xa + dx, y, xb - dx, y, dashed=dashed)
            if xb > xa:
                s.text(xa + 24, y - 8, label, size=12, fill=MUTED, anchor="start")
            else:
                s.text(xa - 24, y - 8, label, size=12, fill=MUTED, anchor="end")
            y += 32
    s.add(f'<rect x="60" y="{bottom - 2}" width="1120" height="36" rx="8" fill="#fff8d6" '
          'stroke="#e6cf6a"/>')
    s.text(620, bottom + 21, "No version is ever deleted. Nothing is ever pushed. "
           "The tool never approves for you.", size=13, weight="600")
    return s.write("release.svg")


# --- 4. version lifecycle ----------------------------------------------------------------------

def lifecycle() -> Path:
    s = Svg(1240, 330, "Version lifecycle",
            "packaged, awaiting approval, approved, installed, rolled back; every version is "
            "immutable and checksummed.")
    s.add(f'<circle cx="60" cy="150" r="9" fill="{INK}"/>')
    s.box(110, 110, 230, 80, "awaiting-approval", ["skillz package / skillz run",
                                                  "manifest.json with sha256"])
    s.gate(470, 150, 150, 62, "skillz approve", ["a human"])
    s.box(600, 110, 230, 80, "approved", ["approval.json: who, when",
                                          "the only installable state"])
    s.box(910, 110, 270, 80, "installed", [".claude/skills or .agents/skills",
                                           "recorded in registry.json"])
    s.arrow(69, 150, 110, 150)
    s.arrow(340, 150, 395, 150)
    s.arrow(545, 150, 600, 150)
    s.arrow(830, 150, 910, 150, "skillz install")
    s.path("M1100,190 v40 H960 v-40", "skillz rollback: the previous approved version", 1030, 252)
    s.path("M180,110 v-40 H270 v40", "package again: a new version, the old one untouched",
           225, 56)
    s.add(f'<rect x="110" y="270" width="1070" height="40" rx="8" fill="{GOOD_FILL}" '
          f'stroke="{GOOD_STROKE}"/>')
    s.text(645, 295, "approve, install, rollback and promote all re-verify the SHA-256 manifest "
           "first: a tampered version cannot move forward", size=13, fill="#1f5f3f", weight="600")
    return s.write("lifecycle.svg")


# --- 5. the challenger loop ---------------------------------------------------------------------

def loop() -> Path:
    s = Svg(1240, 470, "The challenger loop",
            "capture-failure records a real failure; improve builds a candidate; challenge "
            "grades stable and candidate on the same cases and returns a verdict; promote is "
            "a human command that replaces the stable skill and packages a new version.")
    s.box(24, 60, 150, 70, "a real failure", ["a prompt the skill", "got wrong"], fill="#fff8d6",
          stroke="#e6cf6a")
    s.arrow(174, 95, 272, 95, "capture-failure", label_dy=-10)
    s.store(272, 55, 210, 80, "captured.json", ["persistent case, included by",
                                                "every later eval and challenge"])
    s.arrow(482, 95, 570, 95, "improve", label_dy=-10)
    s.box(570, 50, 240, 90, "candidate", [".factory/candidates/<id>/",
                                          "description + triggers extended",
                                          "stable files untouched"])
    s.arrow(810, 95, 892, 95, "challenge", label_dy=-10)
    s.box(892, 40, 324, 110, "challenge", ["validate stable and candidate",
                                           "grade the union of cases on both sides",
                                           "challenge.json + challenge.md"])
    s.add(f'<path d="M1054,150 v40" stroke="{LINE}" stroke-width="2"/>')
    vy = 200
    s.add(f'<polygon points="1054,{vy - 10} 1124,{vy + 30} 1054,{vy + 70} 984,{vy + 30}" '
          f'fill="{BOX_FILL}" stroke="{BOX_STROKE}" stroke-width="1.6"/>')
    s.text(1054, vy + 35, "verdict", size=14, weight="600")
    s.box(1066, 300, 160, 48, "candidate-unsafe", ["validation error"], fill=WARN_FILL,
          stroke=WARN_STROKE, title_fill=WARN_STROKE)
    s.box(886, 300, 160, 48, "candidate-worse", ["a case regressed"], fill=WARN_FILL,
          stroke=WARN_STROKE, title_fill=WARN_STROKE)
    s.box(540, 300, 300, 48, "candidate-better  or  no-difference", ["nothing regressed"],
          fill=GOOD_FILL, stroke=GOOD_STROKE, title_fill="#1f5f3f")
    s.path("M1084,260 L1140,300")
    s.path("M1024,260 L970,300")
    s.path("M984,230 H690 V300")
    s.text(1146, 380, "refused", size=13, fill=WARN_STROKE, weight="600")
    s.text(966, 380, "refused", size=13, fill=WARN_STROKE, weight="600")
    s.gate(400, 324, 220, 64, "skillz promote", ["explicit human command"])
    s.arrow(540, 324, 510, 324)
    s.path("M290,324 H224")
    s.box(54, 290, 170, 70, "new version", ["awaiting approval,", "previous versions intact"])
    s.gate(139, 200, 170, 56, "skillz approve", ["then install, as always"])
    s.path("M139,290 v-62", dashed=True)
    s.caption("Grading is the same lexical-overlap heuristic as skillz eval: a better verdict "
              "is a signal about the description, not proof a model behaves better.")
    return s.write("loop.svg")


# --- 6. architecture ---------------------------------------------------------------------------

def architecture() -> Path:
    s = Svg(1240, 500, "How skill-factory is built",
            "cli.py dispatches to one module per subcommand; pipeline.py chains the stage "
            "modules; evaluate uses evalcases and the opt-in runner; the challenger loop "
            "reuses evalcases, validate and release.")
    s.box(420, 24, 400, 60, "cli.py", ["the skillz command: argparse, one handler per subcommand",
                                       "dispatches to every module below"])
    s.box(470, 116, 300, 56, "pipeline.py", ["skillz run: chains the stages, stops at the gate"])
    s.arrow(620, 84, 620, 116)
    s.group(30, 204, 820, 120, "stage modules, one subcommand each")
    mods = [("capture.py", ["paragraph, YAML,", "JSON to SkillSpec"]),
            ("generate.py", ["SkillSpec to", "SKILL.md"]),
            ("validate.py", ["14 static rules"]),
            ("render.py", ["Claude + Codex", "variants"]),
            ("evaluate.py", ["cases, grades,", "proposals"]),
            ("release.py", ["package, approve,", "regression"])]
    x, w, gap, y, h = 50, 122, 10, 236, 72
    for i, (t, sub) in enumerate(mods):
        s.box(x + i * (w + gap), y, w, h, t, sub, title_size=14)
    s.arrow(620, 172, 620, 204)
    s.box(880, 214, 330, 100, "install.py", ["install, rollback, list", "skills/registry.json",
                                             "exact platform directories only"])
    s.group(30, 350, 820, 100, "the challenger loop")
    loopmods = [("captured.py", ["capture-failure"]), ("improve.py", ["builds a candidate"]),
                ("challenge.py", ["grades both sides"]), ("promote.py", ["human command"])]
    for i, (t, sub) in enumerate(loopmods):
        s.box(50 + i * 200, 380, 180, 54, t, sub, title_size=14)
    s.box(880, 350, 150, 54, "evalcases.py", ["cases, lexical matcher"], title_size=14)
    s.box(1050, 350, 160, 54, "runner.py", ["opt-in claude / codex"], title_size=14)
    s.box(880, 416, 330, 46, "yamlish.py", ["YAML subset reader and writer, used by capture"],
          title_size=14)
    ev_cx = x + 4 * (w + gap) + w / 2
    s.path(f"M{ev_cx},{y + h} v14 H955 V350", "evaluate", 800, 318, dim=True)
    s.path("M1030,377 H1050", dim=True)
    s.path("M850,400 H880 V404", dim=True)
    s.caption("The loop reuses evalcases, validate and release. Standard library only at runtime; "
              "no module runs a skill's scripts, calls git push or spawns a model CLI without --runner.")
    return s.write("architecture.svg")


if __name__ == "__main__":
    for fn in (pipeline, platforms, release, lifecycle, loop, architecture):
        print(fn())
