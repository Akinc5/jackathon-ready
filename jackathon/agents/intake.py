"""Intake agent: reads a hackathon flyer (image) or PDF and extracts the facts."""

from __future__ import annotations

from pathlib import Path

from ..core.models import HackathonBrief
from ..llm import Image
from .base import Agent

SYSTEM_PROMPT = """You are the Intake Agent on a team that helps people win hackathons.
You're given an official hackathon flyer, poster or document. Extract every useful fact into the brief: name, dates, location, themes or tracks, official problem statements (word for word), prizes, judging criteria, rules (eligibility, team size, submission requirements) and sponsors.

Only record what the material actually says. Leave a field empty rather than guess. The Research agent will fill gaps from the web."""

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
MAX_PDF_CHARS = 60_000


class IntakeError(ValueError):
    pass


class IntakeAgent(Agent[HackathonBrief]):
    name = "Intake"
    role = "intake"
    output_model = HackathonBrief
    max_steps = 3

    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    @staticmethod
    def build_task(file: Path, note: str = "") -> tuple[str, list[Image]]:
        """Turn an uploaded file into a task: images go to the model as pictures, PDFs as their text."""
        intro = f"The user's note: {note}\n\n" if note else ""
        suffix = file.suffix.lower()
        if suffix in IMAGE_TYPES:
            return intro + "Extract the hackathon brief from this flyer.", [Image(file.read_bytes(), IMAGE_TYPES[suffix])]
        if suffix == ".pdf":
            text = pdf_text(file)
            return intro + f"Extract the hackathon brief from this document:\n\n{text}", []
        raise IntakeError(f"Can't read {file.name}: upload a PNG, JPG, WEBP or PDF.")


def pdf_text(file: Path) -> str:
    from pypdf import PdfReader

    pages = [page.extract_text() or "" for page in PdfReader(file).pages]
    text = "\n\n".join(p.strip() for p in pages if p.strip())
    if not text:
        raise IntakeError(
            f"{file.name} has no selectable text (it's probably a scanned image). Upload it as a PNG or JPG instead."
        )
    return text[:MAX_PDF_CHARS]
