

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
                // Setting .files programmatically (above) does not fire a
                // native 'change' event on its own -- dispatch one so the
                // upload-feedback logic below (the file-chip / video-options
                // reveal, section 80) reacts to a live-camera capture the
                // same way it reacts to a gallery pick.
                fileInput.dispatchEvent(new Event('change'));
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
                fileInput.dispatchEvent(new Event('change'));
            });
        }

        window.addEventListener('beforeunload', stopStream);
    }

    // -- Upload-step feedback (section 80): the "you picked a file" chip and
    // the video-only options reveal. Lives here (not a separate file) since
    // this is the one page it applies to and it needs to react to the exact
    // same file input the camera capture code above already manages. Every
    // element it looks for is guarded with a null-check, so this quietly
    // does nothing on any page that doesn't have this markup.
    function setupUploadFeedback() {
        var fileInput = document.querySelector('[data-file-input]');
        var chip = document.querySelector('[data-file-chip]');
        if (!fileInput || !chip) {
            return;
        }

        var chipName = chip.querySelector('[data-file-chip-name]');
        var chipMeta = chip.querySelector('[data-file-chip-meta]');
        var chipChange = chip.querySelector('[data-file-chip-change]');
        var videoOptions = document.querySelector('[data-video-options]');
        var floorField = document.querySelector('[data-floor-field]');
        var videoHint = document.querySelector('[data-video-hint]');
        var previewWrap = document.querySelector('[data-camera-preview]');
        var roadRadio = document.querySelector('#video-surface-road');
        var buildingRadio = document.querySelector('#video-surface-building');

        var HINTS = {
            Road: 'We’ll scan the footage frame-by-frame for road damage (potholes, alligator cracking, etc) and box the clearest frame.',
            Building: 'We’ll estimate which floor/height each part was filmed at — enter the floor count below.'
        };

        function formatSize(bytes) {
            if (bytes < 1024 * 1024) {
                return Math.max(1, Math.round(bytes / 1024)) + ' KB';
            }
            return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        }

        function isVideoFile(file) {
            if (file.type) {
                return file.type.indexOf('video/') === 0;
            }
            return /\.(mp4|mov|avi|mkv|webm)$/i.test(file.name);
        }

        function updateFloorVisibility() {
            if (!floorField) {
                return;
            }
            var isBuilding = !!(buildingRadio && buildingRadio.checked);
            floorField.hidden = !isBuilding;
            if (videoHint) {
                videoHint.textContent = isBuilding ? HINTS.Building : HINTS.Road;
            }
        }

        function refresh() {
            var file = fileInput.files && fileInput.files[0];

            if (!file) {
                chip.hidden = true;
                if (videoOptions) {
                    videoOptions.hidden = true;
                }
                return;
            }

            var video = isVideoFile(file);
            if (videoOptions) {
                videoOptions.hidden = !video;
                if (video) {
                    updateFloorVisibility();
                }
            }

            // The live-camera preview above already shows the captured photo
            // with its own Retake button -- skip the text chip in that one
            // case so there are never two "you picked something" indicators
            // on screen together. A gallery pick (photo or video) has no
            // such preview, so the chip is the only indicator there.
            var cameraPreviewShown = !!(previewWrap && !previewWrap.hidden);
            if (cameraPreviewShown) {
                chip.hidden = true;
                return;
            }

            chip.hidden = false;
            if (chipName) {
                chipName.textContent = file.name;
            }
            if (chipMeta) {
                chipMeta.textContent = (video ? 'Video' : 'Photo') + ' · ' + formatSize(file.size);
            }
        }

        fileInput.addEventListener('change', refresh);
        if (roadRadio) {
            roadRadio.addEventListener('change', updateFloorVisibility);
        }
        if (buildingRadio) {
            buildingRadio.addEventListener('change', updateFloorVisibility);
        }
        if (chipChange) {
            chipChange.addEventListener('click', function () {
                fileInput.value = '';
                refresh();
            });
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        var boxes = document.querySelectorAll('[data-camera-capture]');
        boxes.forEach(setupCameraBox);
        setupUploadFeedback();
    });
})();
