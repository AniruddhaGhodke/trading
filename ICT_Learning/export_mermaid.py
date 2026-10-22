"""Move the notes' flowcharts between Mermaid code blocks and PNG images.

The .mmd files in images/diagrams/ are the editable source.

    python export_mermaid.py            extract each note's ```mermaid block into images/diagrams/<name>.mmd
    python export_mermaid.py --apply    ...and replace the block with a link to images/<name>.png
    python export_mermaid.py --restore  replace the PNG link with the ```mermaid block from the .mmd

Render a PNG with
    npx -y @mermaid-js/mermaid-cli@11.4.2 -i images/diagrams/<name>.mmd -o images/<name>.png -b white -s 2
The notes currently use Mermaid blocks; VS Code's built-in preview shows them blank on this
machine, so read them in Markdown Preview Enhanced's preview.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "images" / "diagrams"
OUT.mkdir(parents=True, exist_ok=True)

NAMES = {
    "README.md": "flow_reading_order",
    "02_htf.md": "flow_htf_topdown",
    "07_fvg_ifvg.md": "flow_ifvg",
    "09_putting_it_together.md": "flow_full_sequence",
}
BLOCK = re.compile(r"```mermaid\n(.*?)```\n", re.S)

for md, name in NAMES.items():
    path = ROOT / md
    text = path.read_text(encoding="utf-8")

    if "--restore" in sys.argv:
        link = f"![{name}](images/{name}.png)\n"
        assert text.count(link) == 1, f"expected one {link!r} in {md}"
        source = (OUT / f"{name}.mmd").read_text(encoding="utf-8")
        path.write_text(text.replace(link, f"```mermaid\n{source}```\n"), encoding="utf-8")
        print("restored Mermaid block in", md)
        continue

    blocks = BLOCK.findall(text)
    if not blocks:
        print("already converted:", md)
        continue
    assert len(blocks) == 1, md
    (OUT / f"{name}.mmd").write_text(blocks[0], encoding="utf-8")
    print("extracted", md, "->", name)
    if "--apply" in sys.argv:  # swap the block for the PNG once it has been rendered
        assert (ROOT / "images" / f"{name}.png").exists(), f"render {name}.png first"
        path.write_text(BLOCK.sub(f"![{name}](images/{name}.png)\n", text), encoding="utf-8")
        print("  replaced block with image in", md)
