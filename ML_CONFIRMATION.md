# Consecutive ML confirmation

An ML request error previously retained accumulated positive detections. A later
positive frame could therefore trigger automatic pause without a continuous series
of valid positive observations. Errors now reset the confirmation count, as camera
errors already did. Regression coverage checks a positive-positive-error-positive
sequence does not pause until three fresh positive observations arrive.

No model, threshold, confirmation requirement, polling interval, warmup or printer
protocol change is included. The inspected deployment used threshold 0.4, three
confirmations, ten-second polling and 300-second warmup. This ML fix is deployed.
Tree supports can still produce persistent Obico positives; this fix does not
demonstrate support-aware classification or guarantee fewer model false positives.

Startup tests also distinguish expired Snooze recovery (re-enable detection) from
explicitly disabled detection with no Snooze (remain disabled). Existing startup
behavior is intentionally preserved. See INVESTIGATION_FIXES.md for the observed
database transition and evidence limitations.
