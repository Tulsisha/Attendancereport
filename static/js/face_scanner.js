/**
 * Enterprise Face Scanner & Biometric Attendance Engine
 * Powered by face-api.js with TinyFaceDetector, FaceLandmark68Net, and FaceRecognitionNet.
 * Includes:
 * - Anti-Spoofing & Liveness Verification (Eye-Blink EAR + Landmark Micro-Movement)
 * - Multi-Face Rejection (strictly enforces single face in camera frame)
 * - Cryptographic Replay-Protection (One-Time Server Challenge Token)
 * - Multi-Sample Enrollment (Captures 3-5 biometric vectors for centroid calculation)
 * - Cross-Device Camera & Permission Error Diagnostics (Desktop, Android, iOS)
 */

class FaceScanner {
    constructor(options = {}) {
        this.videoEl = document.getElementById(options.videoId || 'faceScannerVideo');
        this.canvasEl = document.getElementById(options.canvasId || 'faceScannerCanvas');
        this.statusTextEl = document.getElementById(options.statusTextId || 'scannerStatusText');
        this.statusIndicatorEl = document.getElementById(options.statusIndicatorId || 'scannerIndicator');
        this.modelsPath = options.modelsPath || '/static/models';
        this.csrfToken = options.csrfToken || this.getCSRFToken();
        this.mode = options.mode || 'punch'; // 'punch', 'punch_in', 'punch_out', 'enroll'
        this.targetUserId = options.targetUserId || null;

        this.stream = null;
        this.isModelLoaded = false;
        this.isScanning = false;
        this.animFrameId = null;

        // Challenge Token for Anti-Replay
        this.challengeId = null;

        // Liveness & Anti-Spoofing State
        this.blinkCount = 0;
        this.isEyeClosed = false;
        this.movementSamples = [];
        this.livenessPassed = false;
        this.scanStartTime = null;

        // Multi-sample enrollment buffer
        this.enrollmentSamples = [];
        this.requiredSamples = options.requiredSamples || 4;

        // Callbacks
        this.onVerified = options.onVerified || null;
        this.onError = options.onError || null;
    }

    getCSRFToken() {
        const csrfInput = document.querySelector('[name=csrfmiddlewaretoken]');
        if (csrfInput) return csrfInput.value;
        const cookie = document.cookie.split('; ').find(row => row.startsWith('csrftoken='));
        return cookie ? cookie.split('=')[1] : '';
    }

    setStatus(message, type = 'info') {
        if (this.statusTextEl) {
            this.statusTextEl.textContent = message;
        }
        if (this.statusIndicatorEl) {
            this.statusIndicatorEl.className = `scanner-badge badge-${type}`;
        }
    }

    /**
     * Request a one-time cryptographic challenge token from the Django backend
     * to prevent replay attacks and token reuse.
     */
    async fetchChallenge() {
        try {
            const resp = await fetch(`/api/face/challenge/?action=${this.mode}`, {
                headers: { 'Accept': 'application/json' }
            });
            if (resp.ok) {
                const data = await resp.json();
                this.challengeId = data.challenge_id;
                console.log("Anti-replay challenge token acquired:", this.challengeId);
                return this.challengeId;
            }
        } catch (e) {
            console.warn("Could not acquire challenge token:", e);
        }
        return null;
    }

    async loadModels() {
        if (this.isModelLoaded) return true;
        this.setStatus('Initializing AI Models...', 'warning');

        try {
            if (typeof faceapi === 'undefined') {
                throw new Error('face-api library not loaded. Check script include in template.');
            }

            // Load tiny face detector, landmark 68, and face recognition net
            await Promise.all([
                faceapi.nets.tinyFaceDetector.loadFromUri(this.modelsPath),
                faceapi.nets.faceLandmark68Net.loadFromUri(this.modelsPath),
                faceapi.nets.faceRecognitionNet.loadFromUri(this.modelsPath)
            ]);

            this.isModelLoaded = true;
            this.setStatus('Camera Ready', 'info');
            return true;
        } catch (error) {
            console.error('Failed to load face-api models:', error);
            this.setStatus('Verification Error: Biometric models failed to load.', 'danger');
            if (this.onError) this.onError(error);
            return false;
        }
    }

    async startCamera() {
        try {
            this.setStatus('Initializing Camera...', 'warning');

            // Check for Secure Context
            if (!window.isSecureContext && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
                throw new Error('Camera access requires HTTPS or localhost. Please deploy over a secure connection.');
            }

            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                throw new Error('Camera API is not supported on this browser or platform.');
            }

            // Ideal mobile & desktop settings with user-facing camera preference
            const constraints = {
                video: {
                    facingMode: 'user',
                    width: { ideal: 640 },
                    height: { ideal: 480 }
                },
                audio: false
            };

            this.stream = await navigator.mediaDevices.getUserMedia(constraints);
            this.videoEl.srcObject = this.stream;

            await new Promise((resolve) => {
                this.videoEl.onloadedmetadata = () => {
                    this.videoEl.play();
                    resolve();
                };
            });

            if (this.canvasEl) {
                this.canvasEl.width = this.videoEl.videoWidth || 640;
                this.canvasEl.height = this.videoEl.videoHeight || 480;
            }

            this.setStatus('Position Your Face', 'info');
            return true;
        } catch (error) {
            console.error('Camera initialization error:', error);
            if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
                this.setStatus('Camera Permission Denied. Please grant camera permission in your browser.', 'danger');
            } else if (error.name === 'NotFoundError' || error.name === 'DevicesNotFoundError') {
                this.setStatus('No camera detected on this device.', 'danger');
            } else if (error.name === 'NotReadableError' || error.name === 'TrackStartError') {
                this.setStatus('Camera is already in use by another application.', 'danger');
            } else {
                this.setStatus(error.message || 'Camera Unavailable.', 'danger');
            }
            if (this.onError) this.onError(error);
            return false;
        }
    }

    stopCamera() {
        this.isScanning = false;
        if (this.animFrameId) {
            cancelAnimationFrame(this.animFrameId);
            this.animFrameId = null;
        }
        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
        }
        if (this.videoEl) {
            this.videoEl.srcObject = null;
        }
        const ctx = this.canvasEl ? this.canvasEl.getContext('2d') : null;
        if (ctx) {
            ctx.clearRect(0, 0, this.canvasEl.width, this.canvasEl.height);
        }
    }

    // Eye Aspect Ratio (EAR) for blink detection
    computeEAR(eye) {
        // Points: 0 to 5
        const dist = (p1, p2) => Math.hypot(p1.x - p2.x, p1.y - p2.y);
        const a = dist(eye[1], eye[5]);
        const b = dist(eye[2], eye[4]);
        const c = dist(eye[0], eye[3]);
        if (c === 0) return 0.3;
        return (a + b) / (2.0 * c);
    }

    async startScanning() {
        const modelsOk = await this.loadModels();
        if (!modelsOk) return;

        const cameraOk = await this.startCamera();
        if (!cameraOk) return;

        // Fetch fresh anti-replay challenge
        await this.fetchChallenge();

        this.isScanning = true;
        this.blinkCount = 0;
        this.isEyeClosed = false;
        this.movementSamples = [];
        this.livenessPassed = false;
        this.enrollmentSamples = [];
        this.scanStartTime = Date.now();

        this.processFrame();
    }

    async processFrame() {
        if (!this.isScanning || !this.videoEl || this.videoEl.paused || this.videoEl.ended) {
            return;
        }

        const displaySize = {
            width: this.videoEl.videoWidth || 640,
            height: this.videoEl.videoHeight || 480
        };
        faceapi.matchDimensions(this.canvasEl, displaySize);

        try {
            // STEP 1: Detect ALL faces in frame to enforce strictly 1 person
            const allDetections = await faceapi
                .detectAllFaces(this.videoEl, new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.5 }))
                .withFaceLandmarks()
                .withFaceDescriptors();

            const ctx = this.canvasEl.getContext('2d');
            ctx.clearRect(0, 0, this.canvasEl.width, this.canvasEl.height);

            // CASE A: Multiple faces detected -> REJECT
            if (allDetections.length > 1) {
                this.setStatus('⚠️ Multiple Faces Detected! Only one person must be in view.', 'danger');
                for (let d of allDetections) {
                    const box = d.detection.box;
                    ctx.strokeStyle = '#ef4444';
                    ctx.lineWidth = 3;
                    ctx.strokeRect(box.x, box.y, box.width, box.height);
                }
                this.animFrameId = requestAnimationFrame(() => this.processFrame());
                return;
            }

            // CASE B: Zero faces detected
            if (allDetections.length === 0) {
                this.setStatus('Position Your Face Inside the Frame', 'info');
                this.animFrameId = requestAnimationFrame(() => this.processFrame());
                return;
            }

            // CASE C: Exactly 1 face detected -> Proceed with verification
            const detection = allDetections[0];
            const resizedDetection = faceapi.resizeResults(detection, displaySize);
            const box = resizedDetection.detection.box;
            const landmarks = resizedDetection.landmarks;

            // Draw bounding reticle
            ctx.strokeStyle = this.livenessPassed ? '#10b981' : '#3b82f6';
            ctx.lineWidth = 3;
            ctx.strokeRect(box.x, box.y, box.width, box.height);

            // Draw facial landmark points
            const points = landmarks.positions;
            ctx.fillStyle = this.livenessPassed ? '#34d399' : '#60a5fa';
            for (let i = 0; i < points.length; i += 2) {
                ctx.beginPath();
                ctx.arc(points[i].x, points[i].y, 2, 0, 2 * Math.PI);
                ctx.fill();
            }

            // -------------------------------------------------------------
            // LIVENESS & ANTI-SPOOFING VERIFICATION
            // -------------------------------------------------------------
            const leftEye = landmarks.getLeftEye();
            const rightEye = landmarks.getRightEye();
            const earLeft = this.computeEAR(leftEye);
            const earRight = this.computeEAR(rightEye);
            const avgEAR = (earLeft + earRight) / 2.0;

            if (avgEAR < 0.22) {
                this.isEyeClosed = true;
            } else if (this.isEyeClosed && avgEAR > 0.26) {
                this.blinkCount++;
                this.isEyeClosed = false;
            }

            // Micro-movement tracking (nose tip)
            const noseTip = landmarks.getNose()[3];
            this.movementSamples.push({ x: noseTip.x, y: noseTip.y, time: Date.now() });
            if (this.movementSamples.length > 25) {
                this.movementSamples.shift();
            }

            let maxDist = 0;
            if (this.movementSamples.length >= 10) {
                const first = this.movementSamples[0];
                for (let s of this.movementSamples) {
                    const d = Math.hypot(s.x - first.x, s.y - first.y);
                    if (d > maxDist) maxDist = d;
                }
            }

            const elapsedSec = (Date.now() - this.scanStartTime) / 1000.0;

            if (!this.livenessPassed) {
                this.setStatus('Face Detected. Please blink or move slightly for liveness check...', 'warning');

                // Pass liveness if blink detected OR natural micro-movements detected over 1.2s
                if (this.blinkCount >= 1 || (elapsedSec > 1.2 && maxDist >= 2.0 && maxDist <= 80)) {
                    this.livenessPassed = true;
                    this.setStatus('Verifying Identity...', 'info');
                }
            }

            // -------------------------------------------------------------
            // VERIFICATION / ENROLLMENT EXECUTION
            // -------------------------------------------------------------
            if (this.livenessPassed && detection.descriptor) {
                // If in ENROLLMENT mode: collect multiple samples
                if (this.mode === 'enroll') {
                    const currentSample = Array.from(detection.descriptor);
                    this.enrollmentSamples.push(currentSample);
                    this.setStatus(`Capturing Sample ${this.enrollmentSamples.length} of ${this.requiredSamples}...`, 'warning');

                    if (this.enrollmentSamples.length >= this.requiredSamples) {
                        this.isScanning = false;
                        this.setStatus('Face Enrolled Successfully!', 'success');
                        if (this.onVerified) {
                            this.onVerified(this.enrollmentSamples, 1.0, this.challengeId);
                        }
                        return;
                    }
                } else {
                    // PUNCH VERIFICATION mode: single confirmed live descriptor
                    this.isScanning = false;
                    this.setStatus('Face Verified! Submitting Attendance...', 'success');
                    const descriptorArray = Array.from(detection.descriptor);
                    const livenessScore = Math.min(1.0, 0.85 + (this.blinkCount * 0.15));

                    if (this.onVerified) {
                        this.onVerified(descriptorArray, livenessScore, this.challengeId);
                    }
                    return;
                }
            }
        } catch (err) {
            console.error('Frame processing error:', err);
        }

        if (this.isScanning) {
            this.animFrameId = requestAnimationFrame(() => this.processFrame());
        }
    }
}

/**
 * Reverse Geocoding Helper
 */
async function getReverseLocation(lat, lon) {
    try {
        const resp = await fetch(`https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json`, {
            headers: { 'Accept': 'application/json' }
        });
        if (!resp.ok) return 'Location unavailable';
        const data = await resp.json();
        const a = data.address || {};
        const city = a.city || a.town || a.village || a.suburb || '';
        const state = a.state || '';
        const country = a.country || '';
        if (city && state) return `${city}, ${state}`;
        if (city) return city;
        if (state) return state;
        return country || 'Location unavailable';
    } catch {
        return 'Location unavailable';
    }
}

/**
 * Fetch Browser Coordinates
 */
function getBrowserPosition() {
    return new Promise((resolve) => {
        if (!navigator.geolocation) {
            resolve({ lat: null, lng: null, location: 'Geolocation not supported' });
            return;
        }
        navigator.geolocation.getCurrentPosition(
            async (pos) => {
                const lat = pos.coords.latitude;
                const lng = pos.coords.longitude;
                const locName = await getReverseLocation(lat, lng);
                resolve({ lat, lng, location: locName });
            },
            () => {
                resolve({ lat: null, lng: null, location: 'Location permission denied' });
            },
            { enableHighAccuracy: true, timeout: 6000, maximumAge: 0 }
        );
    });
}
