const fs = require("fs");
const path = require("path");
const { Document, Packer } = require("docx");

const { cover, toc, s1, s2, s3 } = require("./content_part1");
const { s4, s5, s6 } = require("./content_part2");
const { s7, s8, s9, s10 } = require("./content_part3");
const { s11, s12, s13, s14, s15, s16, s17 } = require("./content_part4");

const children = [
  ...cover, ...toc,
  ...s1, ...s2, ...s3, ...s4, ...s5, ...s6, ...s7, ...s8, ...s9, ...s10,
  ...s11, ...s12, ...s13, ...s14, ...s15, ...s16, ...s17,
];

const doc = new Document({
  sections: [{
    properties: {
      page: { size: { width: 12240, height: 15840 }, margin: { top: 1080, bottom: 1080, left: 1080, right: 1080 } },
    },
    children,
  }],
  styles: {
    default: {
      document: { run: { font: "Calibri", size: 22 } },
      heading1: { run: { font: "Calibri", size: 32, bold: true, color: "1F3864" } },
      heading2: { run: { font: "Calibri", size: 26, bold: true, color: "2E5496" } },
      heading3: { run: { font: "Calibri", size: 24, bold: true, color: "44546A" } },
      title: { run: { font: "Calibri", size: 44, bold: true, color: "1F3864" } },
    },
  },
});

Packer.toBuffer(doc).then((buf) => {
  const out = path.resolve(__dirname, "..", "Motorq_Hackathon_Solution_Document_CVI.docx");
  fs.writeFileSync(out, buf);
  console.log("Written:", out, "(" + (buf.length / 1024).toFixed(0) + " KB)");
}).catch((e) => {
  console.error("BUILD FAILED:", e);
  process.exit(1);
});
