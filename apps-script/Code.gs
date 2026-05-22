/**
 * Astrix forwarder — by UNDEAD (https://github.com/itsund3ad)
 *
 * Fork of GooseRelayVPN forwarder.
 *
 * Apps Script web app deployed as: Execute as: Me, Access: Anyone.
 * All traffic is AES-GCM encrypted end-to-end; this script is a dumb pipe
 * that never sees plaintext or holds the key.
 *
 * Wire: Client POSTs base64(encrypted batch) → we forward bytes verbatim
 * to VPS relay(s) → return VPS response verbatim.
 *
 * Hot path (doPost) is intentionally kept under 10 lines of real logic
 * so every microsecond goes to the tunnel, not to script overhead.
 */

// ── CONFIGURATION ──────────────────────────────────────────────────

/** VPS relay endpoint(s). Replace with your server address(es). */
var RELAY_URLS = [
  'http://YOUR.VPS.IP:8443/tunnel',
];

var FORWARDER_VERSION = 1;
var PROTOCOL_VERSION = 1;

/** When true, tracks per-deployment daily invocation count via PropertiesService.
 *  Adds ~50ms per request. Leave false for maximum throughput. */
var ENABLE_INVOCATION_COUNTING = false;

// ── HELPERS ────────────────────────────────────────────────────────

// Matches Apps Script URLs to detect accidental relay loops.
var GAS_RELAY_LOOP_RE_ = /^https?:\/\/script\.google\.com\/macros\//i;

// ── HOT PATH: doPost ───────────────────────────────────────────────

function doPost(e) {
  // Loop guard: refuse to forward to another Apps Script URL.
  for (var i = 0; i < RELAY_URLS.length; i++) {
    if (GAS_RELAY_LOOP_RE_.test(RELAY_URLS[i])) {
      return ContentService
        .createTextOutput('relay_loop_detected: RELAY_URLS must point to your VPS /tunnel endpoint, not Apps Script')
        .setMimeType(ContentService.MimeType.TEXT);
    }
  }

  if (ENABLE_INVOCATION_COUNTING) {
    bumpInvocationCount_();
  }

  // Read the raw base64 body from the client.
  var payload = (e && e.postData && e.postData.contents) || '';

  // Try each relay URL in order; return first 200, or last error.
  var lastText = '';
  for (var i = 0; i < RELAY_URLS.length; i++) {
    try {
      var resp = UrlFetchApp.fetch(RELAY_URLS[i], {
        method: 'post',
        contentType: 'text/plain',
        payload: payload,
        muteHttpExceptions: true,
        followRedirects: false,
        deadline: 30,
      });
      var status = resp.getResponseCode();
      var text = resp.getContentText();
      lastText = text;
      if (status === 200) {
        return ContentService
          .createTextOutput(text)
          .setMimeType(ContentService.MimeType.TEXT);
      }
      lastText = JSON.stringify({
        e: 'upstream_status',
        status: status,
        body: text.slice(0, 1024),
      });
    } catch (err) {
      lastText = String(err);
    }
  }

  return ContentService
    .createTextOutput(lastText)
    .setMimeType(ContentService.MimeType.TEXT);
}

// ── METADATA: doGet ────────────────────────────────────────────────

function doGet(e) {
  // Legacy support: plain-text health check.
  if (e && e.parameter && e.parameter.legacy === '1') {
    return ContentService
      .createTextOutput('Astrix forwarder OK')
      .setMimeType(ContentService.MimeType.TEXT);
  }

  // JSON metadata for client pre-flight checks.
  var props = PropertiesService.getScriptProperties();
  var today = pacificDateKey_();
  var count = parseInt(props.getProperty('count_' + today) || '0', 10);
  var out = {
    ok: true,
    date: today,
    count: count,
    version: FORWARDER_VERSION,
    protocol: PROTOCOL_VERSION,
  };
  return ContentService
    .createTextOutput(JSON.stringify(out))
    .setMimeType(ContentService.MimeType.JSON);
}

// ── INVOCATION COUNTER (optional) ──────────────────────────────────

function pacificDateKey_() {
  return Utilities.formatDate(new Date(), 'America/Los_Angeles', 'yyyy-MM-dd');
}

function bumpInvocationCount_() {
  try {
    var props = PropertiesService.getScriptProperties();
    var today = pacificDateKey_();
    var key = 'count_' + today;
    var raw = props.getProperty(key);
    if (raw === null) {
      pruneStaleCounts_(props, today);
    }
    var cur = raw === null ? 0 : parseInt(raw, 10);
    props.setProperty(key, String(cur + 1));
  } catch (err) {
    // Counting is informational; swallow errors rather than break the tunnel.
  }
}

function pruneStaleCounts_(props, today) {
  var keys = props.getKeys();
  var keep = 'count_' + today;
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    if (k.indexOf('count_') === 0 && k !== keep) {
      props.deleteProperty(k);
    }
  }
}
