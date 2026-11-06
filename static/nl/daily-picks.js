/* Northern Lights "Picked for you today": films from what the viewer watched lately (GET /api/nl/daily-picks).
 *
 *   NLDaily.homeRow(section, list, {tv, root, href})   fills the home row; hides it when there is nothing
 *
 * The server makes the list (Wishmatch, once a day) and says for each film whether it is on the server.
 * A film on the server opens its page. Any other card is one button: "Get it" asks mars to find the best
 * copy and download it (POST /api/nl/daily-picks/download); the page never sees a torrent link.
 * One focusable element per card, so the TV remote moves along the row as on any other row.
 * All text goes in through textContent (NL.el). */
(function (global) {
  "use strict";
  var NL = global.NL;
  var el = NL.el;

  function facts(c) {
    var bits = [];
    if (c.release_year) { bits.push(c.release_year); }
    return bits.concat(NL.scoreFacts({ imdb: c.imdb_rating, rt: c.rt_score, tmdb: c.rating })).join(" · ");
  }

  function badgeText(c) {
    if (c.ready) { return "On the server"; }
    return c.downloading ? "Downloading" : "Get it";
  }

  function card(c, opts) {
    var tv = !!opts.tv;
    var p = tv ? "tv" : "nl";
    var li = el("li", tv ? "tv-item tv-item-pick" : "nl-daily-item");
    var box;
    if (c.ready && c.href) {
      box = el("a", p + "-card");
      box.href = (opts.href || function (x) { return x; })(c.href);
    } else {
      box = el("button", p + "-card " + p + "-daily-get");
      box.type = "button";
    }
    if (tv) { box.setAttribute("data-nl-focus", ""); }
    var art = el("div", tv ? "tv-art tv-poster" : "nl-poster");
    NL.fillImage(art, [c.poster_url], c.title);
    var badge = el("span", p + "-badge", badgeText(c));
    art.appendChild(badge);
    box.appendChild(art);
    box.appendChild(el("div", p + "-name", c.title));
    box.appendChild(el("div", p + "-meta", facts(c)));
    box.appendChild(el("p", p + "-reason", c.why || ""));
    var msg = el("p", p + "-daily-msg");
    msg.setAttribute("role", "status");
    box.appendChild(msg);

    function label() {
      var action = c.ready ? "Open" : (c.downloading ? "Downloading" : "Get it");
      box.setAttribute("aria-label", action + ": " + c.title + (c.release_year ? " (" + c.release_year + ")" : "") +
        ". " + facts(c) + ". " + (c.why || ""));
    }
    label();

    if (!c.ready) {
      // aria-disabled, not disabled: a disabled button loses the TV remote's focus.
      var busy = !!c.downloading;
      box.setAttribute("aria-disabled", busy ? "true" : "false");
      box.addEventListener("click", function () {
        if (busy) { return; }
        busy = true;
        box.setAttribute("aria-disabled", "true");
        badge.textContent = "Finding a copy";
        NL.fetchJSON("/api/nl/daily-picks/download", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ media_item_id: c.id })
        }).then(function () {
          c.downloading = true;
          badge.textContent = "Downloading";
          msg.textContent = "Downloading. It shows on Downloads.";
          label();
        }).catch(function (err) {
          busy = false;
          box.setAttribute("aria-disabled", "false");
          badge.textContent = "Get it";
          msg.textContent = err.message || "Couldn't start the download.";
        });
      });
    }
    li.appendChild(box);
    return li;
  }

  function homeRow(sectionEl, listEl, opts) {
    opts = opts || {};
    return NL.fetchJSON("/api/nl/daily-picks").then(function (data) {
      var picks = data.picks || [];
      if (!data.enabled || !picks.length) { sectionEl.hidden = true; return false; }
      picks.forEach(function (c) { listEl.appendChild(card(c, opts)); });
      sectionEl.hidden = false;
      return true;
    }).catch(function () { sectionEl.hidden = true; return false; });
  }

  global.NLDaily = { homeRow: homeRow, card: card };
})(window);
