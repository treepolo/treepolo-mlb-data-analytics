from __future__ import annotations

import mimetypes
from typing import Any


def install(webapp_module: Any) -> None:
    """Append focused Stage 4D compatibility layers to the browser bundle.

    Keep bundle delivery declarative: the static handler only concatenates known
    browser modules. Renderer/lifecycle behavior lives in JavaScript modules, so
    a source-formatting change cannot make the entire Stage 4D bundle disappear.
    """

    if getattr(webapp_module, "_stage4d_frontend_patch_installed", False):
        return
    webapp_module._stage4d_frontend_patch_installed = True
    original_static = webapp_module._Handler._static

    def patched_static(self: Any, request_path: str) -> None:
        if request_path not in {"/stage4d-visualization.js", "stage4d-visualization.js"}:
            return original_static(self, request_path)

        static_dir = webapp_module.STATIC_DIR
        module_names = (
            "stage4d-visualization.js",
            "stage4d-visualization-fixes.js",
            "stage4d-preset-state-reset.js",
            "font-minimum-compat.js",
            "stage4d-layout-containment.js",
            "stage4d-save-lifecycle.js",
            "stage4d-saved-restore.js",
            "stage4d-axis-layout.js",
        )
        chunks = [(static_dir / name).read_bytes() for name in module_names]
        body = b"\n".join(chunks)

        content_type = mimetypes.guess_type(module_names[0])[0] or "text/javascript"
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    webapp_module._Handler._static = patched_static
