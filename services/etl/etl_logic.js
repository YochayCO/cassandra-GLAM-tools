function groupCatParents(queue, n) {
  const byTitle = new Map();
  for (let i = 0; i < n; i++) {
    const c = queue[i];
    let row = byTitle.get(c.page_title);
    if (!row) {
      row = {
        page_title: c.page_title,
        cat_subcats: c.cat_subcats,
        cat_files: c.cat_files,
        cl_to: [],
        cat_level: [],
      };
      byTitle.set(c.page_title, row);
    }
    row.cl_to.push(c.father);
    row.cat_level.push(c.level);
    row.cat_subcats = c.cat_subcats;
    row.cat_files = c.cat_files;
  }
  return Array.from(byTitle.values());
}

module.exports = { groupCatParents };
