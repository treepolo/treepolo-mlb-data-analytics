from __future__ import annotations

import html
import io
import json
import math
from typing import Any

from . import stage4d as base


REPORT_FIELD_METADATA: dict[str, dict[str, Any]] = {
    "play_id": {"label": "播放識別碼 Play ID", "role": "identifier"},
    "criterion": {"label": "選模準則 Criterion", "role": "category"},
    "selection_standard_error": {"label": "選擇標準誤 Selection Standard Error", "role": "measure"},
    "valid": {"label": "有效 Valid", "role": "category"},
    "selected": {"label": "已選 Selected", "role": "category"},
    "cluster_sizes": {"label": "各群樣本數 Cluster Sizes", "role": "category"},
    "minimum_cluster_size": {"label": "最小群大小 Minimum Cluster Size", "role": "sample_size"},
    "adaptive_max_k": {"label": "自適應最大群數 Adaptive Max K", "role": "sample_size"},
    "rejection_reason": {"label": "排除原因 Rejection Reason", "role": "category"},
}

MODE_LABELS = {
    "basic": "基本分析 Basic",
    "sequence_pattern": "球序模式 Sequence Pattern",
    "follow_event": "後續事件 Follow-up Event",
    "arsenal": "球種武器庫 Pitch Arsenal",
    "pitch_role": "球種角色 Pitch Role",
    "temporal": "時間序列 Temporal Comparison",
    "percentile": "個別百分位門檻 Individual Percentile Threshold",
    "cross_level": "層級比較 Level Comparison",
    "arsenal_change": "武器庫變化 Arsenal Change",
    "workflow": "研究工作流 Research Workflow",
    "clustering": "自動分群 Clustering",
    "regression": "迴歸分析 Regression",
    "bootstrap": "Bootstrap／信賴區間 Confidence Interval",
    "cluster_compare": "多階段分群比較 Cluster Comparison",
}

TYPE_LABELS = {
    "line": "折線圖 Line",
    "bar": "長條圖 Bar",
    "scatter": "散點圖 Scatter",
    "range": "區間圖 Range",
    "dumbbell": "啞鈴圖 Dumbbell",
    "difference": "差值圖 Difference",
}

PROVENANCE_LABELS = {
    "source_kind": "來源類型 Source Type",
    "history_id": "分析紀錄編號 History ID",
    "saved_id": "分析庫編號 Saved Analysis ID",
    "visualization_id": "視覺化編號 Visualization ID",
    "visualization_name": "視覺化名稱 Visualization Name",
    "save_mode": "儲存模式 Save Mode",
    "data_revision": "資料版本 Data Revision",
    "backend": "執行器 Backend",
    "created_at": "建立時間 Created At",
    "mode": "分析模式 Mode",
    "grain": "資料層級 Grain",
    "row_count": "資料筆數 Rows",
    "returned_row_count": "載入筆數 Loaded Rows",
    "section_title": "結果區段 Result Section",
    "complete_result": "完整結果 Complete Result",
    "rerun": "重新執行 Rerun",
    "frozen": "凍結快照 Frozen Snapshot",
}

PALETTE = ["#2f6fad", "#c3543f", "#3f8a5b", "#8a5ba5", "#c18a2d", "#477f8e", "#9a5966", "#65728a", "#6c8f36", "#a76d34"]


def _has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _field_label(column: str, prepared: dict[str, Any] | None = None) -> str:
    metadata = (prepared or {}).get("field_metadata") or []
    for item in metadata:
        if str(item.get("name")) != column:
            continue
        label = str(item.get("label") or "")
        if label and label != column:
            return label if _has_cjk(label) else f"資料欄位 Data Field · {label}"
    known = base.KNOWN_FIELDS.get(column) or REPORT_FIELD_METADATA.get(column) or {}
    label = str(known.get("label") or "")
    if label:
        return label if _has_cjk(label) else f"資料欄位 Data Field · {label}"
    return f"資料欄位 Data Field · {column}"


def _human_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是 Yes" if value else "否 No"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(value, list):
        return ", ".join(_human_value(item) for item in value)
    return str(value)


def _mode_label(value: Any) -> str:
    raw = str(value or "")
    return MODE_LABELS.get(raw, raw or "—")


def _preset_label(value: Any) -> str:
    raw = str(value or "")
    if not raw:
        return "自訂 Custom"
    preset_id = raw.split(":", 1)[-1]
    for preset in base.BUILTIN_PRESETS:
        if str(preset.get("id")) == preset_id:
            return f"內建 Built-in · {preset.get('name')}"
    return raw


def _sampling_mode_label(value: Any) -> str:
    return {
        "full": "完整資料 Full Data",
        "automatic": "自動抽樣 Automatic Sampling",
        "manual": "手動抽樣 Manual Sampling",
    }.get(str(value or ""), str(value or "—"))


def _sampling_method_label(value: Any) -> str:
    return {
        "random": "隨機 Random",
        "every_nth": "固定間隔 Every Nth Row",
    }.get(str(value or ""), str(value or "—"))


def _provenance_rows(prepared: dict[str, Any]) -> list[tuple[str, str]]:
    provenance = prepared.get("provenance") or {}
    ordered = [
        "source_kind", "history_id", "saved_id", "visualization_id", "visualization_name", "save_mode",
        "mode", "section_title", "row_count", "returned_row_count", "grain", "backend", "data_revision",
        "created_at", "complete_result", "rerun", "frozen",
    ]
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in ordered + [str(key) for key in provenance.keys()]:
        if key in seen or key not in provenance:
            continue
        seen.add(key)
        label = PROVENANCE_LABELS.get(key, f"欄位 Field · {key}")
        value = _mode_label(provenance[key]) if key == "mode" else _human_value(provenance[key])
        rows.append((label, value))
    return rows


def _presentation_rows(spec: dict[str, Any], prepared: dict[str, Any]) -> list[tuple[str, str]]:
    mapping = spec.get("mapping") or {}
    display = spec.get("display") or {}
    sampling = spec.get("sampling") or {}
    rows: list[tuple[str, str]] = [
        ("規格版本 Spec Version", _human_value(spec.get("version"))),
        ("圖型 Type", TYPE_LABELS.get(str(spec.get("type") or ""), _human_value(spec.get("type")))),
        ("預設 Preset", _preset_label(spec.get("preset"))),
    ]
    for key, label in (("x", "X 軸 X Axis"), ("y", "Y 軸 Y Axis"), ("series", "系列 Series"), ("label", "標籤 Label"), ("lower", "下界 Lower"), ("upper", "上界 Upper")):
        raw = mapping.get(key)
        rows.append((label, _field_label(str(raw), prepared) if raw else "—"))
    display_labels = {
        "title": "標題 Title", "subtitle": "副標題 Subtitle", "width": "寬度 Width", "height": "高度 Height",
        "point_size": "點大小 Point Size", "opacity": "透明度 Opacity", "x_min": "X 最小值 X Min", "x_max": "X 最大值 X Max",
        "y_min": "Y 最小值 Y Min", "y_max": "Y 最大值 Y Max", "reference_x": "參考 X Reference X", "reference_y": "參考 Y Reference Y",
        "bar_orientation": "長條方向 Bar Orientation", "stacked": "堆疊 Stacked", "legend": "圖例 Legend",
        "data_labels": "資料標籤 Data Labels", "show_n": "顯示樣本數 Show N", "equal_axes": "等比例座標 Equal Axes",
    }
    for key, label in display_labels.items():
        if key in display:
            rows.append((label, _human_value(display.get(key))))
    rows.extend([
        ("抽樣模式 Sampling Mode", _sampling_mode_label(sampling.get("mode"))),
        ("抽樣方法 Sampling Method", _sampling_method_label(sampling.get("method"))),
        ("抽樣筆數 Sample Rows", _human_value(sampling.get("size"))),
        ("隨機種子 Seed", _human_value(sampling.get("seed"))),
    ])
    return rows


def _html_kv_table(rows: list[tuple[str, str]]) -> str:
    return "<table class=\"kv-table\"><tbody>" + "".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>" for label, value in rows
    ) + "</tbody></table>"


def _html_report_v2(title: str, prepared: dict[str, Any], spec: dict[str, Any], chart_svg: str) -> bytes:
    section = prepared["section"]
    columns = [str(value) for value in section.get("columns") or []]
    rows = [row for row in section.get("rows") or [] if isinstance(row, dict)]
    provenance = prepared.get("provenance") or {}
    chart = base._sanitize_svg(chart_svg)
    display_title = "分析報告 Analysis Report" if title in {"Analysis Report", "analysis-report"} else title
    labels = {column: _field_label(column, prepared) for column in columns}
    table_head = "".join(f"<th>{html.escape(labels[column])}</th>" for column in columns)
    table_rows = []
    for row in rows[:base.REPORT_TABLE_ROWS]:
        cells = "".join(
            f"<td>{html.escape(str(base._scalar(row.get(column)) if row.get(column) is not None else '—'))}</td>"
            for column in columns
        )
        table_rows.append(f"<tr>{cells}</tr>")
    meta_rows = [
        ("分析模式 Mode", _mode_label((prepared.get("analysis_payload") or {}).get("mode") or provenance.get("mode"))),
        ("結果區段 Result Section", str(section.get("title") or provenance.get("section_title") or "—")),
        ("資料筆數 Rows", str(int(provenance.get("row_count") or len(rows)))),
        ("執行器 Backend", _human_value(provenance.get("backend"))),
        ("資料版本 Data Revision", _human_value(provenance.get("data_revision"))),
        ("資料層級 Grain", _human_value(provenance.get("grain"))),
        ("抽樣 Sampling", f"{_sampling_mode_label((prepared.get('sampling') or {}).get('mode'))} · {_sampling_method_label((prepared.get('sampling') or {}).get('method'))} · {prepared.get('sampling', {}).get('returned_rows', len(rows))} rows"),
        ("產生時間 Generated At", base._now()),
    ]
    body = f"""<!doctype html><html lang=\"zh-Hant\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{html.escape(display_title)}</title><style>
*{{box-sizing:border-box}}html,body{{max-width:100%;overflow-x:hidden}}body{{font-family:'Microsoft JhengHei','Noto Sans CJK TC',Arial,sans-serif;margin:0;color:#1f2933;background:#fff}}main{{max-width:1200px;margin:0 auto;padding:24px}}h1{{font-size:24px;margin:0 0 16px}}h2{{margin:24px 0 10px;font-size:17px}}table{{border-collapse:collapse;width:100%;max-width:100%}}th,td{{border:1px solid #b8c0ca;padding:5px;text-align:left;vertical-align:top;white-space:normal;overflow-wrap:anywhere;word-break:break-word}}th{{background:#eef2f6}}.kv-table{{font-size:12px;table-layout:fixed}}.kv-table th{{width:220px}}.result-wrap{{width:100%;max-width:100%;overflow:hidden}}.result-table{{table-layout:fixed;font-size:10px}}.result-table th,.result-table td{{min-width:0}}.chart{{width:100%;max-width:100%;border:1px solid #d0d7df;padding:8px;overflow:hidden}}.chart svg{{display:block;max-width:100%;height:auto}}.note{{font-size:11px;color:#52606d}}@media(max-width:720px){{main{{padding:14px}}.kv-table th{{width:38%}}.result-table{{font-size:9px}}}}@media print{{main{{max-width:none;padding:0}}body{{margin:10mm}}.result-table{{font-size:8px}}}}
</style></head><body><main>
<h1>{html.escape(display_title)}</h1>{_html_kv_table(meta_rows)}
{f'<h2>視覺化 Visualization</h2><div class="chart">{chart}</div>' if chart else ''}
<h2>結果 Result</h2><p class=\"note\">報告表格最多顯示 {base.REPORT_TABLE_ROWS} 筆；完整資料請使用 CSV／JSON／XLSX／Parquet 匯出。 Report table shows at most {base.REPORT_TABLE_ROWS} rows; use CSV/JSON/XLSX/Parquet export for complete data.</p>
<div class=\"result-wrap\"><table class=\"result-table\"><thead><tr>{table_head}</tr></thead><tbody>{''.join(table_rows)}</tbody></table></div>
<h2>來源資訊 Provenance</h2>{_html_kv_table(_provenance_rows(prepared))}
<h2>視覺化設定 Presentation Spec</h2>{_html_kv_table(_presentation_rows(spec, prepared))}
</main></body></html>"""
    return body.encode("utf-8")


def _pdf_chart_v2(prepared: dict[str, Any], spec: dict[str, Any], font_name: str) -> Any:
    from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String

    rows = [row for row in prepared["section"].get("rows") or [] if isinstance(row, dict)][:1000]
    mapping = spec.get("mapping") or {}
    display = spec.get("display") or {}
    chart_type = spec.get("type") or "scatter"
    x_field = mapping.get("x")
    y_field = mapping.get("y")
    lower_field = mapping.get("lower")
    upper_field = mapping.get("upper")
    series_field = mapping.get("series")
    categories = []
    if series_field:
        for row in rows:
            value = str(row.get(series_field) if row.get(series_field) is not None else "—")
            if value not in categories:
                categories.append(value)
    drawing = Drawing(500, 285)
    if not rows or not y_field:
        drawing.add(String(12, 135, "沒有可繪製資料 No plottable rows", fontName=font_name, fontSize=9))
        return drawing
    y_values = [float(row[y_field]) for row in rows if isinstance(row.get(y_field), (int, float)) and math.isfinite(float(row[y_field]))]
    if not y_values:
        drawing.add(String(12, 135, "Y 欄位沒有數值 Selected Y field is not numeric", fontName=font_name, fontSize=9))
        return drawing
    left, right, bottom, top = 58.0, (420.0 if categories else 490.0), 42.0, 250.0
    y_min, y_max = min(y_values), max(y_values)
    if y_min == y_max:
        y_min -= 1
        y_max += 1
    pad = (y_max - y_min) * 0.06
    y_min -= pad
    y_max += pad

    def sy(value: float) -> float:
        return bottom + (value - y_min) / (y_max - y_min) * (top - bottom)

    for index in range(6):
        ratio = index / 5
        y = bottom + ratio * (top - bottom)
        value = y_min + ratio * (y_max - y_min)
        drawing.add(Line(left, y, right, y, strokeColor="#d8dee6", strokeWidth=0.4))
        drawing.add(String(4, y - 2.5, f"{value:.4g}", fontName=font_name, fontSize=6.5))
    drawing.add(Line(left, bottom, left, top, strokeColor="#56616f", strokeWidth=0.7))
    drawing.add(Line(left, bottom, right, bottom, strokeColor="#56616f", strokeWidth=0.7))

    x_numeric = bool(x_field) and all(row.get(x_field) is None or isinstance(row.get(x_field), (int, float)) for row in rows)
    if x_numeric and x_field:
        xs = [float(row[x_field]) for row in rows if isinstance(row.get(x_field), (int, float))]
        x_min, x_max = min(xs), max(xs)
        if x_min == x_max:
            x_min -= 1
            x_max += 1
        x_pad = (x_max - x_min) * 0.06
        x_min -= x_pad
        x_max += x_pad
        sx = lambda value: left + (float(value) - x_min) / (x_max - x_min) * (right - left)
        for index in range(6):
            ratio = index / 5
            x = left + ratio * (right - left)
            value = x_min + ratio * (x_max - x_min)
            drawing.add(String(x - 10, 29, f"{value:.4g}", fontName=font_name, fontSize=6.5))
    else:
        sx = lambda value: left + float(value) / max(1, len(rows) - 1) * (right - left)
        shown = min(8, len(rows))
        if shown:
            step = max(1, math.ceil(len(rows) / shown))
            for index in range(0, len(rows), step):
                label = str(rows[index].get(x_field) if x_field else index + 1)[:14]
                drawing.add(String(sx(index) - 8, 29, label, fontName=font_name, fontSize=6.5))

    def color_for(category: str) -> str:
        try:
            return PALETTE[categories.index(category) % len(PALETTE)]
        except ValueError:
            return PALETTE[0]

    if chart_type in {"scatter", "line"}:
        series_values = categories or ["All"]
        for category in series_values:
            points: list[tuple[float, float]] = []
            for index, row in enumerate(rows):
                if series_field and str(row.get(series_field) if row.get(series_field) is not None else "—") != category:
                    continue
                if not isinstance(row.get(y_field), (int, float)):
                    continue
                x_value = row.get(x_field) if x_numeric and x_field else index
                points.append((sx(x_value), sy(float(row[y_field]))))
            if chart_type == "line" and len(points) >= 2:
                drawing.add(PolyLine([coordinate for point in points for coordinate in point], strokeColor=color_for(category), strokeWidth=1.3))
            for x, y in points:
                drawing.add(Circle(x, y, 1.9, fillColor=color_for(category), strokeColor=None))
    elif chart_type in {"bar", "difference"}:
        values = [(index, row) for index, row in enumerate(rows[:40]) if isinstance(row.get(y_field), (int, float))]
        width = (right - left) / max(1, len(values))
        zero = sy(0.0) if y_min <= 0 <= y_max else bottom
        for display_index, (_, row) in enumerate(values):
            value = float(row[y_field])
            y = sy(value)
            category = str(row.get(series_field) if series_field and row.get(series_field) is not None else "All")
            drawing.add(Rect(left + display_index * width + 1, min(zero, y), max(1, width - 2), abs(y - zero), fillColor=color_for(category), strokeColor=None))
    elif chart_type == "range":
        shown = rows[:40]
        width = (right - left) / max(1, len(shown))
        for index, row in enumerate(shown):
            if not isinstance(row.get(y_field), (int, float)):
                continue
            x = left + (index + 0.5) * width
            y = sy(float(row[y_field]))
            category = str(row.get(series_field) if series_field and row.get(series_field) is not None else "All")
            color = color_for(category)
            drawing.add(Circle(x, y, 2.2, fillColor=color, strokeColor=None))
            if isinstance(row.get(lower_field), (int, float)) and isinstance(row.get(upper_field), (int, float)):
                drawing.add(Line(x, sy(float(row[lower_field])), x, sy(float(row[upper_field])), strokeColor=color))
    elif chart_type == "dumbbell" and lower_field:
        shown = rows[:30]
        width = (right - left) / max(1, len(shown))
        for index, row in enumerate(shown):
            if not isinstance(row.get(y_field), (int, float)) or not isinstance(row.get(lower_field), (int, float)):
                continue
            x = left + (index + 0.5) * width
            y1, y2 = sy(float(row[lower_field])), sy(float(row[y_field]))
            drawing.add(Line(x, y1, x, y2, strokeColor="#7b8794"))
            drawing.add(Circle(x, y1, 2, fillColor="#7b8794", strokeColor=None))
            drawing.add(Circle(x, y2, 2.4, fillColor=PALETTE[0], strokeColor=None))

    reference_x = display.get("reference_x")
    reference_y = display.get("reference_y")
    if reference_x is not None and x_numeric and x_field:
        try:
            x = sx(float(reference_x))
            if left <= x <= right:
                drawing.add(Line(x, bottom, x, top, strokeColor="#b65f4a", strokeWidth=0.7, strokeDashArray=[3, 2]))
        except (TypeError, ValueError):
            pass
    if reference_y is not None:
        try:
            y = sy(float(reference_y))
            if bottom <= y <= top:
                drawing.add(Line(left, y, right, y, strokeColor="#b65f4a", strokeWidth=0.7, strokeDashArray=[3, 2]))
        except (TypeError, ValueError):
            pass

    drawing.add(String(left, 10, _field_label(str(x_field), prepared) if x_field else "資料列 Row", fontName=font_name, fontSize=7))
    drawing.add(String(4, 262, _field_label(str(y_field), prepared), fontName=font_name, fontSize=7))
    if categories:
        for index, category in enumerate(categories[:10]):
            y = top - index * 16
            drawing.add(Rect(432, y - 6, 7, 7, fillColor=color_for(category), strokeColor=None))
            drawing.add(String(443, y - 5, category[:18], fontName=font_name, fontSize=6.5))
    return drawing


def _pdf_paragraph(value: Any, style: Any) -> Any:
    from reportlab.platypus import Paragraph
    return Paragraph(html.escape(_human_value(value)), style)


def _pdf_report_v2(title: str, prepared: dict[str, Any], spec: dict[str, Any]) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise base.RequestError("PDF export requires the reportlab package") from exc

    section = prepared["section"]
    columns = [str(value) for value in section.get("columns") or []]
    rows = [row for row in section.get("rows") or [] if isinstance(row, dict)]
    provenance = prepared.get("provenance") or {}
    font_name = base._pdf_font()
    wide = len(columns) > 7
    page_size = landscape(A4) if wide else A4
    page_width = page_size[0]
    margin = 12 * mm
    usable_width = page_width - 2 * margin
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=page_size, leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin)
    styles = getSampleStyleSheet()
    for style_name in ("Title", "Heading2", "BodyText"):
        styles[style_name].fontName = font_name
    styles["Title"].fontSize = 20
    styles["Heading2"].fontSize = 14
    small = ParagraphStyle("Stage4DSmall", parent=styles["BodyText"], fontName=font_name, fontSize=7, leading=9, wordWrap="CJK")
    tiny = ParagraphStyle("Stage4DTiny", parent=small, fontSize=6, leading=7.5)
    header = ParagraphStyle("Stage4DHeader", parent=tiny, fontName=font_name, fontSize=6.2, leading=7.5)
    note = ParagraphStyle("Stage4DNote", parent=small, textColor=colors.HexColor("#52606d"))

    display_title = "分析報告 Analysis Report" if title in {"Analysis Report", "analysis-report"} else title
    story: list[Any] = [Paragraph(html.escape(display_title), styles["Title"]), Spacer(1, 7)]
    meta_rows = [
        ("分析模式 Mode", _mode_label((prepared.get("analysis_payload") or {}).get("mode") or provenance.get("mode"))),
        ("結果區段 Result Section", section.get("title") or provenance.get("section_title") or "—"),
        ("資料筆數 Rows", provenance.get("row_count") or len(rows)),
        ("執行器 Backend", provenance.get("backend") or "—"),
        ("資料版本 Data Revision", provenance.get("data_revision") or "—"),
        ("資料層級 Grain", provenance.get("grain") or "—"),
        ("抽樣 Sampling", f"{_sampling_mode_label((prepared.get('sampling') or {}).get('mode'))} · {_sampling_method_label((prepared.get('sampling') or {}).get('method'))} · {(prepared.get('sampling') or {}).get('returned_rows', len(rows))} rows"),
        ("產生時間 Generated At", base._now()),
    ]
    meta_data = [[_pdf_paragraph(label, small), _pdf_paragraph(value, small)] for label, value in meta_rows]
    meta_table = Table(meta_data, colWidths=[42 * mm, usable_width - 42 * mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name), ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), .25, colors.HexColor("#b8c0ca")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f6")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.extend([meta_table, Spacer(1, 9), Paragraph("視覺化 Visualization", styles["Heading2"]), _pdf_chart_v2(prepared, spec, font_name), Spacer(1, 8), Paragraph("結果 Result", styles["Heading2"])])
    story.append(Paragraph(f"報告表格最多顯示 {base.REPORT_TABLE_ROWS} 筆；完整資料請使用 CSV／JSON／XLSX／Parquet 匯出。 Report table shows at most {base.REPORT_TABLE_ROWS} rows; use CSV/JSON/XLSX/Parquet export for complete data.", note))
    story.append(Spacer(1, 4))

    if columns:
        labels = [_field_label(column, prepared) for column in columns]
        data = [[Paragraph(html.escape(label), header) for label in labels]]
        for row in rows[:base.REPORT_TABLE_ROWS]:
            data.append([_pdf_paragraph(base._scalar(row.get(column)) if row.get(column) is not None else "—", tiny) for column in columns])
        col_width = usable_width / max(1, len(columns))
        table = LongTable(data, repeatRows=1, colWidths=[col_width] * len(columns), splitByRow=1)
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font_name), ("GRID", (0, 0), (-1, -1), .2, colors.HexColor("#c2c9d1")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f6")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2.5), ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        story.append(table)

    def append_kv_section(title_text: str, kv_rows: list[tuple[str, str]]) -> None:
        story.extend([Spacer(1, 9), Paragraph(title_text, styles["Heading2"])])
        data = [[_pdf_paragraph(label, small), _pdf_paragraph(value, small)] for label, value in kv_rows]
        table = LongTable(data, colWidths=[52 * mm, usable_width - 52 * mm], splitByRow=1)
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font_name), ("GRID", (0, 0), (-1, -1), .2, colors.HexColor("#c2c9d1")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f6")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(table)

    append_kv_section("來源資訊 Provenance", _provenance_rows(prepared))
    append_kv_section("視覺化設定 Presentation Spec", _presentation_rows(spec, prepared))
    doc.build(story)
    return buffer.getvalue()


def install() -> None:
    if getattr(base, "_stage4d_report_v2_installed", False):
        return
    base._stage4d_report_v2_installed = True
    base.KNOWN_FIELDS.update(REPORT_FIELD_METADATA)
    base._html_report = _html_report_v2
    base._pdf_report = _pdf_report_v2
    base._pdf_chart = _pdf_chart_v2
