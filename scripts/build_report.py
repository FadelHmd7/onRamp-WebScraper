"""Render docs/phase1/REPORT.md, diagrams included, to docs/phase1/REPORT.pdf.

    uv run python scripts/build_report.py

Uses the Chromium that Playwright installs for the browser tests
(`uv run playwright install chromium`). The page loads two pinned libraries
from jsDelivr: marked (Markdown) and mermaid (diagrams), so it needs network
access once per build.
"""

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "phase1" / "REPORT.md"
TARGET = SOURCE.with_suffix(".pdf")

MARKED = "https://cdn.jsdelivr.net/npm/marked@15.0.12/marked.min.js"
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"
# Relative links (DECISIONS.md, ../../exports/) point here in the PDF.
REPO_DOCS = "https://github.com/FadelHmd7/onRamp-WebScraper/blob/phase-1/docs/phase1/"

STYLE = """
@page { size: A4; margin: 16mm 15mm 18mm; }
body { font: 10.5pt/1.45 "Segoe UI", system-ui, sans-serif; color: #1c2430; }
h1 { font-size: 20pt; margin: 0 0 6pt; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; border-bottom: 1px solid #d6dbe2; padding-bottom: 3pt;
     break-after: avoid; }
h3 { font-size: 11.5pt; margin: 12pt 0 4pt; break-after: avoid; }
p, li { margin: 4pt 0; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0; font-size: 9pt; break-inside: auto; }
tr { break-inside: avoid; }
th, td { border: 1px solid #d6dbe2; padding: 3pt 5pt; text-align: left; vertical-align: top; }
th { background: #f1f3f6; }
code { font: 8.8pt Consolas, monospace; background: #f1f3f6; padding: 0 2pt; border-radius: 2pt; }
pre code { display: block; padding: 6pt; white-space: pre-wrap; }
hr { border: 0; border-top: 1px solid #d6dbe2; margin: 14pt 0; }
a { color: #2457c5; text-decoration: none; }
img { max-width: 75%; display: block; margin: 6pt auto; }
.mermaid { text-align: center; margin: 8pt 0; break-inside: avoid; }
.mermaid svg { max-width: 100%; max-height: 225mm; height: auto; }
"""


def main() -> int:
    markdown = SOURCE.read_text(encoding="utf-8")
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><base href="{SOURCE.parent.as_uri()}/">
<title>onRamp Web Scraper — Phase 1 report</title><style>{STYLE}</style>
<script src="{MARKED}"></script><script src="{MERMAID}"></script></head>
<body><main id="report"></main>
<script>
  document.getElementById("report").innerHTML = marked.parse({json.dumps(markdown)});
  for (const a of document.querySelectorAll("a[href]")) {{
    const href = a.getAttribute("href");
    if (!/^(https?:|#|mailto:)/.test(href)) a.href = new URL(href, {json.dumps(REPO_DOCS)}).href;
  }}
  for (const code of document.querySelectorAll("code.language-mermaid")) {{
    const div = document.createElement("div");
    div.className = "mermaid";
    div.textContent = code.textContent;
    code.parentElement.replaceWith(div);
  }}
  mermaid.initialize({{ startOnLoad: false, theme: "neutral" }});
  mermaid.run().then(() => {{ document.body.dataset.ready = "yes"; }},
                     (err) => {{ document.body.dataset.ready = "error: " + err; }});
</script></body></html>"""
    page_file = SOURCE.parent / ".report-build.html"
    page_file.write_text(html, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(page_file.as_uri())
            page.wait_for_function("document.body.dataset.ready", timeout=60_000)
            state = page.evaluate("document.body.dataset.ready")
            if state != "yes":
                print(f"a diagram failed to render: {state}", file=sys.stderr)
                return 1
            page.pdf(
                path=str(TARGET),
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=(
                    '<div style="font-size:8pt;color:#888;width:100%;text-align:center">'
                    'onRamp Web Scraper — Phase 1 · <span class="pageNumber"></span> / '
                    '<span class="totalPages"></span></div>'
                ),
                margin={"top": "16mm", "bottom": "18mm", "left": "15mm", "right": "15mm"},
            )
            browser.close()
    finally:
        page_file.unlink(missing_ok=True)
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
