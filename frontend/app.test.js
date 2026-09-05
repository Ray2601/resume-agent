const assert = require("node:assert/strict");
const { mergeUploadedFiles, appendText } = require("./app.js");

(async () => {
  const files = [
    { name: "one.md", text: async () => "first resume" },
    { name: "two.txt", text: async () => "second resume" },
    { name: "ignored.pdf", text: async () => "ignore" },
  ];
  const merged = await mergeUploadedFiles(files, "REFERENCE RESUME");
  assert.match(merged, /REFERENCE RESUME: one\.md/);
  assert.match(merged, /REFERENCE RESUME: two\.txt/);
  assert.doesNotMatch(merged, /ignored/);
  assert.equal(appendText("a", "b"), "a\n\nb");
  console.log("frontend file merge tests passed");
})().catch(error => { console.error(error); process.exit(1); });
