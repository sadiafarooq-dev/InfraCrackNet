

(function () {
    var POLL_MS = 25000;
    var TOAST_MS = 7000;
    
    var lastSeenTime = new Date();

    function ensureContainer() {
        var el = document.getElementById('live-toast-container');
        if (!el) {
            el = document.createElement('div');
            el.id = 'live-toast-container';
            el.className = 'toast-container';
            document.body.appendChild(el);
        }
        return el;
    }

    function showToast(item) {
        var container = ensureContainer();
        var toast = document.createElement('a');
        toast.className = 'toast-item';
        toast.href = item.href;
        toast.textContent = item.text;
        container.appendChild(toast);

        
        void toast.offsetWidth;
        toast.classList.add('toast-item-visible');

        setTimeout(function () {
            toast.classList.remove('toast-item-visible');
            setTimeout(function () { toast.remove(); }, 300);
        }, TOAST_MS);
    }

    function poll() {
        fetch('/notifications/latest', { credentials: 'same-origin' })
            .then(function (res) { return res.ok ? res.json() : []; })
            .then(function (items) {
                var newest = lastSeenTime;
                items.forEach(function (item) {
                    var t = new Date(item.time);
                    if (t > lastSeenTime) {
                        showToast(item);
                        if (t > newest) { newest = t; }
                    }
                });
                lastSeenTime = newest;
            })
            .catch(function () {
              
            });
    }

    document.addEventListener('DOMContentLoaded', function () {
        
        var loggedIn = document.querySelector('.navbar-notif-bell') ||
            document.querySelector('.sidebar-nav a[href="/notifications"]');
        if (!loggedIn) { return; }
        setInterval(poll, POLL_MS);
    });
})();
