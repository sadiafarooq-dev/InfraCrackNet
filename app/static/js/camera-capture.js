

(function () {
    // ---- Shared helpers for choosing one video or several photos (Upload step) ----
    function isVideoFile(file) {
        if (file.type) {
            return file.type.indexOf('video/') === 0;
        }
        return /\.(mp4|mov|avi|mkv|webm)$/i.test(file.name);
    }

    // The file input itself is the one source of truth for what is chosen.
    // Setting .files by code does not fire a 'change' event, so callers
    // dispatch one afterwards.
    function setInputFiles(input, files) {
        var dt = new DataTransfer();
        files.forEach(function (f) { dt.items.add(f); });
        input.files = dt.files;
    }

    // Applies the rules: a video goes on its own; photos only otherwise, and
    // never more than max. Returns { files, note } (note = what was dropped).
    function applyPickRules(files, max) {
        var note = '';
        var photos = files.filter(function (f) { return !isVideoFile(f); });
        var videos = files.filter(isVideoFile);
        if (files.length > 1 && videos.length) {
            note = 'A video has to be uploaded on its own, so it was skipped.';
            files = photos.length ? photos : [videos[0]];
        }
        if (files.length > 1 && files.length > max) {
            files = files.slice(0, max);
            note = 'You can add up to ' + max + ' photos to one report. The rest were left out.';
        }
        return { files: files, note: note };
    }

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
                if (fileInput.multiple) {
                    // Upload step (several photos allowed): a captured photo is
                    // ADDED to the photos already chosen (a video, if one was
                    // chosen, is replaced by the new photo). The picture list
                    // below the box shows it, so no big preview here.
                    var current = Array.prototype.slice.call(fileInput.files || []).filter(function (f) { return !isVideoFile(f); });
                    current.push(file);
                    var max = parseInt(fileInput.getAttribute('data-max-photos'), 10) || 6;
                    var ruled = applyPickRules(current, max);
                    setInputFiles(fileInput, ruled.files);
                    liveWrap.hidden = true;
                    openBtn.hidden = false;
                    openBtn.textContent = 'Take another photo';
                } else {
                    // A single-photo box (for example the follow-up photo
                    // page): the capture REPLACES any earlier one, and shows
                    // a preview with a Retake button, as it always did.
                    setInputFiles(fileInput, [file]);
                    if (previewImg) {
                        previewImg.src = URL.createObjectURL(blob);
                    }
                    previewWrap.hidden = false;
                    liveWrap.hidden = true;
                }
                stopStream();
                // Setting .files by code does not fire a native 'change'
                // event, so dispatch one: the photo list below reacts to a
                // live-camera capture the same way it reacts to a gallery pick.
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
        var roadRadio = document.querySelector('#video-surface-road');
        var buildingRadio = document.querySelector('#video-surface-building');
        var photoList = document.querySelector('[data-photo-list]');
        var pickNote = document.querySelector('[data-pick-note]');
        var addInput = document.querySelector('[data-add-input]');
        var openCameraBtn = document.querySelector('[data-open-camera]');
        var maxPhotos = parseInt(fileInput.getAttribute('data-max-photos'), 10) || 6;
        var thumbUrls = [];

        var HINTS = {
            Road: 'We’ll scan the footage frame-by-frame for road damage (potholes, alligator cracking, etc) and box the clearest frame.',
            Building: 'We’ll estimate which floor/height each part was filmed at. Enter the floor count below.'
        };

        function formatSize(bytes) {
            if (bytes < 1024 * 1024) {
                return Math.max(1, Math.round(bytes / 1024)) + ' KB';
            }
            return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        }

        function showNote(text) {
            if (!pickNote) { return; }
            pickNote.textContent = text || '';
            pickNote.hidden = !text;
        }

        function currentFiles() {
            return Array.prototype.slice.call(fileInput.files || []);
        }

        // Puts a new list of files into the input and refreshes the screen.
        function setFiles(files, note) {
            var ruled = applyPickRules(files, maxPhotos);
            setInputFiles(fileInput, ruled.files);
            showNote(ruled.note || note || '');
            refresh();
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

        function clearThumbs() {
            thumbUrls.forEach(function (u) { URL.revokeObjectURL(u); });
            thumbUrls = [];
            if (photoList) { photoList.innerHTML = ''; }
        }

        function buildPhotoList(files) {
            clearThumbs();
            if (!photoList) { return; }
            files.forEach(function (file, index) {
                var url = URL.createObjectURL(file);
                thumbUrls.push(url);
                var tile = document.createElement('div');
                tile.className = 'photo-pick';
                var img = document.createElement('img');
                img.src = url;
                img.alt = 'Photo ' + (index + 1);
                var tag = document.createElement('span');
                tag.className = 'photo-pick-num';
                tag.textContent = index === 0 ? 'Main' : String(index + 1);
                var remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'photo-pick-remove';
                remove.setAttribute('aria-label', 'Remove photo ' + (index + 1));
                remove.innerHTML = '&times;';
                remove.addEventListener('click', function () {
                    var rest = currentFiles();
                    rest.splice(index, 1);
                    setFiles(rest);
                });
                tile.appendChild(img);
                tile.appendChild(tag);
                tile.appendChild(remove);
                photoList.appendChild(tile);
            });
            if (files.length < maxPhotos && addInput) {
                var add = document.createElement('button');
                add.type = 'button';
                add.className = 'photo-pick-add';
                add.innerHTML = '<span aria-hidden="true">+</span>Add more';
                add.addEventListener('click', function () { addInput.click(); });
                photoList.appendChild(add);
            }
            photoList.hidden = false;
        }

        function refresh() {
            var files = currentFiles();

            if (!files.length) {
                chip.hidden = true;
                clearThumbs();
                if (photoList) { photoList.hidden = true; }
                if (videoOptions) {
                    videoOptions.hidden = true;
                }
                if (openCameraBtn) { openCameraBtn.textContent = 'Open Live Camera'; }
                return;
            }

            var video = isVideoFile(files[0]);
            if (videoOptions) {
                videoOptions.hidden = !video;
                if (video) {
                    updateFloorVisibility();
                }
            }

            // Photos show as small pictures (each can be removed); a video has
            // no picture, so it just shows in the text chip below.
            if (video) {
                clearThumbs();
                if (photoList) { photoList.hidden = true; }
            } else {
                buildPhotoList(files);
            }

            var total = files.reduce(function (sum, f) { return sum + f.size; }, 0);
            chip.hidden = false;
            if (chipName) {
                chipName.textContent = (files.length === 1)
                    ? files[0].name
                    : files.length + ' photos selected';
            }
            if (chipMeta) {
                chipMeta.textContent = (video ? 'Video' : (files.length === 1 ? 'Photo' : 'Photos of the same crack'))
                    + ' · ' + formatSize(total);
            }
        }

        // The Inspector chose files in the normal file window (replaces the
        // choice) -- or code above dispatched 'change' after a camera capture.
        fileInput.addEventListener('change', function () {
            setFiles(currentFiles());
        });
        if (addInput) {
            addInput.addEventListener('change', function () {
                var added = Array.prototype.slice.call(addInput.files || []);
                addInput.value = '';
                if (!added.length) { return; }
                // Adding to a video replaces it with the new photos.
                var base = currentFiles().filter(function (f) { return !isVideoFile(f); });
                setFiles(base.concat(added));
            });
        }
        if (roadRadio) {
            roadRadio.addEventListener('change', updateFloorVisibility);
        }
        if (buildingRadio) {
            buildingRadio.addEventListener('change', updateFloorVisibility);
        }
        if (chipChange) {
            chipChange.addEventListener('click', function () {
                fileInput.value = '';
                showNote('');
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
