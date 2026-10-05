// Cause and Treatment page -- the point cards (tick, edit, add, AI suggest).
//
// This is the 5th approved exception to the site's "no JavaScript" rule
// (after camera-capture.js, geolocation.js, live-notifications.js and
// suggest-treatment.js).
//
// Why this genuinely needs JavaScript: "Suggest with AI" has to bring back
// a short list of points and show them as cards the Engineer can tick or
// edit BEFORE anything is saved, and "Add your own" has to add a new empty
// card. A plain HTML form can only reload the page or save, so neither can
// be done without a script. The script only adds cards to the page; the
// real saving is an ordinary form submit handled by the server, which keeps
// only the ticked cards.
//
// If anything goes wrong (no internet, AI service down, no API key set up),
// a plain message appears next to the buttons and the Engineer can still
// add and tick their own points.

(function () {
    function setup(section) {
        var kind = section.getAttribute("data-pt-kind");
        var url = section.getAttribute("data-pt-url");
        var list = section.querySelector(".pt-list");
        var status = section.querySelector(".pt-status");
        var suggestBtn = section.querySelector(".pt-suggest");
        var addBtn = section.querySelector(".pt-add");
        var next = parseInt(section.getAttribute("data-pt-start"), 10) || 0;

        function say(text, isError) {
            status.textContent = text;
            status.classList.toggle("is-error", !!isError);
        }

        // Make a box exactly as tall as its text, so short points stay one
        // line and long ones grow instead of scrolling.
        function fit(ta) {
            ta.style.height = "auto";
            ta.style.height = ta.scrollHeight + "px";
        }

        function existingTexts() {
            var out = [];
            list.querySelectorAll(".pt-text").forEach(function (box) {
                out.push(box.value.trim().toLowerCase());
            });
            return out;
        }

        function addCard(text, ticked) {
            var n = next++;
            var li = document.createElement("li");
            li.className = "pt-item";

            var label = document.createElement("label");
            label.className = "pt-check";
            var cb = document.createElement("input");
            cb.type = "checkbox";
            cb.name = kind + "_keep_" + n;
            cb.checked = !!ticked;
            cb.setAttribute("aria-label", "Keep this point");
            var box = document.createElement("span");
            box.className = "pt-box";
            label.appendChild(cb);
            label.appendChild(box);

            var ta = document.createElement("textarea");
            ta.name = kind + "_text_" + n;
            ta.rows = 1;
            ta.className = "pt-text";
            ta.value = text || "";

            ta.addEventListener("input", function () { fit(ta); });
            li.appendChild(label);
            li.appendChild(ta);
            list.appendChild(li);
            fit(ta);
            return ta;
        }

        list.querySelectorAll(".pt-text").forEach(function (ta) {
            ta.addEventListener("input", function () { fit(ta); });
            fit(ta);
        });

        if (addBtn) {
            addBtn.addEventListener("click", function () {
                var ta = addCard("", true);
                ta.focus();
                say("", false);
            });
        }

        if (suggestBtn) {
            suggestBtn.addEventListener("click", function () {
                suggestBtn.disabled = true;
                say("Thinking…", false);

                fetch(url, { method: "POST", headers: { "Accept": "application/json" } })
                    .then(function (response) {
                        return response.json().then(function (data) {
                            return { ok: response.ok, data: data };
                        });
                    })
                    .then(function (result) {
                        var items = result.data && result.data.suggestions;
                        if (result.ok && items && items.length) {
                            var have = existingTexts();
                            var added = 0;
                            items.forEach(function (text) {
                                if (have.indexOf(String(text).trim().toLowerCase()) === -1) {
                                    addCard(text, false);
                                    added++;
                                }
                            });
                            say(added
                                ? added + " suggestion" + (added === 1 ? "" : "s") + " added. Tick the ones you want to keep."
                                : "Those suggestions are already in your list.", false);
                        } else {
                            say((result.data && result.data.error) || "Couldn't get a suggestion right now.", true);
                        }
                    })
                    .catch(function () {
                        say("Couldn't reach the AI suggestion service. Check your internet connection.", true);
                    })
                    .finally(function () {
                        suggestBtn.disabled = false;
                    });
            });
        }
    }

    document.querySelectorAll(".pt-section").forEach(setup);
})();
