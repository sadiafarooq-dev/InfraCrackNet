// Location for the New Report -- Location step.
//
// This is the 3rd approved exception to the site's "no JavaScript" rule
// (after camera-capture.js and live-notifications.js) -- turning
// coordinates into a readable address, and (for the fallback button)
// asking the browser for the device's live GPS, both genuinely need
// JavaScript, which plain HTML/CSS can't do.
//
// Two honestly different jobs in this one file now (see PROJECT_LOG.md
// section 69 for the full story of why):
//
//   1. PREFERRED: if the server already found a real GPS location saved
//      INSIDE the uploaded photo/video itself (see exif_location.py), that
//      location is handed to this file as window.__infracracknet_exif_location
//      by new_report_location.html. This is the trustworthy one -- it's
//      where the photo/video was actually TAKEN. All this file has to do
//      for it is turn those coordinates into a readable address
//      automatically, since the coordinates themselves and the
//      location_source="exif" hidden field are already filled in by the
//      server, in the HTML, before this script even runs.
//
//   2. FALLBACK ONLY: the "Use My Current Location" / "Use my device's
//      current location instead" button. This is the ORIGINAL behaviour
//      from before section 69, kept only as a fallback for files with no
//      GPS saved in them -- it asks the browser "where is this device
//      RIGHT NOW", which is NOT the same as where the photo/video was
//      taken if the Inspector uploads later, from somewhere else. Using it
//      marks location_source="device" so the app can be honest about that
//      difference everywhere the location is later shown.
//
// Either way, if anything goes wrong at any step (permission denied, no
// GPS available, the lookup service doesn't respond), it fails quietly --
// the Inspector can still just type the address in by hand, exactly like
// before. Nothing here is required for the form to work.

(function () {
    const statusEl = document.getElementById("location-status");
    const addressInput = document.getElementById("location-input");
    const latInput = document.getElementById("latitude-input");
    const lonInput = document.getElementById("longitude-input");
    const sourceInput = document.getElementById("location-source-input");

    function reverseGeocode(lat, lon, onDone, onFail) {
        // Turns raw coordinates into a readable address, using a free,
        // no-API-key-needed lookup service (OpenStreetMap's Nominatim). If
        // this fails (offline, service down), the real coordinates are
        // kept either way -- only the address TEXT is lost, and the
        // Inspector can type it in by hand.
        fetch("https://nominatim.openstreetmap.org/reverse?format=json&lat=" + lat + "&lon=" + lon + "&zoom=18")
            .then(function (resp) { return resp.json(); })
            .then(function (data) {
                if (data && data.display_name) {
                    onDone(data.display_name);
                } else {
                    onFail();
                }
            })
            .catch(onFail);
    }

    // -- Job 1: auto-fill the address for a real location already found in
    // the uploaded file (see new_report_location.html for where this
    // global comes from). Runs automatically, no button needed.
    const exifStatusEl = document.getElementById("exif-location-status");
    if (window.__infracracknet_exif_location && exifStatusEl) {
        const loc = window.__infracracknet_exif_location;
        reverseGeocode(
            loc.lat, loc.lon,
            function (address) {
                if (addressInput) addressInput.value = address;
                exifStatusEl.textContent = "Address filled in automatically from the real location -- feel free to edit it.";
            },
            function () {
                exifStatusEl.textContent = "Got the real location from your file, but couldn't turn it into an address -- please type the address in below (the coordinates themselves are still saved).";
                exifStatusEl.style.color = "#B85419";
            }
        );
    }

    // -- Job 2: the fallback button, same behaviour as before section 69,
    // now also honestly marking location_source="device" when used.
    const btn = document.getElementById("use-my-location-btn");
    if (!btn) return; // not on this page

    function setStatus(text, isError) {
        if (!statusEl) return;
        statusEl.textContent = text;
        statusEl.style.color = isError ? "#B85419" : "#4C6789";
    }

    if (!("geolocation" in navigator)) {
        // Feature just doesn't show up as usable -- manual typing still works.
        setStatus("Automatic location isn't supported by this browser -- please type it in below.", true);
        return;
    }

    btn.addEventListener("click", function () {
        setStatus("Getting your device's current location...", false);
        btn.disabled = true;

        navigator.geolocation.getCurrentPosition(
            function (position) {
                const lat = position.coords.latitude;
                const lon = position.coords.longitude;

                if (latInput) latInput.value = lat;
                if (lonInput) lonInput.value = lon;
                if (sourceInput) sourceInput.value = "device";

                // Lets location-map.js (section 122) move its pin to match,
                // without this file needing to know the map exists at all --
                // on a page with no map, nothing is listening and this is a
                // harmless no-op.
                document.dispatchEvent(new CustomEvent("infracracknet:location-updated", { detail: { lat: lat, lon: lon } }));

                setStatus("Device location found (" + lat.toFixed(5) + ", " + lon.toFixed(5) + ") -- looking up the address...", false);

                reverseGeocode(
                    lat, lon,
                    function (address) {
                        if (addressInput) addressInput.value = address;
                        setStatus("Address filled in automatically -- feel free to edit it. Remember, this is your device's current location, which may differ from where the photo/video was actually taken.", false);
                        btn.disabled = false;
                    },
                    function () {
                        setStatus("Got your device's current GPS location, but couldn't turn it into an address -- please type the address in below.", true);
                        btn.disabled = false;
                    }
                );
            },
            function (err) {
                // Permission denied, timed out, or position unavailable.
                setStatus("Couldn't get your device's location automatically -- please type it in below.", true);
                btn.disabled = false;
            },
            { timeout: 10000 }
        );
    });
})();
