from __future__ import annotations

import mimetypes
from typing import Any


def install(webapp_module: Any) -> None:
    """Append focused Stage 4D compatibility layers to the browser bundle."""

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
        layout_containment = webapp_module.STATIC_DIR / "stage4d-layout-containment.js"
        save_lifecycle = webapp_module.STATIC_DIR / "stage4d-save-lifecycle.js"
        saved_restore = webapp_module.STATIC_DIR / "stage4d-saved-restore.js"
        axis_layout = webapp_module.STATIC_DIR / "stage4d-axis-layout.js"

        primary_body = primary.read_bytes()
        old_margin = b'const margin={left:75,right:display.legend?150:30,top:70,bottom:62};'
        new_margin = b'const margin={left:(window.treepoloStage4DLeftMargin?window.treepoloStage4DLeftMargin(yField,yValues,display):75),right:display.legend?150:30,top:70,bottom:62};'
        if old_margin not in primary_body:
            raise RuntimeError("Stage 4D primary renderer margin hook no longer matches")
        primary_body = primary_body.replace(old_margin, new_margin, 1)

        fixups_body = fixups.read_bytes()
        old_frozen_section = (
            b'if(pendingSavedVisualization.save_mode==="live")request.section=Number(pendingSavedVisualization.section_index||0);\n'
            b'          else request.section=0;'
        )
        new_frozen_section = (
            b'if(pendingSavedVisualization.save_mode==="frozen"&&!pendingSavedVisualization.snapshot_hash)request.section=0;\n'
            b'          else request.section=Number(pendingSavedVisualization.section_index||0);'
        )
        if old_frozen_section not in fixups_body:
            raise RuntimeError("Stage 4D saved-section compatibility hook no longer matches")
        fixups_body = fixups_body.replace(old_frozen_section, new_frozen_section, 1)

        body = (
            primary_body
            + b"\n"
            + fixups_body
            + b"\n"
            + preset_state_reset.read_bytes()
            + b"\n"
            + font_minimum_compat.read_bytes()
            + b"\n"
            + layout_containment.read_bytes()
            + b"\n"
            + save_lifecycle.read_bytes()
            + b"\n"
            + saved_restore.read_bytes()
            + b"\n"
            + axis_layout.read_bytes()
        )
        content_type = mimetypes.guess_type(primary.name)[0] or "text/javascript"
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    webapp_module._Handler._static = patched_static
