// Entry point for the onboard golden fixture app.
// This file is a stable FILE anchor used by onboard-repo acceptance tests.
const express = require("express");

const app = express();
app.get("/", (_req, res) => res.send("onboard golden fixture"));

module.exports = app;
