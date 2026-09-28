// AI-suggested Treatment button -- Engineer's Report Review page.
//
// This is the 4th approved exception to the site's "no JavaScript" rule
// (after camera-capture.js, geolocation.js, and live-notifications.js) --
// see PROJECT_LOG.md section 88 for the full story.
//
// Why this genuinely needs JavaScript: clicking "Suggest with AI" asks the
// server for a treatment suggestion and drops it straight into the
// Treatment box so the Engineer can read it, edit it, or delete it --
// WITHOUT saving anything to the database yet. A plain HTML form can only
// either reload the whole page or actually submit/save the form; neither
// of those lets the Engineer see a suggestion and still change their mind
// before saving. So this one small script just swaps the text in the box
// in place, nothing else.
//
// If anything goes wrong (no internet, the AI service is down, no API key
// set up yet), it fails with a plain, friendly message in the status text
// next to the button -- the Engineer can always just type their own
// Treatment by hand instead, exactly like before this feature existed.

(function () {
    function setupSuggestRow(row) {
        var url = row.getAttribute("data-suggest-url");
        var targetId = row.getAttribute("data-target");
        var textarea = targetId ? document.getElementById(targetId) : null;
        var button = row.querySelector(".rr-suggest-btn");
        var status = row.querySelector(".rr-suggest-status");

        if (!url || !textarea || !button || !status) {
            return;
        }

        button.addEventListener("click", function () {
            button.disabled = true;
            status.textContent = "Thinking…";
            status.classList.remove("is-error");

            fetch(url, {
                method: "POST",
                headers: { "Accept": "application/json" }
            })
                .then(function (response) {
                    return response.json().then(function (data) {
                        return { ok: response.ok, data: data };
                    });
                })
                .then(function (result) {
                    if (result.ok && result.data && result.data.suggestion) {
                        textarea.value = result.data.suggestion;
                        status.textContent = "Suggestion added. Feel free to review it.";
                    } else {
                        status.textContent = (result.data && result.data.error) || "Couldn't get a suggestion right now.";
                        status.classList.add("is-error");
                    }
                })
                .catch(function () {
                    status.textContent = "Couldn't reach the AI suggestion service. Check your internet connection.";
                    status.classList.add("is-error");
                })
                .finally(function () {
                    button.disabled = false;
                });
        });
    }

    document.querySelectorAll(".rr-suggest-row").forEach(setupSuggestRow);
})();
