"""Renders the pitch deck (.pptx) from the Storyteller's content.

The agent writes words; this file decides the look: a dark 16:9 theme,
one accent color, screenshots on the demo slides, and speaker notes on
every slide so the presenter has a script.
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

from ..core.models import ProjectState

BG = RGBColor(0x0B, 0x10, 0x20)
ACCENT = RGBColor(0x81, 0x8C, 0xF8)  # indigo
TITLE = RGBColor(0xF8, 0xFA, 0xFC)
BODY = RGBColor(0xCB, 0xD5, 0xE1)
MUTED = RGBColor(0x94, 0xA3, 0xB8)
FONT = "Segoe UI"

WIDTH, HEIGHT = Inches(13.333), Inches(7.5)


def render_deck(state: ProjectState, screenshots: list[Path], out: Path) -> Path:
    pitch, plan, idea, links = state.pitch, state.plan, state.chosen, state.outputs
    prs = Presentation()
    prs.slide_width, prs.slide_height = WIDTH, HEIGHT
    shots = iter(screenshots)

    # Title slide
    s = _slide(prs)
    _text(s, f"{state.hackathon.upper()}  ·  {idea.track.upper()}", 0.9, 2.0, 11.5, 0.5, 14, ACCENT, bold=True)
    _text(s, plan.product_name, 0.9, 2.5, 11.5, 1.3, 60, TITLE, bold=True)
    _text(s, pitch.tagline, 0.9, 3.8, 11.5, 0.9, 26, BODY)
    _text(s, _links_line(links.deploy_url, links.repo_url), 0.9, 6.3, 11.5, 0.5, 14, MUTED)
    _notes(s, f"Hi, we're presenting {plan.product_name}. {pitch.tagline}")

    # Content slides
    for i, slide in enumerate(pitch.slides, 1):
        s = _slide(prs)
        shot = next(shots, None) if slide.show_screenshot else None
        text_width = 5.6 if shot else 11.5
        _text(s, f"{i:02d}", 0.9, 0.6, 1.0, 0.4, 14, ACCENT, bold=True)
        _text(s, slide.title, 0.9, 0.95, text_width, 1.1, 36, TITLE, bold=True)
        _bullets(s, slide.bullets, 0.9, 2.3, text_width, 4.6)
        if shot:
            _picture(s, shot, left=6.9, top=1.2, max_w=5.6, max_h=5.2)
        _notes(s, slide.notes)

    # Closing slide
    s = _slide(prs)
    _text(s, "Try it live", 0.9, 2.2, 11.5, 1.0, 48, TITLE, bold=True)
    _text(s, links.deploy_url or "Run it locally: see the README", 0.9, 3.3, 11.5, 0.6, 24, ACCENT)
    _text(s, links.repo_url or "", 0.9, 3.9, 11.5, 0.6, 20, BODY)
    _text(s, f"Built in {state.team.hours} hours for {state.hackathon}", 0.9, 6.3, 11.5, 0.5, 14, MUTED)
    _notes(s, "Thank you! We'd love your questions, and you can try it yourself at the link on screen.")

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    return out


def _slide(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = BG
    return slide


def _text(slide, text, left, top, width, height, size, color, bold=False, align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size, run.font.bold, run.font.name = Pt(size), bold, FONT
    run.font.color.rgb = color
    return box


def _bullets(slide, bullets, left, top, width, height):
    tf = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height)).text_frame
    tf.word_wrap = True
    for i, bullet in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(14)
        dot = p.add_run()
        dot.text = "▸  "
        dot.font.size, dot.font.name, dot.font.color.rgb = Pt(20), FONT, ACCENT
        run = p.add_run()
        run.text = bullet
        run.font.size, run.font.name, run.font.color.rgb = Pt(20), FONT, BODY


def _picture(slide, path: Path, left, top, max_w, max_h):
    pic = slide.shapes.add_picture(str(path), Inches(left), Inches(top))
    scale = min(Inches(max_w) / pic.width, Inches(max_h) / pic.height)
    pic.width, pic.height = Emu(int(pic.width * scale)), Emu(int(pic.height * scale))
    pic.line.color.rgb = RGBColor(0x33, 0x41, 0x55)


def _notes(slide, text: str) -> None:
    slide.notes_slide.notes_text_frame.text = text


def _links_line(deploy_url: str | None, repo_url: str | None) -> str:
    return "   ·   ".join(x for x in (deploy_url, repo_url) if x)
