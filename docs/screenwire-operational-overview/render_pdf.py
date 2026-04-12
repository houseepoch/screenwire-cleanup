from __future__ import annotations

import argparse
import re
from pathlib import Path

from fpdf import FPDF


FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
SANS_REGULAR = FONT_DIR / "DejaVuSans.ttf"
SANS_BOLD = FONT_DIR / "DejaVuSans-Bold.ttf"
MONO_REGULAR = FONT_DIR / "DejaVuSansMono.ttf"
MONO_BOLD = FONT_DIR / "DejaVuSansMono-Bold.ttf"

HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
ORDERED_RE = re.compile(r"^(\d+)\.\s+(.*)$")


class OperationalOverviewPDF(FPDF):
    def footer(self) -> None:
        self.set_y(-10)
        self.set_text_color(120, 120, 120)
        self.set_font("DejaVu", size=8.5)
        self.cell(0, 5, text=f"Page {self.page_no()}", align="C")
        self.set_text_color(0, 0, 0)


def iter_blocks(markdown: str):
    lines = markdown.splitlines()
    idx = 0

    while idx < len(lines):
        raw = lines[idx]
        stripped = raw.strip()

        if not stripped:
            idx += 1
            continue

        if stripped.startswith("```"):
            code_lines: list[str] = []
            idx += 1
            while idx < len(lines) and not lines[idx].strip().startswith("```"):
                code_lines.append(lines[idx].rstrip())
                idx += 1
            idx += 1
            yield ("code", code_lines)
            continue

        heading = HEADING_RE.match(stripped)
        if heading:
            yield ("heading", len(heading.group(1)), heading.group(2).strip())
            idx += 1
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            items: list[str] = []
            while idx < len(lines):
                candidate = lines[idx].strip()
                if candidate.startswith("- ") or candidate.startswith("* "):
                    items.append(candidate[2:].strip())
                    idx += 1
                    continue
                break
            yield ("bullet_list", items)
            continue

        ordered = ORDERED_RE.match(stripped)
        if ordered:
            items: list[tuple[str, str]] = []
            while idx < len(lines):
                candidate = ORDERED_RE.match(lines[idx].strip())
                if not candidate:
                    break
                items.append((candidate.group(1), candidate.group(2).strip()))
                idx += 1
            yield ("ordered_list", items)
            continue

        paragraph_lines = [stripped]
        idx += 1
        while idx < len(lines):
            candidate = lines[idx].rstrip()
            candidate_stripped = candidate.strip()
            if not candidate_stripped:
                break
            if candidate_stripped.startswith("```"):
                break
            if HEADING_RE.match(candidate_stripped):
                break
            if candidate_stripped.startswith("- ") or candidate_stripped.startswith("* "):
                break
            if ORDERED_RE.match(candidate_stripped):
                break
            paragraph_lines.append(candidate_stripped)
            idx += 1
        yield ("paragraph", " ".join(paragraph_lines))


def register_fonts(pdf: FPDF) -> None:
    pdf.add_font("DejaVu", style="", fname=str(SANS_REGULAR))
    pdf.add_font("DejaVu", style="B", fname=str(SANS_BOLD))
    pdf.add_font("DejaVuMono", style="", fname=str(MONO_REGULAR))
    pdf.add_font("DejaVuMono", style="B", fname=str(MONO_BOLD))


def render_heading(pdf: FPDF, level: int, text: str) -> None:
    spacing_before = {1: 3, 2: 4, 3: 3}[level]
    font_size = {1: 22, 2: 15, 3: 12}[level]
    line_height = {1: 10, 2: 7, 3: 6}[level]

    pdf.ln(spacing_before)
    pdf.set_font("DejaVu", style="B", size=font_size)
    pdf.multi_cell(0, line_height, text=text)
    pdf.ln(1)


def render_paragraph(pdf: FPDF, text: str) -> None:
    pdf.set_font("DejaVu", size=10.5)
    pdf.multi_cell(0, 5.2, text=text)
    pdf.ln(0.8)


def render_list(pdf: FPDF, items: list[str], ordered: bool = False) -> None:
    pdf.set_font("DejaVu", size=10.5)
    indent = pdf.l_margin + 5
    width = pdf.w - indent - pdf.r_margin

    for index, item in enumerate(items, start=1):
        prefix = f"{index}. " if ordered else "- "
        pdf.set_x(indent)
        pdf.multi_cell(width, 5.2, text=f"{prefix}{item}")
    pdf.ln(0.8)


def render_ordered_list(pdf: FPDF, items: list[tuple[str, str]]) -> None:
    pdf.set_font("DejaVu", size=10.5)
    indent = pdf.l_margin + 5
    width = pdf.w - indent - pdf.r_margin

    for number, item in items:
        pdf.set_x(indent)
        pdf.multi_cell(width, 5.2, text=f"{number}. {item}")
    pdf.ln(0.8)


def render_code_block(pdf: FPDF, lines: list[str]) -> None:
    pdf.ln(1.5)
    pdf.set_font("DejaVuMono", size=8.6)
    pdf.set_fill_color(245, 245, 245)
    indent = pdf.l_margin + 4
    width = pdf.w - indent - pdf.r_margin

    if not lines:
        lines = [""]

    for line in lines:
        pdf.set_x(indent)
        pdf.multi_cell(width, 4.2, text=line or " ", fill=True)
    pdf.ln(1.5)


def render_markdown_to_pdf(input_path: Path, output_path: Path) -> None:
    pdf = OperationalOverviewPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_margins(left=18, top=18, right=18)
    register_fonts(pdf)
    pdf.set_title("ScreenWire Operational Overview")
    pdf.set_author("OpenAI Codex")
    pdf.add_page()

    for block in iter_blocks(input_path.read_text(encoding="utf-8")):
        block_type = block[0]
        if block_type == "heading":
            _, level, text = block
            render_heading(pdf, level, text)
        elif block_type == "paragraph":
            _, text = block
            render_paragraph(pdf, text)
        elif block_type == "bullet_list":
            _, items = block
            render_list(pdf, items, ordered=False)
        elif block_type == "ordered_list":
            _, items = block
            render_ordered_list(pdf, items)
        elif block_type == "code":
            _, lines = block
            render_code_block(pdf, lines)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))


def parse_args() -> argparse.Namespace:
    doc_dir = Path(__file__).resolve().parent
    default_input = doc_dir / "screenwire-operational-overview.md"
    default_output = doc_dir / "screenwire-operational-overview.pdf"

    parser = argparse.ArgumentParser(description="Render the ScreenWire operational overview PDF.")
    parser.add_argument("--input", type=Path, default=default_input)
    parser.add_argument("--output", type=Path, default=default_output)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    render_markdown_to_pdf(args.input, args.output)


if __name__ == "__main__":
    main()
