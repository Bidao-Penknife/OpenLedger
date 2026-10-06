"""Generate one self-contained offline manual from the reviewed Markdown and screenshots."""

from __future__ import annotations

import argparse
import base64
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/user/complete-manual.md"
DESTINATION = ROOT / "src/openledger/resources/help/manual.html"


def render(source: Path = SOURCE, *, allow_missing: bool = False) -> str:
    """Escape all content, embed bounded local PNGs and keep navigation entirely offline."""
    blocks: list[str] = []
    contents: list[str] = []
    paragraph: list[str] = []
    table: list[str] = []
    code: list[str] | None = None

    def flush() -> None:
        if paragraph:
            blocks.append("<p>" + inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()
        if table:
            rows = []
            for index, row in enumerate(table):
                if re.fullmatch(r"[| :\-]+", row):
                    continue
                tag = "th" if index == 0 else "td"
                cells = row.strip().strip("|").split("|")
                rows.append(
                    "<tr>"
                    + "".join(f"<{tag}>{inline(cell.strip())}</{tag}>" for cell in cells)
                    + "</tr>"
                )
            blocks.append("<div class='table'><table>" + "".join(rows) + "</table></div>")
            table.clear()

    def inline(text: str) -> str:
        escaped = html.escape(text)
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)

    for line in source.read_text("utf-8").splitlines():
        if line.startswith("```"):
            flush()
            if code is None:
                code = []
            else:
                blocks.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = None
            continue
        if code is not None:
            code.append(line)
            continue
        heading = re.fullmatch(r"(#{1,3}) (.+)", line)
        image = re.fullmatch(r"!\[([^\]]+)\]\(([^)]+)\)", line)
        if heading:
            flush()
            level = len(heading[1])
            anchor = f"section-{len(contents) + 1}" if level == 2 else "title"
            blocks.append(f"<h{level} id='{anchor}'>{inline(heading[2])}</h{level}>")
            if level == 2:
                contents.append(f"<a href='#{anchor}'>{inline(heading[2])}</a>")
        elif image:
            flush()
            path = (source.parent / image[2]).resolve()
            if not path.is_relative_to(source.parent.resolve()) or path.suffix != ".png":
                raise ValueError("Only manual-owned PNG screenshots are allowed")
            if not path.exists():
                if not allow_missing:
                    raise FileNotFoundError(path)
                continue
            content = path.read_bytes()
            if not content.startswith(b"\x89PNG\r\n\x1a\n") or len(content) > 12 * 1024 * 1024:
                raise ValueError("Invalid or excessive screenshot")
            blocks.append(
                "<figure><img src='data:image/png;base64,"
                + base64.b64encode(content).decode("ascii")
                + "' alt='"
                + html.escape(image[1], quote=True)
                + "'><figcaption>"
                + inline(image[1])
                + "</figcaption></figure>"
            )
        elif line.startswith("|"):
            if paragraph:
                flush()
            table.append(line)
        elif not line.strip():
            flush()
        else:
            if table:
                flush()
            paragraph.append(line)
    flush()
    if code is not None:
        raise ValueError("Unclosed fenced example")
    style = """body{font:16px/1.8 system-ui,sans-serif;margin:0 auto;padding:22px;
max-width:850px;color:#243342;background:#f6f7f9}h1,h2{color:#256f62}
h2{border-top:1px solid #dce4e8;padding-top:22px;margin-top:35px}
nav{background:#fff;padding:18px;border-radius:12px}nav a{display:block;color:#256f62}
code,pre{background:#e8eef1;border-radius:4px}code{padding:2px 4px}
pre{padding:15px;overflow:auto}.table{overflow:auto}table{border-collapse:collapse}
th,td{border:1px solid #cad5db;padding:10px;vertical-align:top;min-width:130px}
img{display:block;max-width:100%;max-height:760px;margin:auto}figure{text-align:center}
figcaption{color:#627283;font-size:14px}@media(prefers-color-scheme:dark){
body{color:#e6edf3;background:#161b22}h1,h2,nav a{color:#87cebb}
nav{background:#202833}code,pre{background:#293440}}"""
    policy = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'"
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta http-equiv='Content-Security-Policy' content=\"" + policy + '">'
        "<title>OpenLedger 完整使用手册</title><style>" + style + "</style></head><body>"
        "<nav aria-label='目录'>"
        + "".join(contents)
        + "</nav>"
        + "\n".join(blocks)
        + "</body></html>\n"
    )


def main() -> None:
    """Fail the release build if reviewed screenshots or generated contents are missing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--allow-missing-images", action="store_true", help="Development only")
    arguments = parser.parse_args()
    output = render(allow_missing=arguments.allow_missing_images)
    if arguments.check:
        if not DESTINATION.is_file() or DESTINATION.read_text("utf-8") != output:
            raise ValueError("Offline manual differs from reviewed source")
    else:
        DESTINATION.parent.mkdir(parents=True, exist_ok=True)
        DESTINATION.write_text(output, "utf-8", newline="\n")
    print("Offline manual generated / verified")


if __name__ == "__main__":
    main()
