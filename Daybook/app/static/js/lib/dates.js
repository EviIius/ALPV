// Date helpers. All calendar dates are local "YYYY-MM-DD" strings, never UTC
// (the old app used toISOString(), which showed tomorrow after 7 pm Eastern).
import { getState } from './store.js';

const pad = (n) => String(n).padStart(2, '0');

export const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const today = () => ymd(new Date());
export function parseYmd(s) {
  const [y, m, d] = s.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, d);
}
export function parseLocal(s) {
  if (!s) return null;
  if (s.length <= 10) return parseYmd(s);
  const [date, time] = s.split('T');
  const d = parseYmd(date);
  const [h, mi] = time.split(':').map(Number);
  d.setHours(h, mi, 0, 0);
  return d;
}
export const toLocalDT = (d) => `${ymd(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
export const hm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
export function addDays(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; }
export function addMonths(d, n) { const x = new Date(d.getFullYear(), d.getMonth() + n, 1); return x; }
export function startOfWeek(d, weekStart = getWeekStart()) {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diff = (x.getDay() - weekStart + 7) % 7;
  return addDays(x, -diff);
}
export function getWeekStart() {
  const s = getState().settings;
  return s ? Number(s.appearance.week_start || 0) : 0;
}
export function monthGrid(year, month, weekStart = getWeekStart()) {
  const first = new Date(year, month, 1);
  const start = startOfWeek(first, weekStart);
  const days = [];
  for (let i = 0; i < 42; i++) days.push(addDays(start, i));
  // Trim a trailing week that's entirely next month.
  if (days[35].getMonth() !== month) return days.slice(0, 35);
  return days;
}
export function dayOfYear(d) {
  const start = new Date(d.getFullYear(), 0, 0);
  return Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()) - start) / 86400000);
}
export const daysInYear = (y) => ((y % 4 === 0 && y % 100 !== 0) || y % 400 === 0 ? 366 : 365);
export function isoWeek(d) {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const y0 = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  return Math.ceil(((t - y0) / 86400000 + 1) / 7);
}

const is24 = () => (getState().settings?.appearance?.time_format === '24h');
export function fmtTime(hhmm) {
  if (!hhmm) return '';
  const [h, m] = hhmm.split(':').map(Number);
  if (is24()) return `${pad(h)}:${pad(m)}`;
  const suffix = h < 12 ? 'AM' : 'PM';
  const h12 = h % 12 || 12;
  return m ? `${h12}:${pad(m)} ${suffix}` : `${h12} ${suffix}`;
}
export function fmtHour(h) {
  if (is24()) return `${pad(h)}:00`;
  return `${h % 12 || 12} ${h < 12 ? 'AM' : 'PM'}`;
}
export const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
export const WD_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
export const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
export const MON_SHORT = MONTHS.map((m) => m.slice(0, 3));

export function fmtDate(s, { weekday = false, year = 'auto' } = {}) {
  if (!s) return '';
  const d = typeof s === 'string' ? parseYmd(s) : s;
  const showYear = year === true || (year === 'auto' && d.getFullYear() !== new Date().getFullYear());
  return `${weekday ? WD_SHORT[d.getDay()] + ', ' : ''}${MON_SHORT[d.getMonth()]} ${d.getDate()}${showYear ? ', ' + d.getFullYear() : ''}`;
}
export function fmtDayRelative(s) {
  const t = today();
  if (s === t) return 'Today';
  if (s === ymd(addDays(new Date(), 1))) return 'Tomorrow';
  if (s === ymd(addDays(new Date(), -1))) return 'Yesterday';
  const d = parseYmd(s);
  const diff = Math.round((d - parseYmd(t)) / 86400000);
  if (diff > 1 && diff < 7) return WEEKDAYS[d.getDay()];
  return fmtDate(s);
}
export function fmtAgo(ms) {
  if (!ms) return '';
  const s = Math.round((Date.now() - ms) / 1000);
  if (s < 45) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = new Date(ms);
  if (s < 6 * 86400) return WEEKDAYS[d.getDay()];
  return fmtDate(ymd(d));
}
export function fmtRange(ev) {
  if (ev.all_day) {
    return ev.start === ev.end ? 'All day' : `All day, until ${fmtDate(ev.end)}`;
  }
  const s = ev.start.slice(11, 16);
  const e = ev.end.slice(11, 16);
  const sameDay = ev.start.slice(0, 10) === ev.end.slice(0, 10);
  return sameDay ? `${fmtTime(s)} – ${fmtTime(e)}` : `${fmtTime(s)} – ${fmtDate(ev.end.slice(0, 10))} ${fmtTime(e)}`;
}
export function minutes(hhmm) { const [h, m] = hhmm.split(':').map(Number); return h * 60 + m; }
export function fromMinutes(total) {
  const t = ((total % 1440) + 1440) % 1440;
  return `${pad(Math.floor(t / 60))}:${pad(t % 60)}`;
}
export function fmtBytes(n) {
  if (!n) return '0 KB';
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
