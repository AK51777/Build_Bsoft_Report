"""Render all pages once per Word version; record images without claiming visual review."""
from __future__ import annotations
import os
import shutil
import subprocess
from pathlib import Path
from knowledge_db import sha256_file, now_iso


def render(root):
    from construction_workflow import paths, read, write, get_status
    from construction_word import audit_word
    import pypdfium2 as pdfium
    root, data, state_path = paths(root)
    state = read(state_path)
    docx = Path(state["docx_path"])
    manifest = read(state["manifest_path"])
    if not audit_word(docx, manifest)["valid"]:
        raise ValueError("Word source audit failed before rendering")
    binding_path = data / "render-binding.json"
    if binding_path.exists():
        previous = read(binding_path)
        if previous["docx_sha256"] == sha256_file(docx) and all(Path(p["path"]).is_file() and sha256_file(Path(p["path"])) == p["sha256"] for p in previous["pages"]):
            return {**get_status(root), "render": previous, "render_reused": True}
    render_dir = data / "render" / sha256_file(docx)
    render_dir.mkdir(parents=True, exist_ok=True)
    pdf = render_dir / "construction.pdf"
    state.update(status="rendering", updated_at=now_iso())
    write(state_path, state)
    try:
        if os.name == "nt":
            shell = shutil.which("pwsh") or shutil.which("powershell")
            if not shell:
                raise ValueError("PowerShell/Word renderer is unavailable")
            command = [shell, "-NoProfile", "-NonInteractive", "-File", str(Path(__file__).with_name("render_construction_word.ps1")), "-InputDocx", str(docx), "-OutputPdf", str(pdf)]
            renderer = "Microsoft Word"
        else:
            binary = shutil.which("soffice") or shutil.which("libreoffice")
            if not binary:
                raise ValueError("LibreOffice renderer is unavailable")
            command = [binary, "-env:UserInstallation=" + (render_dir / "lo-profile").as_uri(), "--headless", "--convert-to", "pdf", "--outdir", str(render_dir), str(docx)]
            pdf = render_dir / docx.with_suffix(".pdf").name
            renderer = "LibreOffice"
        subprocess.run(command, check=True, capture_output=True, timeout=600)
        audit = audit_word(docx, manifest)
        if not audit["valid"]:
            raise ValueError("Word field update changed source-bound body")
        pages = []
        with pdfium.PdfDocument(str(pdf)) as document:
            if not len(document):
                raise ValueError("renderer returned no pages")
            for index, page in enumerate(document, 1):
                output = render_dir / f"page-{index:04d}.png"
                bitmap = page.render(scale=1.5)
                bitmap.to_pil().save(output)
                bitmap.close()
                page.close()
                pages.append({"page_number": index, "path": str(output), "sha256": sha256_file(output)})
        binding = {"docx_sha256": sha256_file(docx), "manifest_hash": manifest["manifest_hash"],
                   "pdf_path": str(pdf), "pdf_sha256": sha256_file(pdf), "page_count": len(pages),
                   "pages": pages, "renderer": renderer, "rendered_at": now_iso()}
        write(binding_path, binding)
        state.update(status="docx_created_render_pending", docx_sha256=binding["docx_sha256"], render_binding=str(binding_path), error=None)
        state.pop("render_review", None)
        write(state_path, state)
        return {**get_status(root), "render": binding, "render_reused": False}
    except Exception as exc:
        detail = str(exc)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            detail = exc.stderr.decode("utf-8", errors="replace")[-3000:]
        state.update(status="docx_created_render_pending", error={"type":type(exc).__name__, "message":detail})
        write(state_path, state)
        raise
