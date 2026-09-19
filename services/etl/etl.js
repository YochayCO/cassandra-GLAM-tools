const config = require("../config/config.js");
const mariadb = require("mariadb");
const { groupCatParents } = require("./etl_logic.js");

const log = (...args) =>
  console.log(new Date().toLocaleString() + ":", ...args);

const CONST_USE_PER_QUERY = 10;
const CONST_CAT_PER_QUERY = 40;
var IMAGE_PAGE = 10000;
var USAGE_FLUSH = 5000;

var wikiCaller;
var images;
var catQueue;
var catHead = 0;
var imgIndex;
var countImages = 0;
var catFreeTail = 0;
var usages;
var usagindex = 0;
var usageOurs;
var glam;

async function finalize() {
  log("Number of categories: " + catFreeTail);
  log("Number of images: " + countImages);
  const rows = groupCatParents(catQueue, catFreeTail);
  const titles = rows.map((r) => r.page_title);
  const client = await glam.connection.connect();
  try {
    await client.query("BEGIN");
    for (const r of rows) {
      await client.query(
        "INSERT INTO categories (page_title, cat_subcats, cat_files, cl_to, cat_level) " +
          "VALUES ($1, $2, $3, $4, $5) " +
          "ON CONFLICT (page_title) DO UPDATE SET " +
          "cat_subcats = EXCLUDED.cat_subcats, cat_files = EXCLUDED.cat_files, " +
          "cl_to = EXCLUDED.cl_to, cat_level = EXCLUDED.cat_level",
        [r.page_title, r.cat_subcats, r.cat_files, r.cl_to, r.cat_level]
      );
    }
    if (titles.length) {
      await client.query(
        "DELETE FROM categories WHERE page_title <> ALL($1::varchar[])",
        [titles]
      );
    }
    await client.query(
      "UPDATE images SET is_alive = false WHERE NOT (cl_to && (SELECT array_agg(page_title) FROM categories))"
    );
    await client.query("select * from doMaintenance();");
    await client.query("COMMIT");
  } catch (e) {
    try {
      await client.query("ROLLBACK");
    } catch (_) {}
    throw e;
  } finally {
    client.release();
  }
}

function searchCatQueue(page) {
  var i = 0;
  while (i < catHead) {
    if (catQueue[i].page_title == page) break;
    i++;
  }
  if (i == catHead) i = -1;
  return i;
}

async function wikiOpen(starting_cat) {
  catQueue = [];
  catFreeTail = 1;
  catHead = 0;
  catQueue[0] = new Object();
  catQueue[0].page_title = starting_cat;
  catQueue[0].level = 0;
  catQueue[0].cat_subcats = 0;
  catQueue[0].cat_files = 0;
  catQueue[0].father = "ROOT";
  log("===========================================");
  log("Loading categories...");

  let temp_query =
    "select cat_subcats, cat_files from category where cat_title='" +
    starting_cat.replace(/\'/g, "''") +
    "'";
  const rows = await wikiCaller.query(temp_query);
  if (!rows || rows.length === 0) {
    console.error("Category missing on replica: " + starting_cat);
    process.exit(65);
  }
  catQueue[0].cat_subcats = rows[0].cat_subcats;
  catQueue[0].cat_files = rows[0].cat_files;
  await getLevelChilds();
}

async function getLevelChilds() {
  if (catHead >= catFreeTail) {
    // all visited
    await afterCategories();
    return;
  }

  log("At " + catHead + " of " + catFreeTail);
  var RQ = "";
  var originalHead = catHead;
  while (
    catHead < catFreeTail &&
    catHead < originalHead + CONST_CAT_PER_QUERY
  ) {
    if (catHead > originalHead) RQ += ",";
    RQ += "'" + catQueue[catHead].page_title.replace(/'/g, "''") + "'";
    catHead++;
  }

  var query = buildCategoryQuery(RQ);
  const rows = await wikiCaller.query(query);
  for (var k = 0; k < rows.length; k++) {
    var newPage = rows[k].page_title.toString();
    var father = rows[k].cl_to.toString();
    if (searchCatQueue(newPage) == -1) {
      //prevent loops
      var fatherIndex = searchCatQueue(father);
      catQueue[catFreeTail] = new Object();
      catQueue[catFreeTail].page_title = newPage;
      catQueue[catFreeTail].cat_subcats = rows[k].cat_subcats;
      catQueue[catFreeTail].cat_files = rows[k].cat_files;
      catQueue[catFreeTail].level = catQueue[fatherIndex].level + 1;
      catQueue[catFreeTail].father = father;

      catFreeTail++;
    }
  }
  await getLevelChilds();
}

async function afterCategories() {
  log("===========================================");
  log("Now loading images...");
  catHead = 0;
  imgIndex = 0;
  await loadImages();
}

async function loadImages() {
  if (catHead >= catFreeTail) {
    await loadImagesIntoDB();
    log("===========================================");
    log("Loading usages...");
    usages = [];
    usagindex = 0;
    catHead = 0;
    await prepareUsages();
    await loadUsages();
    return;
  }

  if (countImages >= config.limits["images"]) {
    log(`The number of images (${countImages}) exceeded limit in config`);
  }

  // TODO can be optimized looking for files number and merging little cats calls
  log("At " + catHead + " of " + catFreeTail);
  const prev = await glam.connection.query(
    "SELECT cat_files, cat_subcats FROM categories WHERE page_title = $1",
    [catQueue[catHead].page_title]
  );
  if (
    prev.rows[0] &&
    prev.rows[0].cat_files != null &&
    prev.rows[0].cat_subcats != null &&
    Number(prev.rows[0].cat_files) === Number(catQueue[catHead].cat_files) &&
    Number(prev.rows[0].cat_subcats) === Number(catQueue[catHead].cat_subcats)
  ) {
    catHead++;
    return loadImages();
  }

  var title = catQueue[catHead].page_title;
  var RQ = "'" + title.replace(/'/g, "''") + "'";
  catHead++;

  const names = [];
  let offset = 0;
  for (;;) {
    imgIndex = 0;
    images = [];
    // ponytail: OFFSET walk; keyset on img_name if a category's deep pages dominate runtime
    var query =
      buildImageQuery(RQ) + " LIMIT " + IMAGE_PAGE + " OFFSET " + offset;
    const rows = await wikiCaller.query(query);

    for (var k = 0; k < rows.length; k++) {
      images[imgIndex] = new Object();
      images[imgIndex].img_name = rows[k].img_name.toString();
      images[imgIndex].img_user_text = rows[k].img_user_text.toString();
      images[imgIndex].img_timestamp = convertTimestamp(rows[k].img_timestamp);
      images[imgIndex].img_size = rows[k].img_size;
      images[imgIndex].cl_to = rows[k].cl_to.toString();
      names.push(images[imgIndex].img_name);
      imgIndex++;
      countImages++;
    }
    await loadImagesIntoDB();
    if (rows.length < IMAGE_PAGE) break;
    offset += IMAGE_PAGE;
  }
  imgIndex = 0;
  await glam.connection.query(
    "UPDATE images SET cl_to = array_remove(cl_to, $1) WHERE $1 = ANY(cl_to) AND NOT (img_name = ANY($2::varchar[]))",
    [title, names]
  );
  await glam.connection.query(
    "UPDATE categories SET last_crawled_at = NOW() WHERE page_title = $1",
    [title]
  );
  return loadImages();
}

var convertTimestamp = function (timestamp) {
  const TS = timestamp.toString();
  let retval =
    TS.substring(0, 4) +
    "-" +
    TS.substring(4, 6) +
    "-" +
    TS.substring(6, 8) +
    " " +
    TS.substring(8, 10) +
    ":" +
    TS.substring(10, 12) +
    ":" +
    TS.substring(12, 14);
  return retval;
};

async function loadImagesIntoDB() {
  let storage_query = ""; //"update images set is_alive=false;";
  let i = 0;
  while (i < imgIndex) {
    let temp = "";
    let img = images[i];
    temp +=
      "select * from addImage('" +
      img.img_name.replace(/'/g, "''") +
      "','" +
      img.img_user_text.replace(/'/g, "''") +
      "','" +
      img.img_timestamp +
      "'," +
      img.img_size +
      ",'" +
      img.cl_to.replace(/'/g, "''") +
      "');\r\n";
    storage_query += temp;
    if (i % 10000 == 0) {
      log("Adding 10,000 images to database");
      await glam.connection.query(storage_query);
      storage_query = "";
    }
    i++;
  }
  log("Updating Postgres data...");
  if (storage_query) {
    await glam.connection.query(storage_query);
  }
}

async function loadUsages() {
  if (catHead >= catFreeTail) {
    await afterUsages();
    return;
  }
  log("At " + catHead + " of " + catFreeTail);
  var RQ = "";
  var originalHead = catHead;
  while (
    catHead < catFreeTail &&
    catHead < originalHead + CONST_USE_PER_QUERY
  ) {
    if (catHead > originalHead) RQ += ",";
    RQ += "'" + catQueue[catHead].page_title.replace(/'/g, "''") + "'";
    catHead++;
  }

  var query = buildUsageQuery(RQ);
  let rows = await wikiCaller.query(query);
  for (var k = 0; k < rows.length; k++) {
    usages[usagindex] = new Object();
    usages[usagindex].gil_wiki = rows[k].gil_wiki;
    usages[usagindex].gil_page_title = rows[k].gil_page_title.toString();
    usages[usagindex].gil_to = rows[k].gil_to.toString();
    usages[usagindex].gil_page = rows[k].gil_page;
    usagindex++;
    if (usagindex >= USAGE_FLUSH) {
      await flushUsages();
    }
  }
  rows = null;
  return loadUsages();
}

async function flushUsages() {
  let i = 0;
  let storage_query = "";
  while (i < usagindex) {
    let use = usages[i];
    if (use.gil_page != null && use.gil_page !== "") {
      const key =
        String(use.gil_to) +
        "\0" +
        String(use.gil_wiki) +
        "\0" +
        String(use.gil_page);
      if (usageOurs.has(key)) {
        usageOurs.delete(key);
        i++;
        continue;
      }
    }
    let sql =
      "select * from addUsage('" +
      use.gil_wiki +
      "','" +
      use.gil_page_title.replace(/'/g, "''") +
      "','" +
      use.gil_to.replace(/'/g, "''") +
      "'";
    if (use.gil_page != null && use.gil_page !== "") {
      sql += "," + Number(use.gil_page);
    }
    storage_query += sql + ");\r\n";
    i++;
  }
  if (storage_query) {
    log("Updating Postgres data...");
    await glam.connection.query(storage_query);
  }
  usages = [];
  usagindex = 0;
}

async function afterUsages() {
  await flushUsages();
  await closeVanishedUsages();
  await glam.connection.query(
    "UPDATE usages SET is_alive=false WHERE is_alive = true AND gil_page IS NULL"
  );
  await finalize();
}

async function prepareUsages() {
  usageOurs = new Set();
  const live = await glam.connection.query(
    "SELECT gil_to, gil_wiki, gil_page FROM usages WHERE is_alive = true AND gil_page IS NOT NULL"
  );
  for (const r of live.rows) {
    usageOurs.add(
      String(r.gil_to) + "\0" + String(r.gil_wiki) + "\0" + String(r.gil_page)
    );
  }
}

async function closeVanishedUsages() {
  if (usageOurs.size) {
    const tos = [];
    const wikis = [];
    const pages = [];
    for (const key of usageOurs) {
      const parts = key.split("\0");
      tos.push(parts[0]);
      wikis.push(parts[1]);
      pages.push(Number(parts[2]));
    }
    await glam.connection.query(
      "UPDATE usages SET is_alive=false WHERE is_alive = true AND (gil_to, gil_wiki, gil_page) IN (SELECT * FROM unnest($1::varchar[], $2::varchar[], $3::int[]))",
      [tos, wikis, pages]
    );
  }
  await glam.connection.query(
    "UPDATE usages SET last_seen=CURRENT_DATE WHERE is_alive = true AND gil_page IS NOT NULL"
  );
}

var buildUsageQuery = function (RQ) {
  return `SELECT gil_wiki, gil_page, gil_page_title, gil_to
    FROM globalimagelinks
    JOIN image ON gil_to = img_name
    JOIN page ON img_name = page_title AND page_namespace = 6
    JOIN categorylinks ON page_id = cl_from
    JOIN linktarget ON cl_target_id = lt_id
    WHERE lt_namespace = 14
    AND lt_title IN (${RQ})
    AND gil_page_namespace_id = '0'`;
};
var buildImageQuery = function (RQ) {
  return `SELECT img_name, actor_name AS img_user_text, img_timestamp, img_size, lt_title AS cl_to
    FROM categorylinks
    JOIN linktarget ON cl_target_id = lt_id
    JOIN page ON page_id = cl_from
    JOIN image ON img_name = page_title
    JOIN actor ON img_actor = actor_id
    WHERE lt_namespace = 14
    AND lt_title IN (${RQ})
    AND page_namespace = 6
    ORDER BY img_name`;
};
var buildCategoryQuery = function (RQ) {
  return `SELECT page_title, lt_title AS cl_to, cat_subcats, cat_files
    FROM categorylinks
    JOIN linktarget ON cl_target_id = lt_id
    JOIN page ON page_id = cl_from
    JOIN category ON page_title = cat_title
    WHERE lt_namespace = 14
    AND lt_title IN (${RQ})
    AND page_namespace = 14`;
};

// ENTRY POINT
if (process.argv.length != 3) {
  log("Missing GLAM name");
  process.exit(1);
}

config.loadGlams().then(async (glams) => {
  glam = glams[process.argv[2]];

  if (glam === undefined) {
    log("Unknown GLAM name");
    process.exit(1);
  }

  log("Application launched...");
  wikiCaller = await mariadb.createConnection(config.wmflabs);

  log("Working for " + glam.fullname);
  await wikiOpen(glam.category.replace(/ /g, "_"));
  process.exit(0);
}).catch(e => {
    console.error(e);
    process.exit(1);
});
