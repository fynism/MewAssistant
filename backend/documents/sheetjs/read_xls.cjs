const XLSX = require("xlsx");

function displayedValue(cell) {
  if (cell?.v == null || cell.t === "z") return null;
  if (cell.t === "e") return cell.w || "#VALUE!";
  if (cell.t === "b") return cell.v ? "TRUE" : "FALSE";
  // Excel dates are local serial values. Use the stored format without a time-zone conversion.
  if (cell.t === "n" && cell.z && XLSX.SSF.is_date(cell.z)) return cell.w ?? String(cell.v);
  return String(cell.v).toWellFormed().replaceAll("\0", "");
}

function* cells(sheet) {
  // !ref may describe formatting or an incorrect range. Iterate only stored values.
  for (const ref in sheet) {
    if (ref.startsWith("!")) continue;
    const value = displayedValue(sheet[ref]);
    if (value == null || value === "") continue;
    const {r, c} = XLSX.utils.decode_cell(ref);
    if (r < 0 || r >= 65536 || c < 0 || c >= 256) throw new Error("invalid cell position");
    yield [r + 1, c + 1, value];
  }
}

const [source, mode, ...options] = process.argv.slice(2);
try {
  const book = XLSX.readFile(source, {cellFormula: false, cellHTML: false, cellNF: true});
  if (book.SheetNames.length > 256) process.exit(3);
  if (!book.SheetNames.length || book.SheetNames.some(name => !name.isWellFormed() || name.length > 128 || name.includes("\0"))) {
    process.exit(2);
  }
  if (mode === "preview") {
    const [requested, ...bounds] = options;
    const [row, col, rowLimit, colLimit, textLimit] = bounds.map(Number);
    const sheet = requested || book.SheetNames[0];
    if (!book.SheetNames.includes(sheet)) process.exit(4);
    const page = [];
    let totalRows = 0, totalCols = 0;
    for (const [r, c, value] of cells(book.Sheets[sheet])) {
      totalRows = Math.max(totalRows, r);
      totalCols = Math.max(totalCols, c);
      if (r >= row && r < row + rowLimit && c >= col && c < col + colLimit) {
        const text = value.length > textLimit ? value.slice(0, textLimit - 1).toWellFormed() + "…" : value;
        page.push([r, c, text]);
      }
    }
    process.stdout.write(JSON.stringify([book.SheetNames, sheet, totalRows, totalCols, page]));
  } else {
    const [cellLimit, textLimit] = options.map(Number);
    let count = 0, length = 0;
    const worksheets = book.SheetNames.map(name => {
      const entries = [];
      for (const cell of cells(book.Sheets[name])) {
        length += cell[2].length;
        if (++count > cellLimit || length > textLimit) process.exit(3);
        entries.push(cell);
      }
      entries.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
      return [name, entries];
    });
    process.stdout.write(JSON.stringify(worksheets));
  }
} catch {
  process.exit(2);
}
