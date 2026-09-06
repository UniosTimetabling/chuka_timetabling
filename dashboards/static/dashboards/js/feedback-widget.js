/**
 * feedback-widget.js — Floating feedback/chatbot widget
 * Appears on left-bottom corner of: unios_homepage, student_portal, staff_portal
 *
 * Features:
 *  - Expandable chat bubble (bottom-left)
 *  - Connects to /api/bot-chat/ endpoint
 *  - Saves feedback to DB via /api/submit_feedback/
 *  - Answers questions about courses, collisions, timetables
 *  - Saves records even after resolving
 *  - Input sanitized client-side (server handles further validation)
 *
 * @module FeedbackWidget
 * @version 2.0.0
 */

(function () {
  'use strict';

  /* ── Config ────────────────────────────────────────── */
  var BOT_CHAT_URL    = '/api/bot-chat/';
  var SUBMIT_URL      = '/api/submit_feedback/';
  var CSRF_ATTR       = 'name="csrfmiddlewaretoken"';
  var WIDGET_ID       = 'cu-feedback-widget';
  var OPEN_CLASS      = 'cu-fb-open';
  var MAX_MSG_LENGTH  = 1200;

  /* ── CSRF helper ───────────────────────────────────── */
  function getCSRF() {
    var cookie = document.cookie.split(';')
      .map(function (c) { return c.trim(); })
      .find(function (c) { return c.startsWith('csrftoken='); });
    if (cookie) return cookie.split('=')[1];
    var el = document.querySelector('[name=csrfmiddlewaretoken]');
    return el ? el.value : '';
  }

  /* ── Sanitize user input ───────────────────────────── */
  function sanitize(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#x27;')
      .slice(0, MAX_MSG_LENGTH);
  }

  /* ── Build widget HTML ─────────────────────────────── */
  function buildWidget() {
    var div = document.createElement('div');
    div.id  = WIDGET_ID;
    div.innerHTML = [
      '<!-- Trigger Bubble -->',
      '<button class="cu-fb-trigger" aria-label="Open Feedback &amp; Help" aria-expanded="false" aria-controls="cu-fb-panel">',
      '  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">',
      '    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
      '  </svg>',
      '  <span class="cu-fb-trigger-label">Feedback &amp; Help</span>',
      '  <span class="cu-fb-unread" style="display:none" aria-hidden="true">1</span>',
      '</button>',

      '<!-- Chat Panel -->',
      '<div class="cu-fb-panel" id="cu-fb-panel" role="dialog" aria-modal="true" aria-label="Feedback and Help Chat" style="display:none">',

      '  <!-- Header -->',
      '  <div class="cu-fb-header">',
      '    <div class="cu-fb-header-info">',
      '      <div class="cu-fb-avatar" aria-hidden="true">🎓</div>',
      '      <div>',
      '        <strong>Timetabling Assistant</strong>',
      '        <span class="cu-fb-status">● Online</span>',
      '      </div>',
      '    </div>',
      '    <button class="cu-fb-close" aria-label="Close help panel">×</button>',
      '  </div>',

      '  <!-- Messages -->',
      '  <div class="cu-fb-messages" id="cu-fb-messages" role="log" aria-live="polite" aria-label="Chat messages"></div>',

      '  <!-- Input Area -->',
      '  <div class="cu-fb-input-area">',
      '    <div class="cu-fb-input-row">',
      '      <textarea class="cu-fb-input" id="cu-fb-input" ',
      '        placeholder="Ask about timetables, collisions, courses…" ',
      '        rows="1" maxlength="' + MAX_MSG_LENGTH + '" ',
      '        aria-label="Type your message"></textarea>',
      '      <button class="cu-fb-send" id="cu-fb-send" aria-label="Send message">',
      '        <svg viewBox="0 0 24 24" fill="currentColor" width="18" height="18" aria-hidden="true">',
      '          <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>',
      '        </svg>',
      '      </button>',
      '    </div>',
      '    <p class="cu-fb-disclaimer">Messages &amp; queries are recorded to improve service.</p>',
      '  </div>',

      '</div>',
    ].join('\n');

    document.body.appendChild(div);
    injectStyles();
    return div;
  }

  /* ── CSS ───────────────────────────────────────────── */
  function injectStyles() {
    if (document.getElementById('cu-fb-styles')) return;
    var style = document.createElement('style');
    style.id  = 'cu-fb-styles';
    style.textContent = [
      /* Widget root */
      '#cu-feedback-widget{position:fixed;bottom:2.4rem;left:1.6rem;z-index:8000;font-family:var(--font-body,"Inter",system-ui,sans-serif);}',

      /* Trigger button */
      '.cu-fb-trigger{',
      '  display:flex;align-items:center;gap:8px;',
      '  background:var(--cu-green,#005a48);color:#fff;',
      '  border:none;border-radius:100px;',
      '  padding:.65rem 1.2rem .65rem .95rem;',
      '  font-size:.84rem;font-weight:600;',
      '  cursor:pointer;',
      '  box-shadow:0 6px 24px rgba(0,50,30,.35);',
      '  transition:transform .2s,box-shadow .2s,background .2s;',
      '  white-space:nowrap;',
      '}',
      '.cu-fb-trigger svg{width:18px;height:18px;flex-shrink:0;}',
      '.cu-fb-trigger:hover{background:var(--green-600,#007558);transform:translateY(-2px);box-shadow:0 10px 32px rgba(0,50,30,.4);}',
      '.cu-fb-trigger:focus-visible{outline:3px solid rgba(0,90,72,.5);outline-offset:3px;}',
      '.cu-fb-unread{',
      '  background:#e53935;color:#fff;',
      '  width:18px;height:18px;border-radius:50%;',
      '  font-size:.65rem;font-weight:700;',
      '  display:flex;align-items:center;justify-content:center;',
      '  margin-left:2px;flex-shrink:0;',
      '}',

      /* Panel */
      '.cu-fb-panel{',
      '  position:absolute;bottom:calc(100% + 12px);left:0;',
      '  width:clamp(320px,90vw,420px);',
      '  height:clamp(460px,60vh,600px);',
      '  max-height:calc(100vh - 120px);',
      '  background:#fff;',
      '  border-radius:16px;',
      '  box-shadow:0 16px 48px rgba(0,0,0,.2),0 2px 8px rgba(0,0,0,.1);',
      '  border:1px solid rgba(0,90,72,.12);',
      '  display:flex;flex-direction:column;',
      '  overflow:hidden;',
      '  animation:cuFbIn .25s ease;',
      '}',
      '@keyframes cuFbIn{from{opacity:0;transform:translateY(10px) scale(.97)}to{opacity:1;transform:translateY(0) scale(1)}}',

      /* Header */
      '.cu-fb-header{',
      '  background:var(--cu-green,#005a48);',
      '  padding:.85rem 1rem;',
      '  display:flex;align-items:center;justify-content:space-between;',
      '  flex-shrink:0;',
      '}',
      '.cu-fb-header-info{display:flex;align-items:center;gap:10px;}',
      '.cu-fb-avatar{',
      '  width:36px;height:36px;border-radius:50%;',
      '  background:rgba(255,255,255,.15);',
      '  display:flex;align-items:center;justify-content:center;',
      '  font-size:18px;flex-shrink:0;',
      '}',
      '.cu-fb-header-info strong{display:block;color:#fff;font-size:.88rem;font-weight:700;}',
      '.cu-fb-status{font-size:.7rem;color:rgba(255,255,255,.7);}',
      '.cu-fb-close{',
      '  background:rgba(255,255,255,.15);border:none;color:#fff;',
      '  width:28px;height:28px;border-radius:50%;',
      '  cursor:pointer;font-size:1.1rem;line-height:1;',
      '  display:flex;align-items:center;justify-content:center;',
      '  transition:background .15s;',
      '}',
      '.cu-fb-close:hover{background:rgba(255,255,255,.28);}',

      /* Messages */
      '.cu-fb-messages{',
      '  flex:1;overflow-y:auto;',
      '  padding:1rem 1rem;',
      '  display:flex;flex-direction:column;gap:.75rem;',
      '  scroll-behavior:smooth;',
      '  min-height:0;',
      '}',
      '.cu-fb-msg{display:flex;gap:8px;max-width:92%;}',
      '.cu-fb-msg.bot{align-self:flex-start;}',
      '.cu-fb-msg.user{align-self:flex-end;flex-direction:row-reverse;}',
      '.cu-fb-msg-icon{width:28px;height:28px;border-radius:50%;flex-shrink:0;background:var(--cu-green,#005a48);color:#fff;display:flex;align-items:center;justify-content:center;font-size:14px;margin-top:2px;}',
      '.cu-fb-msg.user .cu-fb-msg-icon{background:var(--cu-blue,#1245a8);}',
      '.cu-fb-bubble{',
      '  padding:.6rem .9rem;border-radius:14px;',
      '  font-size:.84rem;line-height:1.55;',
      '  max-width:calc(100% - 36px);',
      '}',
      '.cu-fb-msg.bot .cu-fb-bubble{',
      '  background:var(--green-50,#f0faf5);',
      '  border:1px solid var(--green-100,#e0f4ed);',
      '  color:var(--cu-text,#4a504c);',
      '  border-top-left-radius:4px;',
      '}',
      '.cu-fb-msg.user .cu-fb-bubble{',
      '  background:var(--cu-green,#005a48);',
      '  color:#fff;',
      '  border-top-right-radius:4px;',
      '}',

      /* Typing dots */
      '.cu-fb-typing{display:flex;gap:4px;padding:4px 0;}',
      '.cu-fb-typing span{width:7px;height:7px;background:#7abba0;border-radius:50%;animation:cuFbDot 1.1s infinite;}',
      '.cu-fb-typing span:nth-child(2){animation-delay:.18s;}',
      '.cu-fb-typing span:nth-child(3){animation-delay:.36s;}',
      '@keyframes cuFbDot{0%,60%,100%{transform:translateY(0);opacity:.5;}30%{transform:translateY(-5px);opacity:1;}}',

      /* Input */
      '.cu-fb-input-area{',
      '  padding:.75rem 1rem;',
      '  border-top:1px solid rgba(0,0,0,.07);',
      '  flex-shrink:0;',
      '}',
      '.cu-fb-input-row{display:flex;gap:8px;align-items:flex-end;}',
      '.cu-fb-input{',
      '  flex:1;border:1.5px solid rgba(0,0,0,.12);',
      '  border-radius:10px;padding:.6rem .9rem;',
      '  font-size:.86rem;font-family:inherit;',
      '  resize:none;outline:none;',
      '  transition:border-color .15s;',
      '  max-height:120px;overflow-y:auto;',
      '  line-height:1.5;',
      '}',
      '.cu-fb-input:focus{border-color:var(--cu-green,#005a48);}',
      '.cu-fb-send{',
      '  width:36px;height:36px;flex-shrink:0;',
      '  background:var(--cu-green,#005a48);color:#fff;',
      '  border:none;border-radius:9px;cursor:pointer;',
      '  display:flex;align-items:center;justify-content:center;',
      '  transition:background .15s,transform .15s;',
      '}',
      '.cu-fb-send:hover{background:var(--green-600,#007558);transform:scale(1.05);}',
      '.cu-fb-send:disabled{background:#ccc;cursor:not-allowed;transform:none;}',
      '.cu-fb-disclaimer{font-size:.68rem;color:#9a9e9b;margin-top:5px;text-align:center;}',

      /* Contact form (rendered inline inside the scrollable messages log,
         styled like a card so it reads as part of the conversation and is
         never clipped by the panel's fixed height) */
      '.cu-fb-contact-form{',
      '  padding:.8rem .9rem;margin-top:.15rem;',
      '  background:var(--green-50,#f0faf5);',
      '  border:1px solid var(--green-100,#e0f4ed);',
      '  border-radius:12px;',
      '  display:flex;flex-direction:column;gap:.6rem;',
      '  flex-shrink:0;',
      '}',
      '.cu-fb-contact-form input,.cu-fb-contact-form textarea{',
      '  width:100%;padding:.5rem .7rem;',
      '  border:1.5px solid rgba(0,0,0,.12);border-radius:8px;',
      '  font-size:.82rem;font-family:inherit;outline:none;',
      '}',
      '.cu-fb-contact-form input:focus,.cu-fb-contact-form textarea:focus{border-color:var(--cu-green,#005a48);}',
      '.cu-fb-contact-form textarea{resize:none;min-height:60px;}',
      '.cu-fb-submit-btn{',
      '  background:var(--cu-green,#005a48);color:#fff;border:none;',
      '  padding:.5rem;border-radius:8px;cursor:pointer;',
      '  font-size:.84rem;font-weight:600;',
      '  transition:background .15s;',
      '}',
      '.cu-fb-submit-btn:hover{background:var(--green-600,#007558);}',

      /* Responsive */
      '@media(max-width:600px){',
      '  .cu-fb-panel{',
      '    width:calc(100vw - 1.6rem);',
      '    left:-0.2rem;',
      '    height:clamp(400px,70vh,520px);',
      '    max-height:calc(100vh - 100px);',
      '  }',
      '  #cu-feedback-widget{bottom:.9rem;left:.9rem;}',
      '}',
      '@media(max-width:400px){',
      '  .cu-fb-panel{width:calc(100vw - 1.2rem);left:-0.1rem;}',
      '}',
    ].join('');
    document.head.appendChild(style);
  }

  /* ── Message rendering ─────────────────────────────── */
  function appendMessage(container, role, html) {
    var icon = role === 'bot' ? '🎓' : '👤';
    var div  = document.createElement('div');
    div.className = 'cu-fb-msg ' + role;
    div.innerHTML =
      '<div class="cu-fb-msg-icon" aria-hidden="true">' + icon + '</div>' +
      '<div class="cu-fb-bubble">' + html + '</div>';
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
  }

  function appendTyping(container) {
    var div = document.createElement('div');
    div.className = 'cu-fb-msg bot cu-fb-typing-row';
    div.innerHTML =
      '<div class="cu-fb-msg-icon" aria-hidden="true">🎓</div>' +
      '<div class="cu-fb-bubble"><div class="cu-fb-typing"><span></span><span></span><span></span></div></div>';
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
  }

  /* ── API calls ─────────────────────────────────────── */
  function callBotChat(message, callback) {
    fetch(BOT_CHAT_URL, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCSRF(),
      },
      body: JSON.stringify({ message: message }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) { callback(null, data); })
      .catch(function (err) { callback(err, null); });
  }

  function submitFeedback(payload, callback) {
    fetch(SUBMIT_URL, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCSRF(),
      },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) { callback(null, data); })
      .catch(function (err) { callback(err, null); });
  }

  /* ── Contact form injection ────────────────────────── */
  function showContactForm(panel, prefillMsg) {
    // Remove any existing form (it lives inside the messages thread now)
    var existing = panel.querySelector('.cu-fb-contact-form');
    if (existing) existing.remove();

    var form = document.createElement('div');
    form.className = 'cu-fb-contact-form';
    form.innerHTML = [
      '<p style="font-size:.8rem;font-weight:600;color:#252825;margin-bottom:.2rem">Send us a message</p>',
      '<input type="text" id="cu-fb-name"   placeholder="Your name *" maxlength="100" aria-label="Full name">',
      '<input type="email" id="cu-fb-email" placeholder="Your email *" maxlength="150" aria-label="Email address">',
      '<input type="text" id="cu-fb-adm"   placeholder="Admission no. (optional)" maxlength="50" aria-label="Admission number">',
      '<textarea id="cu-fb-msg" placeholder="Describe your issue…" maxlength="1000" aria-label="Message">' + (prefillMsg || '') + '</textarea>',
      '<button class="cu-fb-submit-btn" type="button">📨 Send Message</button>',
    ].join('');

    // IMPORTANT: append inside the scrollable messages log (#cu-fb-messages),
    // NOT as a sibling of header/messages/input-area inside the fixed-height
    // panel. The panel uses overflow:hidden with a clamped height, so any
    // extra block appended directly to it (as this used to do) gets pushed
    // past the visible area and clipped — that's what was cutting off the
    // "Send Message" button. Putting it inside the already-scrollable
    // messages container means it scrolls into view like a chat bubble
    // instead of overflowing the panel.
    var msgBox = panel.querySelector('#cu-fb-messages');
    var host = msgBox || panel.querySelector('#cu-fb-panel') || panel;
    host.appendChild(form);
    if (msgBox) msgBox.scrollTop = msgBox.scrollHeight;
    // Keep it in view (also handles panels not yet fully laid out)
    if (typeof form.scrollIntoView === 'function') {
      form.scrollIntoView({ block: 'end', behavior: 'smooth' });
    }

    form.querySelector('.cu-fb-submit-btn').addEventListener('click', function () {
      var name  = (form.querySelector('#cu-fb-name').value  || '').trim();
      var email = (form.querySelector('#cu-fb-email').value || '').trim();
      var msg   = (form.querySelector('#cu-fb-msg').value   || '').trim();
      var adm   = (form.querySelector('#cu-fb-adm').value   || '').trim();

      if (!name || !email || !msg) {
        alert('Please fill in your name, email, and message.');
        return;
      }

      // Validate email format
      var emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRe.test(email)) {
        alert('Please enter a valid email address.');
        return;
      }

      var btn = form.querySelector('.cu-fb-submit-btn');
      btn.disabled = true;
      btn.textContent = '⏳ Sending…';

      submitFeedback({
        full_name: sanitize(name),
        email: email,
        admission_number: sanitize(adm),
        message: sanitize(msg),
      }, function (err, data) {
        btn.disabled = false;
        btn.textContent = '📨 Send Message';
        form.remove();
        var msgBox = panel.querySelector('#cu-fb-messages');
        if (err || !data || (!data.success && !data.ok)) {
          appendMessage(msgBox, 'bot',
            '⚠️ Sorry, I couldn\'t save your message. Please try again or email us directly.');
        } else {
          appendMessage(msgBox, 'bot',
            '✅ <strong>Got it!</strong> Your message has been saved and our team will follow up. ' +
            'Reference: <strong>#' + (data.id || data.feedback_id || 'N/A') + '</strong>');
        }
        if (msgBox) msgBox.scrollTop = msgBox.scrollHeight;
      });
    });
  }

  /* ── Main chat logic ───────────────────────────────── */
  function initChat(widget) {
    var trigger  = widget.querySelector('.cu-fb-trigger');
    var panel    = widget.querySelector('.cu-fb-panel');
    var closeBtn = widget.querySelector('.cu-fb-close');
    var messages = widget.querySelector('#cu-fb-messages');
    var input    = widget.querySelector('#cu-fb-input');
    var sendBtn  = widget.querySelector('#cu-fb-send');
    var unreadEl = widget.querySelector('.cu-fb-unread');

    var isOpen   = false;

    // Welcome message
    appendMessage(messages, 'bot',
      '👋 <strong>Hello!</strong> I can help you with:<br>' +
      '• Course timetables &amp; collisions<br>' +
      '• Exam schedules<br>' +
      '• General queries<br><br>' +
      'Type your question below, or ask me to <em>connect you with staff</em>.'
    );

    /* Open/close */
    function openPanel() {
      isOpen = true;
      panel.style.display = 'flex';
      trigger.setAttribute('aria-expanded', 'true');
      unreadEl.style.display = 'none';
      input.focus();
    }
    function closePanel() {
      isOpen = false;
      panel.style.display = 'none';
      trigger.setAttribute('aria-expanded', 'false');
    }

    trigger.addEventListener('click', function () {
      isOpen ? closePanel() : openPanel();
    });
    closeBtn.addEventListener('click', closePanel);

    // Close on Escape
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && isOpen) closePanel();
    });

    /* Auto-grow textarea */
    input.addEventListener('input', function () {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 100) + 'px';
    });

    /* Send */
    function send() {
      var text = input.value.trim();
      if (!text || sendBtn.disabled) return;

      // Append user message
      appendMessage(messages, 'user', sanitize(text));
      input.value = '';
      input.style.height = 'auto';
      sendBtn.disabled = true;

      var typing = appendTyping(messages);

      callBotChat(text, function (err, data) {
        typing.remove();
        sendBtn.disabled = false;

        if (err || !data) {
          appendMessage(messages, 'bot',
            '⚠️ I\'m having trouble connecting. Please try again in a moment.');
          return;
        }

        var reply = data.reply || data.message || 'Sorry, I couldn\'t generate a response.';
        appendMessage(messages, 'bot', reply.replace(/\n/g, '<br>'));

        // If bot couldn't answer, offer contact form
        if (data.show_form || (reply && reply.toLowerCase().includes('unable to find'))) {
          appendMessage(messages, 'bot',
            'Would you like to <strong>send a message</strong> to the timetabling office instead?<br>' +
            '<button onclick="window._cuShowForm()" style="margin-top:6px;background:var(--cu-green,#005a48);color:#fff;border:none;padding:.4rem .9rem;border-radius:6px;cursor:pointer;font-size:.8rem">Yes, send message</button>'
          );
          window._cuShowForm = function () { showContactForm(widget, text); };
        }
      });
    }

    sendBtn.addEventListener('click', send);
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    });

    // Show unread badge after short delay if not opened
    setTimeout(function () {
      if (!isOpen) unreadEl.style.display = 'flex';
    }, 4000);
  }

  /* ── Bootstrap ─────────────────────────────────────── */
  function init() {
    // Only inject on pages that want the widget
    if (document.getElementById(WIDGET_ID)) return; // already mounted (prevents double init)
    var widget = buildWidget();
    initChat(widget);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

}());
