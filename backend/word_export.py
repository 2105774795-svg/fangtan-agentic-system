"""无需额外依赖的简洁 DOCX 导出；保留纪要章节和带时间点的逐字稿。"""
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ET.register_namespace("w", W)
ET.register_namespace("r", R)


def _tag(name):
    return f"{{{W}}}{name}"


def _paragraph(parent, text, style=None):
    paragraph = ET.SubElement(parent, _tag("p"))
    if style:
        properties = ET.SubElement(paragraph, _tag("pPr"))
        ET.SubElement(properties, _tag("pStyle"), {_tag("val"): style})
    run = ET.SubElement(paragraph, _tag("r"))
    text_node = ET.SubElement(run, _tag("t"))
    text_node.text = text or " "
    text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _styles():
    root = ET.Element(_tag("styles"))
    for style_id, size, bold in (("Normal", "22", False), ("Title", "36", True),
                                 ("Heading1", "30", True), ("Heading2", "26", True)):
        style = ET.SubElement(root, _tag("style"), {_tag("type"): "paragraph", _tag("styleId"): style_id})
        ET.SubElement(style, _tag("name"), {_tag("val"): style_id})
        run = ET.SubElement(style, _tag("rPr"))
        ET.SubElement(run, _tag("rFonts"), {_tag("eastAsia"): "Microsoft YaHei",
                                           _tag("ascii"): "Arial", _tag("hAnsi"): "Arial"})
        ET.SubElement(run, _tag("sz"), {_tag("val"): size})
        if bold:
            ET.SubElement(run, _tag("b"))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def render_minutes(title, markdown, utterances=None):
    document = ET.Element(_tag("document"))
    body = ET.SubElement(document, _tag("body"))
    _paragraph(body, title, "Title")
    for raw in (markdown or "").splitlines():
        line = raw.strip()
        if not line:
            _paragraph(body, "")
        elif line.startswith("## "):
            _paragraph(body, line[3:], "Heading2")
        elif line.startswith("# "):
            _paragraph(body, line[2:], "Heading1")
        else:
            _paragraph(body, line)
    if utterances is not None:
        _paragraph(body, "逐字稿", "Heading1")
    for row in utterances or []:
        milliseconds = max(0, int(row.get("start_ms") or 0))
        stamp = f"{milliseconds // 3600000:02d}:{milliseconds // 60000 % 60:02d}:{milliseconds // 1000 % 60:02d}"
        speaker = (row.get("speaker") or "").strip()
        content = f"[{stamp}] {speaker + '：' if speaker else ''}{row.get('text') or ''}"
        _paragraph(body, content)
    ET.SubElement(body, _tag("sectPr"))
    xml = ET.tostring(document, encoding="utf-8", xml_declaration=True)
    content_types = b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''
    relationships = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    document_relationships = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", xml)
        archive.writestr("word/styles.xml", _styles())
        archive.writestr("word/_rels/document.xml.rels", document_relationships)
    return output.getvalue()
