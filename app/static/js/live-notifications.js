// Live notifications -- the banner, the chime, the unread badge and the
// desktop alert, all updated the instant something new happens for the
// signed-in user (for example an Engineer requests a follow-up photo, or an
// Inspector submits one).
//
// This is one of the approved exceptions to the site's "no JavaScript" rule:
// a page cannot notice by itself that something changed on the server while
// it sits open. It only reads the same notifications the Notifications page
// already shows; it never changes anything.
//
// How it works, in plain words:
//  - The browser keeps a "live line" open to the server
//    (/notifications/stream). The server pushes a message the moment the
//    list changes, so everything below happens within about a second.
//    If the live line cannot be used, it quietly falls back to asking
//    "anything new?" every 10 seconds.
//  - With several tabs open, only ONE tab holds the live line and shares
//    what it hears with the others, so the browser's connection limit is
//    never used up. If that tab is closed, another one takes over.
//  - Every page also asks once when it opens, so the badge is right at once
//    and anything unopened from the last 10 minutes shows as a banner.
//  - The orange badge counts notifications not yet opened on the
//    Notifications page, and clears when that page is opened.
//  - Banner: click it to open the Notifications page, or press x to close it.
//    It stays until you do one of those.
//  - Sound: a soft two-note chime made by the browser itself (no sound
//    file). Browsers keep a page silent until the person has clicked or
//    typed on it once; if the chime was blocked, it plays on your next click
//    or key press. The "Turn on sound and desktop alerts" button does that
//    in one click and also asks the browser for permission to show a normal
//    desktop notification (with your computer's own notification sound), so
//    you are alerted even while Chrome is in the background.
//  - What was already shown is remembered per signed-in user, so moving
//    between pages never repeats a notification.

(function () {
    var NOTIF_PAGE = '/notifications';
    var POLL_MS = 10000;
    var CATCH_UP_MS = 10 * 60 * 1000;     // unopened items from the last 10 minutes
    var CHIME_WAIT_MS = 60 * 1000;        // a blocked chime still plays if you click within this
    var LOCK_NAME = 'icn-notif-stream';
    var CHANNEL_NAME = 'icn-notif';

    var originalTitle = document.title;
    var memory = {};                     // used if the browser will not let us store
    var banner = null;
    var pendingCount = 0;
    var audioCtx = null;
    var chimePendingAt = 0;
    var latest = null;                   // most recent picture from the server
    var channel = null;
    var pollTimer = null;
    var myUid = null;

    // ---------- small remembered numbers (per signed-in user) ----------
    function keyFor(name, uid) { return 'icn_notif_' + name + '_' + uid; }

    function readNum(name, uid) {
        var k = keyFor(name, uid);
        var raw = null;
        try { raw = window.localStorage.getItem(k); } catch (e) { raw = null; }
        if (raw === null && memory[k] !== undefined) { raw = memory[k]; }
        var n = parseInt(raw, 10);
        return isNaN(n) ? 0 : n;
    }

    function writeMax(name, uid, value) {
        var k = keyFor(name, uid);
        var v = Math.max(readNum(name, uid), value);
        memory[k] = String(v);
        try { window.localStorage.setItem(k, String(v)); } catch (e) { /* fine */ }
    }

    // ---------- sound ----------
    function unlockAudio() {
        try {
            var AC = window.AudioContext || window.webkitAudioContext;
            if (!AC) { return Promise.resolve(); }
            if (!audioCtx) { audioCtx = new AC(); }
            if (audioCtx.state === 'suspended') {
                return audioCtx.resume().catch(function () { /* fine */ });
            }
        } catch (e) { /* no sound is fine */ }
        return Promise.resolve();
    }

    function audioReady() { return !!audioCtx && audioCtx.state === 'running'; }

    function playChime() {
        try {
            var now = audioCtx.currentTime;
            [[880, 0], [1318.5, 0.16]].forEach(function (note) {
                var osc = audioCtx.createOscillator();
                var gain = audioCtx.createGain();
                osc.type = 'sine';
                osc.frequency.value = note[0];
                var start = now + note[1];
                gain.gain.setValueAtTime(0.0001, start);
                gain.gain.exponentialRampToValueAtTime(0.2, start + 0.02);
                gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.5);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start(start);
                osc.stop(start + 0.55);
            });
        } catch (e) { /* no sound is fine */ }
    }

    // Plays now if the browser allows it. If not and wait is true, it plays on
    // the person's next click or key press (within a minute).
    function chime(wait) {
        if (!audioCtx) { unlockAudio(); }
        if (audioReady()) { playChime(); chimePendingAt = 0; return true; }
        if (wait) { chimePendingAt = Date.now(); }
        return false;
    }

    function onGesture() {
        unlockAudio().then(function () {
            if (chimePendingAt && Date.now() - chimePendingAt < CHIME_WAIT_MS && audioReady()) {
                chimePendingAt = 0;
                playChime();
            }
        });
    }

    // ---------- desktop alerts (the browser's own notification) ----------
    function canAsk() { return 'Notification' in window && Notification.permission === 'default'; }
    function alertsOn() { return 'Notification' in window && Notification.permission === 'granted'; }

    function turnOnAlerts() {
        // This runs from a click, which is what lets the browser allow sound.
        unlockAudio().then(function () { if (audioReady()) { playChime(); } });
        if (canAsk()) {
            try {
                var res = Notification.requestPermission(function () { refreshEnableUi(); });
                if (res && res.then) { res.then(refreshEnableUi); }
            } catch (e) { /* fine */ }
        }
        refreshEnableUi();
    }

    function desktopAlert(item, extra) {
        try {
            var n = new Notification(extra > 0 ? (extra + 1) + ' new notifications' : 'InfraCrackNet', {
                body: item.text,
                tag: 'icn-' + item.time,
                icon: '/static/img/apple-touch-icon.png',
                silent: false
            });
            n.onclick = function () {
                try { window.focus(); } catch (e) { /* fine */ }
                window.location.href = item.href || NOTIF_PAGE;
                n.close();
            };
        } catch (e) { /* some browsers block this: fine */ }
    }

    // ---------- the enable button (on the banner and the Notifications page) ----------
    function refreshEnableUi() {
        var show = canAsk();
        var bannerBtn = banner && banner.querySelector('.notif-banner-enable');
        if (bannerBtn) { bannerBtn.hidden = !show; }

        var box = document.querySelector('[data-notif-enable]');
        if (box) {
            var status = box.querySelector('[data-notif-enable-status]');
            var btn = box.querySelector('[data-notif-enable-btn]');
            if (!('Notification' in window)) {
                status.textContent = 'This browser cannot show desktop alerts, but you will still see the banner and hear the chime.';
                btn.hidden = true;
            } else if (Notification.permission === 'granted') {
                status.textContent = 'On. You will get a desktop notification, with your computer\'s own sound, even when this window is in the background.';
                btn.hidden = true;
            } else if (Notification.permission === 'denied') {
                status.textContent = 'Blocked in this browser. To turn them on, click the padlock beside the address bar and set Notifications to Allow. The banner and chime still work.';
                btn.hidden = true;
            } else {
                status.textContent = 'Off. Turn on to hear a sound and get a desktop notification the moment something new happens, even when this window is in the background.';
                btn.hidden = false;
            }
            box.hidden = false;
        }
    }

    // ---------- the badge ----------
    function setBadge(count) {
        var hosts = [];
        var side = document.querySelector('.sidebar-nav a[href="/notifications"]');
        if (side) { hosts.push(side); }
        var bell = document.querySelector('.navbar-notif-bell');
        if (bell && bell.parentNode) { hosts.push(bell.parentNode); }
        hosts.forEach(function (host) {
            var badge = host.querySelector('.notif-badge');
            if (!count) {
                if (badge && badge.parentNode) { badge.parentNode.removeChild(badge); }
                host.classList.remove('has-notif-badge');
                return;
            }
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'notif-badge';
                badge.setAttribute('role', 'status');
                host.appendChild(badge);
            }
            host.classList.add('has-notif-badge');
            badge.textContent = count > 9 ? '9+' : String(count);
            badge.setAttribute('aria-label', count + ' unread notification' + (count === 1 ? '' : 's'));
        });
        document.title = count ? '(' + (count > 9 ? '9+' : count) + ') ' + originalTitle : originalTitle;
    }

    // ---------- the banner ----------
    function closeBanner() {
        if (!banner) { return; }
        var el = banner;
        banner = null;
        pendingCount = 0;
        el.classList.remove('notif-banner-visible');
        setTimeout(function () { if (el.parentNode) { el.parentNode.removeChild(el); } }, 300);
    }

    function buildBanner() {
        var el = document.createElement('div');
        el.className = 'notif-banner';
        el.setAttribute('role', 'status');
        el.setAttribute('aria-live', 'polite');

        var row = document.createElement('div');
        row.className = 'notif-banner-row';

        var link = document.createElement('a');
        link.className = 'notif-banner-link';
        link.href = NOTIF_PAGE;

        var icon = document.createElement('span');
        icon.className = 'notif-banner-icon';
        icon.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>';

        var text = document.createElement('span');
        text.className = 'notif-banner-text';
        var title = document.createElement('strong');
        title.className = 'notif-banner-title';
        var detail = document.createElement('span');
        detail.className = 'notif-banner-detail';
        text.appendChild(title);
        text.appendChild(detail);

        var open = document.createElement('span');
        open.className = 'notif-banner-open';
        open.textContent = 'Open';

        link.appendChild(icon);
        link.appendChild(text);
        link.appendChild(open);

        var close = document.createElement('button');
        close.type = 'button';
        close.className = 'notif-banner-close';
        close.setAttribute('aria-label', 'Close notification');
        close.innerHTML = '&times;';
        close.addEventListener('click', closeBanner);

        row.appendChild(link);
        row.appendChild(close);

        var enable = document.createElement('button');
        enable.type = 'button';
        enable.className = 'notif-banner-enable';
        enable.textContent = 'Turn on sound and desktop alerts';
        enable.hidden = true;
        enable.addEventListener('click', turnOnAlerts);

        el.appendChild(row);
        el.appendChild(enable);
        document.body.appendChild(el);
        void el.offsetWidth;            // let the slide-in play
        el.classList.add('notif-banner-visible');
        return el;
    }

    function showBanner(count, newestText) {
        if (!banner) { banner = buildBanner(); pendingCount = 0; }
        pendingCount += count;
        banner.querySelector('.notif-banner-title').textContent =
            pendingCount === 1 ? 'New notification' : pendingCount + ' new notifications';
        banner.querySelector('.notif-banner-detail').textContent = newestText;
        refreshEnableUi();
        chime(true);
    }

    // ---------- reacting to what the server says ----------
    function newerThan(items, threshold) {
        return (items || []).filter(function (item) {
            var t = Date.parse(item.time);
            return !isNaN(t) && t > threshold;
        }).sort(function (a, b) { return Date.parse(b.time) - Date.parse(a.time); });
    }

    function handle(data) {
        if (!data || !data.now || data.user_id == null) { return; }
        if (myUid !== null && String(data.user_id) !== String(myUid)) { return; }   // another person's list
        var uid = data.user_id;
        var serverNow = Date.parse(data.now);
        if (isNaN(serverNow)) { return; }
        latest = data;

        setBadge(data.unseen || 0);

        var seenAt = data.seen_at ? Date.parse(data.seen_at) : 0;
        if (isNaN(seenAt)) { seenAt = 0; }
        var floor = Math.max(seenAt, serverNow - CATCH_UP_MS);

        // 1) In front of the person: banner + chime. Anything not yet shown
        //    (including what arrived while the tab was hidden) shows now.
        if (!document.hidden) {
            var fresh = newerThan(data.items, Math.max(floor, readNum('shown', uid)));
            if (fresh.length) {
                writeMax('shown', uid, Date.parse(fresh[0].time));
                showBanner(fresh.length, fresh[0].text);
            }
        }

        // 2) Window in the background (or hidden tab): desktop notification,
        //    or at least a chime if the browser allows one.
        if (document.hidden || !document.hasFocus()) {
            var away = newerThan(data.items, Math.max(floor, readNum('away', uid)));
            if (away.length) {
                writeMax('away', uid, Date.parse(away[0].time));
                if (alertsOn()) {
                    desktopAlert(away[0], away.length - 1);
                } else if (document.hidden) {
                    chime(false);
                }
            }
        }
    }

    // ---------- getting the picture from the server ----------
    function getLatest() {
        return fetch(NOTIF_PAGE + '/latest', { credentials: 'same-origin', cache: 'no-store' })
            .then(function (res) { return res.ok ? res.json() : null; });
    }

    // The tab holding the live line hears first, then tells the other tabs.
    function publish(data) {
        handle(data);
        if (channel) { try { channel.postMessage(data); } catch (e) { /* fine */ } }
    }

    function startPolling() {
        if (pollTimer) { return; }
        pollTimer = setInterval(function () {
            getLatest()
                .then(function (data) { if (data) { publish(data); } })
                .catch(function () { /* server restarting: try again next time */ });
        }, POLL_MS);
    }

    function openLiveLine() {
        if (!('EventSource' in window)) { startPolling(); return; }
        var source = new EventSource(NOTIF_PAGE + '/stream');
        source.onmessage = function (e) {
            var data = null;
            try { data = JSON.parse(e.data); } catch (err) { data = null; }
            if (data) { publish(data); }
        };
        source.onerror = function () {
            // The browser reconnects by itself after the planned 20-second
            // close. Only if it gives up for good (for example signed out) do
            // we fall back to checking every 10 seconds.
            if (source.readyState === 2) { startPolling(); }
        };
    }

    function becomeLeader() {
        if (navigator.locks && typeof navigator.locks.request === 'function' && channel) {
            // Waits its turn; runs only while no other tab holds the live line.
            navigator.locks.request(LOCK_NAME, function () {
                openLiveLine();
                return new Promise(function () { /* hold it until this tab closes */ });
            });
        } else {
            openLiveLine();
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        var loggedIn = document.querySelector('.navbar-notif-bell') ||
            document.querySelector('.sidebar-nav a[href="/notifications"]');
        if (!loggedIn) { return; }

        var meta = document.querySelector('meta[name="icn-uid"]');
        if (meta && meta.content) { myUid = meta.content; }

        ['click', 'keydown', 'touchstart'].forEach(function (evt) {
            document.addEventListener(evt, onGesture, { passive: true });
        });

        var enableBtn = document.querySelector('[data-notif-enable-btn]');
        if (enableBtn) { enableBtn.addEventListener('click', turnOnAlerts); }
        refreshEnableUi();

        // Coming back to this tab or window: show anything that arrived meanwhile.
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden && latest) { handle(latest); }
        });
        window.addEventListener('focus', function () {
            if (latest) { handle(latest); }
        });

        if ('BroadcastChannel' in window) {
            try {
                channel = new BroadcastChannel(CHANNEL_NAME);
                channel.onmessage = function (e) { handle(e.data); };
            } catch (e) { channel = null; }
        }

        // The badge and any recent unopened items, right away.
        getLatest().then(function (data) { if (data) { handle(data); } }).catch(function () { /* fine */ });
        becomeLeader();
    });
})();
