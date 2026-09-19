const { test } = require("node:test");
const assert = require("node:assert/strict");
const { groupCatParents } = require("../etl/etl_logic.js");

test("two fathers become two-element arrays; last counts win", () => {
  const queue = [
    { page_title: "Root", father: "ROOT", level: 0, cat_subcats: 1, cat_files: 0 },
    { page_title: "Child", father: "Root", level: 1, cat_subcats: 0, cat_files: 4 },
    { page_title: "Child", father: "Other", level: 2, cat_subcats: 0, cat_files: 5 },
  ];
  const rows = groupCatParents(queue, 3);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], {
    page_title: "Root",
    cat_subcats: 1,
    cat_files: 0,
    cl_to: ["ROOT"],
    cat_level: [0],
  });
  assert.deepEqual(rows[1], {
    page_title: "Child",
    cat_subcats: 0,
    cat_files: 5,
    cl_to: ["Root", "Other"],
    cat_level: [1, 2],
  });
});

test("n shorter than queue is the walk length", () => {
  const queue = [
    { page_title: "A", father: "ROOT", level: 0, cat_subcats: 0, cat_files: 1 },
    { page_title: "B", father: "A", level: 1, cat_subcats: 0, cat_files: 2 },
  ];
  const rows = groupCatParents(queue, 1);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].page_title, "A");
});
