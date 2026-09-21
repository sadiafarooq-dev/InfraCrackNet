

(function () {
    function setupCameraBox(box) {
        var openBtn = box.querySelector('[data-open-camera]');
        var liveWrap = box.querySelector('[data-camera-live]');
        var video = box.querySelector('[data-camera-video]');
        var shutterBtn = box.querySelector('[data-camera-shutter]');
        var cancelBtn = box.querySelector('[data-camera-cancel]');
        var previewWrap = box.querySelector('[data-camera-preview]');
        var previewImg = box.querySelector('[data-camera-preview-img]');
        var retakeBtn = box.querySelector('[data-camera-retake]');
        var canvas = box.querySelector('[data-camera-canvas]');
        var fileInput = box.querySelector('[data-camera-input]');

        if (!openBtn || !liveWrap || !video || !shutterBtn || !canvas || !fileInput) {
            return; 
        }

        var hasCameraSupport = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
        if (!hasCameraSupport) {
            
            openBtn.hidden = true;
            return;
        }

        var currentStream = null;

        function stopStream() {
            if (currentStream) {
                currentStream.getTracks().forEach(function (track) { track.stop(); });
                currentStream = null;
            }
        }

        function showError(message) {
            var existing = box.querySelector('[data-camera-error]');
            if (existing) { existing.remove(); }
            var el = document.createElement('p');
            el.setAttribute('data-camera-error', '');
            el.className = 'text-danger mt-2 mb-0';
            el.style.fontSize = '0.82rem';
            el.textContent = message;
            openBtn.insertAdjacentElement('afterend', el);
        }

        openBtn.addEventListener('click', function () {
            navigator.mediaDevices
                .getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
                .then(function (stream) {
                    currentStream = stream;
                    video.srcObject = stream;
                    liveWrap.hidden = false;
                    previewWrap.hidden = true;
                    openBtn.hidden = true;
                })
                .catch(function () {
                    showError('Could not open the camera (permission denied, or no camera found). You can still choose a photo below.');
                });
        });

        shutterBtn.addEventListener('click', function () {
            var w = video.videoWidth;
            var h = video.videoHeight;
            if (!w || !h) { return; }
            canvas.width = w;
            canvas.height = h;
            var ctx = canvas.getContext('2d');
            ctx.drawImage(video, 0, 0, w, h);

            canvas.toBlob(function (blob) {
                if (!blob) { return; }
                var file = new File([blob], 'capture-' + Date.now() + '.jpg', { type: 'image/jpeg' });
                var dataTransfer = new DataTransfer();
                dataTransfer.items.add(file);
                fileInput.files = dataTransfer.files;

                if (previewImg) {
                    previewImg.src = URL.createObjectURL(blob);
                }
                previewWrap.hidden = false;
                liveWrap.hidden = true;
                stopStream();
            }, 'image/jpeg', 0.92);
        });

        if (cancelBtn) {
            cancelBtn.addEventListener('click', function () {
                stopStream();
                liveWrap.hidden = true;
                openBtn.hidden = false;
            });
        }

        if (retakeBtn) {
            retakeBtn.addEventListener('click', function () {
                fileInput.value = '';
                previewWrap.hidden = true;
                openBtn.hidden = false;
            });
        }

        window.addEventListener('beforeunload', stopStream);
    }

    document.addEventListener('DOMContentLoaded', function () {
        var boxes = document.querySelectorAll('[data-camera-capture]');
        boxes.forEach(setupCameraBox);
    });
})();
