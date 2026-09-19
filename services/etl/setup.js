var config = require("../config/config.js");
var fs = require("fs");
var path = require("path");

var DEFAULT_FILES = [
    "db_init.sql",
    "dailyInsert.sql",
    "functions.sql",
    "maintenance.sql",
    "incremental.sql",
];

async function runSqlFiles(db, files) {
    for (var i = 0; i < files.length; i++) {
        var filetoRead = path.join(__dirname, "SQL", files[i]);
        console.log(filetoRead);
        await db.query(fs.readFileSync(filetoRead, "utf8"));
    }
}

async function main() {
    if (process.argv.length < 3) {
        console.log("Missing GLAM name");
        process.exit(1);
    }
    var name = process.argv[2];
    var files = process.argv.length > 3 ? process.argv.slice(3) : DEFAULT_FILES;
    var glams = await config.loadGlams();
    var glam = glams[name];
    if (glam === undefined) {
        console.log("Unknown GLAM name");
        process.exit(1);
    }
    console.log("Setup started");
    try {
        await runSqlFiles(glam.connection, files);
    } catch (err) {
        console.log("Error applying SQL");
        console.log(err);
        process.exit(1);
    }
    console.log("Setup ended");
    process.exit(0);
}

main();
