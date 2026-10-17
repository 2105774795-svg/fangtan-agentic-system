"""Research report structure and a dependency-free, editable Word export."""
import re
from datetime import datetime
from io import BytesIO
from xml.etree import ElementTree as E
from zipfile import ZipFile, ZIP_DEFLATED

REPORT_GUIDE = """按调研整合报告编排，围绕当前项目主题调整对象称谓，不套用任何样例项目事实。
一级标题为项目调研整合报告。标题下依次列副标题、报告日期、报告性质（内部工作讨论稿）、项目背景、资料范围、报告用途、核心原则。
正文按以下固定顺序设置二级标题，章节不足也保留并如实说明缺口：
执行摘要：提炼主要发现、建议；关键数据用“指标／材料中的数据及出处／核实状态”表，缺少数据不得编造。
一 研究范围与判读方法：项目背景、资料构成、判读方法、优先级规则。
二 受访对象现状与需求归纳：按类型比较现状、痛点、明确需求、潜在需求；不能把调研方设想当作受访者诉求。
三 需求整合清单：用“需求事项／证据来源／涉及对象／建议优先级／后续验证”表。P0/P1/P2是研究建议，不是投票统计。
四 逐场访谈与实地记录：每场一个三级标题，保留场次、名称、材料编号、关键原话及其段落引用、个案差异；没有提供现场照片则说明未提供，不生成虚构图号或现场观察。无原文的未结束访谈仅列状态，不编写访谈事实。
五 需要进一步补充的数据：列数据、用途、核实方式，未提供的责任人或日期写待确认。
六 关键分歧与研究风险：保留不同受访者之间的矛盾，说明证据边界、样本局限和下一步核实。
最后附材料索引与来源，标明每份M材料对应的访谈场次和原文段落标记。
资料范围必须区分访谈场次、原始材料份数和独立受访人数。同一人可多次访谈，人数不能按场次或录音片段简单相加。
已结束、未结束、已同步、未同步场次分别说明；仅依据提供的原文形成结论。资料不全时报告性质明确为阶段性讨论稿。
每个实质判断给出[M1-P1]形式的真实出处，至少引用每份原始材料一次；既有纪要、计划和模型输出不作为原始证据。
区分访谈原话、研究发现、用户或业务洞察、需求机会、需求建议与待验证假设。个体功能诉求不等于普遍需求，行为原因的解释标明推断；涉及频次、影响及优先级时说明依据，缺乏证据则标为待验证。\n正文使用段落和精简比较表，避免把整篇解释塞入表格；只输出报告正文，不附聊天追问或操作建议。"""

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
E.register_namespace('w', W)
E.register_namespace('r', R)


def tag(name):
    return '{' + W + '}' + name


def node(parent, name, **attrs):
    return E.SubElement(parent, tag(name), {tag(k): str(v) for k, v in attrs.items()})


def paragraph(parent, text='', style='Normal'):
    p = node(parent, 'p')
    node(node(p, 'pPr'), 'pStyle', val=style)
    for part in re.split(r'(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))', text):
        if not part:
            continue
        r = node(p, 'r')
        if part.startswith('**') and part.endswith('**'):
            node(node(r, 'rPr'), 'b')
            part = part[2:-2]
        elif part.startswith('`') and part.endswith('`'):
            part = part[1:-1]
        elif re.fullmatch(r'\[[^\]]+\]\([^)]+\)', part):
            part = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1（\2）', part)
        t = node(r, 't')
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        t.text = part
    return p


def page_break(parent):
    node(node(node(parent, 'p'), 'r'), 'br', type='page')


def styles():
    root = E.Element(tag('styles'))
    for name, font, size, bold in [('Normal', '宋体', 22, False), ('Title', '黑体', 48, True),
                                   ('Subtitle', '宋体', 23, False), ('Heading1', '黑体', 32, True),
                                   ('Heading2', '黑体', 25, True), ('Heading3', '黑体', 23, True),
                                   ('TableText', '宋体', 20, False), ('TOCEntry', '宋体', 22, False)]:
        s = node(root, 'style', type='paragraph', styleId=name)
        node(s, 'name', val=name)
        if name != 'Normal':
            node(s, 'basedOn', val='Normal')
        props = node(s, 'pPr')
        node(props, 'spacing', before=240 if name.startswith('Heading') else 0,
             after=140 if name.startswith('Heading') else 120, line=300, lineRule='auto')
        if name.startswith('Heading'):
            node(props, 'keepNext')
            node(props, 'outlineLvl', val=int(name[-1])-1)
        elif name == 'Normal':
            node(props, 'ind', firstLine=440)
        if name == 'Title':
            props.find(tag('spacing')).attrib.update({tag('before'): '900', tag('after'): '360'})
        if name != 'Normal':
            node(props, 'ind', firstLine=0)
        run = node(s, 'rPr')
        node(run, 'rFonts', eastAsia=font, ascii='Times New Roman', hAnsi='Times New Roman')
        node(run, 'sz', val=size)
        node(run, 'color', val='000000')
        if bold:
            node(run, 'b')
    return E.tostring(root, encoding='utf-8', xml_declaration=True)


def cells(line):
    return [s.strip().replace(r'\|', '|') for s in re.split(r'(?<!\\)\|', line.strip().strip('|'))]


def table(parent, rows):
    columns = len(rows[0])
    t = node(parent, 'tbl')
    props = node(t, 'tblPr')
    node(props, 'tblW', w=9242, type='dxa')
    borders = node(props, 'tblBorders')
    for edge in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
        node(borders, edge, val='single', sz=4, color='D9D9D9')
    margins = node(props, 'tblCellMar')
    for edge in ['top', 'bottom', 'left', 'right']:
        node(margins, edge, w=110, type='dxa')
    grid = node(t, 'tblGrid')
    widths = [9242 // columns] * columns
    widths[-1] += 9242 - sum(widths)
    for width in widths:
        node(grid, 'gridCol', w=width)
    for i, row in enumerate(rows):
        tr = node(t, 'tr')
        if i == 0:
            node(node(tr, 'trPr'), 'tblHeader')
        for j in range(columns):
            tc = node(tr, 'tc')
            cp = node(tc, 'tcPr')
            node(cp, 'tcW', w=widths[j], type='dxa')
            node(cp, 'vAlign', val='center')
            if i == 0:
                node(cp, 'shd', fill='F0F0F0')
            text = row[j] if j < len(row) else ''
            paragraph(tc, '**' + text + '**' if i == 0 else text, 'TableText')
    paragraph(parent, '', 'TableText')


def render_report(title, markdown):
    lines = (markdown or '').splitlines()
    first = next((i for i, line in enumerate(lines) if line.startswith('# ')), None)
    if first is not None:
        title = lines[first][2:].strip()
        lines = lines[:first] + lines[first + 1:]
    body_start = next((i for i, line in enumerate(lines) if line.startswith('## ')), 0)
    document = E.Element(tag('document'))
    body = node(document, 'body')
    paragraph(body, title, 'Title')
    cover = lines[:body_start]
    if not any('报告日期' in line for line in cover):
        paragraph(body, '报告日期：' + datetime.now().strftime('%Y年%m月%d日'))
    if not any('报告性质' in line for line in cover):
        paragraph(body, '报告性质：内部工作讨论稿，待人工审阅')
    for line in cover:
        if line.strip():
            paragraph(body, line.strip().removeprefix('> '))
    page_break(body)
    paragraph(body, '目录', 'Title')
    for line in lines[body_start:]:
        if line.startswith('## ') and line[3:].strip() != '目录':
            paragraph(body, line[3:].strip(), 'TOCEntry')
    page_break(body)
    i = body_start
    while i < len(lines):
        line = lines[i].strip()
        if i + 1 < len(lines) and '|' in line and re.fullmatch(r'[\s|:\-]+', lines[i + 1]) and '-' in lines[i + 1]:
            rows = [cells(line)]
            i += 2
            while i < len(lines) and '|' in lines[i] and lines[i].strip():
                rows.append(cells(lines[i])); i += 1
            table(body, rows)
            continue
        heading = re.match(r'^(#{1,6})\s+(.+)', line)
        if heading:
            paragraph(body, heading[2], 'Heading' + str(min(max(len(heading[1])-1, 1), 3)))
        elif line and not re.fullmatch(r'[-*_]{3,}', line):
            paragraph(body, re.sub(r'^[-*+]\s+', '• ', line.removeprefix('> ')))
        i += 1
    section = node(body, 'sectPr')
    E.SubElement(section, tag('footerReference'), {tag('type'): 'default', '{'+R+'}id': 'rId2'})
    node(section, 'pgSz', w=11906, h=16838)
    node(section, 'pgMar', top=1304, right=1332, bottom=1219, left=1332, header=624, footer=567, gutter=0)
    node(section, 'titlePg')
    footer = E.Element(tag('ftr'))
    p = paragraph(footer, '', 'TableText')
    node(p.find(tag('pPr')), 'jc', val='center')
    field = node(p, 'fldSimple', instr=' PAGE ')
    node(node(field, 'r'), 't').text = '1'
    types = '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>%s</Types>''' % ''.join(
        f'<Override PartName="/word/{name}.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml"/>' for name, kind in [('document','document.main'),('styles','styles'),('footer1','footer')])
    relationships = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>'
    rel = lambda id, kind, target: f'<Relationship Id="{id}" Type="{R}/{kind}" Target="{target}"/>'
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', types)
        z.writestr('_rels/.rels', relationships % rel('rId1','officeDocument','word/document.xml'))
        z.writestr('word/_rels/document.xml.rels', relationships % (rel('rId1','styles','styles.xml') + rel('rId2','footer','footer1.xml')))
        z.writestr('word/document.xml', E.tostring(document, encoding='utf-8', xml_declaration=True))
        z.writestr('word/styles.xml', styles())
        z.writestr('word/footer1.xml', E.tostring(footer, encoding='utf-8', xml_declaration=True))
    return output.getvalue()
