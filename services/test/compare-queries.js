/**
 * Test script: compare old (cl_to-based) vs new (linktarget-based) categorylinks queries.
 *
 * Connects to the Wikimedia Commons replica database and runs both the old and
 * new versions of each query side-by-side, then asserts that the result sets
 * are identical.
 *
 * NOTE: Both old and new columns must still exist on the server for the
 * comparison to succeed (i.e., run this *before* cl_to is dropped on 1 March).
 *
 * Usage:
 *   ENV=<environment> node services/test/compare-queries.js [category_name]
 *
 * Example:
 *   ENV=production node services/test/compare-queries.js "Images_from_the_Tropenmuseum"
 */

const fs = require("fs");
const path = require("path");
const mariadb = require("mariadb");
const config = require("../config/config.js");
const assert = require("assert");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const log = (...args) =>
  console.log(new Date().toLocaleString() + ":", ...args);

const EXAMPLE_QUERIES_DIR = path.join(__dirname, "..", "example-queries");

/**
 * Read a .sql file from example-queries/, stripping comment lines.
 */
function readExampleQuery(filename) {
  const raw = fs.readFileSync(path.join(EXAMPLE_QUERIES_DIR, filename), "utf8");
  return raw
    .split("\n")
    .filter((l) => !l.trimStart().startsWith("--"))
    .join("\n")
    .trim();
}

// ---------------------------------------------------------------------------
// Old query builders (before linktarget normalization) — kept for comparison
// ---------------------------------------------------------------------------

function buildCategoryQueryOld(RQ) {
  return `SELECT page_title, cl_to, cat_subcats, cat_files
    FROM categorylinks, page, category
    WHERE cl_to IN (${RQ})
    AND page_id = cl_from
    AND page_namespace = 14
    AND page_title = cat_title`;
}

function buildImageQueryOld(RQ) {
  return `SELECT img_name, actor_name AS img_user_text, img_timestamp, img_size, cl_to
    FROM categorylinks, page, image, actor
    WHERE cl_to IN (${RQ})
    AND page_id = cl_from
    AND page_namespace = 6
    AND img_name = page_title
    AND img_actor = actor_id`;
}

function buildUsageQueryOld(RQ) {
  return `SELECT gil_wiki, gil_page_title, gil_to
    FROM globalimagelinks, categorylinks, page, image
    WHERE cl_to IN (${RQ})
    AND gil_to = img_name
    AND gil_page_namespace_id = '0'
    AND page_id = cl_from
    AND page_namespace = 6
    AND img_name = page_title`;
}

// ---------------------------------------------------------------------------
// New query builders (after linktarget normalization) — mirrors etl.js
// ---------------------------------------------------------------------------

function buildCategoryQueryNew(RQ) {
  return `SELECT page_title, lt_title AS cl_to, cat_subcats, cat_files
    FROM categorylinks
    JOIN linktarget ON cl_target_id = lt_id
    JOIN page ON page_id = cl_from
    JOIN category ON page_title = cat_title
    WHERE lt_namespace = 14
    AND lt_title IN (${RQ})
    AND page_namespace = 14`;
}

function buildImageQueryNew(RQ) {
  return `SELECT img_name, actor_name AS img_user_text, img_timestamp, img_size, lt_title AS cl_to
    FROM categorylinks
    JOIN linktarget ON cl_target_id = lt_id
    JOIN page ON page_id = cl_from
    JOIN image ON img_name = page_title
    JOIN actor ON img_actor = actor_id
    WHERE lt_namespace = 14
    AND lt_title IN (${RQ})
    AND page_namespace = 6`;
}

function buildUsageQueryNew(RQ) {
  return `SELECT gil_wiki, gil_page_title, gil_to
    FROM globalimagelinks
    JOIN image ON gil_to = img_name
    JOIN page ON img_name = page_title AND page_namespace = 6
    JOIN categorylinks ON page_id = cl_from
    JOIN linktarget ON cl_target_id = lt_id
    WHERE lt_namespace = 14
    AND lt_title IN (${RQ})
    AND gil_page_namespace_id = '0'`;
}

// ---------------------------------------------------------------------------
// Comparison utilities
// ---------------------------------------------------------------------------

/**
 * Sort an array of row-objects by a deterministic composite key so that
 * set-equality can be checked with deepStrictEqual.
 */
function sortRows(rows, sortKeys) {
  return [...rows].sort((a, b) => {
    for (const key of sortKeys) {
      const aVal = String(a[key] ? a[key] : "");
      const bVal = String(b[key] ? b[key] : "");
      if (aVal < bVal) return -1;
      if (aVal > bVal) return 1;
    }
    return 0;
  });
}

/**
 * Normalise every value in every row to a plain string so that minor type
 * differences (Buffer vs String, BigInt vs Number) don't cause false negatives.
 */
function normaliseRows(rows) {
  return rows.map((row) => {
    const out = {};
    for (const [k, v] of Object.entries(row)) {
      if (Buffer.isBuffer(v)) {
        out[k] = v.toString();
      } else if (typeof v === "bigint") {
        out[k] = v.toString();
      } else if (v === null || v === undefined) {
        out[k] = "";
      } else {
        out[k] = String(v);
      }
    }
    return out;
  });
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function runTest(conn, label, oldQueryFn, newQueryFn, RQ, sortKeys) {
  log(`\n--- ${label} ---`);
  log(`  Params: ${RQ}`);

  const oldQuery = oldQueryFn(RQ);
  const newQuery = newQueryFn(RQ);

  log("  Running OLD query...");
  const oldRows = normaliseRows(await conn.query(oldQuery));
  log(`  OLD returned ${oldRows.length} rows`);

  log("  Running NEW query...");
  const newRows = normaliseRows(await conn.query(newQuery));
  log(`  NEW returned ${newRows.length} rows`);

  const oldSorted = sortRows(oldRows, sortKeys);
  const newSorted = sortRows(newRows, sortKeys);

  try {
    assert.strictEqual(
      oldSorted.length,
      newSorted.length,
      `Row count mismatch: OLD=${oldSorted.length}, NEW=${newSorted.length}`
    );
    assert.deepStrictEqual(oldSorted, newSorted, "Row contents differ");
    log(`  ✅ PASS — ${oldRows.length} rows match`);
  } catch (err) {
    log(`  ❌ FAIL — ${err.message}`);
    if (oldSorted.length > 0) {
      log("  Sample OLD row:", JSON.stringify(oldSorted[0]));
    }
    if (newSorted.length > 0) {
      log("  Sample NEW row:", JSON.stringify(newSorted[0]));
    }
    throw err;
  }
}

(async () => {
  // Accept a category from the command line, or fall back to a small default
  const testCategory =
    process.argv[2] || "Images_from_the_Tropenmuseum";

  const RQ = "'" + testCategory.replace(/'/g, "''") + "'";

  log("=== compare-queries.js ===");
  log(`Test category: ${testCategory}`);
  log("Connecting to Wikimedia Commons replica...");

  let conn;
  try {
    conn = await mariadb.createConnection(config.wmflabs);
  } catch (err) {
    console.error("Could not connect to wmflabs:", err.message);
    process.exit(1);
  }

  let failures = 0;

  // 1. Category query ---------------------------------------------------------
  try {
    await runTest(
      conn,
      "Category Query",
      buildCategoryQueryOld,
      buildCategoryQueryNew,
      RQ,
      ["page_title", "cl_to"]
    );
  } catch {
    failures++;
  }

  // 2. Image query ------------------------------------------------------------
  try {
    await runTest(
      conn,
      "Image Query",
      buildImageQueryOld,
      buildImageQueryNew,
      RQ,
      ["img_name", "cl_to"]
    );
  } catch {
    failures++;
  }

  // 3. Usage query ------------------------------------------------------------
  try {
    await runTest(
      conn,
      "Usage Query",
      buildUsageQueryOld,
      buildUsageQueryNew,
      RQ,
      ["gil_wiki", "gil_page_title", "gil_to"]
    );
  } catch {
    failures++;
  }

  // Cleanup -------------------------------------------------------------------
  await conn.end();

  log("\n===========================================");
  if (failures === 0) {
    log("All 3 query comparisons PASSED ✅");
  } else {
    log(`${failures} query comparison(s) FAILED ❌`);
    process.exit(1);
  }
})();
