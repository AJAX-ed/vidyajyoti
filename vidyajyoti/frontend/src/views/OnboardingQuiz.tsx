import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { toast } from "sonner";
import {
  BookOpen, Sunrise, Moon, Sun, Utensils, Brain, CalendarDays, ChevronLeft, ChevronRight,
  School, Coffee, Bus, Dumbbell, ShowerHead, Sparkles,
} from "lucide-react";

/* ================= Types ================= */
export type SlotType = "study" | "meal" | "school" | "routine" | "break" | "sleep" | "travel";
export interface Slot {
  start: number; // minutes since 00:00
  end: number;
  type: SlotType;
  label: string;
}

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:8000";

/* ================= Time helpers ================= */
function t2m(timeString: string): number {
  const [h, m] = timeString.split(":").map(Number);
  return h * 60 + m;
}
function m2t(minutes: number): string {
  const mm = ((minutes % 1440) + 1440) % 1440;
  const h = Math.floor(mm / 60);
  const m = mm % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/* ================= Rule-based day plan generator ================= */
interface Answers {
  educationType: "School" | "Home School" | "Coaching";
  coachingSubtype: "Dummy" | "Residential";
  grade: string;
  exams: string[];
  morningTasks: { label: string; minutes: number }[];
  wakeTime: string;
  sleepTime: string;
  leaveHome: string;   // when school/coaching starts (after commute)
  backHome: string;    // when back home
  commuteMinutes: number;
  coachingStart: string;
  coachingEnd: string;
  breakfast: string;
  lunch: string;
  dinner: string;
  mealsPerDay: number;
  peakProductivity: "Morning" | "Afternoon" | "Evening" | "Night";
  studySessionMinutes: number;
  breakMinutes: number;
}

// The ML service runs on :9000. We ALWAYS route ML calls through the MAIN
// backend (:8000 → /api/ml/* proxy) instead of hitting :9000 directly, so a
// dead ML service can never surface as a raw {"detail":"Not Found"} from some
// other server (e.g. Vite on :3000 or a stale process on :9000).
const ML_ENDPOINTS = {
  schedule: `${BACKEND_URL}/api/ml/schedule-adjust`,
};

/** Call the self-hosted ML schedule optimizer via the main backend.
 *  NEVER blocks the UI: on any error/timeout we keep the locally generated
 *  plan (which is already valid and personalized). */
async function mlScheduleAdjust(plan: Slot[], a: Answers): Promise<Slot[]> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 5000);
    const res = await fetch(ML_ENDPOINTS.schedule, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: ctrl.signal,
      body: JSON.stringify({
        user_id: 1,
        // Send minutes relative to THIS user's wake time so the ML service
        // cannot mis-derive a "day start" from absolute clock values.
        wake: t2m(a.wakeTime),
        sleep: (() => { let s = t2m(a.sleepTime); if (s <= t2m(a.wakeTime)) s += 1440; return s; })(),
        plan: plan.filter((s) => s.type !== "sleep").map((s) => ({ start: s.start, end: s.end, type: s.type, label: s.label })),
        history: {},
        peak_productivity: a.peakProductivity,
        session_minutes: a.studySessionMinutes,
        break_minutes: a.breakMinutes,
      }),
    });
    clearTimeout(timer);
    if (!res.ok) return plan; // ML offline → local plan stands (no white screen, no error toast)
    const ct = res.headers.get("content-type") || "";
    if (!ct.includes("json")) return plan; // guard against HTML/404 pages
    const data = await res.json();
    if (!data || !Array.isArray(data.plan)) return plan; // e.g. {"detail":"Not Found"}
    const mlPlan: Slot[] = data.plan.filter(
      (s: any) => typeof s?.start === "number" && typeof s?.end === "number" && s.end > s.start &&
                  typeof s.type === "string");
    if (!mlPlan.length) return plan;
    const mlStudy = mlPlan.filter((s) => s.type === "study").reduce((n, s) => n + s.end - s.start, 0);
    const localStudy = plan.filter((s) => s.type === "study").reduce((n, s) => n + s.end - s.start, 0);
    if (mlStudy < localStudy) return plan; // local plan already better — keep it
    return withSleepBookends(mlPlan, plan);
  } catch {
    return plan; // ML service offline — deterministic local plan stands
  }
}

function withSleepBookends(inner: Slot[], reference: Slot[]): Slot[] {
  // ML plans use the same wrap-around canvas (minutes may exceed 1439 for a
  // person who sleeps past midnight). Rebuild the sleep bookends from the
  // REFERENCE plan's real wake/sleep boundaries instead of assuming 00:00.
  const refSleep = reference.filter((s) => s.type === "sleep");
  const first = Math.min(...inner.map((s) => s.start));
  const last = Math.max(...inner.map((s) => s.end));
  let wakeAt = first;
  let sleepStart = last;
  if (refSleep.length) {
    // earliest sleep block that ends at/just before the day's first activity
    const prev = refSleep.find((s) => s.end <= first + 1);
    if (prev) wakeAt = prev.end;
    const nxt = [...refSleep].sort((a, b) => a.start - b.start).find((s) => s.start >= last - 1);
    if (nxt) sleepStart = nxt.start;
  }
  const out: Slot[] = [];
  if (wakeAt > 0 && wakeAt > sleepStart - 1440) {
    out.push({ start: Math.max(0, sleepStart - 1440), end: wakeAt, type: "sleep", label: "Sleep" });
  }
  out.push(...dayOrder(inner, first));
  if (sleepStart < wakeAt + 1440) {
    out.push({ start: sleepStart, end: Math.min(wakeAt + 1440, 2879), type: "sleep", label: "Sleep until tomorrow" });
  }
  void reference;
  return out;
}

/* Peak-productivity windows (minutes since midnight) */
const PEAK_WINDOWS: Record<Answers["peakProductivity"], [number, number]> = {
  Morning: [5 * 60, 12 * 60],
  Afternoon: [12 * 60, 17 * 60],
  Evening: [17 * 60, 22 * 60],
  Night: [20 * 60, 23 * 60 + 59],
};

function overlapsPeak(s: number, e: number, peak: [number, number]): boolean {
  return s < peak[1] && e > peak[0];
}

/** Chronological order across a wrap-around day canvas. Slots whose start is
 *  before the day's first block actually belong to the NEXT calendar day
 *  (e.g. wake 23:00 → dinner at 00:30 comes AFTER evening study, not before). */
function dayOrder(slots: Slot[], dayStart: number): Slot[] {
  const key = (s: number) => (((s - dayStart) % 1440) + 1440) % 1440;
  return [...slots].sort((x, y) => key(x.start) - key(y.start));
}

function fillStudySessions(
  slots: Slot[], start: number, end: number,
  sessionMin: number, breakMin: number, counter: { n: number },
  peak: [number, number]
) {
  // Sanitize inputs so the loop can never stall or produce 0-hour plans
  const sess = Math.min(Math.max(sessionMin || 50, 15), 180);
  const brk = Math.min(Math.max(breakMin || 10, 5), 30);
  const gap = end - start;
  if (gap < 20) {
    if (gap >= 5) slots.push({ start, end, type: "break", label: "Short break / free time" });
    return;
  }
  let cur = start;
  // If even one session + trailing room doesn't fit, still cover the window
  // with breaks/free time — never leave unaccounted gaps inside the day.
  if (end - cur < sess) {
    if (end - cur >= 5) slots.push({ start: cur, end, type: "break", label: "Free time" });
    return;
  }
  let guard = 0; // hard safety cap — impossible to hang
  while (end - cur >= sess && guard++ < 100) {
    counter.n += 1;
    const isPeak = overlapsPeak(cur, cur + sess, peak);
    slots.push({
      start: cur, end: cur + sess, type: "study",
      label: `Study session ${counter.n}${isPeak ? " ⚡ peak focus" : ""}`,
    });
    cur += sess;
    // add a break only if a full session still fits after it
    if (end - cur >= brk + sess) {
      slots.push({ start: cur, end: cur + brk, type: "break", label: "Break" });
      cur += brk;
    } else {
      break;
    }
  }
  const leftover = end - cur;
  if (leftover >= 5) slots.push({ start: cur, end, type: "break", label: "Free time" });
}

function generateDayPlan(a: Answers): Slot[] {
  /* ---- Personalized anchors: every timestamp comes from THIS user's answers.
     Nothing in the plan is a hardcoded default unless the user left a field
     blank. Times live on a 0–1439 clock and may cross midnight (night owls). */
  const rawWake = t2m(a.wakeTime || "06:00");
  let rawSleep = t2m(a.sleepTime || "22:00");
  if (rawSleep <= rawWake) rawSleep += 1440;            // sleep later that night / next morning
  const sleepHours = (rawSleep - rawWake) / 60;
  if (sleepHours < 4 || sleepHours > 14) {              // implausible entry → keep 7–9 h
    rawSleep = rawWake + (sleepHours < 4 ? 7.5 : 9) * 60;
  }
  // NO shifting of the user's clock times anymore (that was the "wakes at 4:29"
  // bug). Instead we use a WRAP-AROUND canvas: minutes ≥ 1440 mean "next day"
  // and are displayed modulo 1440 in chronological order.

  const norm = (ts: string, fallback: number) => {
    try { return isNaN(t2m(ts)) ? fallback : t2m(ts); } catch { return fallback; }
  };

  /* Canonicalize every clock time onto THIS person's day canvas [wake, wake+1440):
     a wall time earlier than the wake minute belongs to the same waking day
     (e.g. wake 10:00 → "08:00" means tonight 20:00; wake 01:57 → "00:30" means
     01:30 after wake). Applied uniformly to routines, meals, school and
     coaching so nothing can ever be scheduled before the user gets up. */
  const onCanvas = (mins: number) => {
    let m = mins;
    while (m < rawWake) m += 1440;
    while (m >= rawWake + 1440) m -= 1440;
    return m;
  };

  const fixed: Slot[] = [];

  const isSchoolTrack = a.educationType === "School" ||
    (a.educationType === "Coaching" && a.coachingSubtype === "Dummy");
  /* Canonicalize each anchor against the RAW wake minute first — this keeps
     the leave→back (and start→end) ORDER intact across midnight, e.g.
     leave 23:00 → 1380, back 00:30 → 1470 (not 30 before 1380). */
  const lv = onCanvas(norm(a.leaveHome, rawWake % 1440));
  const bk = onCanvas(norm(a.backHome, rawWake % 1440));
  const st = onCanvas(norm(a.coachingStart, rawWake % 1440));
  const en = onCanvas(norm(a.coachingEnd, rawWake % 1440));
  const leave = lv;
  const back = bk > lv ? bk : lv + 30;   // nonsensical same-time entry → min duration
  const cs = st;
  const ce = en > st ? en : st + 30;
  const schoolStart = isSchoolTrack ? leave
    : (a.educationType === "Coaching" && a.coachingSubtype === "Residential") ? cs
    : rawSleep; // Home School: no external anchor

  // Morning routine starts exactly when THIS user wakes up — but NEVER runs
  // into school/coaching: it squeezes between wake and the first hard anchor.
  const routineLimit = Math.min(rawSleep, Math.max(schoolStart, rawWake));
  let cursor = rawWake;
  for (const task of a.morningTasks) {
    const dur = Math.max(Number(task.minutes) || 10, 5);
    const rEnd = Math.min(cursor + dur, routineLimit);
    if (rEnd - cursor >= 5) fixed.push({ start: cursor, end: rEnd, type: "routine", label: task.label });
    cursor = rEnd;
    if (cursor >= routineLimit) break;
  }

  // Meals — personalized durations & times, placed inside the waking window.
  const mealDur = a.mealsPerDay >= 5 ? 20 : a.mealsPerDay <= 2 ? 30 : undefined;
  const addMeal = (label: string, rawMin: number, dur: number) => {
    let m = onCanvas(rawMin);
    if (m > rawSleep - dur) m -= 1440;                    // occurrence too late today
    if (m < rawWake) m = rawWake;                          // truly no room → at wake
    m = Math.max(m, cursor);                               // after morning routine
    if (label === "Breakfast") {                           // must fit BETWEEN wake and school
      const latest = schoolStart - dur;
      if (m > latest) m = Math.max(latest, rawWake);       // slide back, never past school
    }
    m = Math.min(m, rawSleep - dur);
    m = Math.max(m, rawWake);
    if (m + dur > m) fixed.push({ start: m, end: m + dur, type: "meal", label });
  };
  addMeal("Breakfast", norm(a.breakfast, rawWake % 1440), mealDur ?? 20);
  addMeal("Lunch", norm(a.lunch, rawWake % 1440), mealDur ?? 30);
  addMeal("Dinner", norm(a.dinner, rawWake % 1440), mealDur ?? 30);

  // School / dummy coaching + commute — anchored to THIS user's times.
  if (isSchoolTrack) {
    const comm = Math.max(Number(a.commuteMinutes) || 0, 0);
    const travelOut = Math.max(leave - comm, rawWake);
    if (leave > travelOut) fixed.push({ start: travelOut, end: leave, type: "travel", label: "Travel to school/coaching" });
    if (Math.min(back, rawSleep) > leave) fixed.push({ start: leave, end: Math.min(back, rawSleep), type: "school", label: a.educationType === "School" ? "School" : "Dummy school" });
    if (back > leave && back + comm <= rawSleep) fixed.push({ start: back, end: back + comm, type: "travel", label: "Travel home" });
  }
  // Residential coaching
  if (a.educationType === "Coaching" && a.coachingSubtype === "Residential") {
    if (Math.min(ce, rawSleep) > cs) fixed.push({ start: cs, end: Math.min(ce, rawSleep), type: "school", label: "Coaching" });
  }

  // Drop zero/negative-length artifacts and blocks outside the waking window
  const valid = fixed.filter((b) => b.end > b.start && b.start >= rawWake && b.start < rawSleep);

  // Resolve overlaps with priority clipping: immovable blocks (school/travel)
  // win over meals, which win over the morning-routine chain.
  const rank: Record<SlotType, number> = {
    sleep: 0, school: 1, travel: 2, meal: 3, routine: 4, study: 5, break: 6,
  };
  // Order by type priority, then chronologically on THIS person's canvas
  // (a dummy-school "leave at 23:00, back at 00:30" pair must keep its order
  // even though 00:30 < 23:00 on the raw wall clock).
  const ckey = (m: number) => (((m - rawWake) % 1440) + 1440) % 1440;
  const ordered = [...valid].sort(
    (x, y) => rank[x.type] - rank[y.type] || ckey(x.start) - ckey(y.start)
  );

  type IV = { s: number; e: number };
  let claimed: IV[] = []; // sorted, non-overlapping "already taken" intervals

  const claim = (s: number, e: number) => {
    if (e <= s) return;
    const out: IV[] = [];
    for (const c of claimed) {
      if (c.e <= s || c.s >= e) out.push(c);            // untouched
      else { if (c.s < s) out.push({ s: c.s, e: s }); } // keep left fragment
    }
    out.push({ s, e });
    out.sort((a, b) => a.s - b.s);
    const mergedIv: IV[] = [];
    for (const iv of out) {
      const last = mergedIv[mergedIv.length - 1];
      if (last && iv.s <= last.e) last.e = Math.max(last.e, iv.e);
      else mergedIv.push({ ...iv });
    }
    claimed = mergedIv;
  };
  const free = (s: number, e: number): boolean =>
    claimed.every((c) => c.e <= s || c.s >= e);

  const merged: Slot[] = [];
  for (const b of ordered) {
    if (free(b.start, b.end)) {
      merged.push({ ...b });
      claim(b.start, b.end);
    } else if (b.type === "meal") {
      // nearest free window of the same length. Breakfast searches BACKWARD
      // first (it must stay before school); other meals search forward.
      const dur = b.end - b.start;
      let placed = false;
      const limit = b.label === "Breakfast" ? Math.min(rawSleep, schoolStart) : rawSleep;
      const tryAt = (s: number) => {
        if (s >= rawWake && s + dur <= limit && free(s, s + dur)) {
          merged.push({ ...b, start: s, end: s + dur });
          claim(s, s + dur);
          placed = true;
          return true;
        }
        return false;
      };
      if (b.label === "Breakfast") {
        for (let s = b.start; !placed && s - dur >= rawWake; s -= 5) tryAt(s);
      }
      for (let s = b.start; !placed && s + dur <= limit; s += 5) tryAt(s);
      // last resort: drop a meal only when literally nowhere fits
    } else if (b.type === "routine") {
      // trim the routine's tail so it ends where the first claimed interval begins
      const cut = claimed.find((c) => c.s > b.start && c.s < b.end)?.s ?? b.start;
      if (cut - b.start >= 5) {
        merged.push({ ...b, end: cut });
        claim(b.start, cut);
      }
    }
    // school/travel fully overlapping another immovable block: drop silently
  }

  // Walk from wake → sleep, filling gaps with study sessions. All blocks are
  // guaranteed to lie inside [rawWake, rawSleep] by the clamps above, so a
  // simple chronological walk is exact (no wrap ambiguity left).
  const peak = PEAK_WINDOWS[a.peakProductivity] || PEAK_WINDOWS.Morning;
  const slots: Slot[] = [];
  const counter = { n: 0 };
  let cur = rawWake;
  for (const block of [...merged].sort((x, y) => x.start - y.start)) {
    if (block.end <= cur) continue;              // fully overlapped — handled by merge
    if (block.start > cur) fillStudySessions(slots, cur, block.start, a.studySessionMinutes, a.breakMinutes, counter, peak);
    slots.push(block);
    cur = Math.max(cur, block.end);
  }
  if (cur < rawSleep) fillStudySessions(slots, cur, rawSleep, a.studySessionMinutes, a.breakMinutes, counter, peak);

  // Sleep bookends on the wrap-around canvas (displayed mod 1440, in order):
  //   previous-day sleep  [rawSleep - 1440 , rawWake]   (only if wake ≠ 00:00)
  //   next-day sleep      [rawSleep , rawWake + 1440]   (always)
  if (rawWake > 0) slots.push({ start: rawSleep - 1440, end: rawWake, type: "sleep", label: "Sleep" });
  slots.push({ start: rawSleep, end: rawWake + 1440, type: "sleep", label: "Sleep until tomorrow" });

  // Chronological sort on the canvas starting at the earliest block (prev-day
  // sleep when present, otherwise wake), then clip any residual overlap.
  const dayStart = Math.min(...slots.map((s) => s.start));
  const out = dayOrder(
    slots.map((s) => ({ ...s })).filter((s) => s.end > s.start),
    dayStart
  );
  for (let i = 1; i < out.length; i++) {
    if (out[i].start < out[i - 1].end) out[i].start = out[i - 1].end;
  }
  return out.filter((s) => s.end > s.start);
}

/* ================= Small UI helpers ================= */
const Pill = ({ active, onClick, children }: any) => (
  <button
    onClick={onClick}
    className={`px-4 py-2 rounded-full border text-sm font-medium transition ${
      active ? "gradient-emerald text-white border-transparent" : "bg-surface border-app text-app bg-surface-hover"
    }`}
  >
    {children}
  </button>
);

const PRESET_TASKS = [
  { label: "Brush & freshen up", minutes: 10, icon: ShowerHead },
  { label: "Exercise / yoga", minutes: 30, icon: Dumbbell },
  { label: "Meditation / prayer", minutes: 15, icon: Sparkles },
  { label: "Reading / revision", minutes: 20, icon: BookOpen },
];

/* ================= Component ================= */
export default function OnboardingQuiz({ onComplete }: { onComplete: () => void }) {
  const { theme, toggleTheme } = useThemeLocal();
  const [step, setStep] = useState(0);
  const [dayPlan, setDayPlan] = useState<Slot[]>([]);
  const [saving, setSaving] = useState(false);

  const [a, setA] = useState<Answers>({
    educationType: "School", coachingSubtype: "Dummy", grade: "11", exams: ["JEE Main"],
    morningTasks: [{ label: "Brush & freshen up", minutes: 10 }],
    wakeTime: "06:00", sleepTime: "22:30", leaveHome: "08:00", backHome: "15:00",
    commuteMinutes: 30, coachingStart: "09:00", coachingEnd: "17:00",
    breakfast: "07:00", lunch: "13:00", dinner: "20:00", mealsPerDay: 3,
    peakProductivity: "Morning", studySessionMinutes: 50, breakMinutes: 10,
  });
  const set = (patch: Partial<Answers>) => setA((p) => ({ ...p, ...patch }));
  const [customTask, setCustomTask] = useState("");
  const [customMin, setCustomMin] = useState("20");

  const next = async () => {
    if (step === 5) {
      const plan = generateDayPlan(a);
      // Personalization pass by the SELF-HOSTED ML optimizer (via backend
      // proxy). Non-blocking: any failure keeps the local personalized plan.
      let finalPlan = plan;
      try { finalPlan = await mlScheduleAdjust(plan, a); } catch {}
      setDayPlan(finalPlan);
      try {
        localStorage.setItem("vj_day_plan", JSON.stringify(finalPlan));
        localStorage.setItem("vj_onboarding_data", JSON.stringify(a));
      } catch {}
    }
    if (step < 6) setStep(step + 1);
  };

  const finish = async () => {
    setSaving(true);
    try {
      await fetch(`${BACKEND_URL}/api/onboarding/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: 1, data: a, plan: dayPlan }),
      });
      toast.success("Day plan saved! (backend offline is OK — stored locally)");
    } catch {
      toast.info("Backend not reachable — plan kept in your browser.");
    }
    setSaving(false);
    onComplete();
  };

  const totalStudy = dayPlan.filter((s) => s.type === "study").reduce((n, s) => n + (s.end - s.start), 0);
  const nSessions = dayPlan.filter((s) => s.type === "study").length;
  const studyH = Math.floor(totalStudy / 60);
  const studyM = totalStudy % 60;

  return (
    <div className="min-h-screen bg-app text-app flex items-center justify-center p-4">
      <button onClick={toggleTheme} className="fixed top-4 right-4 z-50 p-2 rounded-lg border border-app bg-surface">
        {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
      </button>
      <div className="w-full max-w-2xl bg-surface border border-app rounded-2xl p-6 md:p-8 shadow-xl">
        {/* Progress */}
        <div className="flex items-center gap-2 mb-6">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className={`h-1.5 flex-1 rounded-full ${i <= step ? "gradient-emerald" : "bg-[var(--border)]"}`} />
          ))}
        </div>
        <p className="text-muted text-xs mb-1">Step {step + 1} of 7</p>

        <AnimatePresence mode="wait">
          <motion.div
            key={step}
            initial={{ opacity: 0, x: 30 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -30 }}
            transition={{ duration: 0.25 }}
          >
            {/* STEP 0 — education type */}
            {step === 0 && (
              <Step title="How do you study?" icon={<School size={20} />}>
                <div className="flex flex-wrap gap-2">
                  {(["School", "Home School", "Coaching"] as const).map((t) => (
                    <Pill key={t} active={a.educationType === t} onClick={() => set({ educationType: t })}>{t}</Pill>
                  ))}
                </div>
                {a.educationType === "Coaching" && (
                  <div className="mt-4 flex gap-2">
                    {(["Dummy", "Residential"] as const).map((t) => (
                      <Pill key={t} active={a.coachingSubtype === t} onClick={() => set({ coachingSubtype: t })}>{t} Coaching</Pill>
                    ))}
                  </div>
                )}
              </Step>
            )}

            {/* STEP 1 — grade + exams */}
            {step === 1 && (
              <Step title="Grade & target exams" icon={<BookOpen size={20} />}>
                <select value={a.grade} onChange={(e) => set({ grade: e.target.value })} className="mb-4">
                  {["8","9","10","11","12","Drop year"].map((g) => <option key={g}>{g}</option>)}
                </select>
                <div className="flex flex-wrap gap-2">
                  {["JEE Main", "JEE Advanced", "NEET", "CBSE Boards", "SSC", "Olympiads"].map((ex) => (
                    <Pill key={ex} active={a.exams.includes(ex)}
                      onClick={() => set({ exams: a.exams.includes(ex) ? a.exams.filter((x) => x !== ex) : [...a.exams, ex] })}>
                      {ex}
                    </Pill>
                  ))}
                </div>
              </Step>
            )}

            {/* STEP 2 — morning routine */}
            {step === 2 && (
              <Step title="Morning routine" icon={<Sunrise size={20} />}>
                <div className="flex flex-wrap gap-2 mb-4">
                  {PRESET_TASKS.map((t) => {
                    const on = a.morningTasks.some((m) => m.label === t.label);
                    return (
                      <Pill key={t.label} active={on}
                        onClick={() => set({ morningTasks: on ? a.morningTasks.filter((m) => m.label !== t.label) : [...a.morningTasks, { label: t.label, minutes: t.minutes }] })}>
                        {t.label} ({t.minutes}m)
                      </Pill>
                    );
                  })}
                </div>
                <div className="flex gap-2">
                  <input placeholder="Custom task…" value={customTask} onChange={(e) => setCustomTask(e.target.value)} />
                  <input type="number" placeholder="min" value={customMin} onChange={(e) => setCustomMin(e.target.value)} className="!w-24" />
                  <button className="px-4 rounded-lg gradient-emerald text-white whitespace-nowrap"
                    onClick={() => { if (customTask.trim()) { set({ morningTasks: [...a.morningTasks, { label: customTask.trim(), minutes: Number(customMin) || 15 }] }); setCustomTask(""); } }}>
                    Add
                  </button>
                </div>
              </Step>
            )}

            {/* STEP 3 — schedule */}
            {step === 3 && (
              <Step title="Daily schedule" icon={<CalendarDays size={20} />}>
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Wake up"><input type="time" value={a.wakeTime} onChange={(e) => set({ wakeTime: e.target.value })} /></Field>
                  <Field label="Sleep"><input type="time" value={a.sleepTime} onChange={(e) => set({ sleepTime: e.target.value })} /></Field>
                  {(a.educationType === "School" || a.coachingSubtype === "Dummy") && (<>
                    <Field label="Leave home"><input type="time" value={a.leaveHome} onChange={(e) => set({ leaveHome: e.target.value })} /></Field>
                    <Field label="Back home"><input type="time" value={a.backHome} onChange={(e) => set({ backHome: e.target.value })} /></Field>
                    <Field label="Commute (one way, min)"><input type="number" value={a.commuteMinutes} onChange={(e) => set({ commuteMinutes: Number(e.target.value) || 0 })} /></Field>
                  </>)}
                  {a.educationType === "Coaching" && a.coachingSubtype === "Residential" && (<>
                    <Field label="Coaching start"><input type="time" value={a.coachingStart} onChange={(e) => set({ coachingStart: e.target.value })} /></Field>
                    <Field label="Coaching end"><input type="time" value={a.coachingEnd} onChange={(e) => set({ coachingEnd: e.target.value })} /></Field>
                  </>)}
                </div>
              </Step>
            )}

            {/* STEP 4 — meals */}
            {step === 4 && (
              <Step title="Meals" icon={<Utensils size={20} />}>
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Breakfast"><input type="time" value={a.breakfast} onChange={(e) => set({ breakfast: e.target.value })} /></Field>
                  <Field label="Lunch"><input type="time" value={a.lunch} onChange={(e) => set({ lunch: e.target.value })} /></Field>
                  <Field label="Dinner"><input type="time" value={a.dinner} onChange={(e) => set({ dinner: e.target.value })} /></Field>
                  <Field label="Meals per day"><input type="number" min={2} max={6} value={a.mealsPerDay} onChange={(e) => set({ mealsPerDay: Number(e.target.value) })} /></Field>
                </div>
              </Step>
            )}

            {/* STEP 5 — study preferences */}
            {step === 5 && (
              <Step title="Study preferences" icon={<Brain size={20} />}>
                <p className="text-muted text-sm mb-2">Peak productivity time</p>
                <div className="flex flex-wrap gap-2 mb-4">
                  {(["Morning", "Afternoon", "Evening", "Night"] as const).map((p) => (
                    <Pill key={p} active={a.peakProductivity === p} onClick={() => set({ peakProductivity: p })}>{p}</Pill>
                  ))}
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Study session length (min)"><input type="number" value={a.studySessionMinutes} onChange={(e) => set({ studySessionMinutes: Number(e.target.value) || 50 })} /></Field>
                  <Field label="Break length (min)"><input type="number" value={a.breakMinutes} onChange={(e) => set({ breakMinutes: Number(e.target.value) || 10 })} /></Field>
                </div>
              </Step>
            )}

            {/* STEP 6 — generated plan */}
            {step === 6 && (
              <Step title="Your day plan ✨" icon={<Coffee size={20} />}>
                <div className="flex gap-3 mb-4 text-xs">
                  <span className="px-3 py-1 rounded-full bg-surface border border-app">{nSessions} study sessions</span>
                  <span className="px-3 py-1 rounded-full bg-surface border border-app">{studyH}h {studyM}m study/day</span>
                  <span className="px-3 py-1 rounded-full bg-surface border border-app">{dayPlan.length} blocks</span>
                </div>
                <div className="max-h-80 overflow-y-auto pr-2 space-y-1">
                  {dayPlan.map((s, i) => (
                    <div key={i} className="flex items-center gap-3 p-2 rounded-lg bg-[var(--bg)] border border-app text-sm">
                      <span className="font-mono text-xs text-muted w-24">{m2t(s.start)}–{m2t(s.end)}</span>
                      <TypeIcon type={s.type} />
                      <span className="capitalize">{s.label}</span>
                    </div>
                  ))}
                </div>
              </Step>
            )}
          </motion.div>
        </AnimatePresence>

        {/* Nav buttons */}
        <div className="flex justify-between mt-6">
          <button disabled={step === 0} onClick={() => setStep(step - 1)}
            className="flex items-center gap-1 px-4 py-2 rounded-lg border border-app text-muted disabled:opacity-30">
            <ChevronLeft size={16} /> Back
          </button>
          {step < 6 ? (
            <button onClick={next} className="flex items-center gap-1 px-5 py-2 rounded-lg gradient-emerald text-white font-semibold">
              Next <ChevronRight size={16} />
            </button>
          ) : (
            <button onClick={finish} disabled={saving} className="px-5 py-2 rounded-lg gradient-emerald text-white font-semibold disabled:opacity-60">
              {saving ? "Saving…" : "Start Learning 🚀"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/* Reuse the theme hook without circular import issues */
function useThemeLocal() {
  const [theme, setTheme] = useState<"dark" | "light">(() =>
    localStorage.getItem("vj_theme") === "light" ? "light" : "dark");
  const toggleTheme = () => {
    const root = document.documentElement;
    const nextT = theme === "dark" ? "light" : "dark";
    root.classList.remove("dark", "light");
    root.classList.add(nextT);
    localStorage.setItem("vj_theme", nextT);
    setTheme(nextT);
  };
  return { theme, toggleTheme };
}

const Step = ({ title, icon, children }: any) => (
  <div>
    <h2 className="text-xl font-bold mb-4 flex items-center gap-2">{icon}{title}</h2>
    {children}
  </div>
);

const Field = ({ label, children }: any) => (
  <label className="block">
    <span className="text-xs text-muted">{label}</span>
    {children}
  </label>
);

const TypeIcon = ({ type }: { type: SlotType }) => {
  const map: Record<SlotType, any> = {
    study: BookOpen, meal: Utensils, school: School, routine: Sunrise,
    break: Coffee, sleep: Moon, travel: Bus,
  };
  const I = map[type];
  const colors: Record<SlotType, string> = {
    study: "text-[var(--primary)]", meal: "text-orange-400", school: "text-sky-400",
    routine: "text-purple-400", break: "text-yellow-400", sleep: "text-indigo-400", travel: "text-pink-400",
  };
  return <I size={16} className={colors[type]} />;
};
