const fs = require("fs");
const path = require("path");
const {
  Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell,
  WidthType, AlignmentType, PageBreak, ImageRun, ShadingType,
} = require("docx");

const ROOT = path.resolve(__dirname, "..");
const img = (p) => fs.readFileSync(path.join(ROOT, p));

const H1 = (t) => new Paragraph({ text: t, heading: HeadingLevel.HEADING_1, spacing: { before: 300, after: 150 } });
const H2 = (t) => new Paragraph({ text: t, heading: HeadingLevel.HEADING_2, spacing: { before: 240, after: 120 } });
const H3 = (t) => new Paragraph({ text: t, heading: HeadingLevel.HEADING_3, spacing: { before: 180, after: 100 } });
const P = (t) => new Paragraph({ children: [new TextRun({ text: t })], spacing: { after: 120 } });
const PB = (runs) => new Paragraph({ children: runs, spacing: { after: 120 } });
const bold = (t) => new TextRun({ text: t, bold: true });
const ital = (t) => new TextRun({ text: t, italics: true });
const plain = (t) => new TextRun({ text: t });
const bullet = (t) => new Paragraph({ text: t, bullet: { level: 0 }, spacing: { after: 60 } });
const pending = (t) => new Paragraph({
  spacing: { after: 120 },
  children: [new TextRun({ text: "[PENDING — ", bold: true, color: "B00000" }),
             new TextRun({ text: t, bold: true, color: "B00000" }),
             new TextRun({ text: "]", bold: true, color: "B00000" })],
});
const note = (t) => new Paragraph({ children: [new TextRun({ text: t, italics: true, color: "555555", size: 20 })], spacing: { after: 120 } });
const pageBreak = () => new Paragraph({ children: [new PageBreak()] });

function cell(text, opts = {}) {
  const { header = false, width = 2000, shade = null } = opts;
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    shading: shade ? { type: ShadingType.CLEAR, fill: shade } : undefined,
    children: [new Paragraph({ children: [new TextRun({ text: String(text), bold: header })] })],
  });
}
function row(cells, opts = {}) {
  return new TableRow({ children: cells.map((c, i) => cell(c, { ...opts, width: opts.widths ? opts.widths[i] : 2200 })) });
}
function table(headers, rows, widths) {
  return new Table({
    width: { size: 9350, type: WidthType.DXA },
    rows: [
      row(headers, { header: true, shade: "D9E2F3", widths }),
      ...rows.map((r) => row(r, { widths })),
    ],
  });
}
function image(relPath, w, h) {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [new ImageRun({ type: "png", data: img(relPath), transformation: { width: w, height: h } })],
  });
}

module.exports = { H1, H2, H3, P, PB, bold, ital, plain, bullet, pending, note, pageBreak, table, image, img };
