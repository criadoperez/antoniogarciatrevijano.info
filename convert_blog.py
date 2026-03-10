"""
Convert blog posts from ficheros/publicos/blog_2006-2011/ for the RAG pipeline.

Each .md file contains a post followed by reader comments (marked with ### headings).
This script strips the comments and the metadata header, keeping only the post title
and body, then converts via Docling's markdown pipeline and writes output to
output/publicos/blog_2006-2011/ — the same format produced by convert_documents.py.

Usage:
    venv/bin/python3 convert_blog.py
"""

import re
import tempfile
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import MarkdownPipelineOptions
from docling.document_converter import DocumentConverter, MarkdownFormatOption

INPUT_DIR = Path("ficheros/publicos/blog_2006-2011")
OUTPUT_DIR = Path("output/publicos/blog_2006-2011")


def extract_post_body(content: str) -> str:
    """
    Return title + date + post body only, stripping author/URL header lines and all comments.

    Structure of each file:
        # Title
        **Fecha:** YYYY-MM-DD
        **Autor:** ...
        **URL original:** ...
        ---
        <post body>
        ### Commenter — date
        <comment text>
        ...
    """
    # Extract title
    title_match = re.match(r"^# (.+)", content)
    title = title_match.group(1).strip() if title_match else ""

    # Extract date
    date_match = re.search(r"\*\*Fecha:\*\*\s*(\d{4}-\d{2}-\d{2})", content)
    date = date_match.group(1) if date_match else ""

    # Everything after the first --- separator is post + comments
    sep = content.find("\n---\n")
    if sep == -1:
        body = content
    else:
        body = content[sep + 5:]

    # Strip comments: everything from the first ### heading onward
    comment_start = re.search(r"\n### ", body)
    if comment_start:
        body = body[: comment_start.start()]

    body = body.strip()

    parts = []
    if title:
        parts.append(f"# {title}")
    if date:
        parts.append(f"Fecha: {date}")
    parts.append("Autor: Antonio García-Trevijano")
    if body:
        parts.append(body)
    return "\n\n".join(parts)


def create_converter() -> DocumentConverter:
    return DocumentConverter(
        format_options={
            InputFormat.MD: MarkdownFormatOption(
                pipeline_options=MarkdownPipelineOptions(keep_code_blocks=False)
            ),
        }
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(INPUT_DIR.glob("*.md"))
    converter = create_converter()
    succeeded, skipped, failed = [], [], []

    for i, f in enumerate(files, 1):
        out_json = OUTPUT_DIR / f.with_suffix(".json").name
        if out_json.exists():
            skipped.append(f.name)
            print(f"[{i}/{len(files)}] SKIP (exists): {f.name}")
            continue

        content = f.read_text(encoding="utf-8")
        post_only = extract_post_body(content)

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", encoding="utf-8", delete=False
            ) as tmp:
                tmp.write(post_only)
                tmp_path = Path(tmp.name)

            result = converter.convert(str(tmp_path))
            tmp_path.unlink()

            out_json.write_text(
                result.document.model_dump_json(indent=2), encoding="utf-8"
            )
            out_json.with_suffix(".md").write_text(
                result.document.export_to_markdown(), encoding="utf-8"
            )
            succeeded.append(f.name)
            print(f"[{i}/{len(files)}] OK: {f.name}")
        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            failed.append(f.name)
            print(f"[{i}/{len(files)}] FAIL: {f.name} — {e}")

    print(f"\nDone: {len(succeeded)} converted, {len(skipped)} skipped, {len(failed)} failed")


if __name__ == "__main__":
    main()
