/* ============================================================
   우리집 루틴 보드 — app.js
   ============================================================ */
(function () {
  'use strict';

  var U = RB.util, Store = RB.Store;
  var D = U.DAYS, DL = U.DAY_LABEL;

  /* ---------------- tiny helpers ---------------- */
  function st() { return Store.state; }
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function pad2(n) { n = Math.floor(n); return n < 10 ? '0' + n : '' + n; }
  function pad(n) { return pad2(n); }
  function nf(n) { return Number(n || 0).toLocaleString('ko-KR'); }
  function hhmmToMin(t) {
    if (!t) return null;
    var p = String(t).split(':');
    if (p.length < 2) return null;
    var h = parseInt(p[0], 10), m = parseInt(p[1], 10);
    if (isNaN(h) || isNaN(m)) return null;
    return h * 60 + m;
  }
  function hhmm(m) { m = ((Math.round(m) % 1440) + 1440) % 1440; return pad2(Math.floor(m / 60)) + ':' + pad2(m % 60); }
  function normHM(v) {
    v = String(v == null ? '' : v).replace(/[^0-9:]/g, '').trim();
    if (/^\d{3,4}$/.test(v)) v = v.length === 3 ? '0' + v.charAt(0) + ':' + v.slice(1) : v.slice(0, 2) + ':' + v.slice(2);
    var m = /^(\d{1,2}):(\d{2})$/.exec(v);
    if (!m) return '';
    var h = parseInt(m[1], 10), mi = parseInt(m[2], 10);
    if (h > 23 || mi > 59) return '';
    return pad2(h) + ':' + pad2(mi);
  }
  function timeOf(ts) { var d = new Date(ts); return pad2(d.getHours()) + ':' + pad2(d.getMinutes()); }
  function nowMin() { var d = new Date(); return d.getHours() * 60 + d.getMinutes(); }
  function dateLabel(dateStr) {
    var d = U.parseYmd(dateStr);
    return (d.getMonth() + 1) + '월 ' + d.getDate() + '일 (' + DL[U.dayKeyOf(dateStr)] + ')';
  }
  function weekdayLabel(dateStr) {
    var d = U.parseYmd(dateStr);
    return DL[U.dayKeyOf(dateStr)] + ' ' + d.getDate();
  }
  function clampNum(v, min, max, fallback) {
    var n = Number(v);
    if (isNaN(n)) n = fallback || 0;
    return Math.max(min, Math.min(max, n));
  }
  function round1(n) { return Math.round(n * 10) / 10; }
  function minsLabel(n) { n = Number(n) || 0; if (n <= 0) return '0'; if (n < 10) return String(Math.round(n * 10) / 10); return String(Math.round(n)); }

  /* ---------------- runtime ui state ---------------- */
  var ui = {
    view: 'today', period: 'day', unlocked: false,
    pin: '', edit: null, dayPick: null, planDraft: null, drag: null,
    confirmCb: null, poolDay: null, lastDay: U.today()
  };
  var timers = {};      /* key -> {kind,mode,label,emoji,startedAt,targetSec,itemId,child} */
  var alarm = { active: false, cfg: null };
  var wakeLock = { sentinel: null, want: false };

  /* ---------------- data helpers ---------------- */
  function itemById(id) {
    var items = st().items;
    for (var i = 0; i < items.length; i++) if (items[i].id === id) return items[i];
    return null;
  }
  function childOf(id) { return st().children[id] || { name: '아이', emoji: '🙂' }; }
  function curChild() { return st().ui.child; }
  function isDone(child, date, id) {
    var c = st().completions[child];
    return !!(c && c[date] && c[date][id]);
  }
  function doneAt(child, date, id) {
    var c = st().completions[child];
    return c && c[date] ? c[date][id] : null;
  }
  function ensureDay(child, date) {
    var c = st().completions[child] = st().completions[child] || {};
    c[date] = c[date] || {};
    return c[date];
  }
  function applies(it, child, date) {
    if (it.kind === 'routine') return true;
    var sched = st().schedule[child];
    return !!(sched && (sched[U.dayKeyOf(date)] || []).indexOf(it.id) >= 0);
  }
  function itemsForDate(child, date) {
    return st().items.filter(function (it) { return applies(it, child, date); });
  }
  function dayProgress(child, date) {
    var items = itemsForDate(child, date);
    var done = items.filter(function (it) { return isDone(child, date, it.id); }).length;
    return { items: items, count: items.length, done: done, pct: items.length ? Math.round(done / items.length * 100) : 0 };
  }
  function studyDoneOn(child, date) {
    var map = (st().completions[child] || {})[date] || {};
    return st().items.filter(function (it) { return it.kind === 'study' && map[it.id]; }).length;
  }
  function pendingPenalty(child, date) {
    return itemsForDate(child, date).filter(function (it) { return !isDone(child, date, it.id); })
      .reduce(function (a, it) { return a + (it.penalty || 0); }, 0);
  }
  function pendingCount(child, date) {
    return itemsForDate(child, date).filter(function (it) { return !isDone(child, date, it.id); }).length;
  }

  /* ---------------- score ---------------- */
  function score(child) {
    var earn = 0, pen = 0, spent = 0;
    st().log.forEach(function (l) {
      if (l.child !== child) return;
      switch (l.type) {
        case 'earn': case 'manual_plus': earn += l.amount; break;
        case 'cancel': case 'manual_minus': earn -= l.amount; break;
        case 'penalty': pen += l.amount; break;
        case 'penalty_undo': pen -= l.amount; break;
        case 'redeem': spent += l.amount; break;
      }
    });
    pen = Math.max(0, pen);
    return { earned: earn, penalty: pen, spent: spent, net: earn - pen - spent };
  }
  function logAdd(entry) {
    var e = { id: U.uid('lg'), ts: Date.now() };
    for (var k in entry) e[k] = entry[k];
    st().log.push(e);
    return e;
  }

  /* ---------------- time log (break / play / game) ---------------- */
  function addTime(kind, mins, itemId) {
    if (!(mins > 0.05)) return;
    st().timeLog.push({
      id: U.uid('tl'), child: st().ui.child, date: U.today(), kind: kind,
      mins: round1(mins), itemId: itemId || '', at: Date.now()
    });
  }
  function timeTotals(child, from, to) {
    var t = { break: 0, play: 0, game: 0 };
    st().timeLog.forEach(function (e) {
      if (e.child !== child || e.date < from || e.date > to) return;
      if (t[e.kind] === undefined) t[e.kind] = 0;
      t[e.kind] += (e.mins || 0);
    });
    return t;
  }
  function totalMins(t) { return (t.break || 0) + (t.play || 0) + (t.game || 0); }

  /* ---------------- period stats ---------------- */
  function periodRange(mode) {
    var today = U.today(), start = today;
    if (mode === 'week') {
      var off = (U.parseYmd(today).getDay() + 6) % 7;
      start = U.addDays(today, -off);
    } else if (mode === 'month') {
      start = today.slice(0, 8) + '01';
    }
    var sd = st().startDate;
    if (sd && start < sd) start = sd;
    if (start > today) start = today;
    var days = 0, d = start, guard = 0;
    while (d <= today && guard < 400) { days++; d = U.addDays(d, 1); guard++; }
    return { start: start, end: today, days: Math.max(1, days) };
  }
  var KIND_ORDER = { study: 0, promise: 1, routine: 2 };
  function periodStats(child, mode) {
    var r = periodRange(mode);
    var rows = st().items.map(function (it) {
      var exp = 0, done = 0, d = r.start, guard = 0;
      while (d <= r.end && guard < 400) {
        if (applies(it, child, d)) {
          exp++;
          if (isDone(child, d, it.id)) done++;
        }
        d = U.addDays(d, 1); guard++;
      }
      return { item: it, expected: exp, done: done };
    }).filter(function (x) { return x.expected > 0 || x.done > 0; });
    rows.sort(function (a, b) {
      var ka = KIND_ORDER[a.item.kind] || 0, kb = KIND_ORDER[b.item.kind] || 0;
      if (ka !== kb) return ka - kb;
      var ia = st().items.indexOf(a.item), ib = st().items.indexOf(b.item);
      return ia - ib;
    });
    return { range: r, rows: rows };
  }
  function studyDoneCount(child, from, to) {
    var n = 0, d = from, guard = 0;
    while (d <= to && guard < 400) {
      var map = (st().completions[child] || {})[d] || {};
      st().items.forEach(function (it) { if (it.kind === 'study' && map[it.id]) n++; });
      d = U.addDays(d, 1); guard++;
    }
    return n;
  }

  /* ---------------- day plan / free time ---------------- */
  function planFor(child, dayKey) {
    var dp = (st().dayplan[child] || {})[dayKey] || {};
    return {
      out: dp.out || '13:30',
      bed: dp.bed || '21:30',
      blocks: (dp.blocks || []).filter(function (b) { return b && b.start && b.end; }).map(function (b) { return { id: b.id, name: b.name || '일정', start: b.start, end: b.end }; })
    };
  }
  function timelineSegments(child, dayKey) {
    var p = planFor(child, dayKey);
    var out = hhmmToMin(p.out); if (out == null) out = 810;
    var bed = hhmmToMin(p.bed); if (bed == null) bed = 1290;
    if (bed <= out) bed = out + 60;
    var blocks = p.blocks.map(function (b) {
      return { name: b.name, s: hhmmToMin(b.start), e: hhmmToMin(b.end) };
    }).filter(function (b) { return b.s != null && b.e != null && b.e > b.s; })
      .sort(function (a, b) { return a.s - b.s; });
    var segs = [], cursor = out;
    blocks.forEach(function (b) {
      var s = Math.max(out, b.s), e = Math.min(bed, b.e);
      if (e <= out || s >= bed || e <= s) return;
      if (s > cursor) segs.push({ type: 'free', start: cursor, end: s });
      if (e > cursor) segs.push({ type: 'fixed', start: Math.max(cursor, s), end: e, name: b.name });
      cursor = Math.max(cursor, e);
    });
    if (cursor < bed) segs.push({ type: 'free', start: cursor, end: bed });
    return { out: out, bed: bed, segs: segs };
  }
  function freeTotal(child, dayKey) {
    return timelineSegments(child, dayKey).segs.reduce(function (a, s) {
      return a + (s.type === 'free' ? s.end - s.start : 0);
    }, 0);
  }
  function freeRemainingToday(child) {
    var dayKey = U.dayKeyOf(U.today());
    var tl = timelineSegments(child, dayKey);
    var now = nowMin(), rem = 0;
    tl.segs.forEach(function (s) {
      if (s.type !== 'free') return;
      rem += Math.max(0, s.end - Math.max(now, s.start));
    });
    return { rem: rem, total: freeTotal(child, dayKey) };
  }
  function scheduledStudyMinutes(child, dayKey) {
    var sched = st().schedule[child] || {};
    var ids = sched[dayKey] || [];
    return st().items.reduce(function (a, it) {
      if (it.kind !== 'study' || ids.indexOf(it.id) < 0) return a;
      return a + (it.minutes || 0);
    }, 0);
  }
  function scheduledStudyCount(child, dayKey) {
    var sched = st().schedule[child] || {};
    var ids = sched[dayKey] || [];
    return st().items.filter(function (it) { return it.kind === 'study' && ids.indexOf(it.id) >= 0; }).length;
  }
  function periodFree(child, mode) {
    if (mode === 'day') return freeRemainingToday(child).rem;
    var r = periodRange(mode), total = 0, d = r.start, guard = 0;
    while (d <= r.end && guard < 400) { total += freeTotal(child, U.dayKeyOf(d)); d = U.addDays(d, 1); guard++; }
    return total;
  }

  /* ---------------- settlement ---------------- */
  function settleDate(child, date) {
    var s = st();
    s.settled[child] = s.settled[child] || {};
    if (s.settled[child][date]) return { already: true, penalty: 0, missed: 0 };
    var items = itemsForDate(child, date);
    var missed = items.filter(function (it) { return !isDone(child, date, it.id); });
    var pen = missed.reduce(function (a, it) { return a + (it.penalty || 0); }, 0);
    var logId = null;
    if (pen > 0) {
      var e = logAdd({ child: child, type: 'penalty', date: date, amount: pen, items: missed.map(function (i) { return i.id; }) });
      logId = e.id;
    }
    s.settled[child][date] = { at: Date.now(), penalty: pen, missed: missed.map(function (i) { return i.id; }), logId: logId };
    return { already: false, penalty: pen, missed: missed.length };
  }
  function settleUpToAll(silent) {
    var s = st(), today = U.today(), totalPen = 0, days = 0;
    ['1', '2'].forEach(function (c) {
      var start = s.startDate || today;
      if (start > today) start = today;
      var d = start, guard = 0;
      while (d < today && guard < 200) {
        if (!(s.settled[c] && s.settled[c][d])) {
          var r = settleDate(c, d);
          if (!r.already) { days++; totalPen += r.penalty; }
        }
        d = U.addDays(d, 1); guard++;
      }
    });
    return { totalPen: totalPen, days: days };
  }
  function undoSettle(child, date) {
    var s = st();
    var info = s.settled[child] && s.settled[child][date];
    if (!info) return;
    if (info.logId) s.log = s.log.filter(function (l) { return l.id !== info.logId; });
    delete s.settled[child][date];
    save();
    toast('정산을 되돌렸어요 (' + dateLabel(date) + ')');
  }
  function settleTodayAll() {
    var today = U.today(), total = 0, out = [];
    ['1', '2'].forEach(function (c) {
      var r = settleDate(c, today);
      total += r.penalty;
      out.push(childOf(c).name + ' −' + r.penalty + '점');
    });
    save();
    toast('오늘 마감 정산 완료 · ' + out.join(' / '));
    return total;
  }

  /* ---------------- save / sync hook ---------------- */
  function save(opts) {
    Store.save(opts);
    if (!opts || !opts.quiet) Sync.queue();
  }

  /* ---------------- init ---------------- */
  function init() {
    Store.init();
    var s = st();
    try {
      var p = new URLSearchParams(location.search);
      var c = p.get('child');
      if (c === '1' || c === '2') s.ui.child = c;
      var v = p.get('view');
      if (v === 'week' || v === 'today') s.ui.view = v;
      else if (v === 'mom' && sessionStorage.getItem('rbUnlocked') === '1') s.ui.view = 'mom';
    } catch (e) { /* no-op */ }
    ui.view = s.ui.view || 'today';
    ui.period = s.ui.period || 'day';
    try { ui.unlocked = sessionStorage.getItem('rbUnlocked') === '1'; } catch (e) { ui.unlocked = false; }
    ui.lastDay = U.today();

    settleUpToAll(true);
    Store.write();
    restoreTimers();
    bind();
    Store.subscribe(function () { render(); });
    render();
    setInterval(tick, 1000);
    Sync.init();
    checkSetupLink();
  }

  /* ---------------- render root ---------------- */
  function render() {
    renderHeader();
    renderViewTabs();
    var v = ui.view;
    ['today', 'week', 'mom'].forEach(function (k) {
      var el = document.getElementById('view-' + k);
      if (el) el.hidden = (k !== v);
    });
    if (v === 'today') renderToday();
    else if (v === 'week') renderWeek();
    else renderMom();
    renderDock();
    renderSyncBadge();
  }
  function renderHeader() {
    var s = st(), child = curChild(), ch = childOf(child);
    var pr = dayProgress(child, U.today());
    var sub = $('#brandSub');
    if (sub) sub.textContent = dateLabel(U.today()) + ' · ' + ch.emoji + ' ' + ch.name + ' · 오늘 ' + pr.done + '/' + pr.count + ' 완료';
    var tabs = ['1', '2'].map(function (id) {
      var c = childOf(id), p = dayProgress(id, U.today());
      return '<button type="button" class="ctab' + (id === child ? ' active' : '') + '" data-act="child" data-child="' + id + '" role="tab" aria-selected="' + (id === child) + '">' +
        '<span class="ct-emoji">' + esc(c.emoji || '🙂') + '</span>' + esc(c.name) +
        '<span class="ct-badge">' + p.done + '/' + p.count + '</span></button>';
    }).join('');
    var ct = $('#childTabs'); if (ct) ct.innerHTML = tabs;
    var mb = $('#btnMom'); if (mb) mb.textContent = ui.unlocked ? '🔓 엄마 관리' : '🔒 엄마 관리';
    var wb = $('#btnWake'); if (wb) wb.classList.toggle('on', !!wakeLock.sentinel);
  }
  function renderViewTabs() {
    $$('#viewTabs .vtab').forEach(function (b) {
      var on = b.dataset.view === ui.view;
      b.classList.toggle('active', on);
      if (b.dataset.view === 'mom') b.textContent = (ui.unlocked ? '🔓' : '🔒') + ' 엄마 관리';
    });
  }

  /* ---------------- TODAY VIEW ---------------- */
  function renderToday() {
    var s = st(), child = curChild(), ch = childOf(child), today = U.today(), day = U.dayKeyOf(today);
    if (!s.completions[child]) s.completions[child] = {};
    var items = itemsForDate(child, today);
    var routines = items.filter(function (it) { return it.kind === 'routine'; });
    var quick = routines.filter(function (it) { return it.quick; });
    var rest = routines.filter(function (it) { return !it.quick; });
    var morning = rest.filter(function (it) { return it.slot === 'morning'; });
    var evening = rest.filter(function (it) { return it.slot !== 'morning'; });
    var studies = items.filter(function (it) { return it.kind === 'study'; });
    var promises = items.filter(function (it) { return it.kind === 'promise'; });
    var sc = score(child);
    var prog = dayProgress(child, today);
    var settled = s.settled[child] && s.settled[child][today];
    var pendPen = pendingPenalty(child, today);
    var pendCnt = pendingCount(child, today);
    var studyDone = studyDoneOn(child, today);
    var free = freeRemainingToday(child);
    var tl = timelineSegments(child, day);
    var schedMin = scheduledStudyMinutes(child, day);


    var html = '';

    /* quick strip */
    html += '<div class="card quickwrap">';
    html += '<div class="quickrow">';
    quick.forEach(function (it) { html += quickBtnHTML(it, child, today); });
    html += breakBtnHTML();
    html += '</div></div>';

    /* dashboard */
    html += '<div class="dash">';

    /* score card */
    html += '<div class="card scorecard">';
    html += '<div class="score-head"><div class="avatar">' + esc(ch.emoji || '🙂') + '</div>' +
      '<div class="score-title"><b>' + esc(ch.name) + '</b><span>' + (ch.grade ? esc(ch.grade) + ' · ' : '') + dateLabel(today) + '</span></div>' +
      '<div class="ring" style="--p:' + prog.pct + '"><span>' + prog.pct + '%</span></div></div>';
    html += '<div class="stats">' +
      '<div class="stat"><span>상점</span><b class="plus">' + nf(sc.earned) + '</b></div>' +
      '<div class="stat"><span>벌점</span><b class="minus">' + nf(sc.penalty) + '</b></div>' +
      '<div class="stat"><span>순점수</span><b class="net">' + nf(sc.net) + '</b></div></div>';
    var ttToday = timeTotals(child, today, today);
    html += '<div class="todaytime"><span>☕ 쉰 시간 <b>' + minsLabel(ttToday.break) + '분</b></span>' +
      '<span>🧸 놀이 <b>' + minsLabel(ttToday.play) + '분</b></span>' +
      '<span>🕒 빈 시간 <b>' + free.rem + '분</b></span></div>';
    html += '<div class="gauges">' + gaugeHTML(child, sc) + '</div>';
    html += '</div>';

    /* graph card */
    html += graphCardHTML(child, sc);

    /* timeline card */
    html += timelineCardHTML(child, day, tl, free, schedMin, settled);

    /* checklist card */
    html += '<div class="card checkcard">';
    html += '<div class="checkhead"><h3>✅ 오늘의 체크보드</h3>';
    if (settled) {
      html += '<span class="settled-note">마감 정산 완료 · 벌점 −' + settled.penalty + '점</span>';
    } else {
      html += '<span class="ch-info">미완료 <b>' + pendCnt + '개</b> · 지금 마감하면 <b>−' + pendPen + '점</b></span>';
    }
    html += '<span class="badge' + (free.rem <= 0 ? ' over' : '') + '">🕒 오늘 남은 빈 시간 ' + free.rem + '분</span>';
    html += '</div>';
    html += '<div class="checkcols">';
    html += checkColHTML('🌅 아침 루틴', morning, child, today);
    html += checkColHTML('📚 오늘의 공부', studies, child, today);
    html += checkColHTML('🌙 저녁 루틴', evening, child, today);
    html += '</div></div>';

    /* promise card */
    html += '<div class="card promisecard">';
    html += '<div class="card-head"><h3>🤝 엄마와의 약속</h3>' +
      (ui.unlocked ? '<button class="mini" data-act="addPromise">+ 약속 추가</button>' : '<span class="sub">엄마가 정해 준 약속</span>') + '</div>';
    if (promises.length) {
      html += '<div class="tasks">' + promises.map(function (it) { return taskHTML(it, child, today); }).join('') + '</div>';
    } else {
      html += '<div class="empty">오늘은 약속이 없어요.<br>' + (ui.unlocked ? '약속을 추가해 보세요.' : '엄마가 약속을 정해 줄 거예요.') + '</div>';
    }
    html += '</div>';

    html += '</div>'; /* dash */

    $('#view-today').innerHTML = html;
  }

  function quickBtnHTML(it, child, today) {
    var done = doneAt(child, today, it.id);
    var running = timers['item:' + it.id];
    var sub;
    if (running) {
      sub = (it.timer === 'stopwatch' ? '놀이 중 ' : '남은 ') + clockTextShort(running);
    } else if (done) {
      sub = '완료 ' + timeOf(done.ts);
    } else if (it.time) {
      sub = '목표 ' + it.time;
    } else if (it.timer === 'stopwatch') {
      var tt = timeTotals(child, today, today);
      sub = tt.play > 0 ? '오늘 ' + Math.round(tt.play) + '분' : (it.minutes ? it.minutes + '분 목표' : '눌러서 시작');
    } else if (it.minutes) {
      sub = it.minutes + '분';
    } else {
      sub = '눌러서 체크';
    }
    var timerBtn = it.timer && it.timer !== 'stopwatch'
      ? '<button class="q-timerbtn" data-act="timer" data-id="' + it.id + '">⏱</button>' : '';
    return '<div class="qbtn' + (done ? ' done' : '') + (running ? ' running' : '') + '" role="button" tabindex="0" data-act="toggle" data-id="' + it.id + '">' +
      timerBtn + '<span class="q-emoji">' + esc(it.emoji || '•') + '</span>' +
      '<span class="q-name">' + esc(it.name) + '</span>' +
      '<span class="q-sub">' + esc(sub) + '</span></div>';
  }
  function breakBtnHTML() {
    var t = timers['break'];
    var name = t ? '쉬는 중' : '10분 쉬기';
    var sub = t ? clockTextShort(t) + ' 남음' : '공부 2개마다';
    return '<button class="qbtn breakbtn' + (t ? ' running' : '') + '" data-act="break">' +
      '<span class="q-emoji">☕</span><span class="q-name">' + name + '</span><span class="q-sub">' + sub + '</span></button>';
  }
  function taskHTML(it, child, date) {
    var done = doneAt(child, date, it.id);
    var s = st();
    var locked = !!(s.settled[child] && s.settled[child][date]);
    var meta = [];
    if (it.minutes) meta.push(it.minutes + '분');
    if (it.time) meta.push('⏰ ' + it.time);
    if (done) meta.push('완료 ' + timeOf(done.ts));
    else if (it.timer) meta.push(it.timer === 'stopwatch' ? '스톱워치' : '타이머');
    var timerBtn = it.timer
      ? '<button class="ttimer' + (timers['item:' + it.id] ? ' running' : '') + '" data-act="timer" data-id="' + it.id + '" aria-label="타이머">' + (timers['item:' + it.id] ? '⏸' : '⏱') + '</button>'
      : '';
    return '<div class="task' + (done ? ' done' : '') + '" role="button" tabindex="0" data-act="toggle" data-id="' + it.id + '" data-date="' + date + '" aria-pressed="' + (!!done) + '">' +
      '<span class="tick">✓</span>' +
      '<span class="tinfo"><span class="tname">' + esc(it.name) + '</span>' +
      '<span class="tmeta">' + esc(meta.join(' · ')) + (locked ? ' · 정산됨' : '') + '</span></span>' +
      timerBtn +
      '<span class="tpoints">+' + it.reward + '</span></div>';
  }
  function checkColHTML(title, list, child, today) {
    var done = list.filter(function (it) { return isDone(child, today, it.id); }).length;
    var body = list.length
      ? list.map(function (it) { return taskHTML(it, child, today); }).join('')
      : '<div class="empty">배치된 항목이 없어요.</div>';
    return '<div class="ccol"><h4>' + title + '<em>' + done + '/' + list.length + '</em></h4><div class="tasks">' + body + '</div></div>';
  }

  function gaugeHTML(child, sc) {
    var out = '';
    var rewards = (st().rewards || []).slice().sort(function (a, b) { return a.cost - b.cost; });
    if (rewards.length) {
      var ready = rewards.filter(function (r) { return sc.net >= r.cost; });
      var next = rewards.filter(function (r) { return sc.net < r.cost; })[0];
      if (ready.length) {
        var r = ready[ready.length - 1];
        out += '<div class="gauge"><div class="gauge-top"><span>보상 <b>' + esc(r.name) + '</b></span><span class="g-num">' + r.cost + '점</span></div>' +
          '<div class="gtrack"><div class="gfill reward" style="width:100%"></div></div>' +
          '<div class="gauge-note ok">🎁 지금 받을 수 있어요! (엄마 관리에서 사용)</div></div>';
      } else if (next) {
        var pct = clampNum(Math.round(sc.net / next.cost * 100), 0, 100, 0);
        out += '<div class="gauge"><div class="gauge-top"><span>다음 보상 <b>' + esc(next.name) + '</b></span><span class="g-num">' + sc.net + ' / ' + next.cost + '점</span></div>' +
          '<div class="gtrack"><div class="gfill reward" style="width:' + pct + '%"></div></div>' +
          '<div class="gauge-note">' + (next.cost - sc.net) + '점 더 모으면 받을 수 있어요</div></div>';
      }
    } else {
      out += '<div class="gauge"><div class="gauge-note">보상 조건을 엄마 관리에서 추가해 보세요</div></div>';
    }
    var rules = (st().penaltyRules || []).slice().sort(function (a, b) { return a.threshold - b.threshold; });
    if (rules.length) {
      var hit = rules.filter(function (r) { return sc.penalty >= r.threshold; });
      var near = rules.filter(function (r) { return sc.penalty < r.threshold; })[0];
      if (hit.length) {
        var h = hit[hit.length - 1];
        out += '<div class="gauge"><div class="gauge-top"><span>벌점 <b>' + esc(h.name) + '</b></span><span class="g-num">' + sc.penalty + ' / ' + h.threshold + '점</span></div>' +
          '<div class="gtrack"><div class="gfill warn" style="width:100%"></div></div>' +
          '<div class="gauge-note bad">⚠️ 벌점 조건에 도달했어요</div></div>';
      } else if (near) {
        var p2 = clampNum(Math.round(sc.penalty / near.threshold * 100), 0, 100, 0);
        out += '<div class="gauge"><div class="gauge-top"><span>벌점 경고 <b>' + esc(near.name) + '</b></span><span class="g-num">' + sc.penalty + ' / ' + near.threshold + '점</span></div>' +
          '<div class="gtrack"><div class="gfill warn" style="width:' + p2 + '%"></div></div>' +
          '<div class="gauge-note">' + (near.threshold - sc.penalty) + '점 더 쌓이면 ' + esc(near.name) + '</div></div>';
      }
    } else {
      out += '<div class="gauge"><div class="gauge-note">벌 조건을 엄마 관리에서 추가해 보세요</div></div>';
    }
    return out;
  }

  function graphCardHTML(child, sc) {
    var stats = periodStats(child, ui.period);
    var r = stats.range;
    var tt = timeTotals(child, r.start, r.end);
    var sDone = studyDoneCount(child, r.start, r.end);
    var freeMin = periodFree(child, ui.period);
    var rows = stats.rows;
    var bars = rows.length ? rows.map(function (row) {
      var pct = row.expected ? clampNum(Math.round(row.done / row.expected * 100), 0, 100, 0) : 0;
      return '<div class="bar"><div class="bar-top"><span class="bar-name"><i class="dot" style="background:' + row.item.color + '"></i>' + esc(row.item.name) + '</span>' +
        '<span class="bar-val' + (pct >= 100 ? ' full' : '') + '">' + row.done + '/' + row.expected + ' · ' + pct + '%</span></div>' +
        '<div class="btrack"><div class="bfill" style="width:' + pct + '%;background:' + row.item.color + '"></div></div></div>';
    }).join('') : '<div class="bar-empty">아직 배치된 항목이 없어요. 주간 시간표에서 공부를 배치해 보세요.</div>';

    var label = ui.period === 'day' ? '오늘' : (ui.period === 'week' ? '이번 주' : '이번 달');
    var note;
    if (ui.period === 'day') {
      note = '<b>' + label + '</b> · 공부 <b>' + sDone + '개</b> 완료 · 쉬는 시간 <b>' + minsLabel(tt.break) + '분</b> · 놀이 <b>' + minsLabel(tt.play) + '분</b>' +
        (tt.game ? ' · 게임 ' + minsLabel(tt.game) + '분' : '');
    } else {
      note = '<b>' + label + '</b> · 공부 <b>' + sDone + '개</b> · 쉬는 시간 <b>' + minsLabel(tt.break) + '분</b> (일평균 ' + minsLabel(tt.break / r.days) + '분) · ' +
        '놀이 <b>' + minsLabel(tt.play) + '분</b> (일평균 ' + minsLabel(tt.play / r.days) + '분)' + (tt.game ? ' · 게임 ' + minsLabel(tt.game) + '분' : '');
    }

    return '<div class="card graphcard"><div class="card-head"><h3>📊 공부별 달성 현황</h3>' +
      '<div class="seg"><button data-act="period" data-mode="day" class="' + (ui.period === 'day' ? 'on' : '') + '">일간</button>' +
      '<button data-act="period" data-mode="week" class="' + (ui.period === 'week' ? 'on' : '') + '">주간</button>' +
      '<button data-act="period" data-mode="month" class="' + (ui.period === 'month' ? 'on' : '') + '">월간</button></div></div>' +
      '<div class="bars">' + bars + '</div>' +
      '<div class="timesum"><div class="tsum-row">' +
      '<div class="tsum"><span>공부 완료</span><b>' + sDone + '<small>개</small></b></div>' +
      '<div class="tsum"><span>쉬는 시간</span><b>' + minsLabel(tt.break) + '<small>분</small></b></div>' +
      '<div class="tsum"><span>놀이</span><b>' + minsLabel(tt.play) + '<small>분</small></b></div>' +
      '<div class="tsum"><span>빈 시간</span><b>' + freeMin + '<small>분</small></b></div>' +
      '</div><div class="tsum-note">' + note + '</div></div></div>';
  }

  function timelineCardHTML(child, day, tl, free, schedMin, settled) {
    var span = Math.max(1, tl.bed - tl.out);
    var bar = tl.segs.map(function (s) {
      var w = (s.end - s.start) / span * 100;
      var pct = Math.max(0, Math.min(100, w));
      var past = s.end <= nowMin() ? ' past' : '';
      var label = s.type === 'fixed' || w > 14 ? (s.type === 'free' ? '빈 시간' : s.name) : '';
      return '<div class="tl-seg ' + s.type + past + '" style="width:' + pct + '%;flex:none" title="' + esc(hhmm(s.start) + '~' + hhmm(s.end) + ' ' + (s.type === 'free' ? '빈 시간' : s.name)) + '">' + esc(label) + '</div>';
    }).join('');
    var gaps = tl.segs.filter(function (s) { return s.type === 'free'; });
    var gapHtml = gaps.length ? gaps.map(function (g) {
      var past = g.end <= nowMin();
      var remain = Math.max(0, g.end - Math.max(nowMin(), g.start));
      return '<div class="gap' + (past ? ' past' : '') + '"><span class="g-time">' + hhmm(g.start) + ' ~ ' + hhmm(g.end) + '</span>' +
        '<span class="g-mins">' + (past ? '지난 시간' : '비어 있어요 · ' + remain + '분') + '</span></div>';
    }).join('') : '<div class="gap-none">오늘은 빈 시간이 없어요.</div>';

    var total = free.total;
    var diff = total - schedMin;
    var foot = schedMin
      ? ('빈 시간 <b>' + total + '분</b> · 공부 <b>' + schedMin + '분</b> 배치 → ' +
        (diff >= 0 ? '<b class="ok">' + diff + '분 여유</b>' : '<b class="over">⚠️ ' + Math.abs(diff) + '분 초과!</b>'))
      : ('빈 시간 <b>' + total + '분</b> · 공부 배치 없음');

    return '<div class="card timelinecard"><div class="card-head"><h3>🕒 방과 후 빈 시간</h3>' +
      '<span class="badge' + (free.rem <= 0 ? ' over' : '') + '">' + (free.rem > 0 ? '오늘 ' + free.rem + '분 남음' : '오늘 빈 시간 종료') + '</span></div>' +
      '<div class="tl">' + bar + '</div>' +
      '<div class="tl-scale"><span>' + hhmm(tl.out) + ' 하교</span><span>' + hhmm(tl.bed) + ' 취침</span></div>' +
      '<div class="gaps">' + gapHtml + '</div>' +
      '<div class="tl-foot">' + foot + '</div></div>';
  }

  /* ---------------- dock / clock text ---------------- */
  function clockText(t) {
    var el = (Date.now() - t.startedAt) / 1000;
    if (t.mode === 'stopwatch') {
      var s = Math.max(0, Math.floor(el));
      return pad2(Math.floor(s / 60)) + ':' + pad2(s % 60);
    }
    var rem = Math.ceil(t.targetSec - el);
    if (rem >= 0) return pad2(Math.floor(rem / 60)) + ':' + pad2(rem % 60);
    var over = Math.abs(rem);
    return '+' + pad2(Math.floor(over / 60)) + ':' + pad2(over % 60);
  }
  function clockTextShort(t) {
    var el = (Date.now() - t.startedAt) / 1000;
    if (t.mode === 'stopwatch') {
      var s = Math.max(0, Math.floor(el));
      return pad2(Math.floor(s / 60)) + ':' + pad2(s % 60);
    }
    var rem = Math.ceil(t.targetSec - el);
    if (rem >= 0) return pad2(Math.floor(rem / 60)) + ':' + pad2(rem % 60);
    return pad2(Math.floor(Math.abs(rem) / 60)) + ':' + pad2(Math.abs(rem) % 60);
  }

  /* ---------------- WEEK VIEW ---------------- */
  function poolChip(it, sched) {
    var days = D.filter(function (d) { return (sched[d] || []).indexOf(it.id) >= 0; });
    var daysLabel = days.length ? (days.length > 3 ? days.length + '일' : days.map(function (d) { return DL[d]; }).join('')) : '';
    var buttons = it.kind === 'routine'
      ? '<button class="chip-b" data-act="editItem" data-id="' + it.id + '" aria-label="수정">✎</button>'
      : '<button class="chip-b" data-act="days" data-id="' + it.id + '" aria-label="요일 선택">＋</button><button class="chip-b" data-act="editItem" data-id="' + it.id + '" aria-label="수정">✎</button>';
    return '<div class="chip" style="--c:' + it.color + '" data-drag-item="' + it.id + '" data-drag-from="pool"' + (it.kind === 'routine' ? ' data-nodrag="1"' : '') + '>' +
      '<span class="chip-e">' + esc(it.emoji || '') + '</span>' +
      '<span class="chip-n">' + esc(it.name) + '</span>' +
      (it.minutes ? '<span class="chip-dur">' + it.minutes + '분</span>' : '') +
      (daysLabel ? '<span class="chip-days">' + daysLabel + '</span>' : '') + buttons + '</div>';
  }
  function dayChip(it, dk) {
    return '<div class="chip" style="--c:' + it.color + '" data-drag-item="' + it.id + '" data-drag-from="' + dk + '">' +
      '<span class="chip-e">' + esc(it.emoji || '') + '</span>' +
      '<span class="chip-n">' + esc(it.name) + '</span>' +
      (it.minutes ? '<span class="chip-dur">' + it.minutes + '분</span>' : '') +
      '<button class="chip-b" data-act="unassign" data-id="' + it.id + '" data-day="' + dk + '" aria-label="빼기">×</button></div>';
  }

  function renderWeek() {
    var s = st(), child = curChild(), ch = childOf(child);
    var sched = s.schedule[child] || {};
    var pool = { routine: [], study: [], promise: [] };
    s.items.forEach(function (it) { (pool[it.kind] || pool.study).push(it); });

    var html = '<div class="weektop">';
    html += '<div class="card poolcard">';
    html += '<div class="card-head"><h3>🧩 공부·루틴 리스트</h3>' +
      '<div class="mrow"><button class="mini" data-act="addRoutine">+ 생활 루틴</button><button class="mini good" data-act="addItem">+ 새 항목</button></div></div>';
    html += '<div class="weektip" style="margin-bottom:8px">카드를 요일 칸으로 <b>끌어다 놓으면</b> 배치돼요. 태블릿에서는 카드의 <b>＋</b> 버튼으로 요일을 골라도 됩니다.</div>';
    html += '<div class="pool-group"><div class="pool-label">🌅 생활 루틴 <em>· 매일 자동 표시</em></div><div class="pool">' +
      (pool.routine.length ? pool.routine.map(function (it) { return poolChip(it, sched); }).join('') : '<span class="empty">없음</span>') + '</div></div>';
    html += '<div class="pool-group"><div class="pool-label">📚 공부 리스트 <em>· 요일별로 배치</em></div><div class="pool">' +
      (pool.study.length ? pool.study.map(function (it) { return poolChip(it, sched); }).join('') : '<span class="empty">없음</span>') + '</div></div>';
    html += '<div class="pool-group"><div class="pool-label">🤝 엄마와의 약속 <em>· 요일별로 배치</em></div><div class="pool">' +
      (pool.promise.length ? pool.promise.map(function (it) { return poolChip(it, sched); }).join('') : '<span class="empty">없음</span>') + '</div></div>';
    html += '</div>';
    html += planQuickCardHTML(child);
    html += '</div>';
    html += weekGridHTML(child);
    $('#view-week').innerHTML = html;
  }

  function planQuickCardHTML(child) {
    var rows = D.map(function (dk) {
      var p = planFor(child, dk);
      var blockNames = p.blocks.map(function (b) { return esc(b.name); }).join(', ');
      return '<div class="dprow"><span class="dname">' + DL[dk] + '</span>' +
        '<input type="text" inputmode="numeric" maxlength="5" data-plan="out" data-day="' + dk + '" value="' + p.out + '" aria-label="' + DL[dk] + '요일 하교 시각">' +
        '<input type="text" inputmode="numeric" maxlength="5" data-plan="bed" data-day="' + dk + '" value="' + p.bed + '" aria-label="' + DL[dk] + '요일 취침 시각">' +
        '<span class="dpb" title="' + esc(p.blocks.map(function (b) { return b.name + ' ' + b.start + '~' + b.end; }).join(', ')) + '">' + (blockNames || '고정 일정 없음') + '</span>' +
        '<button class="mini" data-act="planDay" data-day="' + dk + '">일정</button></div>';
    }).join('');
    return '<div class="card"><div class="card-head"><h3>🏫 하교 · 고정 일정</h3><span class="sub">비는 시간 자동 계산</span></div>' +
      '<div class="dayplanrows">' + rows + '</div>' +
      '<div class="momnote" style="margin-top:6px">하교 → 취침 사이에서 학원·태권도 시간을 뺀 나머지가 ‘빈 시간’이에요.</div></div>';
  }

  function weekGridHTML(child) {
    var s = st(), sched = s.schedule[child] || {};
    var todayKey = U.dayKeyOf(U.today());
    var off = (U.parseYmd(U.today()).getDay() + 6) % 7;
    var weekStart = U.addDays(U.today(), -off);
    var weekFree = 0, weekStudy = 0;
    var cols = D.map(function (dk, idx) {
      var date = U.addDays(weekStart, idx);
      var ids = (sched[dk] || []).filter(function (id) { return !!itemById(id); });
      var mins = scheduledStudyMinutes(child, dk);
      var free = freeTotal(child, dk);
      weekFree += free; weekStudy += mins;
      var over = mins > free;
      var tl = timelineSegments(child, dk);
      var span = Math.max(1, tl.bed - tl.out);
      var mini = tl.segs.map(function (sg) {
        var w = (sg.end - sg.start) / span * 100;
        return '<div class="dp-seg ' + sg.type + '" style="width:' + Math.max(0, Math.min(100, w)) + '%"></div>';
      }).join('');
      var chips = ids.length ? ids.map(function (id) { return dayChip(itemById(id), dk); }).join('') : '<div class="day-empty">카드를 끌어다<br>놓아 보세요</div>';
      return '<div class="daycol' + (dk === todayKey ? ' today' : '') + '" data-drop="' + dk + '" data-day="' + dk + '">' +
        '<div class="dayhead"><span class="dow">' + DL[dk] + '</span><span style="font-size:10px;color:#B7AD9C;font-weight:800">' + U.parseYmd(date).getDate() + '</span><span class="dcount">' + ids.length + '</span></div>' +
        '<div class="dayplan-mini" title="초록=빈 시간, 보라=고정 일정">' + mini + '</div>' +
        '<div class="daymeta' + (over ? ' over' : '') + '">빈 <b>' + free + '분</b> · 공부 <b>' + mins + '분</b>' + (over ? ' ⚠️초과' : '') + '</div>' +
        '<div class="daychips">' + chips + '</div>' +
        '<button class="dayadd" data-act="dayAdd" data-day="' + dk + '">+ 추가</button></div>';
    }).join('');
    return '<div class="card weekcard"><div class="card-head"><h3>🗓️ ' + esc(childOf(child).name) + ' 주간 시간표</h3>' +
      '<div class="weeksum"><span>이번 주 빈 시간 <b>' + weekFree + '분</b></span><span>공부 배치 <b>' + weekStudy + '분</b></span></div></div>' +
      '<div class="weekgrid">' + cols + '</div>' +
      '<div class="weektip" style="margin-top:6px">칩을 다른 요일로 끌어 옮길 수 있어요 · <b>×</b> 그 요일에서 빼기 · <b>✎</b> 항목 수정</div></div>';
  }

  /* ---------------- MOM VIEW ---------------- */
  function momStatusCard() {
    var html = '<div class="card mcard"><h3>👀 아이들 오늘 현황</h3><div class="hint">아이가 체크하면 이 화면에 바로 반영돼요.</div>';
    ['1', '2'].forEach(function (c) {
      var ch = childOf(c), p = dayProgress(c, U.today()), sc = score(c), pen = pendingPenalty(c, U.today());
      html += '<div class="kidrow"><div class="kr-top"><b>' + esc(ch.emoji + ' ' + ch.name) + '</b>' +
        '<span>' + p.done + '/' + p.count + ' 완료 · 순점수 ' + sc.net + '점</span></div>' +
        '<div class="btrack"><div class="bfill" style="width:' + p.pct + '%;background:' + (p.pct >= 80 ? '#12B886' : '#FFB547') + '"></div></div>' +
        '<div class="kr-sub">상점 ' + sc.earned + ' · 벌점 ' + sc.penalty + ' · 지금 정산하면 −' + pen + '점</div></div>';
    });
    return html + '</div>';
  }
  function momManualCard() {
    var child = curChild(), ch = childOf(child);
    return '<div class="card mcard"><h3>✍️ 상·벌점 직접 주기</h3>' +
      '<div class="hint">대상: ' + esc(ch.emoji + ' ' + ch.name) + ' (위쪽 아이 탭에서 바꿔요)</div>' +
      '<div class="mrow"><div class="field" style="max-width:96px"><label>점수</label><input type="number" id="manualAmt" value="3" min="1" max="999"></div>' +
      '<div class="field"><label>이유 (선택)</label><input type="text" id="manualWhy" placeholder="예: 숙제 스스로 함"></div></div>' +
      '<div class="mrow"><button class="btn good" data-act="manualPlus">+ 상점 주기</button><button class="btn bad" data-act="manualMinus">− 벌점 주기</button></div>' +
      '<div class="mrow"><button class="mini" data-act="addReward">+ 보상 조건</button><button class="mini" data-act="addRule">+ 벌 조건</button>' +
      '<button class="mini" data-act="view" data-view="week">항목·약속 관리</button></div></div>';
  }
  function momRewardsCard() {
    var child = curChild(), sc = score(child);
    var rewards = (st().rewards || []).slice().sort(function (a, b) { return a.cost - b.cost; });
    var list = rewards.length ? rewards.map(function (r) {
      var can = sc.net >= r.cost;
      return '<div class="lrow"><span class="grow">' + esc(r.name) + ' <span class="sub">' + r.cost + '점</span></span>' +
        '<button class="btn' + (can ? ' good' : '') + '" data-act="redeem" data-id="' + r.id + '" ' + (can ? '' : 'disabled') + '>사용</button>' +
        '<button class="x" data-act="editReward" data-id="' + r.id + '" aria-label="수정">✎</button>' +
        '<button class="x" data-act="delReward" data-id="' + r.id + '" aria-label="삭제">×</button></div>';
    }).join('') : '<div class="empty">보상 조건이 없어요.</div>';
    return '<div class="card mcard"><h3>🎁 보상 조건</h3>' +
      '<div class="hint">순점수가 조건을 넘으면 아이 화면 게이지가 가득 차요. ‘사용’을 누르면 점수가 차감됩니다. (현재 ' + sc.net + '점)</div>' +
      '<div class="list">' + list + '</div>' +
      '<div class="mrow" style="margin-top:8px"><div class="field"><label>보상 이름</label><input type="text" id="rewardName" placeholder="주말 치킨"></div>' +
      '<div class="field" style="max-width:90px"><label>필요 점수</label><input type="number" id="rewardCost" value="30" min="1"></div>' +
      '<button class="btn good" data-act="addRewardSubmit">추가</button></div></div>';
  }
  function momRulesCard() {
    var sc = score(curChild());
    var rules = (st().penaltyRules || []).slice().sort(function (a, b) { return a.threshold - b.threshold; });
    var list = rules.length ? rules.map(function (r) {
      return '<div class="lrow"><span class="grow">' + esc(r.name) + ' <span class="sub">벌점 ' + r.threshold + '점</span></span>' +
        '<button class="x" data-act="editRule" data-id="' + r.id + '" aria-label="수정">✎</button>' +
        '<button class="x" data-act="delRule" data-id="' + r.id + '" aria-label="삭제">×</button></div>';
    }).join('') : '<div class="empty">벌 조건이 없어요.</div>';
    return '<div class="card mcard"><h3>⚠️ 벌 조건</h3>' +
      '<div class="hint">벌점이 기준을 넘으면 아이 화면에 빨간 경고가 가득 차요. (현재 벌점 ' + sc.penalty + '점)</div>' +
      '<div class="list">' + list + '</div>' +
      '<div class="mrow" style="margin-top:8px"><div class="field"><label>벌 조건 이름</label><input type="text" id="ruleName" placeholder="주말 유튜브 금지"></div>' +
      '<div class="field" style="max-width:90px"><label>벌점 기준</label><input type="number" id="ruleThreshold" value="10" min="1"></div>' +
      '<button class="btn bad" data-act="addRuleSubmit">추가</button></div></div>';
  }
  function momSettleCard() {
    var today = U.today();
    var html = '<div class="card mcard"><h3>🧾 마감 정산</h3>' +
      '<div class="hint">자정이 지나면 어제 미완료 항목이 자동으로 벌점 처리돼요. 같은 날은 두 번 정산되지 않아요.</div>';
    ['1', '2'].forEach(function (c) {
      var ch = childOf(c), settled = st().settled[c] && st().settled[c][today];
      html += '<div class="lrow"><span class="grow">' + esc(ch.emoji + ' ' + ch.name) + ' <span class="sub">오늘 미완료 ' + pendingCount(c, today) + '개</span></span>' +
        '<span class="chip-status ' + (settled ? 'warn' : '') + '">' + (settled ? '정산 −' + settled.penalty + '점' : '예상 −' + pendingPenalty(c, today) + '점') + '</span></div>';
    });
    html += '<button class="btn gold wide" data-act="settleToday" style="margin-top:8px">오늘 마감 정산하기 (테스트)</button>';
    var hist = [];
    ['1', '2'].forEach(function (c) {
      var map = st().settled[c] || {};
      Object.keys(map).sort().reverse().slice(0, 4).forEach(function (d) {
        hist.push({ child: c, date: d, info: map[d] });
      });
    });
    hist.sort(function (a, b) { return a.date < b.date ? 1 : -1; });
    html += '<div class="hint" style="margin-top:8px">정산 이력</div><div class="list">' + (hist.length ? hist.slice(0, 8).map(function (h) {
      return '<div class="lrow"><span class="grow">' + esc(childOf(h.child).name) + ' · ' + h.date.slice(5).replace('-', '/') + ' <span class="sub">미완료 ' + (h.info.missed ? h.info.missed.length : 0) + '개</span></span>' +
        '<span class="num">−' + h.info.penalty + '점</span>' +
        '<button class="x" data-act="undoSettle" data-child="' + h.child + '" data-date="' + h.date + '" aria-label="되돌리기">↺</button></div>';
    }).join('') : '<div class="empty">아직 정산 기록이 없어요.</div>') + '</div></div>';
    return html;
  }
  function momAlarmCard() {
    var s = st().settings;
    return '<div class="card mcard"><h3>⏰ 기상 · 취침 알람</h3>' +
      '<div class="hint">앱이 켜져 있는 동안 그 시각에 알람이 울려요.</div>' +
      '<div class="mrow"><label style="display:flex;align-items:center;gap:5px;font-size:12px;font-weight:800"><input type="checkbox" data-set="wake-on" ' + (s.wake.enabled ? 'checked' : '') + '> 기상</label>' +
      '<input type="time" data-set="wake-time" value="' + (s.wake.time || '07:30') + '" style="max-width:110px">' +
      '<label style="display:flex;align-items:center;gap:5px;font-size:12px;font-weight:800;margin-left:6px"><input type="checkbox" data-set="sleep-on" ' + (s.sleep.enabled ? 'checked' : '') + '> 취침</label>' +
      '<input type="time" data-set="sleep-time" value="' + (s.sleep.time || '21:30') + '" style="max-width:110px"></div>' +
      '<div class="mrow"><button class="btn" data-act="wakeLock">💡 화면 켜짐 유지 ' + (wakeLock.sentinel ? 'ON' : 'OFF') + '</button>' +
      '<button class="btn" data-act="testAlarm">🔔 소리 테스트</button>' +
      '<label style="display:flex;align-items:center;gap:5px;font-size:12px;font-weight:800"><input type="checkbox" data-set="sound" ' + (s.sound ? 'checked' : '') + '> 알람 소리</label></div>' +
      '<div class="momnote" style="margin-top:6px">※ 앱 화면이 켜져 있을 때만 알람이 울립니다. 기상 알람은 기기 시계 앱도 함께 맞춰 두세요.</div></div>';
  }
  function momPlanCard() {
    var child = curChild();
    var rows = D.map(function (dk) {
      var p = planFor(child, dk);
      return '<div class="dprow"><span class="dname">' + DL[dk] + '</span>' +
        '<input type="text" inputmode="numeric" maxlength="5" data-plan="out" data-day="' + dk + '" value="' + p.out + '" aria-label="하교">' +
        '<input type="text" inputmode="numeric" maxlength="5" data-plan="bed" data-day="' + dk + '" value="' + p.bed + '" aria-label="취침">' +
        '<span class="dpb" title="' + esc(p.blocks.map(function (b) { return b.name + ' ' + b.start + '~' + b.end; }).join(', ')) + '">' + (p.blocks.map(function (b) { return esc(b.name); }).join(', ') || '고정 일정 없음') + '</span>' +
        '<button class="mini" data-act="planDay" data-day="' + dk + '">일정</button></div>';
    }).join('');
    return '<div class="card mcard"><h3>🏫 방과 후 시간</h3><div class="hint">하교·취침·학원 시간을 넣으면 빈 시간이 자동 계산돼요.</div>' +
      '<div class="dayplanrows">' + rows + '</div></div>';
  }
  function momKidsCard() {
    var a = st().children['1'], b = st().children['2'];
    return '<div class="card mcard"><h3>👧 아이 설정</h3><div class="hint">이름과 학년을 바꿀 수 있어요.</div>' +
      '<div class="mrow"><div class="field"><label>첫째 이름</label><input type="text" id="name1" value="' + esc(a.name) + '"></div>' +
      '<div class="field" style="max-width:80px"><label>학년</label><input type="text" id="grade1" value="' + esc(a.grade || '') + '"></div></div>' +
      '<div class="mrow"><div class="field"><label>둘째 이름</label><input type="text" id="name2" value="' + esc(b.name) + '"></div>' +
      '<div class="field" style="max-width:80px"><label>학년</label><input type="text" id="grade2" value="' + esc(b.grade || '') + '"></div></div>' +
      '<div class="mrow"><button class="btn primary" data-act="saveNames">이름 저장</button>' +
      '<button class="btn" data-act="pinChange">🔒 PIN 변경</button></div></div>';
  }
  function momDataCard() {
    var s = st().settings;
    return '<div class="card mcard"><h3>☁️ 동기화 · 백업</h3>' +
      '<div class="hint">지금은 이 기기에 저장 중이에요. Firebase 주소를 넣으면 엄마 폰과 아이 태블릿이 실시간으로 같은 기록을 봐요.</div>' +
      '<div class="lrow"><span class="grow">동기화 상태</span><span class="chip-status ' + (s.syncEnabled ? 'ok' : '') + '">' + (s.syncEnabled ? (Sync.status === 'ok' ? '연결됨' : '연결 시도 중') : '로컬 저장') + '</span></div>' +
      '<div class="mrow" style="margin-top:6px"><button class="btn" data-act="shareSetup">📤 아이 태블릿으로 보내기</button>' +
      '<button class="btn" data-act="sync">☁️ 동기화 설정</button></div>' +
      '<div class="mrow"><button class="btn" data-act="export">⬇️ 백업 내보내기</button><button class="btn" data-act="import">⬆️ 가져오기</button>' +
      '<button class="btn bad" data-act="reset">데이터 초기화</button></div>' +
      '<div class="momnote" style="margin-top:6px">데이터 초기화는 모든 체크·점수 기록을 지웁니다. 먼저 백업해 두세요.</div></div>';
  }
  function renderMom() {
    var child = curChild(), ch = childOf(child);
    var html = '<div class="momhead"><div><h2>엄마 관리</h2><div class="who">' + esc(ch.emoji + ' ' + ch.name) + ' 기준 · 위쪽 아이 탭에서 대상을 바꿀 수 있어요</div></div>' +
      '<div class="mrow"><button class="btn gold" data-act="shareSetup">📤 아이 태블릿으로 보내기</button>' +
      '<button class="btn" data-act="lock">🔒 다시 잠그기</button></div></div>';
    html += '<div class="mgrid">';
    html += momStatusCard() + momManualCard() + momRewardsCard() + momRulesCard() + momSettleCard() + momAlarmCard() + momPlanCard() + momKidsCard() + momDataCard();
    html += '</div>';
    $('#view-mom').innerHTML = html;
  }

  /* ---------------- interactions ---------------- */
  function setView(v) {
    if (v === 'mom' && !ui.unlocked) { openPin(); return; }
    ui.view = v; st().ui.view = v; Store.save({ quiet: true }); render();
  }
  function setChild(id) {
    if (id !== '1' && id !== '2') return;
    st().ui.child = id; Store.save({ quiet: true }); render();
  }
  function setPeriod(mode) {
    ui.period = mode; st().ui.period = mode; Store.save({ quiet: true }); render();
  }

  function onToggle(itemId, date) {
    date = date || U.today();
    var s = st(), child = curChild(), it = itemById(itemId);
    if (!it) return;
    if (s.settled[child] && s.settled[child][date]) { toast('오늘은 마감 정산되어 수정할 수 없어요. 엄마 관리에서 되돌릴 수 있어요.'); return; }
    if (it.timer === 'stopwatch') { toggleStopwatch(it); return; }
    var day = ensureDay(child, date);
    if (day[itemId]) {
      delete day[itemId];
      logAdd({ child: child, type: 'cancel', date: date, amount: it.reward, item: itemId });
      save(); toast(it.name + ' 체크 취소'); return;
    }
    day[itemId] = { ts: Date.now(), points: it.reward };
    logAdd({ child: child, type: 'earn', date: date, amount: it.reward, item: itemId });
    Sound.unlock(); Sound.chime(); fx(itemId, it.reward);
    save();
  }
  function completeSilently(itemId) {
    var s = st(), child = curChild(), today = U.today(), it = itemById(itemId);
    if (!it) return false;
    if (s.settled[child] && s.settled[child][today]) return false;
    var day = ensureDay(child, today);
    if (day[itemId]) return false;
    day[itemId] = { ts: Date.now(), points: it.reward };
    logAdd({ child: child, type: 'earn', date: today, amount: it.reward, item: itemId });
    return true;
  }
  function toggleStopwatch(it) {
    var key = 'item:' + it.id;
    if (timers[key]) { finishTimer(key, 'manual'); return; }
    startTimer(key, { kind: 'play', mode: 'stopwatch', label: it.name, emoji: it.emoji || '🧸', itemId: it.id, targetSec: (it.minutes || 30) * 60 });
    Sound.unlock();
    toast('🧸 ' + it.name + ' 시작! 끝나면 다시 눌러 주세요');
  }
  function onTimerBtn(itemId) {
    var it = itemById(itemId);
    if (!it) return;
    var key = 'item:' + itemId;
    if (timers[key]) { finishTimer(key, 'manual'); return; }
    if (it.timer === 'countdown') {
      startTimer(key, { kind: 'game', mode: 'countdown', label: it.name, emoji: it.emoji || '⏱', itemId: itemId, targetSec: (it.minutes || 30) * 60 });
      Sound.unlock(); toast('⏱ ' + it.name + ' ' + (it.minutes || 30) + '분 시작');
    } else {
      startTimer(key, { kind: 'play', mode: 'stopwatch', label: it.name, emoji: it.emoji || '🧸', itemId: itemId, targetSec: (it.minutes || 30) * 60 });
      Sound.unlock();
    }
  }
  function startBreak() {
    if (timers['break']) { toast('이미 쉬는 중이에요'); return; }
    try { sessionStorage.setItem('breakDismiss', String(studyDoneOn(curChild(), U.today()))); } catch (e) { }
    startTimer('break', { kind: 'break', mode: 'countdown', label: '10분 쉬는 시간', emoji: '☕', targetSec: 600 });
    Sound.unlock(); toast('☕ 10분 쉬는 시간 시작!');
  }
  function manual(sign) {
    var amtEl = $('#manualAmt');
    if (!amtEl) return;
    var amt = clampNum(amtEl.value, 1, 999, 3);
    var whyEl = $('#manualWhy');
    var why = whyEl ? whyEl.value.trim() : '';
    var child = curChild();
    if (sign > 0) logAdd({ child: child, type: 'manual_plus', date: U.today(), amount: amt, note: why });
    else logAdd({ child: child, type: 'penalty', date: U.today(), amount: amt, note: why, manual: true });
    save();
    toast((sign > 0 ? '상점 +' : '벌점 −') + amt + '점 · ' + childOf(child).name + (why ? ' (' + why + ')' : ''));
    if (whyEl) whyEl.value = '';
  }
  function redeem(id) {
    var r = (st().rewards || []).filter(function (x) { return x.id === id; })[0];
    if (!r) return;
    var sc = score(curChild());
    if (sc.net < r.cost) { toast('순점수가 부족해요 (' + sc.net + '/' + r.cost + '점)'); return; }
    openConfirm('보상 사용', '<b>' + esc(r.name) + '</b> (' + r.cost + '점)을 사용 처리할까요? 순점수에서 ' + r.cost + '점이 차감돼요.', '사용하기', function () {
      logAdd({ child: curChild(), type: 'redeem', amount: r.cost, note: r.name });
      save(); toast('🎁 ' + r.name + ' 사용 완료');
    });
  }

  /* ---------------- timers ---------------- */
  function startTimer(key, cfg) {
    timers[key] = {
      key: key, kind: cfg.kind, mode: cfg.mode, label: cfg.label, emoji: cfg.emoji || '⏱',
      startedAt: Date.now(), targetSec: cfg.targetSec || 0, itemId: cfg.itemId || '', child: curChild()
    };
    persistTimers(); render();
  }
  function finishTimer(key, how) {
    var t = timers[key];
    if (!t) return;
    var mins = Math.max(0, (Date.now() - t.startedAt) / 60000);
    delete timers[key]; persistTimers();
    if (mins > 0.05) addTime(t.kind, mins, t.itemId);
    if (t.kind === 'play' && mins > 0.05) completeSilently(t.itemId);
    if (t.kind === 'game' && how === 'auto') completeSilently(t.itemId);
    if (t.kind === 'break' && how === 'auto') {
      fireAlarm({ icon: '☕', title: '쉬는 시간 끝!', sub: '10분 푹 쉬었어요. 다시 공부 시작해 볼까요?' });
    } else if (t.kind === 'game' && how === 'auto') {
      fireAlarm({ icon: '🎮', title: '게임 시간 끝!', sub: '오늘 게임 ' + Math.round(mins) + '분 했어요. 이제 다음 할 일로 가 볼까요?' });
    } else if (mins > 0.05) {
      toast((t.kind === 'break' ? '☕ 쉬는 시간 ' : t.kind === 'play' ? '🧸 놀이시간 ' : '🎮 게임 ') + minsLabel(mins) + '분 기록했어요');
    }
    save();
  }
  function tickTimers() {
    Object.keys(timers).forEach(function (k) {
      var t = timers[k];
      if (t.mode === 'countdown') {
        var el = (Date.now() - t.startedAt) / 1000;
        if (el >= t.targetSec) finishTimer(k, 'auto');
      }
    });
  }
  function clockText(t) {
    var el = (Date.now() - t.startedAt) / 1000;
    if (t.mode === 'stopwatch') {
      var s = Math.max(0, Math.floor(el));
      return pad2(Math.floor(s / 60)) + ':' + pad2(s % 60);
    }
    var rem = Math.ceil(t.targetSec - el);
    if (rem >= 0) return pad2(Math.floor(rem / 60)) + ':' + pad2(rem % 60);
    var over = Math.abs(rem);
    return '+' + pad2(Math.floor(over / 60)) + ':' + pad2(over % 60);
  }
  function clockTextShort(t) {
    var el = (Date.now() - t.startedAt) / 1000;
    if (t.mode === 'stopwatch') {
      var s = Math.max(0, Math.floor(el));
      return pad2(Math.floor(s / 60)) + ':' + pad2(s % 60);
    }
    var rem = Math.ceil(t.targetSec - el);
    if (rem >= 0) return pad2(Math.floor(rem / 60)) + ':' + pad2(rem % 60);
    return pad2(Math.floor(Math.abs(rem) / 60)) + ':' + pad2(Math.abs(rem) % 60);
  }
  function breakSuggestionActive() {
    var n = studyDoneOn(curChild(), U.today());
    var dismissed = '';
    try { dismissed = sessionStorage.getItem('breakDismiss') || ''; } catch (e) { }
    return n > 0 && n % 2 === 0 && !timers['break'] && String(n) !== dismissed;
  }
  function renderDock() {
    var dock = $('#dock');
    if (!dock) return;
    var keys = Object.keys(timers);
    var parts = [];
    if (breakSuggestionActive()) {
      var bn = studyDoneOn(curChild(), U.today());
      parts.push('<div class="timer suggest"><span class="t-emoji">☕</span>' +
        '<div class="t-main"><div class="t-label">공부 ' + bn + '개 완료!</div><div class="t-clock">10분 쉬는 시간</div></div>' +
        '<button class="t-stop" data-act="break">시작</button>' +
        '<button class="t-stop" data-act="breakDismiss" data-n="' + bn + '" aria-label="닫기">×</button></div>');
    }
    dock.innerHTML = parts.concat(keys.map(function (k) {
      var t = timers[k];
      var elapsed = (Date.now() - t.startedAt) / 1000;
      var over = t.targetSec > 0 && elapsed > t.targetSec;
      return '<div class="timer ' + (t.kind === 'play' ? 'play' : '') + '"><span class="t-emoji">' + esc(t.emoji) + '</span>' +
        '<div class="t-main"><div class="t-label">' + esc(t.label) + (t.kind === 'play' ? ' · 목표 ' + Math.round(t.targetSec / 60) + '분' : '') + '</div>' +
        '<div class="t-clock' + (over ? ' over' : '') + '" data-clock="' + k + '">' + clockText(t) + '</div></div>' +
        '<button class="t-stop" data-act="stopTimer" data-key="' + k + '">' + (t.mode === 'stopwatch' ? '끝' : '그만') + '</button></div>';
    }).join(''));
  }
  function updateDockClocks() {
    Object.keys(timers).forEach(function (k) {
      var t = timers[k];
      var el = document.querySelector('[data-clock="' + k + '"]');
      if (!el) return;
      el.textContent = clockText(t);
      var elapsed = (Date.now() - t.startedAt) / 1000;
      el.classList.toggle('over', t.targetSec > 0 && elapsed > t.targetSec);
    });
  }
  function persistTimers() {
    try { sessionStorage.setItem('rbTimers', JSON.stringify(timers)); } catch (e) { }
  }
  function restoreTimers() {
    try {
      var raw = sessionStorage.getItem('rbTimers');
      if (!raw) return;
      var obj = JSON.parse(raw);
      Object.keys(obj).forEach(function (k) {
        var t = obj[k];
        if (!t || !t.startedAt) return;
        var el = (Date.now() - t.startedAt) / 1000;
        if (t.mode === 'countdown' && t.targetSec && el > t.targetSec + 300) return;
        timers[k] = t;
      });
    } catch (e) { }
  }

  /* ---------------- sound ---------------- */
  var Sound = {
    ctx: null, _int: null,
    ensure: function () {
      if (!st().settings.sound) return null;
      try {
        if (!this.ctx) {
          var AC = window.AudioContext || window.webkitAudioContext;
          if (!AC) return null;
          this.ctx = new AC();
        }
        if (this.ctx.state === 'suspended') this.ctx.resume();
        return this.ctx;
      } catch (e) { return null; }
    },
    unlock: function () { this.ensure(); },
    note: function (freq, at, dur, type, vol) {
      var ctx = this.ensure();
      if (!ctx) return;
      var o = ctx.createOscillator(), g = ctx.createGain();
      o.type = type || 'sine'; o.frequency.value = freq;
      g.gain.setValueAtTime(0.0001, at);
      g.gain.linearRampToValueAtTime(vol || 0.16, at + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
      o.connect(g); g.connect(ctx.destination);
      o.start(at); o.stop(at + dur + 0.03);
    },
    chime: function () {
      var ctx = this.ensure();
      if (!ctx) return;
      var t = ctx.currentTime, self = this;
      [1046.5, 1318.5, 1568.0].forEach(function (f, i) { self.note(f, t + i * 0.16, 0.34, 'triangle', 0.15); });
    },
    alarmLoop: function () {
      if (!st().settings.sound) return;
      this.stopAlarm();
      this.chime();
      var self = this;
      this._int = setInterval(function () { self.chime(); }, 1600);
    },
    stopAlarm: function () { if (this._int) { clearInterval(this._int); this._int = null; } }
  };

  /* ---------------- alarm ---------------- */
  function fireAlarm(cfg) {
    if (alarm.active) return;
    alarm.active = true; alarm.cfg = cfg;
    Sound.alarmLoop();
    try { if (navigator.vibrate && (!navigator.userActivation || navigator.userActivation.hasBeenActive)) navigator.vibrate([300, 150, 300, 150, 600]); } catch (e) { }
    openModal('<div class="alarm-modal"><div class="alarm-icon">' + cfg.icon + '</div><h2>' + esc(cfg.title) + '</h2>' +
      '<div class="sh-sub">' + esc(cfg.sub) + '</div><button class="alarm-off" data-act="alarmOff">알람 끄기</button></div>', { lock: true });
  }
  function dismissAlarm() {
    Sound.stopAlarm();
    alarm.active = false; alarm.cfg = null;
    var root = $('#modalRoot');
    if (root) { root.dataset.lock = ''; root.innerHTML = ''; root.classList.remove('open'); }
    document.body.style.overflow = '';
  }
  function checkClockAlarms() {
    var s = st(), now = new Date();
    var hm = pad2(now.getHours()) + ':' + pad2(now.getMinutes());
    var today = U.today();
    s.settings.lastAlarms = s.settings.lastAlarms || {};
    if (s.settings.wake && s.settings.wake.enabled && s.settings.wake.time === hm && s.settings.lastAlarms.wake !== today) {
      s.settings.lastAlarms.wake = today; Store.write();
      fireAlarm({ icon: '🌅', title: '기상 시간이에요!', sub: '오늘도 좋은 아침! 이불 정리부터 시작해 볼까요?' });
      return;
    }
    if (s.settings.sleep && s.settings.sleep.enabled && s.settings.sleep.time === hm && s.settings.lastAlarms.sleep !== today) {
      s.settings.lastAlarms.sleep = today; Store.write();
      fireAlarm({ icon: '🌙', title: '취침 시간이에요!', sub: '내일을 위해 이제 자자. 양치하고 이불 속으로!' });
    }
  }
  function toggleWakeLock() {
    if (!('wakeLock' in navigator)) { toast('이 브라우저는 화면 켜짐 유지를 지원하지 않아요'); return; }
    if (wakeLock.sentinel) {
      wakeLock.want = false;
      try { wakeLock.sentinel.release(); } catch (e) { }
      wakeLock.sentinel = null; render(); toast('화면 켜짐 유지 껐어요'); return;
    }
    wakeLock.want = true;
    navigator.wakeLock.request('screen').then(function (s) {
      wakeLock.sentinel = s;
      s.addEventListener('release', function () { wakeLock.sentinel = null; renderHeader(); });
      render(); toast('💡 화면 켜짐 유지 켬 (앱이 앞에 있을 때만)');
    }).catch(function (e) { toast('화면 유지 실패: ' + (e && e.message ? e.message : '지원 안 됨')); });
  }

  /* ---------------- modals ---------------- */
  function openModal(html, opts) {
    var root = $('#modalRoot');
    root.innerHTML = '<div class="backdrop" data-act="closeModal"></div><div class="sheet' + (opts && opts.wide ? ' wide' : '') + '">' + html + '</div>';
    root.dataset.lock = (opts && opts.lock) ? '1' : '';
    root.classList.add('open');
    document.body.style.overflow = 'hidden';
  }
  function closeModal() {
    var root = $('#modalRoot');
    if (!root) return;
    if (root.dataset.lock === '1' && alarm.active) return;
    root.dataset.lock = '';
    root.classList.remove('open');
    root.innerHTML = '';
    document.body.style.overflow = '';
  }
  function openConfirm(title, text, okLabel, cb, danger) {
    ui.confirmCb = cb;
    openModal('<h2>' + esc(title) + '</h2><div class="sh-sub">' + text + '</div>' +
      '<div class="sheet-foot"><button class="btn" data-act="closeModal">취소</button>' +
      '<button class="btn ' + (danger ? 'bad' : 'primary') + '" data-act="confirmOk">' + esc(okLabel) + '</button></div>');
  }
  function confirmOk() {
    var cb = ui.confirmCb;
    ui.confirmCb = null;
    closeModal();
    if (cb) cb();
  }
  function openPin() {
    ui.pin = '';
    var keys = '';
    [1, 2, 3, 4, 5, 6, 7, 8, 9].forEach(function (n) { keys += '<button class="pinkey" data-act="pinKey" data-k="' + n + '">' + n + '</button>'; });
    keys += '<button class="pinkey" data-act="pinKey" data-k="clear">C</button><button class="pinkey" data-act="pinKey" data-k="0">0</button><button class="pinkey" data-act="pinKey" data-k="del">⌫</button>';
    openModal('<h2>🔒 엄마 관리</h2><div class="sh-sub">PIN 4자리를 입력해 주세요. (처음 기본값 8282)</div>' +
      '<div class="pin-dots" id="pinDots">' + pinDotsHTML(0) + '</div><div class="pin-msg" id="pinMsg"></div>' +
      '<div class="pinpad">' + keys + '</div><div class="sheet-foot"><button class="btn" data-act="closeModal">취소</button></div>');
  }
  function pinDotsHTML(n) {
    var s = '';
    for (var i = 0; i < 4; i++) s += '<span class="pin-dot' + (i < n ? ' on' : '') + '"></span>';
    return s;
  }
  function pinKey(k) {
    if (k === 'del') ui.pin = ui.pin.slice(0, -1);
    else if (k === 'clear') ui.pin = '';
    else if (ui.pin.length < 4) ui.pin += k;
    var dots = $('#pinDots'); if (dots) dots.innerHTML = pinDotsHTML(ui.pin.length);
    var msg = $('#pinMsg'); if (msg) msg.textContent = '';
    if (ui.pin.length === 4) {
      if (ui.pin === st().pin) {
        ui.unlocked = true;
        try { sessionStorage.setItem('rbUnlocked', '1'); } catch (e) { }
        closeModal();
        ui.view = 'mom'; st().ui.view = 'mom'; Store.save({ quiet: true }); render();
        toast('엄마 관리 모드 열림');
      } else {
        var sheet = $('.sheet');
        if (sheet) { sheet.classList.remove('shake'); void sheet.offsetWidth; sheet.classList.add('shake'); }
        if (msg) msg.textContent = 'PIN이 맞지 않아요. 다시 입력해 주세요.';
        ui.pin = ''; if (dots) dots.innerHTML = pinDotsHTML(0);
      }
    }
  }
  function lockMom() {
    ui.unlocked = false;
    try { sessionStorage.removeItem('rbUnlocked'); } catch (e) { }
    ui.view = 'today'; st().ui.view = 'today';
    Store.save({ quiet: true }); render();
    toast('엄마 모드를 잠갔어요');
  }

  /* ---------------- item editor ---------------- */
  function openItemEditor(id, kind) {
    var it = id ? itemById(id) : null;
    var k = it ? it.kind : (kind || 'study');
    ui.edit = { id: it ? it.id : null, kind: k, color: it ? it.color : U.PALETTE[0] };
    var kinds = [['routine', '🌅 생활 루틴'], ['study', '📚 공부'], ['promise', '🤝 약속']];
    var sw = U.PALETTE.map(function (c) {
      return '<span class="sw' + (c === ui.edit.color ? ' on' : '') + '" style="background:' + c + '" data-act="colorPick" data-color="' + c + '"></span>';
    }).join('');
    var timerOpts = [['', '없음'], ['countdown', '카운트다운'], ['stopwatch', '스톱워치']].map(function (o) {
      return '<option value="' + o[0] + '"' + ((it ? it.timer : '') === o[0] ? ' selected' : '') + '>' + o[1] + '</option>';
    }).join('');
    var slotOpts = [['morning', '아침'], ['evening', '저녁'], ['any', '언제든']].map(function (o) {
      return '<option value="' + o[0] + '"' + ((it ? it.slot : 'any') === o[0] ? ' selected' : '') + '>' + o[1] + '</option>';
    }).join('');

    openModal('<h2>' + (it ? '항목 수정' : '새 항목') + '</h2>' +
      '<div class="sh-sub">이름 · 상점 · 벌점 · 예상 소요시간을 정해 주세요.</div>' +
      '<div class="formgrid">' +
      '<div class="field full"><label>이름</label><input type="text" id="itName" value="' + esc(it ? it.name : '') + '" placeholder="예: 연산 문제"></div>' +
      '<div class="field full"><label>종류</label><div class="kindseg" id="kindSeg">' + kinds.map(function (o) {
        return '<button type="button" data-act="kindPick" data-kind="' + o[0] + '" class="' + (o[0] === k ? 'on' : '') + '">' + o[1] + '</button>';
      }).join('') + '</div></div>' +
      '<div class="field"><label>이모지</label><input type="text" id="itEmoji" value="' + esc(it ? it.emoji : '📘') + '" maxlength="4"></div>' +
      '<div class="field"><label>색상</label><div class="swatches" id="swatches">' + sw + '</div></div>' +
      '<div class="field"><label>완료 상점 (+)</label><input type="number" id="itReward" min="0" value="' + (it ? it.reward : 2) + '"></div>' +
      '<div class="field"><label>미완료 벌점 (−)</label><input type="number" id="itPenalty" min="0" value="' + (it ? it.penalty : 2) + '"></div>' +
      '<div class="field"><label>예상 소요시간 (분) *</label><input type="number" id="itMinutes" min="0" value="' + (it ? it.minutes : 20) + '"></div>' +
      '<div class="field"><label>타이머</label><select id="itTimer">' + timerOpts + '</select></div>' +
      '<div class="field"><label>목표 시간 (생활 루틴)</label><input type="time" id="itTime" value="' + esc(it ? it.time : '') + '"></div>' +
      '<div class="field"><label>생활 루틴 위치</label><select id="itSlot">' + slotOpts + '</select></div>' +
      '<div class="field full"><label style="display:flex;align-items:center;gap:6px;font-size:12px"><input type="checkbox" id="itQuick" ' + (it && it.quick ? 'checked' : '') + '> 생활 루틴을 위쪽 큰 버튼으로 표시</label></div>' +
      '</div>' +
      '<div class="sheet-foot">' + (it ? '<button class="btn bad" data-act="delItem" data-id="' + it.id + '">삭제</button>' : '') +
      '<button class="btn" data-act="closeModal">취소</button><button class="btn primary" data-act="itemSave">저장</button></div>');
  }
  function saveItem() {
    var nameEl = $('#itName');
    if (!nameEl) return;
    var name = nameEl.value.trim();
    if (!name) { toast('이름을 입력해 주세요'); return; }
    var kind = ui.edit.kind;
    var mins = clampNum($('#itMinutes').value, 0, 600, 0);
    if (kind === 'study' && !(mins > 0)) { toast('공부 예상 소요시간(분)을 입력해 주세요'); return; }
    var data = {
      name: name, kind: kind, emoji: $('#itEmoji').value.trim() || '📘', color: ui.edit.color,
      reward: clampNum($('#itReward').value, 0, 999, 0), penalty: clampNum($('#itPenalty').value, 0, 999, 0),
      minutes: mins, timer: $('#itTimer').value, time: $('#itTime').value, slot: $('#itSlot').value,
      quick: $('#itQuick').checked
    };
    if (ui.edit.id) {
      var it = itemById(ui.edit.id);
      if (it) for (var k in data) it[k] = data[k];
    } else {
      var ni = { id: U.uid('it') };
      for (var k2 in data) ni[k2] = data[k2];
      st().items.push(ni);
      if (kind !== 'routine') toast('저장했어요. 주간 시간표에서 요일을 골라 주세요');
    }
    closeModal(); save();
  }
  function deleteItem(id) {
    var it = itemById(id);
    if (!it) return;
    openConfirm('항목 삭제', '<b>' + esc(it.name) + '</b>을(를) 삭제할까요? 시간표에서도 빠지고 지난 기록 통계에서도 제외돼요.', '삭제', function () {
      st().items = st().items.filter(function (x) { return x.id !== id; });
      ['1', '2'].forEach(function (c) {
        D.forEach(function (d) {
          var arr = st().schedule[c][d] || [];
          var i = arr.indexOf(id);
          if (i >= 0) arr.splice(i, 1);
        });
      });
      closeModal(); save(); toast('삭제했어요');
    }, true);
  }
  function openDayPicker(itemId) {
    var it = itemById(itemId);
    if (!it) return;
    var sched = st().schedule[curChild()] || {};
    ui.dayPick = { id: itemId, days: {} };
    D.forEach(function (d) { ui.dayPick.days[d] = (sched[d] || []).indexOf(itemId) >= 0; });
    renderDayPicker();
  }
  function renderDayPicker() {
    var it = itemById(ui.dayPick.id);
    if (!it) return;
    openModal('<h2>요일 선택</h2><div class="sh-sub">' + esc(it.name) + ' · ' + esc(childOf(curChild()).name) + ' 시간표에 넣을 요일을 골라 주세요.</div>' +
      '<div class="daytoggles">' + D.map(function (d) {
        return '<button class="daytoggle' + (ui.dayPick.days[d] ? ' on' : '') + '" data-act="daytoggle" data-day="' + d + '">' + DL[d] + '</button>';
      }).join('') + '</div>' +
      '<div class="sheet-foot"><button class="btn" data-act="closeModal">취소</button><button class="btn primary" data-act="dayPickSave">적용</button></div>');
  }
  function saveDayPick() {
    var sched = st().schedule[curChild()];
    var id = ui.dayPick.id;
    D.forEach(function (d) {
      var arr = sched[d] || (sched[d] = []);
      var i = arr.indexOf(id);
      if (ui.dayPick.days[d] && i < 0) arr.push(id);
      if (!ui.dayPick.days[d] && i >= 0) arr.splice(i, 1);
    });
    var name = itemById(id) ? itemById(id).name : '';
    closeModal(); save();
    toast(name + ' 요일을 저장했어요');
  }
  function openPoolPicker(day) {
    ui.poolDay = day;
    renderPoolPicker();
  }
  function renderPoolPicker() {
    var day = ui.poolDay, sched = st().schedule[curChild()] || {};
    var ids = sched[day] || [];
    var list = st().items.filter(function (it) { return it.kind !== 'routine'; });
    openModal('<h2>' + DL[day] + '요일 추가</h2><div class="sh-sub">' + esc(childOf(curChild()).name) + ' ' + DL[day] + '요일 시간표에 넣을 항목을 눌러 주세요.</div>' +
      '<div class="pool" style="gap:6px">' + (list.length ? list.map(function (it) {
        var on = ids.indexOf(it.id) >= 0;
        return '<button class="chip" style="--c:' + it.color + ';' + (on ? 'opacity:.5' : '') + '" data-act="poolAdd" data-id="' + it.id + '" data-day="' + day + '">' +
          '<span class="chip-e">' + esc(it.emoji || '') + '</span><span class="chip-n">' + esc(it.name) + '</span>' +
          (it.minutes ? '<span class="chip-dur">' + it.minutes + '분</span>' : '') + (on ? '<span class="chip-days">추가됨</span>' : '') + '</button>';
      }).join('') : '<div class="empty">항목이 없어요.</div>') + '</div>' +
      '<div class="sheet-foot"><button class="btn primary" data-act="closeModal">완료</button></div>');
  }

  /* ---------------- day plan ---------------- */
  function openPlan(day) {
    var p = planFor(curChild(), day);
    ui.planDraft = { day: day, out: p.out, bed: p.bed, blocks: p.blocks.map(function (b) { return { id: b.id || U.uid('bk'), name: b.name, start: b.start, end: b.end }; }) };
    renderPlan();
  }
  function renderPlan() {
    var d = ui.planDraft;
    if (!d) return;
    var ch = childOf(curChild());
    openModal('<h2>🏫 ' + DL[d.day] + '요일 시간 설정</h2><div class="sh-sub">' + esc(ch.name) + ' · 하교부터 취침까지 빈 시간을 자동으로 계산해요.</div>' +
      '<div class="formgrid"><div class="field"><label>하교 시각</label><input type="time" id="planOut" value="' + d.out + '"></div>' +
      '<div class="field"><label>취침 시각</label><input type="time" id="planBed" value="' + d.bed + '"></div></div>' +
      '<h3 style="margin:14px 0 6px;font-size:12.5px">고정 일정 (학원 · 태권도 등)</h3>' +
      (d.blocks.length ? d.blocks.map(function (b, i) {
        return '<div class="planblock"><input type="text" data-pb="name" data-i="' + i + '" value="' + esc(b.name) + '" placeholder="일정 이름">' +
          '<input type="time" data-pb="start" data-i="' + i + '" value="' + b.start + '">' +
          '<input type="time" data-pb="end" data-i="' + i + '" value="' + b.end + '">' +
          '<button class="rm" data-act="planBlockRemove" data-idx="' + i + '">×</button></div>';
      }).join('') : '<div class="empty">고정 일정이 없어요. 하교부터 취침까지 전부 빈 시간이에요.</div>') +
      '<button class="mini" data-act="planBlockAdd" style="margin-top:6px">+ 일정 추가</button>' +
      '<div class="sheet-foot"><button class="btn" data-act="closeModal">취소</button><button class="btn primary" data-act="planSave">저장</button></div>');
  }
  function readPlanDraft() {
    var d = ui.planDraft;
    if (!d) return;
    var out = $('#planOut'), bed = $('#planBed');
    if (out) d.out = out.value;
    if (bed) d.bed = bed.value;
    $$('[data-pb]').forEach(function (el) {
      var i = +el.dataset.i;
      if (!d.blocks[i]) return;
      d.blocks[i][el.dataset.pb] = el.value;
    });
  }
  function savePlan() {
    readPlanDraft();
    var d = ui.planDraft;
    var out = hhmmToMin(d.out), bed = hhmmToMin(d.bed);
    if (out == null || bed == null) { toast('시간을 입력해 주세요'); return; }
    if (bed <= out) { toast('취침 시각이 하교 시각보다 늦어야 해요'); return; }
    st().dayplan[curChild()][d.day] = {
      out: d.out, bed: d.bed,
      blocks: d.blocks.filter(function (b) { return b.start && b.end; }).map(function (b) { return { id: b.id, name: b.name || '일정', start: b.start, end: b.end }; })
    };
    closeModal(); save(); toast(DL[d.day] + '요일 시간을 저장했어요');
  }

  /* ---------------- drag & drop ---------------- */
  function onPointerDown(e) {
    if (e.button !== undefined && e.button > 0) return;
    var chip = e.target.closest ? e.target.closest('.chip') : null;
    if (!chip) return;
    if (chip.dataset.nodrag === '1') return;
    if (e.target.closest('button')) return;
    var id = chip.dataset.dragItem;
    if (!id) return;
    ui.drag = { id: id, from: chip.dataset.dragFrom || 'pool', startX: e.clientX, startY: e.clientY, active: false, chip: chip, ghost: null };
  }
  function onPointerMove(e) {
    var g = ui.drag;
    if (!g) return;
    if (!g.active) {
      if (Math.abs(e.clientX - g.startX) + Math.abs(e.clientY - g.startY) < 8) return;
      startDrag(e);
    }
    e.preventDefault();
    if (g.ghost) { g.ghost.style.left = e.clientX + 'px'; g.ghost.style.top = e.clientY + 'px'; }
    var target = document.elementFromPoint(e.clientX, e.clientY);
    var col = target && target.closest ? target.closest('[data-drop]') : null;
    $$('.daycol').forEach(function (c) { c.classList.toggle('drop-on', c === col); });
  }
  function startDrag(e) {
    var g = ui.drag;
    g.active = true;
    var rect = g.chip.getBoundingClientRect();
    var ghost = g.chip.cloneNode(true);
    ghost.classList.add('dragghost');
    ghost.style.width = Math.max(80, rect.width) + 'px';
    ghost.style.left = e.clientX + 'px';
    ghost.style.top = e.clientY + 'px';
    document.body.appendChild(ghost);
    g.ghost = ghost;
    g.chip.classList.add('picked');
    document.body.classList.add('dragging');
  }
  function onPointerUp(e) {
    var g = ui.drag;
    if (!g) return;
    if (g.active) {
      var target = document.elementFromPoint(e.clientX, e.clientY);
      var col = target && target.closest ? target.closest('[data-drop]') : null;
      if (col) assignToDay(g.id, col.dataset.drop, g.from);
    }
    if (g.ghost) g.ghost.remove();
    if (g.chip) g.chip.classList.remove('picked');
    $$('.daycol').forEach(function (c) { c.classList.remove('drop-on'); });
    document.body.classList.remove('dragging');
    ui.drag = null;
  }
  function assignToDay(id, day, from) {
    var it = itemById(id);
    if (!it) return;
    if (it.kind === 'routine') { toast('생활 루틴은 매일 자동으로 표시돼요'); return; }
    var sched = st().schedule[curChild()];
    if (!sched[day]) sched[day] = [];
    if (from && from !== 'pool' && from !== day) {
      var src = sched[from] || [];
      var i = src.indexOf(id);
      if (i >= 0) src.splice(i, 1);
    }
    if (sched[day].indexOf(id) < 0) sched[day].push(id);
    save();
    toast(it.name + ' → ' + DL[day] + '요일');
  }

  /* ---------------- setup share link ---------------- */
  function encodeSetup(obj) {
    var json = JSON.stringify(obj);
    var b64 = btoa(unescape(encodeURIComponent(json)));
    return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function decodeSetup(str) {
    var b64 = String(str).replace(/-/g, '+').replace(/_/g, '/');
    while (b64.length % 4) b64 += '=';
    var json = decodeURIComponent(escape(atob(b64)));
    return JSON.parse(json);
  }
  function setupPayload() {
    var s = st();
    var ids = {};
    var items = s.items.map(function (it, i) {
      ids[it.id] = i;
      var ci = U.PALETTE.indexOf(it.color);
      return [it.name, it.kind, it.slot, ci < 0 ? 0 : ci, it.emoji || '', it.reward, it.penalty, it.time || '', it.minutes, it.timer || '', it.quick ? 1 : 0];
    });
    function days(arr) {
      return D.map(function (d) {
        return (arr[d] || []).map(function (id) { return ids[id]; }).filter(function (i) { return i != null; });
      });
    }
    function planDays(c) {
      return D.map(function (d) {
        var p = planFor(c, d);
        return [p.out, p.bed, p.blocks.map(function (b) { return [b.name, b.start, b.end]; })];
      });
    }
    return {
      t: 'bmr', v: 2,
      ch: s.children,
      it: items,
      sc: { '1': days(s.schedule['1']), '2': days(s.schedule['2']) },
      dp: { '1': planDays('1'), '2': planDays('2') },
      rw: s.rewards.map(function (r) { return [r.name, r.cost]; }),
      pr: s.penaltyRules.map(function (r) { return [r.name, r.threshold]; }),
      wk: [s.settings.wake.enabled ? 1 : 0, s.settings.wake.time],
      sl: [s.settings.sleep.enabled ? 1 : 0, s.settings.sleep.time]
    };
  }
  function applySetup(data) {
    if (data && data.t === 'bmr' && data.v === 2) return applySetupV2(data);
    applySetupLegacy(data);
  }
  function applySetupV2(d) {
    var s = st();
    if (d.ch) {
      ['1', '2'].forEach(function (c) {
        if (!d.ch[c]) return;
        s.children[c] = {
          name: d.ch[c].name || s.children[c].name,
          emoji: d.ch[c].emoji || s.children[c].emoji,
          grade: d.ch[c].grade || ''
        };
      });
    }
    if (Array.isArray(d.it) && d.it.length) {
      var byKey = {};
      s.items.forEach(function (it) { byKey[it.name + '|' + it.kind] = it.id; });
      var newIds = [];
      s.items = d.it.map(function (a, i) {
        var name = a[0] || '이름 없음';
        var kind = a[1] || 'study';
        var id = byKey[name + '|' + kind] || U.uid('it');
        newIds[i] = id;
        return {
          id: id, name: name, kind: kind, slot: a[2] || 'any',
          color: U.PALETTE[a[3]] || U.PALETTE[0], emoji: a[4] || '',
          reward: Number(a[5]) || 0, penalty: Number(a[6]) || 0,
          time: a[7] || '', minutes: Number(a[8]) || 0,
          timer: a[9] || '', quick: !!a[10]
        };
      });
      ['1', '2'].forEach(function (c) {
        if (!d.sc) return;
        var src = d.sc[c] || [];
        D.forEach(function (dk, i) {
          var arr = Array.isArray(src[i]) ? src[i] : [];
          s.schedule[c][dk] = arr.map(function (idx) { return newIds[idx]; }).filter(function (v) { return !!v; });
        });
      });
    }
    if (d.dp) {
      ['1', '2'].forEach(function (c) {
        var src = d.dp[c] || [];
        D.forEach(function (dk, i) {
          var p = src[i];
          if (!p) return;
          s.dayplan[c][dk] = {
            out: p[0] || '13:30', bed: p[1] || '21:30',
            blocks: (p[2] || []).map(function (b) { return { id: U.uid('bk'), name: b[0] || '일정', start: b[1] || '', end: b[2] || '' }; })
          };
        });
      });
    }
    if (Array.isArray(d.rw)) s.rewards = d.rw.map(function (r) { return { id: U.uid('rw'), name: r[0] || '보상', cost: Number(r[1]) || 10, note: '' }; });
    if (Array.isArray(d.pr)) s.penaltyRules = d.pr.map(function (r) { return { id: U.uid('pr'), name: r[0] || '벌 조건', threshold: Number(r[1]) || 10 }; });
    if (d.wk) s.settings.wake = { enabled: !!d.wk[0], time: d.wk[1] || '07:30' };
    if (d.sl) s.settings.sleep = { enabled: !!d.sl[0], time: d.sl[1] || '21:30' };
    Store.save();
  }
  function applySetupLegacy(data) {
    var s = st();
    if (data.children) {
      ['1', '2'].forEach(function (c) {
        if (data.children[c]) s.children[c] = { name: data.children[c].name || s.children[c].name, emoji: data.children[c].emoji || s.children[c].emoji, grade: data.children[c].grade || '' };
      });
    }
    if (Array.isArray(data.items) && data.items.length) {
      s.items = data.items.map(function (it) {
        return {
          id: it.id || U.uid('it'), name: it.name || '이름 없음', kind: it.kind || 'study', slot: it.slot || 'any',
          color: it.color || U.PALETTE[0], emoji: it.emoji || '', reward: Number(it.reward) || 0,
          penalty: Number(it.penalty) || 0, time: it.time || '', minutes: Number(it.minutes) || 0,
          timer: it.timer || '', quick: !!it.quick
        };
      });
    }
    if (data.schedule) {
      ['1', '2'].forEach(function (c) {
        var src = data.schedule[c] || {};
        D.forEach(function (d) { s.schedule[c][d] = Array.isArray(src[d]) ? src[d].slice() : []; });
      });
    }
    if (data.dayplan) {
      ['1', '2'].forEach(function (c) {
        var src = data.dayplan[c] || {};
        D.forEach(function (d) {
          var p = src[d];
          if (!p) return;
          s.dayplan[c][d] = { out: p.out || '13:30', bed: p.bed || '21:30', blocks: Array.isArray(p.blocks) ? p.blocks.map(function (b) { return { id: b.id || U.uid('bk'), name: b.name || '일정', start: b.start, end: b.end }; }) : [] };
        });
      });
    }
    if (Array.isArray(data.rewards)) s.rewards = data.rewards.map(function (r) { return { id: r.id || U.uid('rw'), name: r.name || '보상', cost: Number(r.cost) || 10, note: '' }; });
    if (Array.isArray(data.penaltyRules)) s.penaltyRules = data.penaltyRules.map(function (r) { return { id: r.id || U.uid('pr'), name: r.name || '벌 조건', threshold: Number(r.threshold) || 10 }; });
    if (data.wake) s.settings.wake = { enabled: !!data.wake.enabled, time: data.wake.time || '07:30' };
    if (data.sleep) s.settings.sleep = { enabled: !!data.sleep.enabled, time: data.sleep.time || '21:30' };
    Store.save();
  }
  function hasLocalRecords() {
    var s = st();
    if (s.log && s.log.length) return true;
    if (s.timeLog && s.timeLog.length) return true;
    return Object.keys(s.completions['1'] || {}).length > 0 || Object.keys(s.completions['2'] || {}).length > 0;
  }
  function checkSetupLink() {
    var raw = '';
    try { raw = new URLSearchParams(location.search).get('setup') || ''; } catch (e) { return; }
    if (!raw) return;
    var data;
    try { data = decodeSetup(raw); } catch (e) { toast('공유 링크를 읽을 수 없어요. 링크가 잘렸는지 확인해 주세요.'); return; }
    if (!data || (data.t !== 'babamam-routine' && data.t !== 'bmr')) return;
    var apply = function () {
      applySetup(data);
      try { history.replaceState(null, '', location.pathname); } catch (e) { }
      toast('세팅을 적용했어요! 이제 체크보드를 써 보세요');
    };
    if (hasLocalRecords()) {
      openConfirm('공유 세팅 적용', '이 기기에 이미 기록이 있어요.<br>공유된 <b>공부 리스트 · 주간 시간표 · 하교 일정 · 약속 · 보상/벌 조건</b>으로 바꿀까요?<br><span style="color:#8A8175">체크·점수 기록은 이 기기에 그대로 남아요.</span>', '적용하기', apply);
    } else {
      apply();
    }
  }
  function shareSetup() {
    var payload = encodeSetup(setupPayload());
    var base = location.origin + location.pathname;
    var url = base + '?setup=' + payload;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () {
        toast('링크를 복사했어요! 아이 태블릿에 붙여 열어 주세요');
      }).catch(function () { });
    }
    openModal('<h2>📤 아이 태블릿으로 보내기</h2>' +
      '<div class="sh-sub">아래 링크를 아이 태블릿 브라우저에서 열면 공부 리스트 · 시간표 · 하교 일정 · 약속 · 보상 조건이 그대로 적용돼요.</div>' +
      '<div class="sharebox"><textarea id="shareUrl" readonly rows="4">' + esc(url) + '</textarea></div>' +
      '<div class="mrow" style="margin-top:8px"><button class="btn primary wide" data-act="copyShare">📋 링크 복사</button></div>' +
      '<div class="momnote" style="margin-top:8px">· 카카오톡 ‘나에게 보내기’나 메모로 옮겨 붙이는 게 가장 편해요.<br>' +
      '· 문자 앱은 긴 링크를 잘라 버릴 수 있어요.<br>· 받는 기기에 이미 기록이 있으면 덮어쓸지 한 번 물어봐요.<br>' +
      '· 상점·벌점 기록은 공유되지 않고, 설정만 전달됩니다.</div>' +
      '<div class="sheet-foot"><button class="btn" data-act="closeModal">닫기</button></div>', { wide: true });
  }
  function copyShare() {
    var el = $('#shareUrl');
    if (!el) return;
    var text = el.value;
    var done = function () { toast('링크를 복사했어요. 아이 태블릿에서 열어 주세요'); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function () {
        el.focus(); el.select();
        try { document.execCommand('copy'); done(); } catch (e) { toast('길게 눌러 직접 복사해 주세요'); }
      });
    } else {
      el.focus(); el.select();
      try { document.execCommand('copy'); done(); } catch (e) { toast('길게 눌러 직접 복사해 주세요'); }
    }
  }

  /* ---------------- sync ---------------- */
  var Sync = {
    es: null, status: 'local', _pending: null, _adopting: false,
    init: function () { this.disconnect(); if (st().settings.syncEnabled && st().settings.firebaseUrl) this.connect(); else this.renderBadge(); },
    url: function () {
      var u = (st().settings.firebaseUrl || '').trim();
      if (!u) return '';
      u = u.replace(/\/+$/, '');
      if (!/^https?:\/\//i.test(u)) u = 'https://' + u;
      return u + '/routineBoard/state.json';
    },
    connect: function () {
      var self = this, url = this.url();
      if (!url) { this.renderBadge(); return; }
      try {
        this.es = new EventSource(url);
        this.es.addEventListener('open', function () { self.status = 'ok'; self.renderBadge(); });
        this.es.addEventListener('error', function () { self.status = 'err'; self.renderBadge(); });
        this.es.addEventListener('put', function (ev) { self.onData(ev.data); });
        this.es.onmessage = function (ev) { self.onData(ev.data); };
        this.pull();
      } catch (e) { this.status = 'err'; this.renderBadge(); }
    },
    disconnect: function () {
      if (this.es) { try { this.es.close(); } catch (e) { } this.es = null; }
      this.status = 'local';
    },
    onData: function (raw) {
      try {
        var msg = JSON.parse(raw);
        var data = msg && msg.data && msg.data.items ? msg.data : msg;
        if (!data || !data.items) return;
        if ((data.updatedAt || 0) > (st().updatedAt || 0) + 500) this.adopt(data);
      } catch (e) { }
    },
    pull: function () {
      var self = this, url = this.url();
      if (!url) return;
      fetch(url + (url.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now())
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) { if (data && data.items) self.onData(JSON.stringify(data)); else { self.status = 'ok'; self.renderBadge(); } })
        .catch(function () { self.status = 'err'; self.renderBadge(); });
    },
    adopt: function (data) {
      if (this._adopting) return;
      this._adopting = true;
      Store.replace(data, { quiet: true });
      this._adopting = false;
      this.status = 'ok';
      render(); this.renderBadge();
      toast('다른 기기에서 바뀐 내용을 불러왔어요');
    },
    queue: function () {
      if (!st().settings.syncEnabled || !this.url()) return;
      var self = this;
      clearTimeout(this._pending);
      this._pending = setTimeout(function () { self.push(); }, 600);
    },
    push: function () {
      var self = this, url = this.url();
      if (!url) return;
      fetch(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(st()) })
        .then(function (r) { self.status = r.ok ? 'ok' : 'err'; self.renderBadge(); })
        .catch(function () { self.status = 'err'; self.renderBadge(); });
    },
    renderBadge: function () { renderSyncBadge(); }
  };
  function renderSyncBadge() {
    var el = $('#syncBadge');
    if (!el) return;
    var s = st().settings;
    el.classList.remove('ok', 'err');
    if (!s.syncEnabled || !s.firebaseUrl) { el.textContent = '💾 이 기기에 저장'; }
    else if (Sync.status === 'ok') { el.textContent = '☁️ 동기화 연결됨'; el.classList.add('ok'); }
    else if (Sync.status === 'err') { el.textContent = '☁️ 연결 확인 필요'; el.classList.add('err'); }
    else { el.textContent = '☁️ 연결 중…'; }
  }

  /* ---------------- toast / fx ---------------- */
  var toastT = null;
  function toast(msg) {
    var el = $('#toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toastT);
    toastT = setTimeout(function () { el.classList.remove('show'); }, 2600);
  }
  function fx(itemId, points) {
    var el = document.querySelector('[data-act="toggle"][data-id="' + itemId + '"]');
    if (!el) return;
    var r = el.getBoundingClientRect();
    var x = r.left + r.width / 2, y = r.top + r.height / 2;
    var f = document.createElement('div');
    f.className = 'floatpt';
    f.textContent = '+' + points;
    f.style.left = x + 'px'; f.style.top = y + 'px';
    document.body.appendChild(f);
    setTimeout(function () { f.remove(); }, 900);
    for (var i = 0; i < 7; i++) {
      (function (i) {
        var c = document.createElement('div');
        c.className = 'conf';
        c.style.left = x + 'px'; c.style.top = y + 'px';
        c.style.background = ['#12B886', '#FFC147', '#FF7A59', '#7C5CD6', '#4C6EF5'][i % 5];
        c.style.setProperty('--dx', (Math.random() * 120 - 60) + 'px');
        c.style.setProperty('--dy', (Math.random() * -70 - 20) + 'px');
        document.body.appendChild(c);
        setTimeout(function () { c.remove(); }, 800);
      })(i);
    }
  }

  /* ---------------- clock ---------------- */
  function tick() {
    var today = U.today();
    if (today !== ui.lastDay) {
      ui.lastDay = today;
      var res = settleUpToAll(true);
      Store.write();
      render();
      if (res.totalPen) toast('새 날 시작 · 어제 미완료 벌점 −' + res.totalPen + '점');
    }
    tickTimers();
    checkClockAlarms();
    updateDockClocks();
  }

  /* ---------------- action dispatch ---------------- */
  function handle(act, el) {
    var d = el.dataset;
    switch (act) {
      case 'view': setView(d.view); break;
      case 'child': setChild(d.child); break;
      case 'period': setPeriod(d.mode); break;
      case 'mom': if (ui.unlocked) setView('mom'); else openPin(); break;
      case 'lock': lockMom(); break;
      case 'toggle': onToggle(d.id, d.date); break;
      case 'timer': onTimerBtn(d.id); break;
      case 'stopTimer': finishTimer(d.key, 'manual'); break;
      case 'break': startBreak(); break;
      case 'breakDismiss': try { sessionStorage.setItem('breakDismiss', d.n || ''); } catch (e) { } render(); break;
      case 'addItem': openItemEditor(null, 'study'); break;
      case 'addPromise': openItemEditor(null, 'promise'); break;
      case 'addRoutine': openItemEditor(null, 'routine'); break;
      case 'editItem': openItemEditor(d.id); break;
      case 'delItem': deleteItem(d.id); break;
      case 'kindPick':
        ui.edit.kind = d.kind;
        $$('#kindSeg button').forEach(function (b) { b.classList.toggle('on', b.dataset.kind === d.kind); });
        break;
      case 'colorPick':
        ui.edit.color = d.color;
        $$('#swatches .sw').forEach(function (b) { b.classList.toggle('on', b.dataset.color === d.color); });
        break;
      case 'itemSave': saveItem(); break;
      case 'days': openDayPicker(d.id); break;
      case 'daytoggle': ui.dayPick.days[d.day] = !ui.dayPick.days[d.day]; renderDayPicker(); break;
      case 'dayPickSave': saveDayPick(); break;
      case 'dayAdd': openPoolPicker(d.day); break;
      case 'poolAdd':
        var arr = st().schedule[curChild()][d.day] || (st().schedule[curChild()][d.day] = []);
        if (arr.indexOf(d.id) < 0) arr.push(d.id);
        save(); renderPoolPicker();
        break;
      case 'unassign':
        var sched0 = st().schedule[curChild()], ar = sched0[d.day] || [];
        var idx = ar.indexOf(d.id);
        if (idx >= 0) ar.splice(idx, 1);
        save(); break;
      case 'planDay': openPlan(d.day); break;
      case 'planBlockAdd':
        readPlanDraft();
        ui.planDraft.blocks.push({ id: U.uid('bk'), name: '', start: '', end: '' });
        renderPlan(); break;
      case 'planBlockRemove':
        readPlanDraft();
        ui.planDraft.blocks.splice(+d.idx, 1);
        renderPlan(); break;
      case 'planSave': savePlan(); break;
      case 'settleToday': confirmSettle(); break;
      case 'undoSettle': undoSettle(d.child, d.date); break;
      case 'manualPlus': manual(1); break;
      case 'manualMinus': manual(-1); break;
      case 'redeem': redeem(d.id); break;
      case 'addRewardSubmit':
        var rnEl = $('#rewardName');
        var rn = rnEl ? rnEl.value.trim() : '';
        var rcEl = $('#rewardCost');
        if (!rn) { toast('보상 이름을 입력해 주세요'); break; }
        st().rewards.push({ id: U.uid('rw'), name: rn, cost: clampNum(rcEl && rcEl.value, 1, 9999, 30), note: '' });
        save(); toast('보상 조건을 추가했어요'); break;
      case 'editReward':
        var rr0 = (st().rewards || []).filter(function (x) { return x.id === d.id; })[0];
        if (rr0) openEditReward(rr0); break;
      case 'delReward':
        st().rewards = st().rewards.filter(function (x) { return x.id !== d.id; });
        save(); toast('보상 조건을 지웠어요'); break;
      case 'addRuleSubmit':
        var nnEl = $('#ruleName');
        var nn = nnEl ? nnEl.value.trim() : '';
        var ntEl = $('#ruleThreshold');
        if (!nn) { toast('벌 조건 이름을 입력해 주세요'); break; }
        st().penaltyRules.push({ id: U.uid('pr'), name: nn, threshold: clampNum(ntEl && ntEl.value, 1, 9999, 10) });
        save(); toast('벌 조건을 추가했어요'); break;
      case 'editRule':
        var rr1 = (st().penaltyRules || []).filter(function (x) { return x.id === d.id; })[0];
        if (rr1) openEditRule(rr1); break;
      case 'delRule':
        st().penaltyRules = st().penaltyRules.filter(function (x) { return x.id !== d.id; });
        save(); toast('벌 조건을 지웠어요'); break;
      case 'pinKey': pinKey(d.k); break;
      case 'pinChange': openPinChange(); break;
      case 'saveNames': saveNames(); break;
      case 'sync': openSync(); break;
      case 'syncSave': saveSync(); break;
      case 'shareSetup': shareSetup(); break;
      case 'copyShare': copyShare(); break;
      case 'export': exportData(); break;
      case 'import': importData(); break;
      case 'reset': resetData(); break;
      case 'testAlarm': testAlarm(); break;
      case 'wakeLock': toggleWakeLock(); break;
      case 'alarmOff': dismissAlarm(); break;
      case 'closeModal': closeModal(); break;
      case 'confirmOk': confirmOk(); break;
      case 'formSubmit': if (ui.formCb && ui.formCb() !== false) closeModal(); break;
      default: break;
    }
  }

  function confirmSettle() {
    var today = U.today();
    var already = ['1', '2'].every(function (c) { return st().settled[c] && st().settled[c][today]; });
    if (already) { toast('오늘은 이미 정산이 완료됐어요'); return; }
    var lines = ['1', '2'].map(function (c) {
      return childOf(c).name + ' 미완료 ' + pendingCount(c, today) + '개 → −' + pendingPenalty(c, today) + '점';
    }).join('<br>');
    openConfirm('오늘 마감 정산', '지금 정산하면 오늘 남은 항목이 벌점으로 기록됩니다. (테스트용)<br>' + lines + '<br><br>정산 후에도 이력에서 되돌릴 수 있어요.', '정산하기', function () { settleTodayAll(); }, true);
  }
  function openEditReward(r) {
    openFormModal({
      title: '보상 조건 수정',
      fields: [
        { name: 'name', label: '보상 이름', type: 'text', value: r.name },
        { name: 'cost', label: '필요 점수', type: 'number', value: r.cost, min: 1 }
      ],
      onSubmit: function () {
        var nameEl = $('#fm_name');
        var name = nameEl ? nameEl.value.trim() : '';
        if (!name) { toast('이름을 입력해 주세요'); return false; }
        r.name = name;
        var costEl = $('#fm_cost');
        r.cost = clampNum(costEl && costEl.value, 1, 9999, 30);
        save(); toast('수정했어요');
      }
    });
  }
  function openEditRule(r) {
    openFormModal({
      title: '벌 조건 수정',
      fields: [
        { name: 'name', label: '벌 조건 이름', type: 'text', value: r.name },
        { name: 'threshold', label: '벌점 기준', type: 'number', value: r.threshold, min: 1 }
      ],
      onSubmit: function () {
        var nameEl = $('#fm_name');
        var name = nameEl ? nameEl.value.trim() : '';
        if (!name) { toast('이름을 입력해 주세요'); return false; }
        r.name = name;
        var thEl = $('#fm_threshold');
        r.threshold = clampNum(thEl && thEl.value, 1, 9999, 10);
        save(); toast('수정했어요');
      }
    });
  }
  function openFormModal(opts) {
    var fields = opts.fields.map(function (f) {
      var input;
      if (f.type === 'number') input = '<input type="number" id="fm_' + f.name + '" value="' + esc(f.value == null ? '' : f.value) + '" min="' + (f.min || 0) + '">';
      else if (f.type === 'time') input = '<input type="time" id="fm_' + f.name + '" value="' + esc(f.value || '') + '">';
      else if (f.type === 'password') input = '<input type="password" id="fm_' + f.name + '" value="" inputmode="numeric" maxlength="8" placeholder="' + esc(f.placeholder || '') + '">';
      else input = '<input type="text" id="fm_' + f.name + '" value="' + esc(f.value == null ? '' : f.value) + '" placeholder="' + esc(f.placeholder || '') + '">';
      return '<div class="field" style="flex:1;min-width:130px"><label>' + esc(f.label) + '</label>' + input + '</div>';
    }).join('');
    ui.formCb = opts.onSubmit;
    openModal('<h2>' + esc(opts.title) + '</h2>' + (opts.desc ? '<div class="sh-sub">' + opts.desc + '</div>' : '') +
      '<div class="mrow" style="align-items:flex-end;flex-wrap:wrap">' + fields + '</div>' +
      '<div class="sheet-foot"><button class="btn" data-act="closeModal">취소</button>' +
      '<button class="btn primary" data-act="formSubmit">' + esc(opts.submitLabel || '저장') + '</button></div>');
  }
  function openPinChange() {
    openFormModal({
      title: '🔒 PIN 변경', desc: '숫자 4자리로 정해 주세요. 잊으면 저장된 데이터를 초기화해야 할 수 있어요.',
      fields: [
        { name: 'pin1', label: '새 PIN', type: 'password', placeholder: '4자리' },
        { name: 'pin2', label: '새 PIN 확인', type: 'password', placeholder: '4자리' }
      ],
      onSubmit: function () {
        var aEl = $('#fm_pin1'), bEl = $('#fm_pin2');
        var a = aEl ? aEl.value : '', b = bEl ? bEl.value : '';
        if (!/^\d{4}$/.test(a)) { toast('숫자 4자리로 입력해 주세요'); return false; }
        if (a !== b) { toast('두 번 입력한 PIN이 달라요'); return false; }
        st().pin = a; save(); toast('PIN을 바꿨어요');
      }
    });
  }
  function saveNames() {
    var n1El = $('#name1'), g1El = $('#grade1'), n2El = $('#name2'), g2El = $('#grade2');
    var n1 = n1El ? n1El.value.trim() : '', g1 = g1El ? g1El.value.trim() : '';
    var n2 = n2El ? n2El.value.trim() : '', g2 = g2El ? g2El.value.trim() : '';
    if (n1) st().children['1'].name = n1;
    if (n2) st().children['2'].name = n2;
    st().children['1'].grade = g1;
    st().children['2'].grade = g2;
    save(); toast('아이 이름을 저장했어요');
  }
  function openSync() {
    var s = st().settings;
    openModal('<h2>☁️ 기기 간 동기화</h2><div class="sh-sub">Firebase Realtime Database 주소를 넣으면 엄마 폰과 아이들 태블릿이 같은 기록을 실시간으로 봐요.</div>' +
      '<div class="field"><label>Firebase DB URL</label><input type="url" id="syncUrl" value="' + esc(s.firebaseUrl) + '" placeholder="https://내프로젝트-default-rtdb.firebaseio.com"></div>' +
      '<div class="mrow" style="margin-top:10px"><label style="display:flex;align-items:center;gap:6px;font-size:12.5px;font-weight:800"><input type="checkbox" id="syncOn" ' + (s.syncEnabled ? 'checked' : '') + '> 동기화 사용</label></div>' +
      '<div class="momnote" style="margin-top:10px">· Firebase 콘솔 → Realtime Database → 규칙에서 읽기/쓰기를 허용해야 해요.<br>· 연결하지 않아도 이 기기에 100% 저장되고 그대로 동작해요.<br>· 연결하면 바뀐 내용이 자동으로 주고받아져요.</div>' +
      '<div class="sheet-foot"><button class="btn" data-act="closeModal">취소</button><button class="btn primary" data-act="syncSave">저장</button></div>');
  }
  function saveSync() {
    var urlEl = $('#syncUrl'), onEl = $('#syncOn');
    var url = urlEl ? urlEl.value.trim() : '';
    var on = !!(onEl && onEl.checked);
    st().settings.firebaseUrl = url;
    st().settings.syncEnabled = on && !!url;
    Store.save({ quiet: true });
    closeModal();
    Sync.init();
    render();
    toast(st().settings.syncEnabled ? '동기화를 켰어요' : '로컬 저장 모드로 두었어요');
  }
  function testAlarm() { Sound.unlock(); if (!st().settings.sound) { toast('알람 소리가 꺼져 있어요'); return; } Sound.chime(); toast('🔔 이 소리로 알려 줄게요'); }
  function exportData() {
    try {
      var blob = new Blob([Store.exportJSON()], { type: 'application/json' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'routine-board-' + U.today() + '.json';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 3000);
      toast('백업 파일을 내려받았어요');
    } catch (e) { toast('백업 파일을 만들지 못했어요'); }
  }
  function importData() {
    var input = document.createElement('input');
    input.type = 'file'; input.accept = 'application/json,.json';
    input.onchange = function () {
      var f = input.files && input.files[0];
      if (!f) return;
      var rd = new FileReader();
      rd.onload = function () {
        try {
          var data = JSON.parse(rd.result);
          if (!data || !data.items) throw new Error('bad');
          openConfirm('백업 가져오기', '지금 기록을 백업 파일로 바꿉니다. 계속할까요?', '가져오기', function () {
            Store.replace(data); toast('백업을 가져왔어요');
          });
        } catch (e) { toast('백업 파일을 읽을 수 없어요'); }
      };
      rd.readAsText(f);
    };
    input.click();
  }
  function resetData() {
    openConfirm('데이터 초기화', '모든 체크·점수·시간 기록이 지워지고 기본 상태로 돌아갑니다. 되돌릴 수 없어요.<br>먼저 <b>백업 내보내기</b>를 해 두세요.', '모두 지우기', function () {
      Store.reset(); toast('초기화했어요');
    }, true);
  }

  /* ---------------- events ---------------- */
  function onClick(e) {
    Sound.unlock();
    var el = e.target.closest ? e.target.closest('[data-act]') : null;
    if (!el) return;
    handle(el.dataset.act, el);
  }
  function onChange(e) {
    var el = e.target;
    if (!el || !el.dataset) return;
    if (el.dataset.plan) {
      var child = curChild(), day = el.dataset.day, kind = el.dataset.plan;
      var p = st().dayplan[child][day];
      if (!p) return;
      var val = el.type === 'time' ? el.value : normHM(el.value);
      if (!val) { toast('시간을 13:30 형식으로 입력해 주세요'); el.value = p[kind]; return; }
      p[kind] = val; save({ quiet: true }); render();
      return;
    }
    if (el.dataset.set) {
      var k = el.dataset.set, s = st().settings;
      if (k === 'wake-on') s.wake.enabled = el.checked;
      else if (k === 'sleep-on') s.sleep.enabled = el.checked;
      else if (k === 'sound') { s.sound = el.checked; if (!el.checked) Sound.stopAlarm(); }
      else if (k === 'wake-time') s.wake.time = el.value;
      else if (k === 'sleep-time') s.sleep.time = el.value;
      save({ quiet: true }); render();
    }
  }
  function onKey(e) {
    if (e.key === 'Escape') { if (alarm.active) return; closeModal(); return; }
    if (e.key === 'Enter' || e.key === ' ') {
      var el = document.activeElement;
      if (el && el.getAttribute && el.getAttribute('role') === 'button' && el.dataset.act) {
        e.preventDefault();
        handle(el.dataset.act, el);
      }
    }
  }
  function onVisible() {
    if (document.visibilityState === 'visible') {
      if (wakeLock.want && !wakeLock.sentinel && 'wakeLock' in navigator) {
        navigator.wakeLock.request('screen').then(function (s) { wakeLock.sentinel = s; renderHeader(); }).catch(function () { });
      }
      if (st().settings.syncEnabled) Sync.pull();
      renderHeader();
    }
  }
  function bind() {
    document.addEventListener('click', onClick);
    document.addEventListener('change', onChange);
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onPointerDown, { passive: true });
    document.addEventListener('pointermove', onPointerMove, { passive: false });
    document.addEventListener('pointerup', onPointerUp);
    document.addEventListener('pointercancel', onPointerUp);
    document.addEventListener('visibilitychange', onVisible);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
