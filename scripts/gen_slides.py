"""Write docs/slides.pdf. Uses only the standard PDF fonts."""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

SLIDES: list[tuple[str, list[str]]] = [
    (
        "TrustScout",
        [
            "Vultr Agent Rush on lablab.ai",
            "Challenge: Blast Radius Zero",
            "A marketplace listing check with screenshot receipts,",
            "and a sandbox that survives the page.",
        ],
    ),
    (
        "Problem",
        [
            "Scam listings cost buyers money.",
            "An agent that browses for them can be hijacked",
            "by the page it is reading.",
            "Hidden instructions, hostile scripts, and prices",
            "that are too good are the same threat.",
        ],
    ),
    (
        "Solution",
        [
            "Paste listing URLs.",
            "A throwaway browser screenshots the page.",
            "A Vultr vision model reads the price and seller.",
            "A second container with no network computes comps.",
            "The verdict links every reason to a receipt.",
            "A human click is required before any action is logged.",
        ],
    ),
    (
        "Architecture",
        [
            "Vultr VM: FastAPI control plane, Docker, Caddy.",
            "Every model call: Vultr Serverless Inference.",
            "Browser sandbox: Playwright, allowlist proxy only.",
            "Code sandbox: python:3.12-slim, network none.",
            "Limits: 1 GB, 1 CPU, 128 pids, 90 second wall clock.",
            "Read-only root, no capabilities, no secrets, then rm -f.",
            "gVisor runsc when it is installed, otherwise runc.",
        ],
    ),
    (
        "Containment",
        [
            "rm -rf: read-only root, host canary untouched.",
            "Fork bomb: pids limit stops it.",
            "Infinite loop: wall-clock kill.",
            "Memory bomb: OOM kill inside the cap.",
            "curl exfil: no network, or the proxy denies the host.",
            "Prompt injection: flagged, stripped, key never present.",
            "The next task runs in a new container.",
        ],
    ),
    (
        "Who pays",
        [
            "Resellers checking a deal before they buy.",
            "Procurement teams reviewing a supplier page.",
            "Marketplace trust and safety teams.",
            "The cost of a miss is a bad purchase or a hijacked agent.",
            "A check is a few model calls, well under a cent in mock math.",
        ],
    ),
    (
        "Revenue and competitors",
        [
            "Free: a few checks a day on the mock-style demo.",
            "Team: a monthly seat for resellers and procurement.",
            "Market: a per-listing price for trust and safety queues.",
            "Competitors: manual review, and extensions with no sandbox.",
            "TrustScout's difference: the receipt and the containment.",
        ],
    ),
    (
        "Roadmap",
        [
            "More markets, still read-only and rate-limited.",
            "A team inbox and a daily price diff.",
            "Optional embeddings for recycled photos.",
            "Paranoid mode: one throwaway VM per task.",
            "The control plane keeps the key. The sandbox never does.",
        ],
    ),
]


def build(path: Path) -> None:
    """Render the slide deck to ``path``."""
    pdf = FPDF(orientation="L", format="Letter")
    pdf.set_auto_page_break(auto=False)
    for title, lines in SLIDES:
        pdf.add_page()
        pdf.set_fill_color(28, 22, 18)
        pdf.rect(0, 0, 279, 18, style="F")
        pdf.set_text_color(243, 236, 223)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_xy(12, 5)
        pdf.cell(0, 8, "TrustScout  ·  Vultr Agent Rush")
        pdf.set_text_color(28, 22, 18)
        pdf.set_font("Helvetica", "B", 28)
        pdf.set_xy(16, 32)
        pdf.multi_cell(240, 14, title)
        pdf.set_font("Helvetica", "", 18)
        y = 62
        for line in lines:
            pdf.set_xy(16, y)
            pdf.multi_cell(240, 10, line)
            y += 14
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(path)


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "docs" / "slides.pdf"
    build(target)
    print(target)
