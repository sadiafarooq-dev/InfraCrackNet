// Real interactive map for the New Report -- Location step (section 122,
// replaces the old static placeholder box with a pin icon on a gray grid).
//
// Uses Leaflet + OpenStreetMap tiles -- both free and needing no API key,
// the same family of service as the free Nominatim address lookups
// geolocation.js already uses for this same page.
//
// This map is purely a visual confirmation, nothing more:
//   - It shows a pin at whatever coordinates the app already has, either
//     the real GPS location found inside the uploaded photo/video (filled
//     in server-side, before this script even runs), or the device's
//     current location from the "Use My Current Location" fallback button.
//   - It does NOT geocode a manually-typed address -- typing an address by
//     hand with no coordinates just leaves the map at its default zoomed-out
//     view.
//   - The pin can't be dragged to correct the location; the coordinates
//     always come from the existing hidden fields, same as before this
//     map existed.
//
// If Leaflet fails to load (offline, blocked), this file quietly does
// nothing -- the address field and hidden coordinate fields work exactly
// as they did before, so nothing about submitting the report depends on
// the map actually rendering.

(function () {
    const mapEl = document.getElementById("location-map");
    if (!mapEl || typeof L === "undefined") return;

    const latInput = document.getElementById("latitude-input");
    const lonInput = document.getElementById("longitude-input");

    const DEFAULT_CENTER = [30.3753, 69.3451]; // Pakistan -- only used until a real location is known
    const DEFAULT_ZOOM = 5;
    const LOCATED_ZOOM = 16;

    const map = L.map(mapEl);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    let marker = null;

    function showAt(lat, lon) {
        const latlng = [lat, lon];
        if (marker) {
            marker.setLatLng(latlng);
        } else {
            marker = L.marker(latlng).addTo(map);
        }
        map.setView(latlng, LOCATED_ZOOM);
    }

    function readCurrentCoords() {
        const lat = parseFloat(latInput && latInput.value);
        const lon = parseFloat(lonInput && lonInput.value);
        if (!isNaN(lat) && !isNaN(lon)) {
            showAt(lat, lon);
            return true;
        }
        return false;
    }

    if (!readCurrentCoords()) {
        map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
    }

    // geolocation.js dispatches this once the "Use My Current Location"
    // button successfully fills the hidden coordinate fields, so the map
    // can move its pin without the two files needing to know much about
    // each other.
    document.addEventListener("infracracknet:location-updated", function (e) {
        if (e.detail && typeof e.detail.lat === "number" && typeof e.detail.lon === "number") {
            showAt(e.detail.lat, e.detail.lon);
        } else {
            readCurrentCoords();
        }
    });
})();
