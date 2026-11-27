/* 우리집 루틴 보드 — Store layer (localStorage first, sync-ready) */
(function (global) {
  'use strict';

  var KEY = 'routineBoard.v3';
  var DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
  var DAY_LABEL = { mon: '월', tue: '화', wed: '수', thu: '목', fri: '금', sat: '토', sun: '일' };
  var PALETTE = ['#12B886', '#4C6EF5', '#9C6ADE', '#FF7A59', '#E64980', '#F0A020', '#22A6B3', '#8A8578'];

  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function ymd(d) { d = d || new Date(); return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function parseYmd(s) { var p = String(s).split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function addDays(s, n) { var d = parseYmd(s); d.setDate(d.getDate() + n); return ymd(d); }
  function dayKeyOf(s) { var d = (s instanceof Date) ? s : parseYmd(s); return DAYS[(d.getDay() + 6) % 7]; }
  function uid(prefix) { return (prefix || 'id') + '_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7); }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function emptySchedule() { var s = {}; DAYS.forEach(function (d) { s[d] = []; }); return s; }

  function item(o) {
    var base = {
      id: uid('it'), name: '', kind: 'study', slot: 'any', color: PALETTE[0], emoji: '📘',
      reward: 2, penalty: 2, time: '', minutes: 0, timer: '', quick: false
    };
    for (var k in o) { if (Object.prototype.hasOwnProperty.call(o, k)) base[k] = o[k]; }
    return base;
  }

  function block(name, start, end) { return { id: uid('bk'), name: name, start: start, end: end }; }

  function defaultDayPlan(child) {
    var plan = {};
    DAYS.forEach(function (d) {
      plan[d] = { out: child === '2' ? '13:00' : '13:30', bed: '21:30', blocks: [] };
    });
    if (child === '1') {
      plan.mon.blocks = [block('태권도', '15:00', '16:00')];
      plan.tue.blocks = [block('영어학원', '17:00', '18:30')];
      plan.wed.blocks = [block('태권도', '15:00', '16:00')];
      plan.thu.blocks = [block('영어학원', '17:00', '18:30')];
      plan.fri.blocks = [block('태권도', '15:00', '16:00')];
    } else {
      plan.mon.blocks = [block('태권도', '15:30', '16:30')];
      plan.wed.blocks = [block('피아노', '16:00', '16:40')];
      plan.thu.blocks = [block('태권도', '15:30', '16:30')];
      plan.fri.blocks = [block('피아노', '16:00', '16:40')];
    }
    return plan;
  }

  function defaultState() {
    var today = ymd();
    var items = [
      /* ---- 생활 루틴 (매일, 퀵 버튼) ---- */
      item({ id: 'r_wake', name: '기상', kind: 'routine', slot: 'morning', quick: true, emoji: '🌅', color: '#F0A020', reward: 3, penalty: 3, time: '07:30' }),
      item({ id: 'r_brush_m', name: '양치질 (아침)', kind: 'routine', slot: 'morning', quick: true, emoji: '🪥', color: '#4C6EF5', reward: 2, penalty: 2 }),
      item({ id: 'r_game', name: '게임 시간', kind: 'routine', slot: 'evening', quick: true, emoji: '🎮', color: '#9C6ADE', reward: 2, penalty: 2, minutes: 30, timer: 'countdown' }),
      item({ id: 'r_play', name: '놀이시간', kind: 'routine', slot: 'any', quick: true, emoji: '🧸', color: '#F0A020', reward: 2, penalty: 0, minutes: 30, timer: 'stopwatch' }),
      item({ id: 'r_brush_e', name: '양치질 (저녁)', kind: 'routine', slot: 'evening', quick: true, emoji: '🪥', color: '#4C6EF5', reward: 2, penalty: 2 }),
      item({ id: 'r_sleep', name: '취침', kind: 'routine', slot: 'evening', quick: true, emoji: '🌙', color: '#22A6B3', reward: 3, penalty: 3, time: '21:30' }),
      /* ---- 생활 루틴 (목록) ---- */
      item({ id: 'r_bed', name: '이불 정리', kind: 'routine', slot: 'morning', emoji: '🛏️', color: '#12B886', reward: 1, penalty: 1, minutes: 5 }),
      item({ id: 'r_breakfast', name: '아침밥', kind: 'routine', slot: 'morning', emoji: '🍚', color: '#FF7A59', reward: 2, penalty: 2, minutes: 20 }),
      item({ id: 'r_ready', name: '옷 입고 준비물', kind: 'routine', slot: 'morning', emoji: '🎒', color: '#E64980', reward: 3, penalty: 2, minutes: 15 }),
      item({ id: 'r_homework', name: '숙제 확인', kind: 'routine', slot: 'evening', emoji: '✏️', color: '#12B886', reward: 2, penalty: 2, minutes: 20 }),
      item({ id: 'r_tidy', name: '내 방 정리', kind: 'routine', slot: 'evening', emoji: '🧹', color: '#8A8578', reward: 1, penalty: 1, minutes: 10 }),
      /* ---- 공부 ---- */
      item({ id: 's_math', name: '수학 문제집', kind: 'study', emoji: '📘', color: '#4C6EF5', reward: 3, penalty: 3, minutes: 30 }),
      item({ id: 's_korean', name: '국어 독서', kind: 'study', emoji: '📖', color: '#12B886', reward: 2, penalty: 2, minutes: 20 }),
      item({ id: 's_eng', name: '영어 리딩', kind: 'study', emoji: '🔤', color: '#9C6ADE', reward: 3, penalty: 3, minutes: 20 }),
      item({ id: 's_calc', name: '연산 문제', kind: 'study', emoji: '➗', color: '#FF7A59', reward: 2, penalty: 2, minutes: 15 }),
      item({ id: 's_dict', name: '받아쓰기', kind: 'study', emoji: '📝', color: '#F0A020', reward: 2, penalty: 2, minutes: 10 }),
      item({ id: 's_simon', name: '사이먼 영어', kind: 'study', emoji: '🗣️', color: '#22A6B3', reward: 3, penalty: 3, minutes: 20 }),
      /* ---- 엄마와의 약속 ---- */
      item({ id: 'p_talk', name: '엄마랑 10분 이야기', kind: 'promise', emoji: '🤝', color: '#E64980', reward: 3, penalty: 3, minutes: 10 }),
      item({ id: 'p_walk', name: '주말 가족 산책', kind: 'promise', emoji: '🚶', color: '#12B886', reward: 3, penalty: 2, minutes: 60 })
    ];

    var s1 = emptySchedule();
    s1.mon = ['s_math', 's_korean', 's_eng', 'p_talk'];
    s1.tue = ['s_math', 's_korean', 's_eng'];
    s1.wed = ['s_math', 's_korean', 's_eng', 'p_talk'];
    s1.thu = ['s_math', 's_korean', 's_dict'];
    s1.fri = ['s_math', 's_korean', 's_eng', 'p_talk'];
    s1.sat = ['s_korean', 'p_walk'];
    s1.sun = ['p_walk'];

    var s2 = emptySchedule();
    s2.mon = ['s_calc', 's_korean', 's_simon', 'p_talk'];
    s2.tue = ['s_calc', 's_dict', 's_simon'];
    s2.wed = ['s_calc', 's_korean', 's_simon', 'p_talk'];
    s2.thu = ['s_calc', 's_dict'];
    s2.fri = ['s_calc', 's_korean', 's_simon', 'p_talk'];
    s2.sat = ['p_walk'];
    s2.sun = [];

    return {
      version: 3,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      startDate: today,
      pin: '8282',
      children: {
        '1': { name: '첫째', emoji: '🦊', grade: '초4' },
        '2': { name: '둘째', emoji: '🐻', grade: '초2' }
      },
      items: items,
      schedule: { '1': s1, '2': s2 },
      dayplan: { '1': defaultDayPlan('1'), '2': defaultDayPlan('2') },
      completions: { '1': {}, '2': {} },
      settled: { '1': {}, '2': {} },
      log: [],
      timeLog: [],
      rewards: [
        { id: uid('rw'), name: '주말 치킨', cost: 30, note: '' },
        { id: uid('rw'), name: '게임 30분 추가', cost: 15, note: '' }
      ],
      penaltyRules: [
        { id: uid('pr'), name: '주말 유튜브 금지', threshold: 10 }
      ],
      settings: {
        firebaseUrl: '',
        syncEnabled: false,
        sound: true,
        autoSettle: true,
        wake: { enabled: true, time: '07:30' },
        sleep: { enabled: true, time: '21:30' },
        lastAlarms: {}
      },
      ui: { child: '1', view: 'today', period: 'day' }
    };
  }

  function migrate(s) {
    var def = defaultState();
    if (!s || typeof s !== 'object' || !Array.isArray(s.items)) return def;
    s.version = 3;
    s.startDate = s.startDate || ymd(new Date(s.createdAt || Date.now()));
    s.pin = s.pin || '8282';
    s.children = s.children || def.children;
    ['1', '2'].forEach(function (c) { if (!s.children[c]) s.children[c] = def.children[c]; });
    s.schedule = s.schedule || {};
    s.dayplan = s.dayplan || {};
    ['1', '2'].forEach(function (c) {
      if (!s.schedule[c]) s.schedule[c] = emptySchedule();
      DAYS.forEach(function (d) { if (!Array.isArray(s.schedule[c][d])) s.schedule[c][d] = []; });
      if (!s.dayplan[c]) s.dayplan[c] = defaultDayPlan(c);
      DAYS.forEach(function (d) {
        if (!s.dayplan[c][d]) s.dayplan[c][d] = { out: '13:30', bed: '21:30', blocks: [] };
        s.dayplan[c][d].blocks = Array.isArray(s.dayplan[c][d].blocks) ? s.dayplan[c][d].blocks : [];
        s.dayplan[c][d].out = s.dayplan[c][d].out || '13:30';
        s.dayplan[c][d].bed = s.dayplan[c][d].bed || '21:30';
      });
    });
    s.completions = s.completions || { '1': {}, '2': {} };
    s.settled = s.settled || { '1': {}, '2': {} };
    ['1', '2'].forEach(function (c) { s.completions[c] = s.completions[c] || {}; s.settled[c] = s.settled[c] || {}; });
    s.log = Array.isArray(s.log) ? s.log : [];
    s.timeLog = Array.isArray(s.timeLog) ? s.timeLog : [];
    s.rewards = Array.isArray(s.rewards) ? s.rewards : [];
    s.penaltyRules = Array.isArray(s.penaltyRules) ? s.penaltyRules : [];
    s.settings = s.settings || {};
    s.settings.firebaseUrl = s.settings.firebaseUrl || '';
    s.settings.syncEnabled = !!s.settings.syncEnabled;
    s.settings.sound = s.settings.sound !== false;
    s.settings.autoSettle = s.settings.autoSettle !== false;
    s.settings.wake = s.settings.wake || { enabled: true, time: '07:30' };
    s.settings.sleep = s.settings.sleep || { enabled: true, time: '21:30' };
    s.settings.lastAlarms = s.settings.lastAlarms || {};
    s.ui = s.ui || {};
    s.ui.child = s.ui.child === '2' ? '2' : '1';
    s.ui.view = s.ui.view || 'today';
    s.ui.period = s.ui.period || 'day';
    if (!s.ui.checkPeriod) s.ui.checkPeriod = 'day';
    s.items.forEach(function (it) {
      if (!it.id) it.id = uid('it');
      it.kind = it.kind || 'study';
      it.slot = it.slot || 'any';
      it.color = it.color || PALETTE[0];
      it.emoji = it.emoji || '';
      it.reward = Number(it.reward) || 0;
      it.penalty = Number(it.penalty) || 0;
      it.time = it.time || '';
      it.minutes = Number(it.minutes) || 0;
      it.timer = it.timer || '';
      it.quick = !!it.quick;
    });
    return s;
  }

  var Store = {
    state: null,
    _subs: [],
    init: function () { this.state = this.read(); return this.state; },
    read: function () {
      try {
        var raw = localStorage.getItem(KEY);
        if (!raw) return defaultState();
        return migrate(JSON.parse(raw));
      } catch (e) {
        console.warn('저장된 데이터를 읽지 못했습니다.', e);
        return defaultState();
      }
    },
    write: function () {
      try { localStorage.setItem(KEY, JSON.stringify(this.state)); }
      catch (e) { console.warn('저장 실패', e); }
    },
    save: function (opts) {
      this.state.updatedAt = Date.now();
      this.write();
      if (!opts || !opts.quiet) this.emit();
    },
    emit: function () {
      var s = this.state;
      this._subs.forEach(function (fn) { try { fn(s); } catch (e) { console.error(e); } });
    },
    subscribe: function (fn) { this._subs.push(fn); },
    replace: function (next, opts) {
      this.state = migrate(next);
      this.write();
      if (!opts || !opts.quiet) this.emit();
    },
    reset: function () { this.state = defaultState(); this.write(); this.emit(); },
    exportJSON: function () { return JSON.stringify(this.state, null, 2); }
  };

  global.RB = {
    Store: Store,
    util: {
      KEY: KEY, DAYS: DAYS, DAY_LABEL: DAY_LABEL, PALETTE: PALETTE,
      pad: pad, today: function () { return ymd(); }, ymd: ymd, parseYmd: parseYmd,
      addDays: addDays, dayKeyOf: dayKeyOf, uid: uid, clone: clone, emptySchedule: emptySchedule
    }
  };
})(window);
