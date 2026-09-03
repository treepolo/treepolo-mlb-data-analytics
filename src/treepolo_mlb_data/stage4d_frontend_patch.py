from __future__ import annotations

import mimetypes
from typing import Any


def install(webapp_module: Any) -> None:
    """Append small renderer/UI compatibility fixups to the Stage 4D browser bundle.

    Stage 4D is intentionally layered on the existing static UI. Serving the
    fixups after the main bundle lets focused renderer/lifecycle corrections stay
    isolated without creating a second application entry point.
    """

    if getattr(webapp_module, "_stage4d_frontend_patch_installed", False):
        return
    webapp_module._stage4d_frontend_patch_installed = True
    original_static = webapp_module._Handler._static

    def patched_static(self: Any, request_path: str) -> None:
        if request_path not in {"/stage4d-visualization.js", "stage4d-visualization.js"}:
            return original_static(self, request_path)
        primary = webapp_module.STATIC_DIR / "stage4d-visualization.js"
        fixups = webapp_module.STATIC_DIR / "stage4d-visualization-fixes.js"
        preset_state_reset = webapp_module.STATIC_DIR / "stage4d-preset-state-reset.js"
        font_minimum_compat = webapp_module.STATIC_DIR / "font-minimum-compat.js"
        body = (
            primary.read_bytes()
            + b"\n"
            + fixups.read_bytes()
            + b"\n"
            + preset_state_reset.read_bytes()
            + b"\n"
            + font_minimum_compat.read_bytes()
        )
        content_type = mimetypes.guess_type(primary.name)[0] or "text/javascript"
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    webapp_module._Handler._static = patched_static
